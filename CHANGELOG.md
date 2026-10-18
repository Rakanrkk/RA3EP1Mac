# Changelog

## 1.1.0

- Derive the fullscreen safe viewport from the primary display frame and safe-area
  insets rather than a single fixed3024×1900 target.
- Keep the whole-screen Wine outer window for edge-scroll input while presenting
  Metal content inside the safe area.
- Distinguish Wine raw Retina coordinates from CAMetalLayer backing pixels.
- Preserve the corrected Cocoa mask units and output-only audio backend.
- Include native launcher, integrity checks, separate writable player data,
  prepared-input import, reproducible patch/module build and signed App packaging.
- Validate native3024×1900 and2560×1600→3024×1900 owned drawing/pointer paths and
  top/bottom marker pixels; local game visual verification passed on a2× notched
  Apple Silicon display.
- Ship Full and Toolkit DMG editions; no personal saves, replays or runtime logs.

Other displays, rotated/multi-monitor configurations and older macOS runtime
versions remain outside physical-device verification. Exit and relaunch after
changing the primary display or display scaling during play.

## 1.0.0

The `archive/fixed-viewport` branch preserves the earlier locally accepted
3024×1900 Metal viewport, whole-screen input window and bottom-mask correction.
It is a reference/rollback source branch, not the current adaptive implementation.
