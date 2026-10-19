# Third-party notices

RA3Mac is an independent compatibility and packaging project. It is not an EA,
Apple, CodeWeavers, Valve, WineHQ or mtld3d product, and no endorsement by those
parties is claimed. Component names and game trademarks belong to their owners.
A disclaimer does not change any component's license or grant additional rights.

| Component | Provenance | License / scope |
| --- | --- | --- |
| Original RA3Mac launcher, packaging, tests and independent timing code | This repository | MIT; see LICENSE |
| Wine and Wine-derived patches | [athei/wine](https://github.com/athei/wine), source base `3562fea11d1830725859ac5ed790a3b9789ccd14`; matched runtime `cx-26.3.0-7` | LGPL-2.1-or-later; see LICENSES/LGPL-2.1.txt and Wine-LICENSE.txt |
| mtld3d | [athei/mtld3d v0.13.0](https://github.com/athei/mtld3d/releases/tag/v0.13.0) | zlib; see LICENSES/mtld3d-zlib.txt |
| Noto Sans CJK SC | [notofonts/noto-cjk](https://github.com/notofonts/noto-cjk) | SIL Open Font License 1.1; notice included with the full distribution |
| Microsoft-compatible runtime support / LLVM-MinGW | [mstorsjo/llvm-mingw](https://github.com/mstorsjo/llvm-mingw) | Component-specific upstream licenses; compiler/runtime notices remain applicable |
| Command & Conquer: Red Alert3—Uprising, executable/data/artwork | Electronic Arts and other credited rights holders | Proprietary; not covered by the source repository license |
| Apple frameworks and optional runtime libraries carried by the supplied Wine package | Apple and their credited third parties | Their supplied license terms; not relicensed by RA3Mac |

## Wine changes

Modified Wine files are `dlls/win32u/sysparams.c`, `dlls/win32u/input.c`,
`dlls/winemac.drv/window.c`, `dlls/winemac.drv/cocoa_window.m`,
`dlls/winemac.drv/display.c`, and `dlls/winecoreaudio.drv/coreaudio.c`.
The patch set adds launch-time safe viewport geometry, edge input handling,
Cocoa mask unit correction, explicit Metal backing scale, and output-only audio.
These changes are marked as RA3Mac-specific and dated 2026-10-18. They are not
represented as unmodified upstream Wine. The standalone contract header is
included when applying adaptive patches.

The source release contains the exact upstream source identifier and the full
patch sequence. The accompanying Wine source archive supplies the matching
base plus applied modifications for rebuilding the changed Unix modules.
Nothing in this project's MIT license restricts rights granted by the LGPL,
including applicable library replacement or reverse engineering for debugging
modifications.

## Distribution editions

The Git repository contains source and documentation. Release files are
separate: the Runtime DMG supplies the runtime and app-building tools; the
Standalone DMG contains the prepared game App and its runtime components.
Standalone includes proprietary game content alongside the open-source components
listed above, each under its own license.
Neither edition includes a developer private key, signing credentials,
personal saved games or session logs.

Existing upstream notices in runtime packages remain applicable. Signing is
an integrity/provenance mechanism, not a grant of rights to third-party code
or content. Official game support and account services are outside this project.
