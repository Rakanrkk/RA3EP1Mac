# Independent Uprising timing patch 时序补丁

Source for the build-specific in-process60Hz client timing patch used by RA3Mac. It validates the supported game executable fingerprint before launch. The DLL edits process memory only; it does not rewrite the game executable.

Game simulation/effect timing and renderer Present rate are distinct; this is not a guaranteed60FPS benchmark across all hardware or gameplay. Build i386 targets with LLVM-MinGW; see[BUILDING](../../docs/BUILDING.md). Original game files are not included in this source directory.
