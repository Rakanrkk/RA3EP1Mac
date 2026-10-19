#!/usr/bin/env python3
"""Read-only SHA-256 inventory for RA3 player data, Wine registries and app prefs.

Only game player folders and explicitly supplied registry/preferences files are
read. Windows/Wine system files and other prefix contents are never traversed.
Reports are local-only, mode 0600, and created only at absent output paths.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import sys

SCHEMA = "ra3-user-data-snapshot-v1"
GAME_DIRS = {"red alert 3 uprising", "紅色警戒3 - 起義時刻"}
PLAYER_DIR_NAMES = {"saved games", "replays"}
REGISTRY_NAMES = {"system.reg", "user.reg", "userdef.reg"}
PREFERENCE_SUFFIXES = {".plist"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _safe_relative(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError as error:
        raise ValueError(f"input is outside its declared root: {path}") from error


def inventory_player_data(users_root: Path):
    users_root = users_root.expanduser().resolve(strict=True)
    if not users_root.is_dir():
        raise ValueError("--users-root must be a directory")
    found = []
    profile_roots = []
    # Game data lives under profile roots like drive_c/users/<profile>/AppData/
    # Roaming, Documents, My Documents — and macOS-style "<profile>/Saved Games"
    # (where the game folder sits directly inside). Windows system and
    # app-support directory names at the user root are skipped so a prefix's
    # AppData/Windows tree is never walked as if it were a profile.
    for profile in users_root.iterdir():
        if not profile.is_dir() or profile.is_symlink():
            continue
        if profile.name.lower() in {"appdata", "local", "locallow", "roaming",
                                    "application data", "windows", "program files",
                                    "program files (x86)", "programdata"}:
            continue
        parents = [profile / relative for relative in
                   (Path("AppData/Roaming"), Path("Documents"), Path("My Documents"))]
        # The launcher-style location: <profile>/Saved Games/<GameFolder>/...
        saved_games = profile / "Saved Games"
        if saved_games.is_dir() and not saved_games.is_symlink():
            parents.append(saved_games)
        for parent in parents:
            if not parent.is_dir() or parent.is_symlink():
                continue
            for game_dir in parent.iterdir():
                if game_dir.name.lower() in GAME_DIRS and not game_dir.is_symlink() and game_dir.is_dir():
                    profile_roots.append(game_dir)
    for game_root in sorted(set(profile_roots)):
        for current, dirs, names in os.walk(game_root, followlinks=False):
            parent = Path(current)
            kept = []
            for name in sorted(dirs):
                path = parent / name
                relative = path.relative_to(users_root)
                if path.is_symlink():
                    found.append({"category": "player_data", "path": relative.as_posix(),
                                  "kind": "symlink", "target": os.readlink(path)})
                else:
                    kept.append(name)
            dirs[:] = kept
            for name in sorted(names):
                path = parent / name
                relative = path.relative_to(users_root)
                if path.is_symlink() or not path.is_file():
                    found.append({"category": "player_data", "path": relative.as_posix(),
                                  "kind": "symlink" if path.is_symlink() else "special"})
                    continue
                found.append({"category": "player_data", "path": relative.as_posix(),
                              "kind": "file", "bytes": path.stat().st_size,
                              "sha256": sha256_file(path)})
    return found


def inventory_explicit(paths, category: str):
    found = []
    for supplied in paths:
        path = Path(supplied).expanduser()
        if not path.exists() or path.is_symlink() or not path.is_file():
            raise ValueError(f"{category} input must be an existing regular file, not a symlink: {path}")
        name = path.name
        if category == "registry" and name.lower() not in REGISTRY_NAMES:
            raise ValueError(f"registry filename is outside the allowed player registry set: {name}")
        if category == "preferences" and path.suffix.lower() not in PREFERENCE_SUFFIXES:
            raise ValueError(f"preferences input must be a .plist file: {name}")
        absolute = path.resolve(strict=True)
        found.append({"category": category, "logical_path": f"{category}/{name}",
                      "path": str(absolute), "kind": "file",
                      "bytes": absolute.stat().st_size, "sha256": sha256_file(absolute)})
    logical = [item["logical_path"] for item in found]
    if len(logical) != len(set(logical)):
        raise ValueError(f"duplicate {category} basename; specify only one file per basename")
    return found


def snapshot(users_root: Path, registry_files, preference_files):
    entries = inventory_player_data(users_root)
    entries.extend(inventory_explicit(registry_files, "registry"))
    entries.extend(inventory_explicit(preference_files, "preferences"))
    for item in entries:
        if item["category"] == "player_data":
            item["logical_path"] = "player_data/" + item["path"]
    entries.sort(key=lambda item: (item["category"], item.get("logical_path", item["path"])))
    return {"schema": SCHEMA, "local_only": True, "read_only": True,
            "scope": {"player_data": "only game-specific user folders beneath drive_c/users",
                      "registry": "only explicitly named Wine registry files",
                      "preferences": "only explicitly named macOS .plist files",
                      "windows_prefix_internals_traversed": False},
            "entries": entries}


def compare(before: dict, after: dict):
    if before.get("schema") != SCHEMA or after.get("schema") != SCHEMA:
        raise ValueError("both inputs must be user-data snapshot files from this tool")
    left = {item.get("logical_path", item["path"]): item for item in before.get("entries", [])}
    right = {item.get("logical_path", item["path"]): item for item in after.get("entries", [])}
    added, removed, changed = [], [], []
    for key in sorted(set(left) | set(right)):
        old, new = left.get(key), right.get(key)
        if old is None:
            added.append(new)
        elif new is None:
            removed.append(old)
        elif (old.get("kind"), old.get("bytes"), old.get("sha256"), old.get("target")) != \
             (new.get("kind"), new.get("bytes"), new.get("sha256"), new.get("target")):
            changed.append({"before": old, "after": new})
    return {"schema": "ra3-user-data-snapshot-diff-v1", "local_only": True,
            "unchanged": len(set(left) & set(right)) - len(changed),
            "added": added, "removed": removed, "changed": changed}


def write_absent(path: Path, value: dict):
    path = path.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    make = actions.add_parser("snapshot", help="inventory game data and named registry/preferences files")
    make.add_argument("--users-root", required=True, type=Path,
                      help="Wine drive_c/users directory containing RA3 player folders")
    make.add_argument("--registry-file", action="append", default=[], type=Path,
                      help="explicit Wine system.reg, user.reg, or userdef.reg; repeat as needed")
    make.add_argument("--preferences-file", action="append", default=[], type=Path,
                      help="explicit macOS .plist preferences file; repeat as needed")
    make.add_argument("--output", required=True, type=Path,
                      help="new absent local snapshot JSON path")
    diff = actions.add_parser("compare", help="compare two local snapshots by path and SHA-256")
    diff.add_argument("before", type=Path)
    diff.add_argument("after", type=Path)
    diff.add_argument("--output", required=True, type=Path,
                      help="new absent local comparison JSON path")
    args = parser.parse_args(argv)
    try:
        if args.action == "snapshot":
            result = snapshot(args.users_root, args.registry_file, args.preferences_file)
        else:
            before = json.loads(args.before.read_text(encoding="utf-8"))
            after = json.loads(args.after.read_text(encoding="utf-8"))
            result = compare(before, after)
        write_absent(args.output, result)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"SNAPSHOT ERROR: {error}", file=sys.stderr)
        return 2
    if args.action == "snapshot":
        print(f"Snapshot created locally: {len(result['entries'])} entries")
    else:
        print(f"Comparison created locally: {len(result['added'])} added, "
              f"{len(result['removed'])} removed, {len(result['changed'])} changed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
