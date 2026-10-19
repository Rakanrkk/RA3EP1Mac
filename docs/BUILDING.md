# Building the local macOS app

This repository provides the packaging tools for building a user-local
Red Alert 3: Uprising app. Building the app requires game files you own, a
complete matching Wine runtime and prefix, a locally built patched Wine audio
module, and an icon you provide. Those large inputs are not included in the
repository, so a clean checkout alone cannot create the app. The preparation
scripts are `packaging/macos/prepare_wine_source.py` for a supplied Wine
checkout, `packaging/macos/import_runtime.py` for cloning a supplied runtime
and initialized prefix, and `packaging/macos/build_wine_modules.py` for
building patched modules into the imported clone. They still require those
local inputs and the macOS build toolchain; review each script's `--help` and
the relevant Wine build requirements.

The release wrapper compiles the native launcher and display helpers with the
macOS Swift toolchain, assembles the bundle, signs it, and verifies the
signature. It does not install or launch the game. The safe-area adaptive
candidate is the default and uses its own application identity and
Application Support data. Use `--presentation fixed` to build with the
existing fixed viewport identity.

## Requirements

- A Mac with `xcrun swiftc` and a macOS SDK supporting deployment target 15 or
  newer.
- The upstream Wine source at the revision expected by
  `packaging/macos/prepare_wine_source.py`, and the build dependencies
  documented by that Wine checkout, if preparing patched Wine modules
  yourself.
- A prepared runtime root containing `wine/`, `prefix/`, and
  `candidate-manifest.json`. Its manifest must pin the runtime, game files,
  and launcher payload, and all pinned hashes must match. The packaging inputs
  must come from the same matching runtime preparation.
- A patched `winecoreaudio.so` Mach-O containing the `RA3_NO_AUDIO_CAPTURE`
  marker. If building it yourself, `build_wine_modules.py` overlays Wine
  modules into the imported runtime clone; use that clone's resulting
  `winecoreaudio.so` as `--audio-module`.
- An `AppIcon.icns` file you are entitled to use.
- One timing-payload artifact per offered frame rate for a 1.2 launcher
  manifest (see "Timing payloads" below).

The wrapper refuses an incomplete runtime, missing or invalid input, a
pre-existing output app, or a build-tools target it would need to overwrite.
It checks the input contract before compiling. Keep the prepared runtime,
audio module, and icon in paths you control; none is copied back into the
source runtime. Review upstream licenses and third-party notices for the
inputs you provide and the bundle you create. The tools do not upload your
local inputs or reports.

## Build

Run from the repository root, replacing the example paths with your prepared
inputs and an output path that does not exist:

```bash
python3 -I packaging/macos/release_build.py \
  --runtime-root /path/to/prepared-matching-runtime \
  --audio-module /path/to/patched/winecoreaudio.so \
  --icon /path/to/AppIcon.icns \
  --output "$HOME/Library/Caches/org.ra3mac.Packaging/release/红警3起义时刻.app"
```

The default presentation is `safe-area`, which builds the isolated candidate
identity `org.ra3mac.UprisingMetal.Adaptive`. To select the fixed viewport and
its existing identity, add `--presentation fixed` to the complete build
command.

By default, compiled native tools are placed in `build/release-tools/`, and
build and signing reports are written to `build/release-reports/`. Existing
tool files are never overwritten. Choose alternate directories with
`--tools-dir` and `--reports-dir` if needed.

The default signing identity is `-`, which creates an ad-hoc local signature.
To sign with an identity already available on your Mac, pass it explicitly
with `--identity`. This workflow does not configure Developer ID distribution
or notarization.

If you already have compiled helpers, pass `--launcher`, `--display-helper`,
and, for safe-area builds, `--display-plan`. When supplied, each helper skips
its compilation. An omitted helper is compiled for arm64 macOS 15; the
adaptive display planner is required by safe-area packaging.

Show CLI help without building or invoking the compiler:

```bash
python3 -I packaging/macos/release_build.py --help
```

## Timing payloads (frame-rate chooser)

A 1.2 launcher manifest bundles one timing payload per offered frame rate;
this release offers 60 and 120. For this release, use the three pinned PE files
shipped together in the Runtime volume's flat `timing/` directory with
`--timing-dir`. Their identities are frozen for this Mac release; arbitrary
source-built binaries are not accepted as drop-in release inputs.

The builder also supports `--timing-60` and `--timing-120` artifact directories
for development builds. Rebuilding the timing source produces different binary
identities from the canonical Mac-integration payloads. To integrate rebuilt
binaries, deliberately update the accepted pins and rerun the relevant
verification before assembling a new release; source builds alone do not make
release-compatible inputs.

The builder stages the payloads under `Contents/Resources/timing/` and writes
the launcher manifest (schema `ra3mac-app-v1`, version 1.2, `data_version` 1.0,
default frame rate 60). The launcher refuses a manifest whose profile pins do
not verify. Legacy manifests built without timing payloads keep their
original auto-launch behavior. Normal full-app builds do not pass
`--runtime-only`; that flag belongs to the internal Runtime-DMG validation
flow only.

## Reports and user data

The build report records source pin checks, packaging actions, and the
selected presentation. The signing report records the signing identity,
signed Mach-O images, and bundle verification. Both are stored outside the
`.app` in the reports directory.

The launcher uses an Application Support directory selected by the app's
bundle identity. The adaptive candidate keeps its data separate from the
fixed release. Before replacing an installed app, back up the installed
bundle and its Application Support prefix. If you want to transfer saves,
copy only into the matching profile in the promoted app's prefix after
backing up both prefixes. Keep any same-path files with different contents as
conflicts; do not overwrite them during migration. Preserve the tested
adaptive app identity when promoting it so the candidate continues to use its
existing prefix.

This packaging flow does not replace an installed app, migrate saves, delete
backups, or launch the game. Perform any app replacement or user-data
migration as a separate, deliberate operation after reviewing the reports and
backups. Share a built app bundle only where you have the rights to
distribute every included game, runtime, and third-party component; the
repository does not upload bundles or inputs.

## Runtime DMG (no game)

The runtime download mounts under the volume name `RA3EP1Mac-1.2.0-Runtime`.
The volume root contains `wine/`, `prefix/`, `timing/` (the bundled timing
payloads), `Tools/` (RA3Launcher, display helpers, AppIcon), `Source/` (an
export of this repository, usable as the working checkout), `manifest.json`
and `README.txt`. No Xcode is required if all three bundled native helpers
are supplied as overrides. Python 3 is still required.

First generate the compatibility resources from your own game and copy the
three produced archives into a copy of the game directory's `Data/` folder;
the original game directory is never modified:

```bash
python3 -I packaging/resources/compat_resources.py \
  --game-dir "/path/to/Red Alert 3 Uprising" \
  --output-dir "$HOME/RA3Mac-input/compat-resources" \
  --high-refresh-light
```

Import the runtime and the game copy, then build:

```bash
python3 -I packaging/macos/import_runtime.py \
  --wine "/Volumes/RA3EP1Mac-1.2.0-Runtime/wine" \
  --prefix "/Volumes/RA3EP1Mac-1.2.0-Runtime/prefix" \
  --game-dir "/path/to/game-copy" \
  --output "$HOME/RA3Mac-work/runtime" \
  --presentation safe-area
```

```bash
python3 -I packaging/macos/release_build.py \
  --runtime-root "$HOME/RA3Mac-work/runtime" \
  --audio-module "/Volumes/RA3EP1Mac-1.2.0-Runtime/wine/lib/wine/x86_64-unix/winecoreaudio.so" \
  --icon "/Volumes/RA3EP1Mac-1.2.0-Runtime/Tools/AppIcon.icns" \
  --launcher "/Volumes/RA3EP1Mac-1.2.0-Runtime/Tools/RA3Launcher" \
  --display-helper "/Volumes/RA3EP1Mac-1.2.0-Runtime/Tools/display-mode" \
  --display-plan "/Volumes/RA3EP1Mac-1.2.0-Runtime/Tools/display-plan" \
  --timing-dir "/Volumes/RA3EP1Mac-1.2.0-Runtime/timing" \
  --output "$HOME/Library/Caches/RA3Mac-build/红警3起义时刻.app" \
  --public \
  --presentation safe-area
```

## DMG packaging

Create a new staging folder containing the prepared App, an Applications
symlink, installation notes and third-party notices. Never copy a live user
prefix or saves into a release template. Use `prepare_distribution_app.py` to
make a separate sharing copy and remove player artifacts, then sign that copy
again.

```bash
python3 -I packaging/macos/make_dmg.py --source /path/to/dmg-staging --output /path/to/new/RA3Mac.dmg --volume-name RA3Mac --format ULFO
```

Each release asset must be below GitHub's 2 GiB limit. The Standalone image
uses native DMG segmentation: `RA3EP1Mac-v1.2.0-macos-arm64-standalone.001.dmg`
followed by `.002.dmgpart`, `.003.dmgpart` and `.004.dmgpart` with the same
filename stem. Keep all four parts together with their filenames unchanged
and open only `.001.dmg`; macOS reads the remaining parts automatically.
Verify `SHA256SUMS.txt` before opening.
