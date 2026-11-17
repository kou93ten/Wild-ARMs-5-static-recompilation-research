#!/usr/bin/env python3
"""Make an experimental native checkpoint from a PCSX2 state.

The bridge transfers EE CPU registers/RAM/scratchpad and optionally GS drawing
registers/VRAM. It retains the native checkpoint's HLE kernel, IOP, timing,
DMA, GIF/VIF/VU, disc and pad state. The result is a diagnostic, not a faithful
resumption of the PCSX2 frame.
"""

import argparse
import hashlib
import json
import struct
from pathlib import Path

from import_pcsx2_state import read_cpu


EE_CPU_AT = 20
EE_CPU_SIZE = 1456
EE_RAM_SIZE = 32 << 20
SPR_SIZE = 16 << 10
GS_RAM_SIZE = 4 << 20
GS_STATE_SIZE = 2536
GS_VRAM_AFTER_STATE = 197
PCSX2_GS_VRAM_AT = 425
PCSX2_INTERNAL_GS_AT = 0x16CA
EE_THREAD_SIZE = 76
NATIVE_THREAD_SIZE = 752
NATIVE_CONTEXT_AT = 64


def bridge_ee_threads(native, ram, running_pc):
    """Copy PCSX2 BIOS EE thread records into the native HLE scheduler.

    This is deliberately limited to the six thread IDs already present in the
    native checkpoint. Existing semaphore wait lists are synchronized, but
    semaphore counts, interrupt and device state remain diagnostic gaps.
    """
    pattern = struct.pack("<III", 0xAC420000, 0, 0)
    matches = [at for at in range(0, 0x5000, 4)
               if ram[at:at + len(pattern)] == pattern]
    if len(matches) != 1:
        raise ValueError("cannot uniquely locate PCSX2's BIOS EE thread table")
    op = u32(ram, matches[0] + 24)
    table = (0x10000 + (op & 0xFFFF) - 8) & 0x3FFFFF
    tag = b"eekern\0\0"
    kernel_at = native.find(tag, EE_CPU_AT + EE_CPU_SIZE + EE_RAM_SIZE + SPR_SIZE)
    if kernel_at < 0 or native.find(tag, kernel_at + 1) >= 0:
        raise ValueError("missing or ambiguous native EE kernel section")
    count = u32(native, kernel_at + 8)
    if count != 6:
        raise ValueError(f"expected six native EE threads; found {count}")
    native_threads = kernel_at + 12
    source_threads = []
    source_waiters = {}
    running = []
    running_thread_saved_pc = None
    for tid in range(1, count + 1):
        src = table + tid * EE_THREAD_SIZE
        _, _, status, pc, regctx, gp = struct.unpack_from("<6I", ram, src)
        init_priority, priority = struct.unpack_from("<hh", ram, src + 24)
        wait, wait_id, wakeups, attr, option, entry, _, _, stack, stack_size, _, heap = struct.unpack_from(
            "<12I", ram, src + 28)
        if status not in (1, 2, 4, 8, 12, 16) or wait not in (0, 1, 2):
            raise ValueError(f"invalid PCSX2 thread {tid} status/wait")
        if status == 1:
            running.append(tid)
            # PCSX2's BIOS thread record is the last saved context, whereas
            # cpuRegs holds the live instruction. The record can lag within
            # the same scheduler loop while that thread is running.
            if abs(pc - running_pc) > 0x100 or not any(ram[pc:pc + 16]):
                raise ValueError("running BIOS thread PC is outside the live EE code block")
            running_thread_saved_pc = pc
        if status in (4, 12) and wait == 2:
            source_waiters.setdefault(wait_id, []).append(tid)
        target = native_threads + (tid - 1) * (4 + NATIVE_THREAD_SIZE)
        if u32(native, target) != tid or u32(native, target + 4) != tid:
            raise ValueError("native EE thread IDs/layout differ from expected")
        fields = [status, priority, init_priority, entry, stack, stack_size, gp,
                  heap, option, attr, wakeups, wait, wait_id]
        struct.pack_into("<13I", native, target + 8, *fields)
        ctx_at = regctx & 0x01FFFFFF
        if ctx_at + 640 > len(ram):
            raise ValueError(f"thread {tid} register context outside EE RAM")
        dst = target + 4 + NATIVE_CONTEXT_AT
        if status != 1:
            native[dst + 16:dst + 512] = ram[ctx_at + 16:ctx_at + 512]
            # BIOS uses k0/k1 stack slots for HI/LO; they are not guest GPRs.
            native[dst + 26 * 16:dst + 28 * 16] = b"\0" * 32
            native[dst + 512:dst + 528] = ram[ctx_at + 16 + 25 * 16:ctx_at + 16 + 26 * 16]
            native[dst + 528:dst + 544] = ram[ctx_at + 16 + 26 * 16:ctx_at + 16 + 27 * 16]
            struct.pack_into("<II", native, dst + 544, u32(ram, ctx_at), pc)
            native[dst + 552:dst + 680] = ram[ctx_at + 512:ctx_at + 640]
            struct.pack_into("<II", native, dst + 680,
                             u32(ram, ctx_at + 8), u32(ram, ctx_at + 4))
        source_threads.append({"id": tid, "status": status, "pc": f"0x{pc:08X}",
                               "wait": wait, "wait_id": wait_id})
    if len(running) != 1:
        raise ValueError(f"expected one running BIOS EE thread; found {running}")

    # Locate the scheduler's current-thread ID after the variable-sized maps.
    pos = native_threads + count * (4 + NATIVE_THREAD_SIZE)
    sema_start = pos
    semas = u32(native, pos)
    pos += 4
    sema_bytes = bytearray(struct.pack("<I", semas))
    found_semas = set()
    for _ in range(semas):
        sema_id = u32(native, pos)
        found_semas.add(sema_id)
        waiter_count = u32(native, pos + 24)
        sema_bytes.extend(native[pos:pos + 24])
        waiters = source_waiters.get(sema_id, [])
        sema_bytes.extend(struct.pack("<I", len(waiters)))
        for tid in waiters:
            sema_bytes.extend(struct.pack("<I", tid))
        pos += 28 + waiter_count * 4
    if set(source_waiters) - found_semas:
        raise ValueError("a BIOS thread waits on a semaphore missing from native state")
    for item_size in (20, 20):  # INTC and DMAC handler POD arrays
        entries = u32(native, pos)
        pos += 4 + entries * item_size
    alarms = u32(native, pos)
    pos += 4 + alarms * 28
    if u32(native, pos) != 1:
        raise ValueError("unexpected current thread in base checkpoint")
    struct.pack_into("<I", native, pos, running[0])
    # Replace the map after locating and patching current: its size may change.
    old_sema_end = sema_start + 4
    for _ in range(semas):
        old_sema_end += 28 + 4 * u32(native, old_sema_end + 24)
    native[sema_start:old_sema_end] = sema_bytes
    return {"bios_thread_table": f"0x{table:06X}", "running_thread": running[0],
            "running_thread_saved_pc": f"0x{running_thread_saved_pc:08X}",
            "threads": source_threads, "semaphore_waiters": source_waiters,
            "limit": "native semaphore counts, interrupt and device state retained"}


def u32(data, at):
    return struct.unpack_from("<I", data, at)[0]


def u64(data, at):
    return struct.unpack_from("<Q", data, at)[0]


def copy_bytes(target, target_at, source, source_at, size):
    target[target_at:target_at + size] = source[source_at:source_at + size]


def native_gs_at(state):
    tag = b"gs".ljust(8, b"\0")
    start = EE_CPU_AT + EE_CPU_SIZE + EE_RAM_SIZE + SPR_SIZE
    at = state.find(tag, start)
    while at >= 0:
        vram = at + 8 + GS_STATE_SIZE + GS_VRAM_AFTER_STATE
        if (vram + 2 * GS_RAM_SIZE + 2060 < len(state)
                and u32(state, vram + GS_RAM_SIZE) == 1
                and u32(state, vram + GS_RAM_SIZE + 4) == GS_RAM_SIZE // 4
                and u32(state, vram + 2 * GS_RAM_SIZE + 8) == 512
                and state[vram + 2 * GS_RAM_SIZE + 12 + 2048:
                          vram + 2 * GS_RAM_SIZE + 20 + 2048] == b"gif".ljust(8, b"\0")):
            return at
        at = state.find(tag, at + 1)
    raise ValueError("cannot locate the expected native version-9 GS section")


def bridge(extracted, native_path, output, expected_serial, include_gs, include_threads=False):
    version = (extracted / "PCSX2 Savestate Version.id").read_bytes()
    current_layout = len(version) == 36 and u32(version, 0) == 0x9A590000 and version[4:10] == b"v2.8.2"
    older_ee_layout = len(version) == 36 and u32(version, 0) == 0x9A540000 and version[4:12] == b"v2.5.229"
    if not current_layout and not (older_ee_layout and not include_gs):
        raise ValueError("requires PCSX2 2.8.2, or PCSX2 2.5.229 with --ee-only")
    internal = (extracted / "PCSX2 Internal Structures.dat").read_bytes()
    ram = (extracted / "eeMemory.bin").read_bytes()
    spr = (extracted / "Scratchpad.bin").read_bytes()
    if len(ram) != EE_RAM_SIZE or len(spr) != SPR_SIZE:
        raise ValueError("incorrect EE RAM or scratchpad size")
    cpu = read_cpu(internal, ram, expected_serial)
    pc = int(cpu["ee_pc"], 16)
    native = bytearray(native_path.read_bytes())
    if native[:20] != b"PS2STATE\x09\0\0\0ee\0\0\0\0\0\0":
        raise ValueError("requires a native PS2STATE version-9 checkpoint")
    ram_at = EE_CPU_AT + EE_CPU_SIZE
    if u32(native, ram_at + pc) != u32(ram, pc):
        raise ValueError("native checkpoint and PCSX2 state have different code at EE PC")

    cpu_tag = internal.find(b"cpuRegs".ljust(32, b"\0"))
    if cpu_tag < 0 or internal.find(b"cpuRegs".ljust(32, b"\0"), cpu_tag + 1) >= 0:
        raise ValueError("missing or ambiguous PCSX2 EE CPU section")
    source_cpu = cpu_tag + 32
    copy_bytes(native, EE_CPU_AT, internal, source_cpu, 544)           # GPR, HI, LO
    copy_bytes(native, EE_CPU_AT + 544, internal, source_cpu + 672, 4) # sa
    copy_bytes(native, EE_CPU_AT + 548, internal, source_cpu + 680, 4) # pc
    copy_bytes(native, EE_CPU_AT + 1312, internal, source_cpu + 544, 128) # COP0
    native[ram_at:ram_at + EE_RAM_SIZE] = ram
    native[ram_at + EE_RAM_SIZE:ram_at + EE_RAM_SIZE + SPR_SIZE] = spr

    thread_result = bridge_ee_threads(native, ram, pc) if include_threads else None

    if include_gs:
        gs = (extracted / "GS.bin").read_bytes()
        if len(gs) != PCSX2_GS_VRAM_AT + GS_RAM_SIZE + 84 or u32(gs, 0) != 9:
            raise ValueError("unsupported PCSX2 GS snapshot layout")
        gs_at = native_gs_at(native)
        state_at = gs_at + 8
        vram_at = state_at + GS_STATE_SIZE + GS_VRAM_AFTER_STATE
        vram = gs[PCSX2_GS_VRAM_AT:PCSX2_GS_VRAM_AT + GS_RAM_SIZE]
        native[vram_at:vram_at + GS_RAM_SIZE] = vram
        # Native state holds a second, GPU-side copy when scale is 1.
        gpu_vram_at = vram_at + GS_RAM_SIZE + 8
        native[gpu_vram_at:gpu_vram_at + GS_RAM_SIZE] = vram

        common_regs = [
            (4, 0), (12, 16), (20, 56), (28, 64), (36, 72),
            (44, 80), (52, 88), (60, 96), (68, 104), (76, 112),
            (84, 120), (92, 144), (100, 128), (108, 136),
            (316, 24), (324, 32),
        ]
        for source_at, target_at in common_regs:
            copy_bytes(native, state_at + target_at, gs, source_at, 8)
        copy_bytes(native, state_at + 40, gs, 332, 4)  # UV
        native[state_at + 44:state_at + 48] = b"\0" * 4
        copy_bytes(native, state_at + 48, gs, 336, 4)  # FOG
        native[state_at + 52:state_at + 56] = b"\0" * 4
        for index in range(2):
            source = 124 + index * 96
            target = state_at + 152 + index * 104
            for src_word, dst_word in [
                (0, 4), (1, 0), (2, 2), (3, 1), (4, 5), (5, 6),
                (6, 7), (7, 8), (8, 9), (9, 10), (10, 11), (11, 12),
            ]:
                copy_bytes(native, target + dst_word * 8, gs, source + src_word * 8, 8)
        copy_bytes(native, state_at + 360, gs, len(gs) - 4, 4)  # Q

        bank = PCSX2_INTERNAL_GS_AT
        if u64(internal, bank) != 0x66 or u64(internal, bank + 0x20) != 1:
            raise ValueError("PCSX2 GS privileged register bank has moved")
        target_bank = state_at + GS_STATE_SIZE
        for target_word, source_offset in enumerate([
            0x0, 0x10, 0x20, 0x30, 0x40, 0x50, 0x60,
            0x70, 0x90, 0x80, 0xA0, 0xB0, 0xC0, 0xD0, 0xE0,
            0x1000, 0x1010, 0x1040, 0x1080,
        ]):
            copy_bytes(native, target_bank + target_word * 8,
                       internal, bank + source_offset, 8)

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(native)
    result = {
        "output": str(output),
        "native_base": str(native_path),
        "native_base_sha256": hashlib.sha256(native_path.read_bytes()).hexdigest(),
        "output_sha256": hashlib.sha256(native).hexdigest(),
        "pcsx2_ee_pc": cpu["ee_pc"],
        "included_gs": include_gs,
        "included_ee_threads": include_threads,
        "ee_threads": thread_result,
        "status": ("experimental hybrid; EE CPU/RAM/scratchpad"
                   + (" and BIOS EE thread contexts" if include_threads else "")
                   + (" and GS registers/VRAM" if include_gs else "")
                   + " transferred; native HLE devices retained"),
    }
    output.with_suffix(output.suffix + ".json").write_text(
        json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("extracted", type=Path, help="importer's extracted/ directory")
    parser.add_argument("native_state", type=Path, help="existing native version-9 checkpoint")
    parser.add_argument("output", type=Path, help="new diagnostic .p2state path")
    parser.add_argument("--serial", default="SLUS_216.15")
    parser.add_argument("--ee-only", action="store_true", help="leave native GS state in place")
    parser.add_argument("--ee-threads", action="store_true", help="also import BIOS EE thread contexts (experimental)")
    args = parser.parse_args()
    paths = [p.resolve() for p in (args.extracted, args.native_state, args.output)]
    if paths[2] == paths[1] or paths[2].is_relative_to(paths[0]):
        parser.error("output must not overwrite either source")
    try:
        result = bridge(paths[0], paths[1], paths[2], args.serial, not args.ee_only, args.ee_threads)
    except (OSError, ValueError, struct.error) as exc:
        parser.exit(1, f"bridge failed: {exc}\n")
    print(f"Wrote {result['output']} at EE PC {result['pcsx2_ee_pc']}")
    print(result["status"])


if __name__ == "__main__":
    main()
