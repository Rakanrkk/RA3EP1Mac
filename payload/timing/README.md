# Timing payload — identity and integration

The game timing patches (frame-rate selection and timing corrections) are
developed in the independent project
[RA3EP1 FPS Unlock](https://github.com/Rakanrkk/RA3EP1-FPS-Unlock). This directory is
RA3EP1Mac's consumption point: it pins exactly which payload builds this
release integrates, and documents how they are verified. It is not the patch
source.

## What lives here

- `SHA256.json` — the pinned identity of the payload files this release
  consumes: the loader (`Uprising60.exe`) and one DLL per offered frame rate
  (`fps60.dll`, `fps120.dll`). The launcher and the packaging builder verify
  these pins before use; a mismatch refuses to launch or build rather than
  falling back.
- This README.

The payload implementation, per-rate build scripts, tests and research notes
live in [RA3EP1 FPS Unlock](https://github.com/Rakanrkk/RA3EP1-FPS-Unlock). The
`SHA256.json` pins the canonical Mac-integration payloads shipped with this
RA3EP1Mac release; those binary identities are independent of payloads built
from the FPS Unlock source archive. To rebuild from source, follow that
project's build instructions, inspect the generated `build-result.json`, and
record the resulting binary identities deliberately. Compilers and build
options can produce different hashes.

The 60 FPS payload consumed by 1.2.0 is the corrected-effect/suspension
revision; the original accepted 60 FPS payload from earlier releases remains
available in RA3EP1Mac's release history.

## How the payloads are packaged

- A full build stages the pinned loader and both rates' DLLs under the app's
  `Contents/Resources/timing/`, with the launcher manifest binding each rate
  to its DLL by SHA-256. Every launch stages a session-local copy.
- The Runtime DMG ships the payloads in its `timing/` directory; see
  [BUILDING](../../docs/BUILDING.md).

## Legacy prefix payload

`import_runtime.py`'s default payload location (`drive_c/u60-mtld3d` inside
the prefix) continues to work unchanged: a prefix that already carries a
payload keeps using it, and removing the patch source from this directory does
not affect that path. The `timing/` payloads on the Runtime volume are the
canonical source for new builds.

## License

The timing-patch source is MIT-licensed in RA3EP1 FPS Unlock. The pins in this
directory describe binaries consumed as a release dependency; their license
terms are those of that project and its upstream components.
