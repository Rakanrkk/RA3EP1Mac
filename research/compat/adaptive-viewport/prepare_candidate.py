"""Clone accepted inputs into an absent adaptive candidate; never replace a prefix."""
import ctypes
import ctypes.util
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[3]
ACCEPTED = ROOT / "build/compat-fixed-viewport"
BASE = ROOT / "build/compat-adaptive-viewport"


def digest(path):
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def main():
    if BASE.exists() or BASE.is_symlink():
        raise RuntimeError("Candidate already exists; preserve it and choose a new version deliberately")
    processes = subprocess.check_output(["/bin/ps", "-axww", "-o", "comm="], text=True).lower()
    if "ra3ep1_1.0.game" in processes:
        raise RuntimeError("Game active; do not clone a running prefix")
    manifest = json.loads((ACCEPTED / "candidate-manifest.json").read_text())
    for group, folder in [("runtime_pins", ACCEPTED / "wine"),
                          ("game_pins", ACCEPTED / "prefix/drive_c/ra3-mtld3d"),
                          ("payload_pins", ACCEPTED / "prefix/drive_c/u60-mtld3d")]:
        for relative, expected in manifest[group].items():
            if digest(folder / relative) != expected:
                raise RuntimeError("Accepted source differs: " + relative)
    library = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
    clone = library.clonefile
    clone.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int]
    clone.restype = ctypes.c_int
    BASE.mkdir()
    records = []
    try:
        for name in ("wine", "prefix", "wine-source"):
            source, target = ACCEPTED / name, BASE / name
            if clone(os.fsencode(source), os.fsencode(target), 0):
                raise OSError(ctypes.get_errno(), "APFS candidate clone failed: " + name)
            records.append({"source": str(source), "destination": str(target), "method": "APFS clonefile"})
        for name in ("fullscreen_probe.exe", "fullscreen_probe_readback.exe", "cursor_probe.exe",
                     "edge_probe.exe", "window-geometry"):
            shutil.copy2(ACCEPTED / name, BASE / name)
        old = ("Z:" + str(ACCEPTED / "wine")).replace("/", "\\").replace("\\", "\\\\").encode()
        new = ("Z:" + str(BASE / "wine")).replace("/", "\\").replace("\\", "\\\\").encode()
        for name in ("system.reg", "user.reg", "userdef.reg", "system 2.reg"):
            path = BASE / "prefix" / name
            if path.is_file():
                path.write_bytes(path.read_bytes().replace(old, new))
        record = {"schema": "ra3mac-adaptive-viewport-isolation-v1", "clones": records,
                  "base_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
                  "source_candidate_manifest_sha256": digest(ACCEPTED / "candidate-manifest.json"),
                  "accepted_app": "/Applications/红警3起义时刻.app", "no_shared_writable_prefix": True,
                  "candidate_status": "not-built-not-accepted"}
        with (BASE / "isolation.json").open("x") as stream:
            json.dump(record, stream, ensure_ascii=False, indent=2)
    except BaseException:
        # Only this invocation's newly created, unpublished directory is removed.
        shutil.rmtree(BASE)
        raise
    print("Prepared independent adaptive source/runtime/prefix; accepted inputs unchanged.")


if __name__ == "__main__":
    main()
