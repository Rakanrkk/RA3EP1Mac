# Changelog

## 1.2.0

- Add a startup frame-rate chooser offering 60 and 120 FPS (default 60). The
  choice can be remembered so the next launch starts automatically, with a
  2-second window to change the rate or cancel. `--fps 60` / `--fps 120`
  start the game directly without the chooser.
- Bundle two SHA-256-pinned timing payloads (60/120 FPS) under
  `Contents/Resources/timing`. Each launch stages a session-local copy
  selected by the chooser; the launcher verifies the manifest pins before
  use.
- Keep game logic at the native 15 Hz at both rates; particles and suspension
  physics keep their authored 30 Hz cadence while the display interpolates at
  the selected rate. Saves, settings, replays and the Application Support
  identity carry over unchanged.
- Update the launcher manifest to schema version 1.2 (`data_version` stays
  1.0). Legacy manifests keep their original auto-launch behavior.
- 120 FPS verification: local visual acceptance on an Apple Silicon Mac with
  a 2× notched display; steady in-match windows measured approximately 120
  client updates/s and 15 logic cycles/s with a game-clock/wall-clock ratio
  near 1. The Jet Tengu moving-landing transformation remains under
  investigation in the independent RA3EP1 FPS Unlock project.

Other Mac models, multi-display setups and display changes during play remain
outside verification. Exit and relaunch after changing the primary display or
scaling.

## 1.1.0

- Derive the fullscreen safe viewport from the primary display frame and safe-area
  insets rather than a single fixed 3024×1900 target.
- Keep the whole-screen Wine outer window for edge-scroll input while presenting
  Metal content inside the safe area.
- Distinguish Wine raw Retina coordinates from CAMetalLayer backing pixels.
- Preserve the corrected Cocoa mask units and output-only audio backend.
- Include native launcher, integrity checks, separate writable player data,
  prepared-input import, reproducible patch/module build and signed App packaging.
- Validate native 3024×1900 and 2560×1600→3024×1900 owned drawing/pointer paths and
  top/bottom marker pixels; local game visual verification passed on a 2× notched
  Apple Silicon display.
- Ship Standalone and Runtime DMG editions; no personal saves, replays or runtime logs.

Other displays, rotated/multi-monitor configurations and older macOS runtime
versions remain outside physical-device verification. Exit and relaunch after
changing the primary display or display scaling during play.

## 1.0.0

The `archive/fixed-viewport` branch preserves the earlier locally accepted
3024×1900 Metal viewport, whole-screen input window and bottom-mask correction.
It is a reference/rollback source branch, not the current adaptive implementation.
