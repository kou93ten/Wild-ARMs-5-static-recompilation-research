# Wild ARMs 5 native PC research

An experimental Windows native build of **Wild ARMs 5** (North American PS2 release), built on [ps2-native-recomp](https://github.com/exoteric33/ps2-native-recomp). The project investigates game compatibility, exact-resolution 3D rendering, widescreen presentation, text and HUD quality, frame pacing, and reproducible visual diagnosis.

**Status: active research, not a packaged release.** The current developer build reaches and can be replayed through tested field, menu, and battle scenes. That is not a claim of a complete playthrough or compatibility in every area. Most testing is on Windows with specific saved states; Linux and a full-game validation remain open.

## Current progress

| Area | Current state |
| --- | --- |
| Native execution | The pinned recompiler/runtime plus the project patch runs the tested scenes. Scripted input, save-state capture, and ordinary memory-card saves support repeatable debugging. |
| Rendering | An exact-resolution native scene path is compared frame by frame with the GS renderer. Selected effects still need bounded GS preservation where native composition cannot yet reproduce them. Field camera motion and several battle lighting/effect cases have regression coverage. |
| Display and input | Window, borderless, and fullscreen modes; aspect choices including widescreen; keyboard/controller mappings; and output-dependent render scale are implemented in the development build. |
| Text and HUD | Original game glyphs, outlines, compass art, Force Level display, and controller pictograms are sampled at the presentation scale. This improves enlargement without substituting a different font. |
| Performance diagnosis | An opt-in workflow records at least five seconds of gameplay, frame times, CPU/GPU use, and per-frame rendering costs. Battles target 60 FPS today; higher targets, including 120 FPS, are future work. |
| Automated visual checks | Scripted field and battle replays, paired GS/native screenshots, text/HUD frame checks, and a 36-sequence renderer regression suite across 720p, 1080p, and 4K. These checks cover recorded scenarios, not the entire game. |

The next priorities are broader area and battle coverage, reducing performance dips, eliminating remaining rendering differences, and making the build and test workflow usable without this developer's local setup.

## What is in this repository

- `tools/`: replay, capture, comparison, performance, save, and build-support scripts.
- `patches/ps2-native-recomp/current-runtime.patch`: the complete current runtime delta against upstream commit `b1af7232b3200b12fbb8135ba583364c992f0aa5`.
- `patches/ps2-native-recomp/additions/`: two new runtime files not present in upstream. The RenderDoc API header retains its MIT notice.
- `patches/ps2-native-recomp/0001-*.patch` through `0011-*.patch`: earlier focused investigation patches. **Do not apply these on top of `current-runtime.patch`**; their changes are already represented in the current snapshot.
- `Play *.cmd`: developer launch shortcuts. Several scripts still contain local paths and are not yet portable installers.

The upstream runtime source is cloned into the ignored `external/` directory. The patch is the versioned record of this project's runtime changes; generated builds and extracted game files stay local.

## Preparing the source

On Windows, with Git and PowerShell installed, run from the repository root:

```powershell
.\tools\bootstrap_upstream.ps1
```

This checks out the pinned public upstream commit under `external/ps2-native-recomp`, applies `current-runtime.patch`, and copies the two additional source files. It refuses to overwrite an existing checkout. To verify against a different checkout location, pass `-Target`.

The native game build still requires the user's own PS2 disc image and the upstream toolchain and translation steps described in [ps2-native-recomp's documentation](https://github.com/exoteric33/ps2-native-recomp#readme). Local captures and saved states used by the diagnostic scripts are not distributed here, so the visual suites need user-supplied test states before they can run elsewhere.

## Game data

**No ISO, ROM, boot ELF, BIOS, memory card, save state, extracted archive, or generated game binary is included.** Supply your own legally obtained copy of the game. The repository's ignore rules also keep local captures and toolchains out of Git.

This is an independent research project and is not affiliated with Sony, Media.Vision, or the game's publishers. The upstream recompiler is separately maintained and retains its own MIT license.
