"""Clone a verified App and remove player artifacts only from its sharing template."""
import argparse
import ctypes
import ctypes.util
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

PRIVATE_SUFFIXES = {".ra3save", ".ra3ureplay", ".log", ".dmp", ".mdmp"}
PLAYER_ARCHIVE_DIRS = {"saved games", "replays"}
GAME_PLAYER_DIRS = {"red alert 3 uprising", "紅色警戒3 - 起義時刻"}


def is_private_player_file(path, users_root):
    """Identify logs and every file below a player's Saved Games/Replays folder."""
    path, users_root = Path(path), Path(users_root)
    try:
        relative = path.relative_to(users_root)
    except ValueError:
        return False
    parts = relative.parts
    is_game_profile = any(part.lower() in GAME_PLAYER_DIRS for part in parts)
    is_saved_content = any(part.lower() in PLAYER_ARCHIVE_DIRS for part in parts)
    return path.suffix.lower() in PRIVATE_SUFFIXES or (is_game_profile and is_saved_content)


def remove_player_archive_directories(users_root, output_root, removed):
    """Remove game save/replay directories from the cloned sharing copy only."""
    users_root, output_root = Path(users_root), Path(output_root)
    candidates = []
    if not users_root.is_dir() or users_root.is_symlink():
        return
    for current, dirs, _files in os.walk(users_root, followlinks=False):
        parent = Path(current)
        for name in list(dirs):
            path = parent / name
            if path.is_symlink():
                dirs.remove(name)
                continue
            parts = path.relative_to(users_root).parts
            in_game_tree = any(part.lower() in GAME_PLAYER_DIRS for part in parts)
            if in_game_tree and name.lower() in PLAYER_ARCHIVE_DIRS:
                candidates.append(path)
                dirs.remove(name)
    for path in sorted(candidates, key=lambda item: len(item.parts), reverse=True):
        relative = str(path.relative_to(output_root))
        if path.is_symlink():
            path.unlink()
        elif path.exists():
            shutil.rmtree(path)
        removed.append(relative)


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def sanitize_registry(text):
    """Remove hardware/session caches while retaining application settings."""
    blocks = text.split("\n[")
    kept, removed = [blocks[0]], 0
    cache_keys = ("mmdevices", "winecoreaudio", "initial display mode", "/enum/display",
                  "/enum/pci", "/enum/usb", "/enum/bth", "/control/video",
                  "hardware/devicemap/video", "/control/deviceclasses", "volatile environment")
    for block in blocks[1:]:
        header = block.split("\n", 1)[0].lower().replace("\\\\", "/")
        if any(key in header for key in cache_keys):
            removed += 1
            continue
        if "/control/class/{4d36e968-" in header:
            removed += 1
            continue
        import re
        block = re.sub(r'(?m)^"(ComputerName|Hostname|NV Hostname)"="[^"\n]*"$',
                       lambda match: '"' + match.group(1) + '"="RA3EP1MAC"', block)
        kept.append(block)
    return "\n[".join(kept), removed


def prepare(source, output):
    source, output = Path(source).resolve(), Path(output).absolute()
    if output.suffix != ".app" or output.exists() or output.is_symlink():
        raise RuntimeError("Choose a new absent .app output")
    if source == output or source in output.parents or output in source.parents:
        raise RuntimeError("Source and sharing copy must be separate")
    subprocess.run(["/usr/bin/codesign", "--verify", "--deep", "--strict", str(source)], check=True)
    resources = source / "Contents/Resources"
    manifest = json.loads((resources / "manifest.json").read_text())
    identity = digest(resources / "manifest.json")
    library = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
    clone = library.clonefile
    clone.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int]
    clone.restype = ctypes.c_int
    output.parent.mkdir(parents=True, exist_ok=True)
    if clone(os.fsencode(source), os.fsencode(output), 0):
        raise OSError(ctypes.get_errno(), "Sharing App clone failed")
    removed = []
    try:
        template = output / "Contents/Resources/prefix-template"
        users = template / "drive_c/users"
        for current, dirs, files in os.walk(template, followlinks=False):
            parent = Path(current)
            dirs[:] = [name for name in dirs if not (parent / name).is_symlink()]
            for name in files:
                path = parent / name
                if path.is_symlink():
                    continue
                lower = name.lower()
                is_player = users in path.parents and any(part in path.parts for part in
                    ("Red Alert 3 Uprising", "紅色警戒3 - 起義時刻"))
                if is_private_player_file(path, users) or (is_player and lower.startswith("stats_")):
                    removed.append(str(path.relative_to(output)))
                    path.unlink()
        backup = template / "system 2.reg"
        if backup.exists():
            removed.append(str(backup.relative_to(output)))
            backup.unlink()
        for registry in template.glob("*.reg"):
            cleaned, count = sanitize_registry(registry.read_text(encoding="utf-8"))
            registry.write_text(cleaned, encoding="utf-8")
            if count:
                removed.append(str(registry.relative_to(output)) + f":{count}-cached-device-sections")
        # Remove compiler source paths carried by local debug symbols. Code/data
        # section equivalence and a fresh signature are checked before release.
        for module in ("win32u.so", "winemac.so", "winecoreaudio.so"):
            binary = output / "Contents/Resources/wine/lib/wine/x86_64-unix" / module
            subprocess.run(["/usr/bin/strip", "-S", str(binary)], check=True, capture_output=True)
        game = template / "drive_c/ra3-mtld3d"
        for name in ("mtld3d-logs",):
            path = game / "Data" / name
            if path.exists():
                removed.append(str(path.relative_to(output)))
                shutil.rmtree(path)
        # Remove whole player save/replay directories: shipped campaign and
        # autosave files use EA-specific extensions, not only .ra3save.
        remove_player_archive_directories(users, output, removed)
        # Ship neutral initial skirmish/movie state; keep profile controls intact.
        for path in users.rglob("*"):
            if path.is_file() and not path.is_symlink() and path.name in ("Skirmish.ini", "movies.ini"):
                removed.append(str(path.relative_to(output)))
                path.unlink()
        for group, folder in [("game_pins", game),
                              ("payload_pins", template / "drive_c/u60-mtld3d")]:
            for relative, expected in manifest[group].items():
                if digest(folder / relative) != expected:
                    raise RuntimeError("Required sharing component changed: " + relative)
        if digest(resources / "manifest.json") != identity:
            raise RuntimeError("Source App changed while making sharing copy")
        return {"source_app": str(source), "sharing_app": str(output), "removed_player_artifacts": removed,
                "source_manifest_sha256": identity, "core_components_unchanged": True,
                "signature_requires_refresh": True, "saved_games_migrated_into_sharing_copy": False}
    except BaseException:
        shutil.rmtree(output)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if args.report.exists():
        raise RuntimeError("Report exists")
    result = prepare(args.source, args.output)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    with args.report.open("x") as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2)
    print("Prepared sharing copy; removed", len(result["removed_player_artifacts"]), "player artifacts.")


if __name__ == "__main__":
    main()
