// Extract evenly spaced BMP frames from a local MP4 using Windows Media Foundation.
// Build with llvm-mingw: clang++ -std=c++17 extract_video_frames.cpp -lole32 -lmfplat -lmfreadwrite -lmfuuid -o extract_video_frames.exe
#include <windows.h>
#include <mfapi.h>
#include <mfidl.h>
#include <mfreadwrite.h>
#include <cstdio>
#include <cstdlib>
#include <vector>

static bool save_bmp(const wchar_t *path, const BYTE *pixels, DWORD stride, DWORD width, DWORD height) {
    const DWORD row = width * 4;
    BITMAPFILEHEADER file{};
    BITMAPINFOHEADER info{};
    file.bfType = 0x4D42;
    file.bfOffBits = sizeof(file) + sizeof(info);
    file.bfSize = file.bfOffBits + row * height;
    info.biSize = sizeof(info);
    info.biWidth = static_cast<LONG>(width);
    info.biHeight = -static_cast<LONG>(height);
    info.biPlanes = 1;
    info.biBitCount = 32;
    info.biCompression = BI_RGB;
    FILE *out = _wfopen(path, L"wb");
    if (!out) return false;
    bool ok = fwrite(&file, 1, sizeof(file), out) == sizeof(file) &&
              fwrite(&info, 1, sizeof(info), out) == sizeof(info);
    for (DWORD y = 0; ok && y < height; ++y)
        ok = fwrite(pixels + y * stride, 1, row, out) == row;
    fclose(out);
    return ok;
}

int wmain(int argc, wchar_t **argv) {
    if (argc != 3) { fwprintf(stderr, L"usage: extract_video_frames.exe video.mp4 output_prefix\n"); return 2; }
    if (FAILED(CoInitializeEx(nullptr, COINIT_MULTITHREADED)) || FAILED(MFStartup(MF_VERSION))) return 3;
    IMFAttributes *attributes = nullptr;
    MFCreateAttributes(&attributes, 1);
    attributes->SetUINT32(MF_SOURCE_READER_ENABLE_VIDEO_PROCESSING, TRUE);
    IMFSourceReader *reader = nullptr;
    HRESULT hr = MFCreateSourceReaderFromURL(argv[1], attributes, &reader);
    attributes->Release();
    if (FAILED(hr)) { fwprintf(stderr, L"opening video failed: %08lx\n", hr); return 4; }
    reader->SetStreamSelection(MF_SOURCE_READER_ALL_STREAMS, FALSE);
    reader->SetStreamSelection(MF_SOURCE_READER_FIRST_VIDEO_STREAM, TRUE);
    IMFMediaType *type = nullptr;
    MFCreateMediaType(&type);
    type->SetGUID(MF_MT_MAJOR_TYPE, MFMediaType_Video);
    type->SetGUID(MF_MT_SUBTYPE, MFVideoFormat_RGB32);
    hr = reader->SetCurrentMediaType(MF_SOURCE_READER_FIRST_VIDEO_STREAM, nullptr, type);
    type->Release();
    if (FAILED(hr)) { fwprintf(stderr, L"RGB32 output failed: %08lx\n", hr); return 5; }
    reader->GetCurrentMediaType(MF_SOURCE_READER_FIRST_VIDEO_STREAM, &type);
    UINT32 width = 0, height = 0;
    MFGetAttributeSize(type, MF_MT_FRAME_SIZE, &width, &height);
    GUID subtype{};
    type->GetGUID(MF_MT_SUBTYPE, &subtype);
    LONG default_stride = 0;
    type->GetUINT32(MF_MT_DEFAULT_STRIDE, reinterpret_cast<UINT32 *>(&default_stride));
    type->Release();
    PROPVARIANT duration;
    PropVariantInit(&duration);
    hr = reader->GetPresentationAttribute(MF_SOURCE_READER_MEDIASOURCE, MF_PD_DURATION, &duration);
    if (FAILED(hr) || duration.vt != VT_UI8) { fwprintf(stderr, L"reading duration failed\n"); return 6; }
    const LONGLONG ticks = static_cast<LONGLONG>(duration.uhVal.QuadPart);
    PropVariantClear(&duration);
    wprintf(L"duration %.3f seconds, %ux%u, subtype %08lx, stride %ld\n", ticks / 10000000.0, width, height, subtype.Data1, default_stride);
    for (int i = 0; i < 9; ++i) {
        PROPVARIANT position;
        PropVariantInit(&position);
        position.vt = VT_I8;
        position.hVal.QuadPart = ticks * (i + 1) / 10;
        const LONGLONG target = position.hVal.QuadPart;
        hr = reader->SetCurrentPosition(GUID_NULL, position);
        PropVariantClear(&position);
        if (FAILED(hr)) continue;
        IMFSample *sample = nullptr;
        LONGLONG timestamp = 0;
        for (int j = 0; j < 200; ++j) {
            DWORD stream = 0, flags = 0;
            hr = reader->ReadSample(MF_SOURCE_READER_FIRST_VIDEO_STREAM, 0, &stream, &flags, &timestamp, &sample);
            if (FAILED(hr) || (flags & MF_SOURCE_READERF_ENDOFSTREAM)) break;
            if (sample && timestamp >= target) break;
            if (sample) sample->Release(), sample = nullptr;
        }
        if (!sample) continue;
        IMFMediaBuffer *buffer = nullptr;
        sample->ConvertToContiguousBuffer(&buffer);
        BYTE *pixels = nullptr;
        DWORD maximum = 0, length = 0;
        buffer->Lock(&pixels, &maximum, &length);
        wchar_t path[1024];
        swprintf(path, 1024, L"%ls_%02d.bmp", argv[2], i + 1);
        const DWORD stride = static_cast<DWORD>(std::abs(default_stride));
        if (i == 0) wprintf(L"buffer %lu bytes, row stride %lu\n", length, stride);
        const bool saved = stride >= width * 4 && save_bmp(path, pixels, stride, width, height);
        wprintf(L"%.3f seconds: %ls %ls\n", timestamp / 10000000.0, path, saved ? L"saved" : L"failed");
        buffer->Unlock();
        buffer->Release();
        sample->Release();
    }
    reader->Release();
    MFShutdown();
    CoUninitialize();
    return 0;
}
