"""Run offline adaptive-display gates; never launch Wine, game or graphical UI."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[3]
SOURCE = Path(__file__).resolve().parent
BUILD = ROOT / "build/compat-adaptive-viewport"


def run(arguments):
    result = subprocess.run([str(arg) for arg in arguments], cwd=ROOT, capture_output=True,
                            text=True, timeout=120)
    if result.returncode:
        raise RuntimeError("Check failed: " + " ".join(map(str, arguments)) + "\n" +
                           result.stdout + result.stderr)
    return result.stdout.strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wine-source", type=Path, help="optional prepared patched Wine source for actual-route checks")
    args = parser.parse_args()
    BUILD.mkdir(parents=True, exist_ok=True)
    compiler = ["xcrun", "swiftc", "-O", "-target", "arm64-apple-macos15.0", "-swift-version", "5"]
    results = {}
    run(["clang", "-std=c11", "-Wall", "-Wextra", "-Werror", "-pedantic",
         SOURCE / "test_viewport_spec.c", "-o", BUILD / "test-viewport-spec"])
    results["viewport_spec"] = run([BUILD / "test-viewport-spec"])
    run([*compiler, "-framework", "AppKit", "-framework", "CoreGraphics",
         SOURCE / "DisplayPlan.swift", SOURCE / "test_display_plan.swift", "-o", BUILD / "test-display-plan"])
    results["display_geometry"] = run([BUILD / "test-display-plan"])
    run([*compiler, "-framework", "AppKit", "-framework", "CryptoKit",
         ROOT / "packaging/macos/Launcher.swift", "-o", BUILD / "RA3Launcher-checks"])
    results["launcher_cli"] = run([sys.executable, "-I", "-B", ROOT / "packaging/macos/test_launcher_cli.py",
                                    BUILD / "RA3Launcher-checks"])
    results["launcher_prepare"] = run([sys.executable, "-I", "-B", ROOT / "packaging/macos/test_launcher_prepare.py",
                                        BUILD / "RA3Launcher-checks"])
    results["packaging"] = run([sys.executable, "-I", "-B", ROOT / "packaging/macos/test_build_app.py"])
    results["runner_validator"] = run([sys.executable, "-I", "-B", SOURCE / "run_owned_check.py", "--self-test"])
    if args.wine_source:
        results["actual_wine_routes"] = run([sys.executable, "-I", "-B", SOURCE / "test_wine_routes.py", "--wine-source", args.wine_source])
    else:
        results["actual_wine_routes"] = "SKIPPED: pass --wine-source for checks against prepared Wine source"
    record = {"schema": "ra3-adaptive-offline-checks-v1", "checks": results,
              "game_or_wine_started": False, "graphical_ui_opened": False,
              "head": run(["git", "rev-parse", "HEAD"]),
              "launcher_source_sha256": hashlib.sha256((ROOT / "packaging/macos/Launcher.swift").read_bytes()).hexdigest(),
              "status": "pass"}
    (BUILD / "offline-checks.json").write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n")
    for name, result in results.items():
        print(name + ": " + result.splitlines()[-1])
    print("PASS: offline gates; no Wine, game or graphical UI started")


if __name__ == "__main__":
    main()
