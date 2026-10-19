#!/usr/bin/env python3
"""Build and ad-hoc sign a user-prepared local RA3 Uprising app bundle."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import runpy
import subprocess
import sys


REPO = Path(__file__).resolve().parents[2]
BUILD = REPO / "build"
MACOS = REPO / "packaging/macos"
ADAPTIVE_BUNDLE_ID = "org.ra3mac.UprisingMetal.Adaptive"
FIXED_BUNDLE_ID = "org.ra3mac.UprisingMetal"
MACHO_MAGICS = {
    b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe", b"\xfe\xed\xfa\xcf",
    b"\xfe\xed\xfa\xce", b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca",
    b"\xca\xfe\xba\xbf", b"\xbf\xba\xfe\xca",
}
AUDIO_MARKER = b"RA3_NO_AUDIO_CAPTURE"
TIMING_SHA256 = {
    60: "9017171020424cf6f040b8ea8802016ccaada70049b147fed25d48afe681c84b",
    120: "1ede43dc679bcf2f91c19dd64c8695f8898aa75574c04e25921f4c93c9d0c7c3",
}
PIN_RE = re.compile(r"[0-9a-f]{64}")

# runpy loads the existing builder in this isolated interpreter. Its public main
# function still receives ordinary CLI argv, while REPORTS is set to the
# caller-selected directory before any build work begins.
BUILD_APP_RUNNER = (
    "import importlib.util,sys\n"
    "from pathlib import Path\n"
    "spec=importlib.util.spec_from_file_location('ra3_build_app',sys.argv[1])\n"
    "module=importlib.util.module_from_spec(spec)\n"
    "spec.loader.exec_module(module)\n"
    "module.REPORTS=Path(sys.argv[2])\n"
    "raise SystemExit(module.main(sys.argv[3:]))\n"
)


class ReleaseBuildError(RuntimeError):
    pass


def is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def paths_overlap(left: Path, right: Path) -> bool:
    return is_within(left, right) or is_within(right, left)


def output_path(path: Path) -> Path:
    expanded = path.expanduser()
    # Resolve parent aliases but preserve the final path component so lexists()
    # can reject a dangling symlink instead of following it into another target.
    return expanded.parent.resolve() / expanded.name


def require_file(path: Path, label: str) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise ReleaseBuildError(f"{label} must be an existing file: {resolved}")
    return resolved


def file_digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def validate_flat_timing_directory(directory: Path, runtime_root: Path) -> Path:
    directory = directory.expanduser().resolve()
    if not directory.is_dir():
        raise ReleaseBuildError("--timing-dir must name an existing flat timing directory")
    expected_files = ("Uprising60.exe", "fps60.dll", "fps120.dll")
    payload_root = runtime_root / "prefix/drive_c/u60-mtld3d"
    try:
        manifest = json.loads((runtime_root / "candidate-manifest.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ReleaseBuildError("cannot read runtime candidate manifest for timing loader pin") from error
    loader_pin = manifest.get("payload_pins", {}).get("Uprising60.exe")
    source_loader = payload_root / "Uprising60.exe"
    if not isinstance(loader_pin, str) or not source_loader.is_file() or file_digest(source_loader) != loader_pin:
        raise ReleaseBuildError("runtime source Uprising60.exe does not match its candidate manifest pin")
    for name in expected_files:
        path = directory / name
        if not path.is_file():
            raise ReleaseBuildError(f"--timing-dir is missing {name}")
        if name == "Uprising60.exe":
            actual = file_digest(path)
            if actual != loader_pin:
                raise ReleaseBuildError("--timing-dir/Uprising60.exe does not match the runtime source loader pin")
            data = path.read_bytes()
        else:
            rate = 60 if name == "fps60.dll" else 120
            data = path.read_bytes()
            if len(data) < 0x46 or data[:2] != b"MZ":
                raise ReleaseBuildError(f"--timing-dir/{name} is not a PE image")
            offset = int.from_bytes(data[0x3C:0x40], "little")
            if offset < 0x40 or offset + 6 > len(data) or data[offset:offset + 4] != b"PE\0\0":
                raise ReleaseBuildError(f"--timing-dir/{name} has an invalid PE header")
            if int.from_bytes(data[offset + 4:offset + 6], "little") != 0x014C:
                raise ReleaseBuildError(f"--timing-dir/{name} must be i386")
            if hashlib.sha256(data).hexdigest() != TIMING_SHA256[rate]:
                raise ReleaseBuildError(f"--timing-dir/{name} SHA-256 is not the accepted release identity")
    return directory


def validate_timing_directory(rate: int, directory: Path, expected_sha: str = None) -> Path:
    directory = directory.expanduser().resolve()
    if not directory.is_dir():
        raise ReleaseBuildError(f"--timing-{rate} must name an existing artifact directory")
    dll, report = directory / "uprising120.dll", directory / "build-result.json"
    if not dll.is_file() or not report.is_file():
        raise ReleaseBuildError(f"--timing-{rate} requires uprising120.dll and build-result.json")
    try:
        metadata = json.loads(report.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ReleaseBuildError(f"--timing-{rate} report is invalid JSON") from error
    if not isinstance(metadata, dict):
        raise ReleaseBuildError(f"--timing-{rate} report must be a JSON object")
    target_rate = metadata.get("target_updates_hz")
    if not isinstance(target_rate, int) or isinstance(target_rate, bool) or target_rate != rate:
        raise ReleaseBuildError(f"--timing-{rate} report target_updates_hz must equal {rate}")
    data = dll.read_bytes()
    if len(data) < 0x46 or data[:2] != b"MZ":
        raise ReleaseBuildError(f"--timing-{rate} payload is not a PE image")
    pe_offset = int.from_bytes(data[0x3C:0x40], "little")
    if pe_offset < 0x40 or pe_offset + 6 > len(data) or data[pe_offset:pe_offset + 4] != b"PE\0\0":
        raise ReleaseBuildError(f"--timing-{rate} payload has an invalid PE header")
    if int.from_bytes(data[pe_offset + 4:pe_offset + 6], "little") != 0x014C:
        raise ReleaseBuildError(f"--timing-{rate} payload must be i386")
    actual = hashlib.sha256(data).hexdigest()
    if metadata.get("dll_sha256") != actual:
        raise ReleaseBuildError(f"--timing-{rate} payload differs from its build report SHA-256")
    if expected_sha is None:
        expected_sha = TIMING_SHA256[rate]
    if actual != expected_sha:
        raise ReleaseBuildError(f"--timing-{rate} payload SHA-256 is not the accepted release identity")
    return directory


def timing_report(directory: Path, rate: int) -> dict:
    dll = directory / "uprising120.dll"
    return {"rate": rate, "target_updates_hz": rate,
            "dll_sha256": file_digest(dll), "bytes": dll.stat().st_size,
            "staged_as": f"fps{rate}.dll"}


def validate_runtime_root(runtime_root: Path, runtime_only: bool) -> Path:
    root = runtime_root.expanduser().resolve()
    if not root.is_dir():
        raise ReleaseBuildError(f"--runtime-root must be an existing directory: {root}")

    wine = root / "wine"
    prefix = root / "prefix"
    manifest_path = root / "candidate-manifest.json"
    for path, label in ((wine, "runtime wine directory"),
                        (prefix, "runtime prefix directory"),
                        (manifest_path, "candidate manifest")):
        if not (path.is_dir() if path != manifest_path else path.is_file()):
            raise ReleaseBuildError(f"runtime root is incomplete; missing {label}: {path}")

    runtime_only = bool(runtime_only)
    required_paths = [wine / "bin/wine", wine / "bin/wineserver", prefix / "drive_c/u60-mtld3d"]
    if not runtime_only:
        required_paths.append(prefix / "drive_c/ra3-mtld3d")
    for path in required_paths:
        if not (path.is_dir() if path.is_dir() else path.is_file()):
            raise ReleaseBuildError(f"runtime root is incomplete; missing {path.relative_to(root)}")
    for path in (wine / "bin/wine", wine / "bin/wineserver"):
        if not os.access(path, os.X_OK):
            raise ReleaseBuildError(f"runtime executable is not executable: {path.relative_to(root)}")

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ReleaseBuildError(f"candidate manifest is not valid JSON: {manifest_path}") from error
    if not isinstance(manifest, dict):
        raise ReleaseBuildError("candidate manifest must contain a JSON object")

    pin_roots = {
        "runtime_pins": wine,
        "game_pins": prefix / "drive_c/ra3-mtld3d",
        "payload_pins": prefix / "drive_c/u60-mtld3d",
    }
    runtime_only = bool(runtime_only)
    for group, base in pin_roots.items():
        pins = manifest.get(group)
        if group == "game_pins" and runtime_only:
            if not isinstance(pins, dict):
                raise ReleaseBuildError("candidate manifest game_pins must be a JSON object")
            continue
        if not isinstance(pins, dict) or (not pins and group != "game_pins"):
            raise ReleaseBuildError(f"candidate manifest requires a valid {group} map")
        base_resolved = base.resolve()
        for relative, digest in pins.items():
            rel = PurePosixPath(relative) if isinstance(relative, str) else None
            if (rel is None or not relative or rel.is_absolute() or ".." in rel.parts
                    or "\\" in relative or not PIN_RE.fullmatch(digest or "")):
                raise ReleaseBuildError(f"candidate manifest has an invalid {group} entry")
            pinned = base.joinpath(*rel.parts)
            if not pinned.is_file():
                raise ReleaseBuildError(f"candidate manifest pin is missing: {group}/{relative}")
            if not is_within(pinned.resolve(), base_resolved):
                raise ReleaseBuildError(f"candidate manifest pin escapes its source: {group}/{relative}")
            if file_digest(pinned) != digest:
                raise ReleaseBuildError(f"candidate manifest pin does not match: {group}/{relative}")
    return root


def validate_audio_module(path: Path) -> Path:
    module = require_file(path, "--audio-module")
    try:
        data = module.read_bytes()
    except OSError as error:
        raise ReleaseBuildError(f"cannot read --audio-module: {module}") from error
    if data[:4] not in MACHO_MAGICS or AUDIO_MARKER not in data:
        raise ReleaseBuildError("--audio-module must be a patched Mach-O containing RA3_NO_AUDIO_CAPTURE")
    return module


def validate_output_and_directories(args, runtime_root: Path) -> tuple[Path, Path, Path]:
    output = output_path(args.output)
    tools_dir = args.tools_dir.expanduser().resolve()
    reports_dir = args.reports_dir.expanduser().resolve()
    if output.suffix != ".app":
        raise ReleaseBuildError("--output must name an .app bundle")
    if os.path.lexists(output):
        raise ReleaseBuildError(f"--output must not already exist: {output}")

    for label, directory in (("--tools-dir", tools_dir), ("--reports-dir", reports_dir)):
        if os.path.lexists(directory) and not directory.is_dir():
            raise ReleaseBuildError(f"{label} must be a directory when it already exists: {directory}")
        if paths_overlap(directory, runtime_root):
            raise ReleaseBuildError(f"{label} must be separate from --runtime-root: {directory}")
        if paths_overlap(directory, output):
            raise ReleaseBuildError(f"{label} must be separate from --output: {directory}")
    if is_within(output, runtime_root):
        raise ReleaseBuildError("--output must be outside --runtime-root")
    return output, tools_dir, reports_dir


def validate_inputs(args) -> dict[str, Path]:
    runtime_root = args.runtime_root.expanduser().resolve()
    output, tools_dir, reports_dir = validate_output_and_directories(args, runtime_root)
    if args.public and args.identity != "-":
        raise ReleaseBuildError("--public requires --identity -")
    if args.runtime_only and args.public and args.identity != "-":
        raise ReleaseBuildError("public runtime builds require ad-hoc signing")
    runtime_root = validate_runtime_root(runtime_root, args.runtime_only)
    audio_module = validate_audio_module(args.audio_module)
    icon = require_file(args.icon, "--icon")
    audio_patch = require_file(MACOS / "wine-no-audio-capture.patch", "packaged audio patch")

    for source in (MACOS / "build_app.py", MACOS / "sign_app.py", MACOS / "Launcher.swift",
                   REPO / "research/compat/display_mode.swift",
                   REPO / "research/compat/adaptive-viewport/DisplayPlan.swift",
                   REPO / "research/compat/adaptive-viewport/display_plan_cli.swift"):
        require_file(source, "repository build input")

    overrides = {
        "launcher": require_file(args.launcher, "--launcher") if args.launcher else None,
        "display_helper": require_file(args.display_helper, "--display-helper")
        if args.display_helper else None,
        "display_plan": require_file(args.display_plan, "--display-plan")
        if args.display_plan else None,
    }
    if args.presentation == "safe-area" and overrides["display_plan"] is not None:
        plan = overrides["display_plan"]
        if not os.access(plan, os.X_OK) or plan.open("rb").read(4) not in MACHO_MAGICS:
            raise ReleaseBuildError("--display-plan must be an executable Mach-O helper")
    if overrides["launcher"] is not None and not os.access(overrides["launcher"], os.X_OK):
        raise ReleaseBuildError("--launcher must be executable")
    if overrides["display_helper"] is not None and not os.access(overrides["display_helper"], os.X_OK):
        raise ReleaseBuildError("--display-helper must be executable")

    compile_names = {
        "launcher": "RA3Launcher",
        "display_helper": "display-mode",
        "display_plan": "display-plan",
    }
    for key, name in compile_names.items():
        if overrides[key] is None and os.path.lexists(tools_dir / name):
            raise ReleaseBuildError(f"refusing to overwrite an existing build tool: {tools_dir / name}")

    if sys.platform != "darwin":
        raise ReleaseBuildError("release builds require macOS and the system xcrun Swift toolchain")
    if bool(args.timing_dir) and (args.timing_60 or args.timing_120):
        raise ReleaseBuildError("--timing-dir cannot be combined with --timing-60/--timing-120")
    if not args.timing_dir and bool(args.timing_60) != bool(args.timing_120):
        raise ReleaseBuildError("--timing-60 and --timing-120 must be supplied together")
    if not args.timing_dir and not args.timing_60:
        raise ReleaseBuildError("v1.2.0 builds require --timing-dir or both --timing-60 and --timing-120")
    if args.timing_dir:
        flat_timing = validate_flat_timing_directory(args.timing_dir, runtime_root)
        timing_60 = timing_120 = None
    else:
        flat_timing = None
        timing_60 = validate_timing_directory(60, args.timing_60)
        timing_120 = validate_timing_directory(120, args.timing_120)

    return {
        "runtime_root": runtime_root,
        "audio_module": audio_module,
        "audio_patch": audio_patch,
        "icon": icon,
        "output": output,
        "tools_dir": tools_dir,
        "reports_dir": reports_dir,
        "timing_dir": flat_timing,
        "timing_60": timing_60,
        "timing_120": timing_120,
        **overrides,
    }


def compiler_command(source_args: list[Path], output: Path, frameworks: tuple[str, ...]) -> list[str]:
    command = ["xcrun", "swiftc", "-O", "-target", "arm64-apple-macos15.0", "-swift-version", "5"]
    for framework in frameworks:
        command.extend(("-framework", framework))
    command.extend(str(source) for source in source_args)
    command.extend(("-o", str(output)))
    return command


def compile_tools(paths: dict[str, Path]) -> dict[str, Path]:
    tools_dir = paths["tools_dir"]
    tools_dir.mkdir(parents=True, exist_ok=True)
    launcher = paths["launcher"] or tools_dir / "RA3Launcher"
    display_helper = paths["display_helper"] or tools_dir / "display-mode"
    display_plan = paths["display_plan"] or tools_dir / "display-plan"
    commands = []
    if paths["launcher"] is None:
        commands.append(compiler_command([MACOS / "Launcher.swift"], launcher, ("AppKit", "CryptoKit")))
    if paths["display_helper"] is None:
        commands.append(compiler_command([REPO / "research/compat/display_mode.swift"],
                                         display_helper, ("CoreGraphics",)))
    if paths["display_plan"] is None:
        commands.append(compiler_command([
            REPO / "research/compat/adaptive-viewport/DisplayPlan.swift",
            REPO / "research/compat/adaptive-viewport/display_plan_cli.swift",
        ], display_plan, ("AppKit", "CoreGraphics")))
    for command in commands:
        run_command(command, "Swift compilation")
    return {"launcher": launcher, "display_helper": display_helper, "display_plan": display_plan}


def run_command(command: list[str], label: str, *, cwd: Path = REPO) -> subprocess.CompletedProcess:
    try:
        result = subprocess.run(command, cwd=cwd, capture_output=True, text=True, check=False)
    except OSError as error:
        raise ReleaseBuildError(f"{label} could not start: {error}") from error
    if result.returncode:
        detail = (result.stdout or "") + (result.stderr or "")
        raise ReleaseBuildError(f"{label} failed with exit code {result.returncode}:\n{detail.strip()}")
    if result.stdout:
        print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
    if result.stderr:
        print(result.stderr, file=sys.stderr, end="" if result.stderr.endswith("\n") else "\n")
    return result


def choose_sign_report(reports_dir: Path, output: Path) -> Path:
    base = reports_dir / f"sign-{output.stem}.json"
    candidate = base
    suffix = 2
    while os.path.lexists(candidate):
        candidate = base.with_name(f"{base.stem}-{suffix}{base.suffix}")
        suffix += 1
    return candidate


def build_and_sign(args) -> tuple[Path, Path]:
    # All user inputs and the absent output are checked before xcrun can run.
    paths = validate_inputs(args)
    tools = compile_tools(paths)
    if os.path.lexists(paths["output"]):
        raise ReleaseBuildError(f"--output appeared during compilation; refusing to overwrite: {paths['output']}")

    bundle_id = ADAPTIVE_BUNDLE_ID if args.presentation == "safe-area" else FIXED_BUNDLE_ID
    build_args = [
        "--output", str(paths["output"]),
        "--icon", str(paths["icon"]),
        "--runtime-root", str(paths["runtime_root"]),
        "--launcher", str(tools["launcher"]),
        "--display-helper", str(tools["display_helper"]),
        "--audio-module", str(paths["audio_module"]),
        "--audio-patch", str(paths["audio_patch"]),
        "--bundle-id", bundle_id,
        "--presentation", args.presentation,
        "--display-plan", str(tools["display_plan"]),
    ]
    if paths["timing_dir"]:
        build_args.extend(("--timing-dir", str(paths["timing_dir"])))
    else:
        build_args.extend(("--timing-60", str(paths["timing_60"]),
                           "--timing-120", str(paths["timing_120"])))
    if args.runtime_only:
        build_args.append("--runtime-only")
    builder = [sys.executable, "-I", "-c", BUILD_APP_RUNNER,
               str(MACOS / "build_app.py"), str(paths["reports_dir"]), *build_args]
    run_command(builder, "App bundle build")
    if not paths["output"].is_dir():
        raise ReleaseBuildError("builder reported success but the .app output is missing")

    paths["reports_dir"].mkdir(parents=True, exist_ok=True)
    sign_report = choose_sign_report(paths["reports_dir"], paths["output"])
    signer = [sys.executable, "-I", str(MACOS / "sign_app.py"), str(paths["output"]),
              "--identity", args.identity, "--report", str(sign_report)]
    if args.public:
        signer.append("--public")
    run_command(signer, "App signing and verification")
    if not sign_report.is_file():
        raise ReleaseBuildError("signer reported success but the signing report is missing")
    return paths["output"], sign_report


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build and ad-hoc sign a local app from user-prepared RA3 Uprising inputs."
    )
    parser.add_argument("--runtime-root", required=True, type=Path,
                        help="prepared matching root containing wine/, prefix/, and candidate-manifest.json")
    parser.add_argument("--audio-module", required=True, type=Path,
                        help="user-prepared patched winecoreaudio.so with RA3_NO_AUDIO_CAPTURE")
    parser.add_argument("--icon", required=True, type=Path, help="user-provided AppIcon.icns")
    parser.add_argument("--output", required=True, type=Path, help="absent destination ending in .app")
    parser.add_argument("--presentation", choices=("safe-area", "fixed"), default="safe-area",
                        help="safe-area candidate by default, or fixed viewport")
    parser.add_argument("--identity", default="-", help="codesign identity; default - signs ad hoc")
    parser.add_argument("--public", action="store_true",
                        help="write neutral public signature wording; requires ad-hoc identity")
    parser.add_argument("--runtime-only", action="store_true",
                        help="omit game data from the output app prefix template for Runtime packaging")
    parser.add_argument("--timing-60", type=Path,
                        help="60 FPS artifact dir with uprising120.dll and build-result.json")
    parser.add_argument("--timing-120", type=Path,
                        help="120 FPS artifact dir with uprising120.dll and build-result.json")
    parser.add_argument("--timing-dir", type=Path,
                        help="flat Runtime timing folder containing Uprising60.exe, fps60.dll and fps120.dll")
    parser.add_argument("--tools-dir", type=Path, default=BUILD / "release-tools",
                        help="directory for locally compiled native helper binaries")
    parser.add_argument("--reports-dir", type=Path, default=BUILD / "release-reports",
                        help="directory for build and signing reports")
    parser.add_argument("--launcher", type=Path, help="use an existing compiled launcher instead of compiling it")
    parser.add_argument("--display-helper", type=Path,
                        help="use an existing display-mode helper instead of compiling it")
    parser.add_argument("--display-plan", type=Path,
                        help="use an existing display-plan helper instead of compiling it")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = make_parser().parse_args(argv)
    try:
        output, sign_report = build_and_sign(args)
    except ReleaseBuildError as error:
        print(f"RELEASE BUILD ABORTED: {error}", file=sys.stderr)
        return 2
    print(f"Built and signed app: {output}")
    print(f"Signing report: {sign_report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
