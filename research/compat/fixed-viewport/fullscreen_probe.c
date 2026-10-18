#define COBJMACROS
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <d3d9.h>
#include <stdio.h>
#include <stdlib.h>

static LRESULT CALLBACK probe_proc(HWND window, UINT message, WPARAM w, LPARAM l) {
    if (message == WM_DESTROY) { PostQuitMessage(0); return 0; }
    return DefWindowProcW(window, message, w, l);
}

int main(int argc, char **argv) {
    if (argc != 3) return 2;
    char *end_x = NULL, *end_y = NULL;
    unsigned long width = strtoul(argv[1], &end_x, 10);
    unsigned long height = strtoul(argv[2], &end_y, 10);
    if (!end_x || *end_x || !end_y || *end_y || width < 320 || height < 200 ||
        width > 8192 || height > 8192) return 2;
    SetProcessDPIAware();
    WNDCLASSW wc = {0};
    wc.lpfnWndProc = probe_proc;
    wc.hInstance = GetModuleHandleW(NULL);
    wc.lpszClassName = L"RA3FixedViewportOwnedProbe";
    if (!RegisterClassW(&wc)) return 10;
    HWND window = CreateWindowW(wc.lpszClassName, L"Owned fullscreen viewport probe",
        WS_POPUP, 0, 0, (int)width, (int)height, NULL, NULL, wc.hInstance, NULL);
    if (!window) return 11;
    HMODULE module = LoadLibraryW(L"d3d9.dll");
    if (!module) return 12;
    typedef IDirect3D9 *(WINAPI *Create9)(UINT);
    Create9 create = (Create9)(void *)GetProcAddress(module, "Direct3DCreate9");
    if (!create) return 13;
    IDirect3D9 *d3d = create(D3D_SDK_VERSION);
    if (!d3d) return 14;
    D3DPRESENT_PARAMETERS pp = {0};
    pp.BackBufferWidth = (UINT)width;
    pp.BackBufferHeight = (UINT)height;
    pp.BackBufferFormat = D3DFMT_X8R8G8B8;
    pp.BackBufferCount = 1;
    pp.SwapEffect = D3DSWAPEFFECT_DISCARD;
    pp.hDeviceWindow = window;
    pp.Windowed = FALSE;
    pp.PresentationInterval = D3DPRESENT_INTERVAL_IMMEDIATE;
    IDirect3DDevice9 *device = NULL;
    HRESULT hr = IDirect3D9_CreateDevice(d3d, 0, D3DDEVTYPE_HAL, window,
        D3DCREATE_HARDWARE_VERTEXPROCESSING, &pp, &device);
    printf("pid=%lu create_device=%08lx requested=%lux%lu\n",
        GetCurrentProcessId(), (unsigned long)hr, width, height);
    fflush(stdout);
    if (FAILED(hr)) return 15;
    RECT outer = {0}, client = {0};
    MONITORINFO info = {0}; info.cbSize = sizeof(info);
    GetWindowRect(window, &outer); GetClientRect(window, &client);
    GetMonitorInfoW(MonitorFromWindow(window, MONITOR_DEFAULTTOPRIMARY), &info);
    IDirect3DSurface9 *back = NULL;
    D3DSURFACE_DESC desc = {0};
    if (FAILED(IDirect3DDevice9_GetBackBuffer(device, 0, 0, D3DBACKBUFFER_TYPE_MONO, &back))) return 16;
    if (FAILED(IDirect3DSurface9_GetDesc(back, &desc))) return 17;
    printf("screen=%dx%d window=%ld,%ld,%ld,%ld client=%ldx%ld monitor=%ld,%ld,%ld,%ld backbuffer=%ux%u\n",
        GetSystemMetrics(SM_CXSCREEN), GetSystemMetrics(SM_CYSCREEN),
        outer.left, outer.top, outer.right, outer.bottom, client.right, client.bottom,
        info.rcMonitor.left, info.rcMonitor.top, info.rcMonitor.right, info.rcMonitor.bottom,
        desc.Width, desc.Height);
    fflush(stdout);
    ShowWindow(window, SW_SHOW);
    DWORD started = GetTickCount();
    int status = 42;
    while (GetTickCount() - started < 6000) {
        MSG msg;
        while (PeekMessageW(&msg, NULL, 0, 0, PM_REMOVE)) {
            if (msg.message == WM_QUIT) goto done;
            TranslateMessage(&msg); DispatchMessageW(&msg);
        }
        D3DRECT top = {0, 0, (LONG)desc.Width, 40};
        D3DRECT bottom = {0, (LONG)desc.Height-40, (LONG)desc.Width, (LONG)desc.Height};
        HRESULT clear_full = IDirect3DDevice9_Clear(device, 0, NULL, D3DCLEAR_TARGET, 0xff184050, 1, 0);
        HRESULT clear_top = IDirect3DDevice9_Clear(device, 1, &top, D3DCLEAR_TARGET, 0xffff2000, 1, 0);
        HRESULT clear_bottom = IDirect3DDevice9_Clear(device, 1, &bottom, D3DCLEAR_TARGET, 0xff00ff00, 1, 0);
        if (FAILED(clear_full) || FAILED(clear_top) || FAILED(clear_bottom)) { status = 18; break; }
        if (GetTickCount() - started < 150) {
            IDirect3DSurface9 *cpu = NULL;
            if (SUCCEEDED(IDirect3DDevice9_CreateOffscreenPlainSurface(device, desc.Width, desc.Height,
                desc.Format, D3DPOOL_SYSTEMMEM, &cpu, NULL))) {
                HRESULT readback = IDirect3DDevice9_GetRenderTargetData(device, back, cpu);
                D3DLOCKED_RECT pixels = {0};
                if (SUCCEEDED(readback) && SUCCEEDED(IDirect3DSurface9_LockRect(cpu, &pixels, NULL, D3DLOCK_READONLY))) {
                    DWORD first = *(DWORD *)pixels.pBits;
                    DWORD last = *(DWORD *)((BYTE *)pixels.pBits + (desc.Height-1)*pixels.Pitch);
                    printf("backbuffer_top=%08lx bottom=%08lx readback=%08lx\n",
                        (unsigned long)first, (unsigned long)last, (unsigned long)readback);
                    fflush(stdout);
                    IDirect3DSurface9_UnlockRect(cpu);
                }
                IDirect3DSurface9_Release(cpu);
            }
        }
        if (FAILED(IDirect3DDevice9_Present(device, NULL, NULL, NULL, NULL))) { status = 18; break; }
        Sleep(100);
    }
    if (status == 42) puts("PASS: owned fullscreen present loop completed");
 done:
    IDirect3DSurface9_Release(back);
    IDirect3DDevice9_Release(device);
    IDirect3D9_Release(d3d);
    FreeLibrary(module);
    DestroyWindow(window);
    return status;
}
