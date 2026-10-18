"""Compile and overlay only RA3Mac's three Unix modules into a supplied runtime clone."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

MODULES = {"dlls/win32u/win32u.so": "lib/wine/x86_64-unix/win32u.so",
           "dlls/winemac.drv/winemac.so": "lib/wine/x86_64-unix/winemac.so",
           "dlls/winecoreaudio.drv/winecoreaudio.so": "lib/wine/x86_64-unix/winecoreaudio.so"}


def sha256(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--build-dir", type=Path, required=True)
    parser.add_argument("--runtime-root", type=Path, required=True,
                        help="new imported clone with wine/, prefix/ and candidate-manifest.json")
    parser.add_argument("--toolchain-bin", type=Path, required=True)
    parser.add_argument("--make", default="gmake")
    parser.add_argument("--jobs", type=int, default=8)
    parser.add_argument("--deployment-target", default="15.0")
    args = parser.parse_args()
    if not 1 <= args.jobs <= 16:
        parser.error("Use1..16jobs")
    source, work, runtime = (path.resolve() for path in (args.source, args.build_dir, args.runtime_root))
    if source == work or source in work.parents or work in source.parents:
        raise RuntimeError("Wine source and build directories must be separate")
    if not (runtime / "isolation.json").is_file() or (runtime / "wine").is_symlink():
        raise RuntimeError("Import an independent runtime clone before overlaying modules")
    processes = subprocess.check_output(["/bin/ps", "-axww", "-o", "comm="], text=True).lower()
    if "ra3ep1_1.0.game" in processes:
        raise RuntimeError("Close the game before changing the imported runtime")
    if not (source / "configure").is_file() or not args.toolchain_bin.is_dir():
        raise RuntimeError("Wine configure script or LLVM-MinGW bin directory missing")
    environment = dict(os.environ)
    environment["PATH"] = str(args.toolchain_bin.resolve()) + os.pathsep + environment.get("PATH", "")
    environment["CC"] = "clang -arch x86_64"
    environment["MACOSX_DEPLOYMENT_TARGET"] = args.deployment_target
    work.mkdir(parents=True, exist_ok=True)
    if not (work / "Makefile").is_file():
        with (work / "configure.log").open("wb") as log:
            subprocess.run([str(source / "configure"), "--build=x86_64-apple-darwin", "--enable-archs=i386,x86_64",
                            "--with-mingw=llvm-mingw", "--without-freetype", "--without-x"], cwd=work,
                           env=environment, stdout=log, stderr=subprocess.STDOUT, check=True)
    with (work / "build.log").open("wb") as log:
        subprocess.run([args.make, f"-j{args.jobs}", *MODULES], cwd=work, env=environment,
                       stdout=log, stderr=subprocess.STDOUT, check=True)
    for relative in MODULES:
        binary = work / relative
        if binary.read_bytes()[:4] != b"\xcf\xfa\xed\xfe":
            raise RuntimeError("Expected Mach-O module: " + relative)
    manifest_path = runtime / "candidate-manifest.json"
    manifest = json.loads(manifest_path.read_text())
    for relative, destination in MODULES.items():
        shutil.copy2(work / relative, runtime / "wine" / destination)
        manifest["runtime_pins"][destination] = sha256(runtime / "wine" / destination)
    manifest["module_build"] = {"wine_source": "3562fea11d1830725859ac5ed790a3b9789ccd14",
                               "deployment_target": args.deployment_target, "runtime_validation": "required"}
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    print("Built and overlaid3modules in supplied runtime clone; verify owned drawing/input before use.")


if __name__ == "__main__":
    main()
