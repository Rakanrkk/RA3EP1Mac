"""Apply adaptive deltas and build only three modules in the isolated candidate."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[3]
BASE = ROOT / "build/compat-adaptive-viewport"
SOURCE = BASE / "wine-source"
COMMIT = "3562fea11d1830725859ac5ed790a3b9789ccd14"
MODULES = ["dlls/win32u/win32u.so", "dlls/winemac.drv/winemac.so",
           "dlls/winecoreaudio.drv/winecoreaudio.so"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jobs", type=int, default=8)
    args = parser.parse_args()
    if not 1 <= args.jobs <= 16:
        parser.error("Use 1..16 jobs")
    if not (BASE / "isolation.json").is_file() or SOURCE.is_symlink():
        raise RuntimeError("Prepare an independent candidate clone first")
    head = subprocess.check_output(["git", "-C", str(SOURCE), "rev-parse", "HEAD"], text=True).strip()
    if head != COMMIT:
        raise RuntimeError("Wine source commit differs")
    patches = [Path(__file__).with_name(name) for name in
               ["wine-adaptive-win32u.patch", "wine-adaptive-macdriver.patch"]]
    for patch in patches:
        if not patch.is_file():
            raise RuntimeError("Missing adaptive delta: " + patch.name)
        reverse = subprocess.run(["git", "-C", str(SOURCE), "apply", "--reverse", "--check", str(patch)],
                                 capture_output=True)
        if reverse.returncode:
            subprocess.run(["git", "-C", str(SOURCE), "apply", "--check", str(patch)], check=True)
            subprocess.run(["git", "-C", str(SOURCE), "apply", str(patch)], check=True)
    shutil.copy2(Path(__file__).with_name("viewport_spec.h"), SOURCE / "include/wine/ra3_viewport.h")
    mingw = ROOT / "build/compat-toolchains/llvm-mingw-20261006-extracted/llvm-mingw-20261006-ucrt-macos-universal/bin"
    environment = dict(os.environ)
    environment["PATH"] = ("/opt/homebrew/opt/make/libexec/gnubin:/opt/homebrew/opt/bison/bin:"
                           "/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin:" + str(mingw))
    environment["CC"] = "clang -arch x86_64"
    environment["MACOSX_DEPLOYMENT_TARGET"] = "15.0"
    work = BASE / "wine-build"
    work.mkdir(exist_ok=True)
    if not (work / "Makefile").exists():
        command = [str(SOURCE / "configure"), "--build=x86_64-apple-darwin", "--enable-archs=i386,x86_64",
                   "--with-mingw=llvm-mingw", "--without-freetype", "--without-x"]
        with (BASE / "adaptive-configure.log").open("wb") as log:
            subprocess.run(command, cwd=work, env=environment, stdout=log, stderr=subprocess.STDOUT, check=True)
    with (BASE / "adaptive-module-build.log").open("wb") as log:
        subprocess.run(["make", f"-j{args.jobs}", *MODULES], cwd=work, env=environment,
                       stdout=log, stderr=subprocess.STDOUT, check=True)
    pins = {name: hashlib.sha256((work / name).read_bytes()).hexdigest() for name in MODULES}
    (BASE / "adaptive-built-modules.json").write_text(json.dumps({
        "wine_source_commit": COMMIT, "deployment_target": "15.0", "modules": pins,
        "status": "build-pass-runtime-check-required"}, indent=2) + "\n")
    print("Built three isolated adaptive modules; runtime validation is still required.")


if __name__ == "__main__":
    main()
