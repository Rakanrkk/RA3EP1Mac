"""Offline tests for release_build.py using tiny files and mocked commands."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import release_build as R  # noqa: E402


def digest(data):
    return hashlib.sha256(data).hexdigest()


def pe32(payload: bytes) -> bytes:
    return b"MZ" + b"\0" * 0x3A + (0x40).to_bytes(4, "little") + b"PE\0\0" + (0x014C).to_bytes(2, "little") + b"\1\0" + payload


def make_timing(base: Path, rate: int) -> Path:
    directory = base / f"timing-{rate}"
    directory.mkdir(exist_ok=True)
    data = pe32(f"TIMING-{rate}".encode())
    (directory / "uprising120.dll").write_bytes(data)
    (directory / "build-result.json").write_text(json.dumps({
        "target_updates_hz": rate, "dll_sha256": digest(data), "game_started": False}))
    return directory


def make_inputs(base: Path):
    runtime = base / "runtime"
    wine = runtime / "wine"
    prefix = runtime / "prefix"
    for path in (wine / "bin", prefix / "drive_c/ra3-mtld3d/Data",
                 prefix / "drive_c/u60-mtld3d"):
        path.mkdir(parents=True)
    wine_bytes = b"WINE-FIXTURE"
    game_bytes = b"GAME-FIXTURE"
    payload_bytes = pe32(b"PAYLOAD-FIXTURE")
    for relative, data in (("bin/wine", wine_bytes), ("bin/wineserver", b"SERVER-FIXTURE"),
                           ("lib/runtime.dylib", b"RUNTIME-FIXTURE")):
        target = wine / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    for name in ("wine", "wineserver"):
        (wine / "bin" / name).chmod(0o755)
    (prefix / "drive_c/ra3-mtld3d/Data/game.bin").write_bytes(game_bytes)
    (prefix / "drive_c/u60-mtld3d/Uprising60.exe").write_bytes(payload_bytes)
    manifest = {
        "runtime_pins": {"bin/wine": digest(wine_bytes), "lib/runtime.dylib": digest(b"RUNTIME-FIXTURE")},
        "game_pins": {"Data/game.bin": digest(game_bytes)},
        "payload_pins": {"Uprising60.exe": digest(payload_bytes)},
    }
    (runtime / "candidate-manifest.json").write_text(json.dumps(manifest))
    audio = base / "patched-audio.so"
    audio.write_bytes(next(iter(R.MACHO_MAGICS)) + b"RA3_NO_AUDIO_CAPTURE-PATCHED")
    icon = base / "provided.icns"
    icon.write_bytes(b"USER-ICON")
    overrides = {}
    for name, contents in (("launcher", b"LAUNCHER"), ("display-mode", b"DISPLAY-HELPER"),
                           ("display-plan", next(iter(R.MACHO_MAGICS)) + b"PLAN")):
        path = base / name
        path.write_bytes(contents)
        path.chmod(0o755)
        overrides[name] = path
    timing = {rate: make_timing(base, rate) for rate in (60, 120)}
    R.TIMING_SHA256.update({rate: R.file_digest(path / "uprising120.dll")
                            for rate, path in timing.items()})
    flat_timing = base / "flat-timing"
    flat_timing.mkdir()
    (flat_timing / "Uprising60.exe").write_bytes(payload_bytes)
    for rate, path in timing.items():
        shutil.copy2(path / "uprising120.dll", flat_timing / f"fps{rate}.dll")
    return runtime, audio, icon, overrides, timing, flat_timing


def make_args(base, runtime, audio, icon, *, presentation="safe-area", overrides=None,
              output=None, tools=None, reports=None, timing=None, timing_dir=None,
              runtime_only=False, public=False):
    arguments = ["--runtime-root", str(runtime), "--audio-module", str(audio),
                 "--icon", str(icon), "--output", str(output or base / "Result.app"),
                 "--presentation", presentation]
    if timing_dir is not None:
        arguments.extend(("--timing-dir", str(timing_dir.resolve())))
    else:
        arguments.extend(("--timing-60", str(timing[60].resolve()),
                          "--timing-120", str(timing[120].resolve())))
    arguments.extend(("--tools-dir", str(tools or base / "tools"),
                      "--reports-dir", str(reports or base / "reports")))
    if runtime_only:
        arguments.append("--runtime-only")
    if public:
        arguments.append("--public")
    if overrides:
        for name, option in (("launcher", "--launcher"), ("display-mode", "--display-helper"),
                             ("display-plan", "--display-plan")):
            if name in overrides:
                arguments.extend((option, str(overrides[name])))
    return R.make_parser().parse_args(arguments)


def test_runtime_preflight_checks_complete_pins_and_rejects_escape(base, runtime):
    assert R.validate_runtime_root(runtime, runtime_only=False) == runtime.resolve()
    manifest_path = runtime / "candidate-manifest.json"
    original = manifest_path.read_bytes()
    try:
        manifest = json.loads(original)
        manifest["runtime_pins"]["../../outside"] = "0" * 64
        manifest_path.write_text(json.dumps(manifest))
        try:
            R.validate_runtime_root(runtime, runtime_only=False)
        except R.ReleaseBuildError as error:
            assert "invalid runtime_pins" in str(error)
        else:
            raise AssertionError("manifest traversal pin should be refused")

        manifest = json.loads(original)
        manifest["runtime_pins"]["bin/wine"] = "0" * 64
        manifest_path.write_text(json.dumps(manifest))
        try:
            R.validate_runtime_root(runtime, runtime_only=False)
        except R.ReleaseBuildError as error:
            assert "pin does not match" in str(error)
        else:
            raise AssertionError("stale manifest pin should be refused")
    finally:
        manifest_path.write_bytes(original)


def test_flat_timing_directory_validates_three_files(base, runtime, flat_timing):
    assert R.validate_flat_timing_directory(flat_timing, runtime) == flat_timing.resolve()
    (flat_timing / "fps120.dll").write_bytes(b"truncated")
    try:
        R.validate_flat_timing_directory(flat_timing, runtime)
    except R.ReleaseBuildError as error:
        assert "not a PE image" in str(error)
    else:
        raise AssertionError("truncated flat timing DLL should be refused")


def test_missing_inputs_and_existing_output_abort_before_subprocess(base, runtime, audio, icon, timing):
    calls = []
    args = make_args(base, runtime, audio, icon, timing=timing, output=base / "Already.app")
    args.output.mkdir()
    with mock.patch.object(R.sys, "platform", "darwin"), mock.patch.object(
            R.subprocess, "run", side_effect=lambda *a, **k: calls.append(a)):
        try:
            R.build_and_sign(args)
        except R.ReleaseBuildError as error:
            assert "must not already exist" in str(error)
        else:
            raise AssertionError("existing app output should be refused")
    assert calls == []


def test_invalid_audio_is_refused_before_subprocess(base, runtime, icon, timing):
    bad_audio = base / "original.so"
    bad_audio.write_bytes(next(iter(R.MACHO_MAGICS)) + b"ORIGINAL")
    args = make_args(base, runtime, bad_audio, icon, timing=timing)
    calls = []
    with mock.patch.object(R.sys, "platform", "darwin"), mock.patch.object(
            R.subprocess, "run", side_effect=lambda *a, **k: calls.append(a)):
        try:
            R.build_and_sign(args)
        except R.ReleaseBuildError as error:
            assert "RA3_NO_AUDIO_CAPTURE" in str(error)
        else:
            raise AssertionError("unpatched audio should be refused")
    assert calls == []


def test_safe_area_compiles_and_passes_adaptive_contract(base, runtime, audio, icon, timing):
    args = make_args(base, runtime, audio, icon, timing=timing)
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        if command[0] == "xcrun":
            target = Path(command[command.index("-o") + 1])
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"FAKE-COMPILED-TOOL")
            target.chmod(0o755)
        elif command[2:4] == ["-c", R.BUILD_APP_RUNNER]:
            assert command[1] == "-I"
            assert command[command.index("--output") - 1] == str(args.reports_dir.resolve())
            output = Path(command[command.index("--output") + 1])
            (output / "Contents/Resources").mkdir(parents=True)
        elif Path(command[2]) == R.MACOS / "sign_app.py":
            report = Path(command[command.index("--report") + 1])
            report.parent.mkdir(parents=True, exist_ok=True)
            report.write_text("{}")
        else:
            raise AssertionError(command)
        return subprocess.CompletedProcess(command, 0, "mocked\n", "")

    with mock.patch.object(R.sys, "platform", "darwin"), mock.patch.object(
            R.subprocess, "run", side_effect=fake_run):
        output, report = R.build_and_sign(args)

    compile_calls = [command for command, _ in calls if command[0] == "xcrun"]
    assert len(compile_calls) == 3
    for command in compile_calls:
        assert command[:7] == ["xcrun", "swiftc", "-O", "-target",
                               "arm64-apple-macos15.0", "-swift-version", "5"]
    assert all(str(path) in " ".join(compile_calls[0]) for path in (R.MACOS / "Launcher.swift",))
    assert "-framework" in compile_calls[0] and "AppKit" in compile_calls[0]
    plan_command = next(command for command in compile_calls
                         if any("display_plan_cli.swift" in argument for argument in command))
    assert str(R.REPO / "research/compat/adaptive-viewport/DisplayPlan.swift") in plan_command

    build_command = next(command for command, _ in calls if command[2:4] == ["-c", R.BUILD_APP_RUNNER])
    assert build_command[1] == "-I"
    assert build_command[build_command.index("--bundle-id") + 1] == R.ADAPTIVE_BUNDLE_ID
    assert build_command[build_command.index("--presentation") + 1] == "safe-area"
    assert Path(build_command[build_command.index("--display-plan") + 1]).name == "display-plan"
    assert build_command[build_command.index("--timing-60") + 1] == str(timing[60].resolve())
    assert build_command[build_command.index("--timing-120") + 1] == str(timing[120].resolve())
    assert "--runtime-only" not in build_command
    sign_command = next(command for command, _ in calls if Path(command[2]) == R.MACOS / "sign_app.py")
    assert sign_command[sign_command.index("--identity") + 1] == "-"
    assert sign_command[sign_command.index("--report") + 1] == str(report)
    assert "--public" not in sign_command
    assert output == args.output.resolve() and output.is_dir() and report.is_file()


def test_fixed_uses_override_tools_and_fixed_identity(base, runtime, audio, icon, overrides, timing):
    args = make_args(base, runtime, audio, icon, presentation="fixed", timing=timing, overrides={
        "launcher": overrides["launcher"], "display-mode": overrides["display-mode"],
        "display-plan": overrides["display-plan"],
    }, output=base / "Fixed.app")
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        if command[2:4] == ["-c", R.BUILD_APP_RUNNER]:
            (args.output / "Contents/Resources").mkdir(parents=True)
        elif Path(command[2]) == R.MACOS / "sign_app.py":
            report = Path(command[command.index("--report") + 1])
            report.parent.mkdir(parents=True, exist_ok=True)
            report.write_text("{}")
        else:
            raise AssertionError(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    with mock.patch.object(R.sys, "platform", "darwin"), mock.patch.object(
            R.subprocess, "run", side_effect=fake_run):
        R.build_and_sign(args)
    assert not any(command[0] == "xcrun" for command in calls)
    build_command = next(command for command in calls if command[2:4] == ["-c", R.BUILD_APP_RUNNER])
    assert build_command[build_command.index("--bundle-id") + 1] == R.FIXED_BUNDLE_ID
    assert build_command[build_command.index("--presentation") + 1] == "fixed"
    assert build_command[build_command.index("--launcher") + 1] == str(overrides["launcher"].resolve())
    assert build_command[build_command.index("--display-helper") + 1] == str(overrides["display-mode"].resolve())
    assert build_command[build_command.index("--display-plan") + 1] == str(overrides["display-plan"].resolve())
    assert build_command[build_command.index("--timing-60") + 1] == str(timing[60].resolve())
    assert build_command[build_command.index("--timing-120") + 1] == str(timing[120].resolve())
    assert "--runtime-only" not in build_command


def test_build_runner_overrides_reports_directory(base):
    module = base / "fixture_builder.py"
    module.write_text(
        "from pathlib import Path\n"
        "REPORTS = Path('default-reports')\n"
        "def main(argv):\n"
        "    print(REPORTS)\n"
        "    return 0\n"
    )
    requested = base / "requested-reports"
    result = subprocess.run([sys.executable, "-I", "-c", R.BUILD_APP_RUNNER,
                             str(module), str(requested)], capture_output=True,
                            text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(requested)


def test_help_exits_without_external_commands():
    result = subprocess.run([sys.executable, "-I", str(R.MACOS / "release_build.py"), "--help"],
                            capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    assert "--runtime-root" in result.stdout and "--presentation" in result.stdout
    assert "--timing-60" in result.stdout or "--timing-dir" in result.stdout
    assert "--timing-120" in result.stdout or "--timing-dir" in result.stdout


def main():
    checks = []
    with tempfile.TemporaryDirectory(prefix="ra3-release-test-") as temporary:
        base = Path(temporary)
        runtime, audio, icon, overrides, timing, flat_timing = make_inputs(base)
        checks.append(("runtime preflight", lambda: test_runtime_preflight_checks_complete_pins_and_rejects_escape(base, runtime)))
        checks.append(("flat timing inputs", lambda: test_flat_timing_directory_validates_three_files(base, runtime, flat_timing)))
        checks.append(("output guard", lambda: test_missing_inputs_and_existing_output_abort_before_subprocess(base, runtime, audio, icon, timing)))
        checks.append(("audio guard", lambda: test_invalid_audio_is_refused_before_subprocess(base, runtime, icon, timing)))
        checks.append(("safe-area build", lambda: test_safe_area_compiles_and_passes_adaptive_contract(base, runtime, audio, icon, timing)))
        checks.append(("fixed build", lambda: test_fixed_uses_override_tools_and_fixed_identity(base, runtime, audio, icon, overrides, timing)))
        checks.append(("build report override", lambda: test_build_runner_overrides_reports_directory(base)))
        checks.append(("help", test_help_exits_without_external_commands))
        for label, check in checks:
            check()
            print("PASS:", label)
    print(f"ALL SELF-TESTS PASSED ({len(checks)} checks)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
