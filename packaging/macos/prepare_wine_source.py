"""Prepare pinned, caller-supplied Wine source with the release's display patches."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[2]
WINE_COMMIT = "3562fea11d1830725859ac5ed790a3b9789ccd14"
FIXED_PATCHES = ["wine-fixed-viewport.patch", "wine-client-surface.patch", "wine-cursor-edge.patch",
                 "wine-native-mode.patch", "wine-window-mask.patch"]


def run(arguments):
    subprocess.run([str(value) for value in arguments], check=True)


def prepare(source, output, presentation="safe-area"):
    source, output = Path(source).resolve(), Path(output).resolve()
    if output.exists() or source == output or source in output.parents or output in source.parents:
        raise RuntimeError("Provide a source checkout and a separate absent output directory")
    head = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
    status = subprocess.check_output(["git", "-C", str(source), "status", "--porcelain=v1"], text=True)
    if head != WINE_COMMIT or status.strip():
        raise RuntimeError("Wine source must be clean and checked out at " + WINE_COMMIT)
    output.parent.mkdir(parents=True, exist_ok=True)
    run(["git", "clone", "--no-hardlinks", "--no-checkout", "--", source, output])
    try:
        run(["git", "-C", output, "checkout", "--detach", WINE_COMMIT])
        patches = [ROOT / "research/compat/fixed-viewport" / name for name in FIXED_PATCHES]
        patches.append(ROOT / "packaging/macos/wine-no-audio-capture.patch")
        if presentation == "safe-area":
            adaptive = ROOT / "research/compat/adaptive-viewport"
            shutil.copy2(adaptive / "viewport_spec.h", output / "include/wine/ra3_viewport.h")
            patches.extend(adaptive / name for name in ["wine-adaptive-win32u.patch", "wine-adaptive-macdriver.patch"])
        for patch in patches:
            run(["git", "-C", output, "apply", "--check", patch])
            run(["git", "-C", output, "apply", patch])
        return [str(path.relative_to(ROOT)) for path in patches]
    except BaseException:
        # This invocation owns only the new checkout, never the supplied source.
        shutil.rmtree(output)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--presentation", choices=["safe-area", "fixed"], default="safe-area")
    args = parser.parse_args()
    patches = prepare(args.source, args.output, args.presentation)
    print(json.dumps({"wine_commit": WINE_COMMIT, "presentation": args.presentation,
                      "patches_applied": patches, "source_modified": False}, indent=2))


if __name__ == "__main__":
    main()
