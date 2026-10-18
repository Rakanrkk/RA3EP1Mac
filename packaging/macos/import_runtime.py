"""Import a supplied, prepared Wine/game prefix into a new local packaging input."""
import argparse
import ctypes
import ctypes.util
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

RUNTIME_FILES = ["bin/wine", "bin/wineserver", "lib/wine/x86_64-unix/win32u.so",
                 "lib/wine/x86_64-unix/winemac.so",
                 "lib/wine/d3d9/mtld3d/i386-windows/d3d9.dll",
                 "lib/wine/d3d9/mtld3d/i386-windows/mtld3d.dll",
                 "lib/wine/d3d9/mtld3d/x86_64-unix/mtld3d.so"]
GAME_FILES = ["Data/ra3ep1_1.0.game", "Data/Shaders.big", "Data/Misc.big", "Data/StaticStream.big"]
PAYLOAD_FILES = ["Uprising60.exe", "uprising60.dll"]
SOURCE_COMMIT = "3562fea11d1830725859ac5ed790a3b9789ccd14"


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def clone_tree(source, target):
    library = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
    clone = library.clonefile
    clone.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int]
    clone.restype = ctypes.c_int
    if clone(os.fsencode(source), os.fsencode(target), 0) == 0:
        return "apfs-clonefile"
    if ctypes.get_errno() not in (1, 18, 45, 95):
        raise OSError(ctypes.get_errno(), "clonefile failed")
    shutil.copytree(source, target, symlinks=True)
    return "copytree-symlinks"


def windows_registry_path(path):
    return ("Z:" + str(path)).replace("/", "\\").replace("\\", "\\\\").encode()


def prepare(wine, prefix, output, game=None, payload=None, presentation="safe-area", clone=clone_tree):
    wine, prefix, output = (Path(p).resolve() for p in (wine, prefix, output))
    game = Path(game).resolve() if game else prefix / "drive_c/ra3-mtld3d"
    payload = Path(payload).resolve() if payload else prefix / "drive_c/u60-mtld3d"
    if output.exists() or output.is_symlink():
        raise RuntimeError("Output already exists; choose a new directory")
    for source in (wine, prefix, game, payload):
        if not source.is_dir() or output == source or source in output.parents or output in source.parents:
            raise RuntimeError("Inputs and output must be separate directories")
    if not (prefix / "system.reg").is_file():
        raise RuntimeError("Supply an initialized Wine prefix with system.reg")
    identities = {
        "runtime_pins": {p: digest(wine / p) for p in RUNTIME_FILES},
        "game_pins": {p: digest(game / p) for p in GAME_FILES},
        "payload_pins": {p: digest(payload / p) for p in PAYLOAD_FILES},
    }
    if identities["game_pins"]["Data/ra3ep1_1.0.game"] != "19b3542a7d2f7a050b16b2ea5e4ba0231b13c3fb7fcdb2c3e89767d4d354430b":
        raise RuntimeError("Game executable is not the supported build")
    output.mkdir(parents=True)
    try:
        clone(wine, output / "wine")
        clone(prefix, output / "prefix")
        for original, relative in [(game, "drive_c/ra3-mtld3d"), (payload, "drive_c/u60-mtld3d")]:
            destination = output / "prefix" / relative
            default_source = prefix / relative
            if original != default_source.resolve():
                if destination.exists():
                    raise RuntimeError("Cloned prefix already contains the destination; use its data or a clean prefix")
                destination.parent.mkdir(parents=True, exist_ok=True)
                clone(original, destination)
        for name in ("system.reg", "user.reg", "userdef.reg", "system 2.reg"):
            path = output / "prefix" / name
            if path.is_file():
                path.write_bytes(path.read_bytes().replace(windows_registry_path(wine),
                                                          windows_registry_path(output / "wine")))
        record = {"schema": "ra3mac-release-input-v1", "wine_source": SOURCE_COMMIT,
                  "viewport": "safe-area-v1" if presentation == "safe-area" else "3024x1900",
                  **identities, "status": "imported-input-requires-module-and-owned-runtime-validation"}
        (output / "candidate-manifest.json").write_text(json.dumps(record, indent=2) + "\n")
        (output / "isolation.json").write_text(json.dumps({"schema": "ra3mac-release-import-v1",
            "wine_source": str(wine), "prefix_source": str(prefix), "sources_modified": False,
            "presentation": presentation, "game_launched": False}, indent=2) + "\n")
        for key, folder in [("runtime_pins", output / "wine"),
                            ("game_pins", output / "prefix/drive_c/ra3-mtld3d"),
                            ("payload_pins", output / "prefix/drive_c/u60-mtld3d")]:
            for relative, expected in identities[key].items():
                if digest(folder / relative) != expected:
                    raise RuntimeError("Imported identity differs: " + relative)
        return record
    except BaseException:
        shutil.rmtree(output)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wine", type=Path, required=True)
    parser.add_argument("--prefix", type=Path, required=True)
    parser.add_argument("--game-dir", type=Path)
    parser.add_argument("--payload-dir", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--presentation", choices=["fixed", "safe-area"], default="safe-area")
    args = parser.parse_args()
    listing = subprocess.check_output(["/bin/ps", "-axww", "-o", "comm="], text=True).lower()
    if "ra3ep1_1.0.game" in listing:
        raise RuntimeError("Close the game before importing its prepared prefix")
    prepare(args.wine, args.prefix, args.output, args.game_dir, args.payload_dir, args.presentation)
    print("Prepared local packaging inputs; source installation and prefix were not changed.")


if __name__ == "__main__":
    main()
