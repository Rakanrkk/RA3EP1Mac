#include <windows.h>
#include <stdio.h>
int wmain(int argc, wchar_t **argv) {
    if (argc != 2) return 2;
    HMODULE dll = LoadLibraryW(argv[1]);
    if (!dll) { fprintf(stderr, "LoadLibrary failed: %lu\n", GetLastError()); return 1; }
    /* The DLL worker must reject this filename before reading any game address. */
    Sleep(2000);
    puts("DLL loaded in a non-game process; inspect rejection log.");
    return 0;
}
