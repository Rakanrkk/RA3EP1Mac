# RA3EP1Mac — Fixed viewport archive

This branch preserves the earlier accepted fixed3024×1900 display implementation.
The current adaptive edition is on[`main`](https://github.com/Rakanrkk/RA3EP1Mac/tree/main).

## Configuration

- Rosetta2 / WineWoW64, matched Wine`cx-26.3.0-7`, mtld3d`v0.13.0`.
- Fixed Metal viewport3024×1900 on the validated3024×1964 display.
- Whole-screen outer window retains top-edge scroll events.
- Corrected Cocoa mask units prevent the bottom128-pixel occlusion.
- Original independent timing patch timing source and output-only audio patch retained.

## Status

The fixed edition was locally accepted before adaptive display planning. It does
not adapt to other resolutions, safe-area sizes or screen layouts. This branch
is a source reference/rollback archive, not a separate claim of broad device support.

No game executable, BIG archive, player data, Wine binary or signing credentials
are in this Git branch. Obtain the appropriate local inputs before building.
The rootMIT license covers original project source; Wine-derived patch context
and changes retain LGPL-2.1-or-later terms. See THIRD-PARTY-NOTICES.md.
