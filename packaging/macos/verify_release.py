#!/usr/bin/env python3
"""Read-only release audit for a signed .app or a publish tree.

Local reports contain paths and exact local indicators. Public reports contain
only aggregate counts and SHA-256 digests. The audit never launches the app,
Wine, or a game executable and never mutates the audited tree.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import plistlib
import re
import shutil
import socket
import subprocess
import sys
from typing import Iterable

SCHEMA_LOCAL = "ra3-release-audit-local-v1"
SCHEMA_PUBLIC = "ra3-release-audit-public-v1"
TEXT_SUFFIXES = {
    ".json", ".plist", ".xml", ".strings", ".txt", ".md", ".reg", ".ini",
    ".cfg", ".conf", ".toml", ".yaml", ".yml", ".swift", ".py", ".c",
    ".h", ".sh", ".metal", ".entitlements", ".xcconfig", ".stringsdict",
}
BINARY_SUFFIXES = {
    ".big", ".dat", ".meg", ".mix", ".exe", ".dll", ".so", ".dylib",
    ".icns", ".png", ".jpg", ".jpeg", ".gif", ".pdf", ".ttf", ".otf",
    ".woff", ".woff2", ".mp4", ".bik", ".dds", ".tga", ".wav", ".mp3",
    ".aiff", ".bin", ".pak", ".cab", ".zip", ".7z",
}
TEXT_BASENAMES = {"license", "licence", "makefile", "dockerfile", ".gitignore",
                  ".gitattributes", ".editorconfig", "delivery"}
PLAYER_DIRS = {"red alert 3 uprising", "紅色警戒3 - 起義時刻"}
PLAYER_ARCHIVE_DIRS = {"saved games", "replays"}
PRIVATE_SUFFIXES = {".ra3save", ".ra3ureplay", ".dmp", ".mdmp"}
MAX_TEXT_BYTES = 16 * 1024 * 1024
MAX_STRINGS_BYTES = 128 * 1024 * 1024
MACHO_SCAN_RELATIVE = (
    "Contents/MacOS/RA3Launcher",
    "Contents/Resources/display-mode",
    "Contents/Resources/display-plan",
    "Contents/Resources/wine/lib/wine/x86_64-unix/win32u.so",
    "Contents/Resources/wine/lib/wine/x86_64-unix/winemac.so",
    "Contents/Resources/wine/lib/wine/x86_64-unix/winecoreaudio.so",
)
TIMING_BINARY_NAMES = {"uprising60.exe", "fps30.dll", "fps60.dll", "fps90.dll", "fps120.dll"}
PRIVATE_DIR_NAMES = {"ci", "ci-logs", "ci_logs", "ci-log", "ci_log",
                    "private-notes", "local-notes"}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_digest(value) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha256_bytes(raw)


def _relative_posix(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _walk_inventory(root: Path, tracked_paths: Iterable[str] | None = None):
    """List regular files, directories, and symlinks without following links."""
    entries = []
    if tracked_paths is not None:
        wanted = sorted(set(tracked_paths))
        for relative in wanted:
            rel = PurePosixPath(relative)
            if rel.is_absolute() or ".." in rel.parts or not rel.parts:
                entries.append({"path": relative, "kind": "unsafe-tracked-path"})
                continue
            path = root.joinpath(*rel.parts)
            try:
                info = path.lstat()
            except FileNotFoundError:
                entries.append({"path": relative, "kind": "missing-tracked-path"})
                continue
            if path.is_symlink():
                entries.append({"path": relative, "kind": "symlink", "target": os.readlink(path)})
            elif path.is_file():
                entries.append({"path": relative, "kind": "file", "bytes": info.st_size})
            elif path.is_dir():
                entries.append({"path": relative, "kind": "directory"})
            else:
                entries.append({"path": relative, "kind": "special"})
        return entries

    for current, dirs, files in os.walk(root, followlinks=False):
        parent = Path(current)
        kept_dirs = []
        for name in sorted(dirs):
            path = parent / name
            rel = _relative_posix(path, root)
            if path.is_symlink():
                entries.append({"path": rel, "kind": "symlink", "target": os.readlink(path)})
            else:
                entries.append({"path": rel, "kind": "directory"})
                kept_dirs.append(name)
        dirs[:] = kept_dirs
        for name in sorted(files):
            path = parent / name
            rel = _relative_posix(path, root)
            try:
                info = path.lstat()
            except FileNotFoundError:
                entries.append({"path": rel, "kind": "vanished-during-inventory"})
                continue
            if path.is_symlink():
                entries.append({"path": rel, "kind": "symlink", "target": os.readlink(path)})
            elif path.is_file():
                entries.append({"path": rel, "kind": "file", "bytes": info.st_size})
            else:
                entries.append({"path": rel, "kind": "special"})
    entries.sort(key=lambda item: item["path"])
    return entries


def _text_candidate(path: Path, size: int) -> bool:
    lower_name = path.name.lower()
    if lower_name in TEXT_BASENAMES or path.name in TEXT_BASENAMES:
        return size <= MAX_TEXT_BYTES
    if path.suffix.lower() in TEXT_SUFFIXES:
        return size <= MAX_TEXT_BYTES
    if path.suffix.lower() in BINARY_SUFFIXES or size > MAX_TEXT_BYTES:
        return False
    try:
        with path.open("rb") as stream:
            sample = stream.read(min(size, 8192))
        if b"\0" in sample:
            return False
        sample.decode("utf-8")
        return True
    except (OSError, UnicodeDecodeError):
        return False


def _indicator_encodings(value: str) -> set[bytes]:
    if not value:
        return set()
    variants = {value, value.replace("/", "\\"), value.replace("\\", "/")}
    encoded = set()
    for variant in variants:
        for encoding in ("utf-8", "utf-16le", "utf-16be"):
            encoded.add(variant.encode(encoding, errors="ignore"))
    return {item for item in encoded if item}


def _indicators(known_terms: Iterable[str]):
    items = []
    home = str(Path.home())
    if home and home != "/":
        items.append({"kind": "local_home_path", "value": home, "source": "Path.home()"})
    try:
        host = socket.gethostname()
    except OSError:
        host = ""
    if host:
        items.append({"kind": "machine_hostname", "value": host, "source": "socket.gethostname()"})
    for term in known_terms:
        if term:
            items.append({"kind": "caller_supplied_exact_term", "value": term,
                          "source": "CLI --known-local-term"})
    # De-duplicate while preserving the most precise source record.
    unique = {}
    for item in items:
        unique.setdefault((item["kind"], item["value"], item["source"]), item)
    return list(unique.values())


def _scan_bytes(path: Path, indicators: list[dict], source: str, relative: str):
    data = path.read_bytes()
    hits = []
    for indicator in indicators:
        encoded_forms = _indicator_encodings(indicator["value"])
        found = False
        offsets = []
        for token in encoded_forms:
            start = 0
            while True:
                position = data.find(token, start)
                if position < 0:
                    break
                offsets.append(position)
                start = position + len(token)
                found = True
                if len(offsets) >= 32:
                    break
            if len(offsets) >= 32:
                break
        if found:
            hits.append({"path": relative, "kind": indicator["kind"],
                         "value": indicator["value"], "indicator_source": indicator["source"],
                         "scan_source": source, "byte_offsets": sorted(set(offsets))})
    return {"path": relative, "scan_source": source, "bytes": len(data),
            "sha256": sha256_bytes(data), "matches": hits}, hits


def _scan_strings(path: Path, relative: str, indicators: list[dict], strings_tool: str):
    size = path.stat().st_size
    if size > MAX_STRINGS_BYTES:
        return {"path": relative, "scan_source": "strings", "bytes": size,
                "sha256": None, "skipped": "over configured strings size limit", "matches": []}, []
    proc = subprocess.run([strings_tool, "-a", "-n", "6", str(path)],
                          capture_output=True, check=False)
    if proc.returncode != 0:
        return {"path": relative, "scan_source": "strings", "bytes": size,
                "sha256": sha256_file(path), "exit_code": proc.returncode,
                "stderr": proc.stderr.decode("utf-8", "replace"), "matches": []}, [
                    {"path": relative, "kind": "strings_scan_failed", "scan_source": "strings"}]
    output = proc.stdout
    text = output.decode("utf-8", "replace")
    hits = []
    for indicator in indicators:
        value = indicator["value"]
        variants = {value, value.replace("/", "\\"), value.replace("\\", "/")}
        line_numbers = []
        for index, line in enumerate(text.splitlines(), 1):
            if any(variant and variant in line for variant in variants):
                line_numbers.append(index)
        if line_numbers:
            hits.append({"path": relative, "kind": indicator["kind"], "value": value,
                         "indicator_source": indicator["source"], "scan_source": "strings",
                         "line_numbers": line_numbers[:32]})
    return {"path": relative, "scan_source": "strings", "bytes": size,
            "strings_output_sha256": sha256_bytes(output), "strings_output_bytes": len(output),
            "matches": hits}, hits


def _path_findings(root: Path, entries: list[dict], app_mode: bool):
    findings = []
    for item in entries:
        rel = item["path"]
        leaf = PurePosixPath(rel).name.lower()
        parts = {part.lower() for part in PurePosixPath(rel).parts}
        if item["kind"] == "missing-tracked-path":
            findings.append({"path": rel, "kind": "tracked_file_missing"})
            continue
        if item["kind"] in {"unsafe-tracked-path", "special", "vanished-during-inventory"}:
            findings.append({"path": rel, "kind": item["kind"]})
            continue
        if item["kind"] == "symlink":
            target = item.get("target", "")
            if target.startswith("/"):
                findings.append({"path": rel, "kind": "external_or_parent_symlink", "target": target})
            else:
                # Relative links are valid inside a bundle when they lexically
                # stay within the audited root and their target exists. Every
                # link in a chain is inventoried, so per-link containment is a
                # complete escape check without following the chain here.
                parent = PurePosixPath(rel).parent
                resolved = PurePosixPath(os.path.normpath(str(parent / target)))
                if target == "" or ".." in resolved.parts[:1] or resolved.is_absolute():
                    findings.append({"path": rel, "kind": "external_or_parent_symlink", "target": target})
                elif not (root / resolved).exists():
                    findings.append({"path": rel, "kind": "dangling_symlink", "target": target,
                                     "resolved": resolved.as_posix()})
        if leaf == ".ds_store" or leaf == "thumbs.db" or leaf.endswith(tuple(PRIVATE_SUFFIXES)):
            findings.append({"path": rel, "kind": "private_or_host_metadata_file"})
        is_player_area = bool(parts & PLAYER_DIRS)
        if is_player_area and (parts & PLAYER_ARCHIVE_DIRS):
            findings.append({"path": rel, "kind": "player_save_or_replay_content"})
        if leaf.endswith(".log"):
            if (not app_mode) or is_player_area or "timing" in parts:
                findings.append({"path": rel, "kind": "log_file_in_publish_scope"})
        # Only explicit CI/private-notes scopes are suspicious: generic Wine
        # prefix directories named "logs" or "notes" are not findings by name.
        # Real .log files above still are.
        if parts & PRIVATE_DIR_NAMES:
            findings.append({"path": rel, "kind": "private_ci_or_notes_directory"})
    return findings


def _scan_targets_for_app(root: Path, entries: list[dict]):
    candidates = set(MACHO_SCAN_RELATIVE)
    for item in entries:
        if item["kind"] != "file":
            continue
        rel = item["path"]
        path = PurePosixPath(rel)
        if "timing" in {part.lower() for part in path.parts} and path.name.lower() in TIMING_BINARY_NAMES:
            candidates.add(rel)
    return sorted(rel for rel in candidates if (root / rel).is_file() and not (root / rel).is_symlink())


def _pin_specs(manifest: dict):
    resources = {
        "runtime_pins": "Contents/Resources/wine",
        "game_pins": "Contents/Resources/prefix-template/drive_c/ra3-mtld3d",
        "payload_pins": "Contents/Resources/prefix-template/drive_c/u60-mtld3d",
        "font_pins": "Contents/Resources/prefix-template/drive_c/windows/Fonts",
        "timing_pins": "Contents/Resources/timing",
    }
    specs = []
    issues = []
    for group, base in resources.items():
        values = manifest.get(group)
        if values is None and group == "font_pins":
            continue
        if not isinstance(values, dict):
            issues.append({"kind": "manifest_pin_group_missing_or_invalid", "group": group})
            continue
        for relative, expected in sorted(values.items()):
            rel = PurePosixPath(str(relative))
            if rel.is_absolute() or ".." in rel.parts or not rel.parts:
                issues.append({"kind": "unsafe_pin_path", "group": group, "path": str(relative)})
                continue
            if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", expected):
                issues.append({"kind": "invalid_pin_digest", "group": group, "path": str(relative)})
                continue
            specs.append((group, base, rel.as_posix(), expected.lower()))
    return specs, issues


def _count_summary(local_report: dict, inventory_digest: str, scanned_digest: str):
    path_issues = local_report.get("path_findings", [])
    matches = local_report.get("privacy_matches", [])
    pins = local_report.get("pins", [])
    signature = local_report.get("signature", {})
    signature_commands = signature.get("commands", [])
    return {
        "files_in_inventory": sum(entry.get("kind") == "file" for entry in local_report.get("path_inventory", [])),
        "directories_in_inventory": sum(entry.get("kind") == "directory" for entry in local_report.get("path_inventory", [])),
        "symlinks_in_inventory": sum(entry.get("kind") == "symlink" for entry in local_report.get("path_inventory", [])),
        "metadata_files_scanned": sum(scan.get("scan_source") == "metadata_bytes" for scan in local_report.get("scans", [])),
        "native_binaries_strings_scanned": sum(scan.get("scan_source") == "strings" for scan in local_report.get("scans", [])),
        "pins_checked": sum(pin.get("matches") is True for pin in pins),
        "pin_failures": sum(pin.get("matches") is not True for pin in pins) + len(local_report.get("pin_issues", [])),
        "privacy_matches": len(matches),
        "path_findings": len(path_issues),
        "signature_checks_passed": sum(command.get("exit_code") == 0 for command in signature_commands),
        "signature_checks_failed": sum(command.get("exit_code") != 0 for command in signature_commands),
        "strings_scan_failures": sum(bool(scan.get("exit_code")) for scan in local_report.get("scans", [])
                                      if scan.get("scan_source") == "strings"),
        "inventory_sha256": inventory_digest,
        "scanned_content_sha256": scanned_digest,
    }


def _safe_public_report(local_report: dict):
    inventory_digest = canonical_digest(local_report.get("path_inventory", []))
    scans_public = [{"scan_source": item.get("scan_source"), "bytes": item.get("bytes"),
                     "sha256": item.get("sha256") or item.get("strings_output_sha256")}
                    for item in local_report.get("scans", [])]
    pins_public = [{"expected_sha256": item.get("expected_sha256"),
                    "actual_sha256": item.get("actual_sha256"), "matches": item.get("matches")}
                   for item in local_report.get("pins", [])]
    manifest_digest = local_report.get("manifest", {}).get("sha256", "")
    # The public document intentionally has no paths, identifiers, raw strings,
    # certificate metadata, or per-file rows: only aggregate counts and hashes.
    counts = _count_summary(local_report, inventory_digest, canonical_digest(scans_public))
    counts.pop("inventory_sha256", None)
    counts.pop("scanned_content_sha256", None)
    return {
        "schema": SCHEMA_PUBLIC,
        "counts": counts,
        "sha256": {
            "manifest": manifest_digest,
            "inventory": inventory_digest,
            "scanned_content": canonical_digest(scans_public),
            "pins": canonical_digest(pins_public),
        },
    }


def _write_json_absent(path: Path, value: dict):
    path = path.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")


def _default_report_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "build/release-1.2/audit-prep"


def _is_inside(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _scan_local_indicators(root: Path, entries: list[dict], indicators: list[dict],
                           extra_strings: Iterable[str] = ()):
    scans, matches = [], []
    records = {item["path"]: item for item in entries}
    for relative, item in records.items():
        if item["kind"] != "file":
            continue
        path = root / relative
        if not _text_candidate(path, item.get("bytes", 0)):
            continue
        try:
            scan, found = _scan_bytes(path, indicators, "metadata_bytes", relative)
        except OSError as error:
            scans.append({"path": relative, "scan_source": "metadata_bytes", "error": str(error)})
            matches.append({"path": relative, "kind": "metadata_scan_failed"})
            continue
        scans.append(scan)
        matches.extend(found)
    strings_tool = shutil.which("strings")
    for relative in sorted(set(extra_strings)):
        path = root / relative
        if not path.is_file() or path.is_symlink():
            continue
        if not strings_tool:
            matches.append({"path": relative, "kind": "strings_tool_unavailable", "scan_source": "strings"})
            continue
        scan, found = _scan_strings(path, relative, indicators, strings_tool)
        scans.append(scan)
        matches.extend(found)
    return scans, matches


def _safe_bundle_file(root: Path, relative: str):
    rel = PurePosixPath(relative)
    if rel.is_absolute() or ".." in rel.parts or not rel.parts:
        return None
    path = root
    for part in rel.parts:
        path = path / part
        if path.is_symlink():
            return None
    try:
        path.resolve(strict=True).relative_to(root.resolve(strict=True))
    except (OSError, ValueError):
        return None
    return path if path.is_file() else None


def _verify_app_pins(root: Path, manifest: dict):
    specs, issues = _pin_specs(manifest)
    results = []
    for group, base, relative, expected in specs:
        display_path = f"{base}/{relative}"
        combined = _safe_bundle_file(root, display_path)
        if combined is None:
            results.append({"group": group, "path": display_path,
                            "expected_sha256": expected, "actual_sha256": None, "matches": False,
                            "error": "missing, non-regular, or symlinked pinned file"})
            continue
        actual = sha256_file(combined)
        results.append({"group": group, "path": display_path,
                        "expected_sha256": expected, "actual_sha256": actual,
                        "matches": actual == expected})
    display = manifest.get("display_plan")
    if display is not None:
        if not isinstance(display, dict) or not re.fullmatch(r"[0-9a-fA-F]{64}", str(display.get("sha256", ""))):
            issues.append({"kind": "invalid_display_plan_pin"})
        else:
            rel = display.get("resource", "Resources/display-plan")
            relpath = PurePosixPath(str(rel))
            if relpath.is_absolute() or ".." in relpath.parts:
                issues.append({"kind": "unsafe_display_plan_path", "path": str(rel)})
            else:
                combined_rel = ("Contents/Resources/" + relpath.as_posix()
                                if relpath.parts[:1] != ("Resources",) else "Contents/" + relpath.as_posix())
                path = _safe_bundle_file(root, combined_rel)
                if path is None:
                    results.append({"group": "display_plan", "path": combined_rel,
                                    "expected_sha256": display["sha256"].lower(), "actual_sha256": None,
                                    "matches": False, "error": "missing, non-regular, or symlinked pinned file"})
                else:
                    actual = sha256_file(path)
                    results.append({"group": "display_plan", "path": combined_rel,
                                    "expected_sha256": display["sha256"].lower(), "actual_sha256": actual,
                                    "matches": actual == display["sha256"].lower()})
    return results, issues


def _signature_metadata(app: Path):
    commands = []
    verify_args = ["/usr/bin/codesign", "--verify", "--deep", "--strict", "--verbose=2", str(app)]
    detail_args = ["/usr/bin/codesign", "-dv", "--verbose=4", str(app)]
    for label, argv in (("verify", verify_args), ("display", detail_args)):
        try:
            proc = subprocess.run(argv, capture_output=True, text=True, check=False)
            commands.append({"operation": label, "exit_code": proc.returncode,
                             "stdout": proc.stdout, "stderr": proc.stderr})
        except OSError as error:
            commands.append({"operation": label, "exit_code": 127, "error": str(error)})
    return {"commands": commands}


def audit_app(app_path: Path, known_terms: Iterable[str] = (), extra_strings: Iterable[str] = ()):
    original = app_path.expanduser()
    if not original.exists() or not original.is_dir() or original.suffix != ".app":
        raise ValueError("expected an existing .app directory")
    root = original.resolve()
    entries = _walk_inventory(root)
    findings = _path_findings(root, entries, app_mode=True)
    manifest_path = root / "Contents/Resources/manifest.json"
    info_path = root / "Contents/Info.plist"
    manifest = {}
    manifest_info = {"present": manifest_path.is_file(), "sha256": None}
    if manifest_path.is_file() and not manifest_path.is_symlink():
        try:
            raw = manifest_path.read_bytes()
            manifest = json.loads(raw.decode("utf-8"))
            if not isinstance(manifest, dict):
                raise ValueError("manifest root is not an object")
            manifest_info["sha256"] = sha256_bytes(raw)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
            findings.append({"path": "Contents/Resources/manifest.json", "kind": "manifest_invalid", "detail": str(error)})
    else:
        findings.append({"path": "Contents/Resources/manifest.json", "kind": "manifest_missing_or_symlinked"})
    info = {}
    if info_path.is_file() and not info_path.is_symlink():
        try:
            raw_plist = info_path.read_bytes()
            info = plistlib.loads(raw_plist)
            manifest_info["info_plist_sha256"] = sha256_bytes(raw_plist)
            manifest_info["bundle_identifier"] = info.get("CFBundleIdentifier") if isinstance(info, dict) else None
        except (OSError, plistlib.InvalidFileException, ValueError) as error:
            findings.append({"path": "Contents/Info.plist", "kind": "info_plist_invalid", "detail": str(error)})
    else:
        findings.append({"path": "Contents/Info.plist", "kind": "info_plist_missing_or_symlinked"})
    if isinstance(info, dict) and isinstance(manifest.get("bundle"), dict):
        if info.get("CFBundleIdentifier") != manifest["bundle"].get("id"):
            findings.append({"path": "Contents/Info.plist", "kind": "bundle_identifier_disagreement"})
        if info.get("CFBundleShortVersionString") != manifest["bundle"].get("short_version"):
            findings.append({"path": "Contents/Info.plist", "kind": "bundle_version_disagreement"})
    rates = manifest.get("frame_rate_profiles")
    if rates is not None:
        expected_rates = {"60", "120"}
        if not isinstance(rates, dict) or set(rates) != expected_rates:
            findings.append({"path": "Contents/Resources/manifest.json", "kind": "frame_rate_profile_contract_invalid"})
        elif manifest.get("default_frame_rate") != 60:
            findings.append({"path": "Contents/Resources/manifest.json", "kind": "default_frame_rate_not_60"})
    policy = manifest.get("signature_policy")
    if isinstance(policy, str) and re.search(r"local\s+signing\s+identity\s+[^;]+", policy, re.IGNORECASE):
        findings.append({"path": "Contents/Resources/manifest.json", "kind": "signing_identity_wording_in_manifest",
                         "value": policy})
    pins, pin_issues = _verify_app_pins(root, manifest)
    findings.extend(pin_issues)
    for item in pins:
        if item["matches"] is not True:
            findings.append({"path": item["path"], "kind": "manifest_pin_mismatch", "group": item["group"]})
    local_indicators = _indicators(known_terms)
    string_paths = list(_scan_targets_for_app(root, entries)) + list(extra_strings)
    scans, privacy_matches = _scan_local_indicators(root, entries, local_indicators, string_paths)
    findings.extend(privacy_matches)
    signature = _signature_metadata(root)
    findings.extend({"path": "<codesign>", "kind": "codesign_operation_failed",
                     "operation": command.get("operation"), "exit_code": command.get("exit_code")}
                    for command in signature["commands"] if command.get("exit_code") != 0)
    local = {
        "schema": SCHEMA_LOCAL,
        "mode": "app",
        "local_only": True,
        "target": str(root),
        "manifest": manifest_info,
        "path_inventory": entries,
        "scans": scans,
        "local_indicators": local_indicators,
        "privacy_matches": privacy_matches,
        "pins": pins,
        "pin_issues": pin_issues,
        "signature": signature,
        "path_findings": findings,
        "read_only": True,
        "launched_app_or_game": False,
    }
    return local


def _git_tracked_paths(root: Path):
    proc = subprocess.run(["/usr/bin/git", "-C", str(root), "ls-files", "-z"],
                          capture_output=True, check=False)
    if proc.returncode != 0:
        raise ValueError("--tracked-only requires a Git working tree")
    return [item.decode("utf-8", "surrogateescape") for item in proc.stdout.split(b"\0") if item]


def audit_tree(tree_path: Path, known_terms: Iterable[str] = (), tracked_only: bool = False,
               extra_strings: Iterable[str] = ()):
    original = tree_path.expanduser()
    if not original.exists() or not original.is_dir():
        raise ValueError("expected an existing publish directory")
    root = original.resolve()
    tracked = _git_tracked_paths(root) if tracked_only else None
    entries = _walk_inventory(root, tracked)
    findings = _path_findings(root, entries, app_mode=False)
    indicators = _indicators(known_terms)
    scans, privacy_matches = _scan_local_indicators(root, entries, indicators, extra_strings)
    findings.extend(privacy_matches)
    # Missing tracked paths and unsafe path entries already appear in findings.
    return {
        "schema": SCHEMA_LOCAL,
        "mode": "tree",
        "local_only": True,
        "target": str(root),
        "tracked_only": tracked_only,
        "tracked_path_count": len(tracked) if tracked is not None else None,
        "path_inventory": entries,
        "scans": scans,
        "local_indicators": indicators,
        "privacy_matches": privacy_matches,
        "pins": [],
        "pin_issues": [],
        "signature": {"commands": []},
        "path_findings": findings,
        "read_only": True,
        "launched_app_or_game": False,
    }


def _persist_reports(local: dict, local_path: Path, public_path: Path | None):
    _write_json_absent(local_path, local)
    if public_path is not None:
        _write_json_absent(public_path, _safe_public_report(local))


def _add_report_args(parser, base_name: str):
    defaults = _default_report_dir()
    parser.add_argument("--report", type=Path,
                        default=defaults / f"{base_name}.local.json",
                        help="new, absent local-only JSON report path")
    parser.add_argument("--public", action="store_true",
                        help="also write an aggregate counts-and-SHA-256 report")
    parser.add_argument("--public-report", type=Path,
                        help="new, absent public aggregate JSON report path (implies --public)")
    parser.add_argument("--known-local-term", action="append", default=[],
                        help="exact local-only indicator to scan; may be repeated")
    parser.add_argument("--strings-path", action="append", default=[],
                        help="extra relative binary path for strings scanning; may be repeated")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    app_parser = commands.add_parser("audit_app", help="audit a signed .app bundle read-only")
    app_parser.add_argument("app", type=Path)
    _add_report_args(app_parser, "app-audit")
    tree_parser = commands.add_parser("audit_tree", help="audit a publish directory read-only")
    tree_parser.add_argument("publish_dir", type=Path)
    tree_parser.add_argument("--tracked-only", action="store_true",
                             help="inventory only Git index paths (requires a Git checkout)")
    _add_report_args(tree_parser, "tree-audit")
    args = parser.parse_args(argv)
    public_path = args.public_report
    if public_path is not None:
        args.public = True
    if args.public and public_path is None:
        public_path = args.report.with_name(args.report.stem.replace(".local", "") + ".public.json")
    try:
        if args.command == "audit_app":
            local = audit_app(args.app, args.known_local_term, args.strings_path)
            target_root = Path(local["target"])
        else:
            local = audit_tree(args.publish_dir, args.known_local_term, args.tracked_only, args.strings_path)
            target_root = Path(local["target"])
        for report_path in (args.report, public_path):
            if report_path is not None and _is_inside(report_path, target_root):
                raise ValueError("report outputs must be outside the audited tree")
        _persist_reports(local, args.report, public_path)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"AUDIT ERROR: {error}", file=sys.stderr)
        return 2
    summary = _count_summary(local, canonical_digest(local["path_inventory"]), canonical_digest(local["scans"]))
    print(json.dumps({"mode": local["mode"], "local_report": str(args.report),
                      "public_report": str(public_path) if public_path else None,
                      "counts": {key: value for key, value in summary.items() if key not in {
                          "inventory_sha256", "scanned_content_sha256"}}}, ensure_ascii=False))
    return 1 if local["path_findings"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
