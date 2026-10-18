# Contributing

Use a focused feature branch and keep the working tree reviewable. Commit source,
patches, tests and portable documentation; keep game files, prefixes, compiled
runtimes, signing identities and personal logs outside Git.

Run the offline checks before submitting changes. Actual Wine display validation
uses a separate supplied runtime and owned test programs. Report software and
hardware coverage precisely; compilation, synthetic geometry tests and game
visual acceptance are separate evidence.

Preserve a known working application and player-data backup before testing changed
runtime modules. One game instance at a time. Do not run pointer/display tests
against an active game. Display hotplug and primary-screen changes during a match
are outside the validated transition model.

Wine-derived changes must retain LGPL scope and upstream attribution. New original
RA3Mac source is submitted under this repository's MIT license. Do not include
third-party proprietary source or personal device/account information in issues,
pull requests or screenshots.
