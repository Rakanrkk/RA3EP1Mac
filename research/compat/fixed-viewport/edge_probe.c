#define COBJMACROS
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <d3d9.h>
#include <stdio.h>

static unsigned moves, outside_moves;
static LRESULT CALLBACK probe_proc(HWND hwnd, UINT message, WPARAM w, LPARAM l) {
    if (message == WM_MOUSEMOVE) {
        int x = (short)LOWORD(l), y = (short)HIWORD(l);
        moves++;
        if (x < 0 || y < 0 || x >= 2560 || y >= 1600) outside_moves++;
        printf("message x=%d y=%d\n", x, y); fflush(stdout);
    }
    return DefWindowProcW(hwnd, message, w, l);
}

int main(void) {
    SetProcessDPIAware();
    POINT saved; RECT saved_clip;
    if (!GetCursorPos(&saved) || !GetClipCursor(&saved_clip)) return 10;
    WNDCLASSW wc = {0};
    wc.lpfnWndProc = probe_proc; wc.hInstance = GetModuleHandleW(NULL);
    wc.lpszClassName = L"RA3OwnedEdgeProbe";
    if (!RegisterClassW(&wc)) return 11;
    HWND hwnd = CreateWindowW(wc.lpszClassName, L"Owned top edge check", WS_POPUP,
                              0, 0, 2560, 1600, NULL, NULL, wc.hInstance, NULL);
    if (!hwnd) return 12;
    HMODULE module = LoadLibraryW(L"d3d9.dll");
    if (!module) return 13;
    typedef IDirect3D9 *(WINAPI *Create9)(UINT);
    Create9 create = (Create9)(void *)GetProcAddress(module, "Direct3DCreate9");
    if (!create) return 14;
    IDirect3D9 *d3d = create(D3D_SDK_VERSION);
    if (!d3d) return 15;
    D3DPRESENT_PARAMETERS pp = {0};
    pp.BackBufferWidth = 2560; pp.BackBufferHeight = 1600;
    pp.BackBufferFormat = D3DFMT_X8R8G8B8; pp.BackBufferCount = 1;
    pp.SwapEffect = D3DSWAPEFFECT_DISCARD; pp.hDeviceWindow = hwnd;
    pp.Windowed = FALSE; pp.PresentationInterval = D3DPRESENT_INTERVAL_IMMEDIATE;
    IDirect3DDevice9 *device = NULL;
    HRESULT hr = IDirect3D9_CreateDevice(d3d, 0, D3DDEVTYPE_HAL, hwnd,
        D3DCREATE_HARDWARE_VERTEXPROCESSING, &pp, &device);
    if (FAILED(hr)) return 16;
    ShowWindow(hwnd, SW_SHOW); SetForegroundWindow(hwnd); SetFocus(hwnd);
    int status = 42;
    for (unsigned phase = 0; phase < 2; phase++) {
        RECT clip = {0, 0, 2560, 1600};
        BOOL clipped = ClipCursor(phase ? &clip : NULL);
        printf("phase=%u clip_result=%d foreground=%d\n", phase, clipped, GetForegroundWindow()==hwnd);
        fflush(stdout);
        DWORD start = GetTickCount();
        while (GetTickCount()-start < 5000) {
            MSG msg;
            while (PeekMessageW(&msg, NULL, 0, 0, PM_REMOVE)) {
                TranslateMessage(&msg); DispatchMessageW(&msg);
            }
            POINT point; RECT actual_clip;
            if (GetCursorPos(&point) && GetClipCursor(&actual_clip)) {
                printf("sample phase=%u x=%ld y=%ld clip=%ld,%ld,%ld,%ld foreground=%d\n",
                    phase, point.x, point.y, actual_clip.left, actual_clip.top,
                    actual_clip.right, actual_clip.bottom, GetForegroundWindow()==hwnd);
                fflush(stdout);
            }
            IDirect3DDevice9_Clear(device, 0, NULL, D3DCLEAR_TARGET, 0xff184050, 1, 0);
            if (FAILED(IDirect3DDevice9_Present(device, NULL, NULL, NULL, NULL))) {status=17; break;}
            Sleep(100);
        }
        if (status != 42) break;
    }
    ClipCursor(&saved_clip); SetCursorPos(saved.x, saved.y);
    printf("done moves=%u outside_messages=%u\n", moves, outside_moves); fflush(stdout);
    IDirect3DDevice9_Release(device); IDirect3D9_Release(d3d);
    FreeLibrary(module); DestroyWindow(hwnd);
    return status;
}
