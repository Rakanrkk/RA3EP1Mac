# Reporting security and privacy issues

Do not post saved games, a full Wine prefix, signing keys, tokens or raw system
reports in a public issue. A minimal description, affected version and redacted
error message are usually enough to start investigation.

The application starts local Windows game code through Wine/Rosetta. It is not a
sandbox for untrusted executables or mods. Only use a trusted game installation
and runtime. The output-only Wine backend disables microphone capture for this
package; no microphone permission is needed.

Checksums validate release file integrity. A local Apple Development signature
is not Developer ID notarization and does not replace normal macOS trust checks.
