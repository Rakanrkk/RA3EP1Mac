"""Publish rebuilt modules only into the independent adaptive Wine clone."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[3]
BASE = ROOT / "build/compat-adaptive-viewport"
ACCEPTED = ROOT / "build/compat-fixed-viewport"
MODULES = {
    "dlls/win32u/win32u.so": "lib/wine/x86_64-unix/win32u.so",
    "dlls/winemac.drv/winemac.so": "lib/wine/x86_64-unix/winemac.so",
    "dlls/winecoreaudio.drv/winecoreaudio.so": "lib/wine/x86_64-unix/winecoreaudio.so",
}


def digest(path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def main():
    if "ra3ep1_1.0.game" in subprocess.check_output(
            ["/bin/ps", "-axww", "-o", "comm="], text=True).lower():
        raise RuntimeError("Game is running; candidate publication refused")
    if not (BASE / "isolation.json").is_file() or (BASE / "wine").is_symlink():
        raise RuntimeError("Independent candidate isolation record is missing")
    target = BASE / "candidate-manifest.json"
    if target.exists():
        raise RuntimeError("Candidate already published; preserve its existing manifest")
    accepted = json.loads((ACCEPTED / "candidate-manifest.json").read_text())
    pins = dict(accepted["runtime_pins"])
    for relative, expected in pins.items():
        if digest(ACCEPTED / "wine" / relative) != expected:
            raise RuntimeError("Accepted runtime changed: " + relative)
    for source in MODULES:
        binary = BASE / "wine-build" / source
        if not binary.is_file():
            raise RuntimeError("Missing rebuilt module: " + source)
        magic = binary.read_bytes()[:4]
        if magic != b"\xcf\xfa\xed\xfe":
            raise RuntimeError("Expected Mach-O module: " + source)
    for source, relative in MODULES.items():
        shutil.copy2(BASE / "wine-build" / source, BASE / "wine" / relative)
        pins[relative] = digest(BASE / "wine" / relative)
    patches = {p.name: digest(p) for p in Path(__file__).parent.glob("*.patch")}
    record = {
        "schema": "ra3-adaptive-viewport-candidate-v1", "wine_source": accepted["wine_source"],
        "runtime_pins": pins, "game_pins": accepted["game_pins"],
        "payload_pins": accepted["payload_pins"], "viewport": "safe-area-v1",
        "presentation": "safe-area-v1", "patches": patches,
        "status": "modules-built-owned-runtime-validation-pending",
        "accepted_rollback": "build/compat-fixed-viewport", "installed_app_modified": False,
    }
    for group, folder in [("runtime_pins", BASE / "wine"),
                          ("game_pins", BASE / "prefix/drive_c/ra3-mtld3d"),
                          ("payload_pins", BASE / "prefix/drive_c/u60-mtld3d")]:
        for relative, expected in record[group].items():
            if digest(folder / relative) != expected:
                raise RuntimeError("Candidate identity differs: " + relative)
    with target.open("x") as stream:
        json.dump(record, stream, indent=2)
    print("Published three rebuilt modules to isolated candidate; identities verified.")


if __name__ == "__main__":
    main()
