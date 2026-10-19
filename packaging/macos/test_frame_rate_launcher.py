"""Fixture tests for the v1.2 four-frame-rate launcher.

Covers, without any UI, Wine or real installed app data:
  - CLI dispatch of the new flags (--fps, --settings-only, combined/bad args).
  - Strict all-or-nothing manifest/profile validation via --verify-only.
  - Resource upgrade contract on a mock user prefix: known previous hash is
    APFS-clone backed up, then atomically replaced; idempotent re-run; unknown
    hash and symlink attacks fail while preserving the existing file; the
    ready marker keeps data_version 1.0; saves and options are untouched.
  - Legacy manifests (no v1.2 fields) still validate unchanged.

Each run uses a uniquely named Application Support directory which is removed
afterwards. The fixture app bundle ids are deliberately NOT in the launcher
allowlist, so GUI/--fps paths stop at the bundle guard (exit 2) and never
create a window or touch the real game data.
"""
import copy
import hashlib
import json
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import tempfile
import uuid

OLD = b"OLD-STATIC-STREAM-FIXTURE"
NEW = b"NEW-STATIC-STREAM-FIXTURE"
UNKNOWN = b"UNKNOWN-STATIC-STREAM-FIXTURE"
TIMING_FILES = ("Uprising60.exe", "fps60.dll", "fps120.dll")


def digest_bytes(data):
    return hashlib.sha256(data).hexdigest()


def digest(path):
    return digest_bytes(Path(path).read_bytes())


def write_manifest(resources, manifest):
    (resources / "manifest.json").write_text(json.dumps(manifest))


def build_fixture_app(base, binary, identifier, version="1.2", data_version="1.0", four_rate=True):
    app = base / "Fixture.app"
    resources = app / "Contents/Resources"
    macos = app / "Contents/MacOS"
    macos.mkdir(parents=True)
    shutil.copy2(binary, macos / "RA3Launcher")
    template = resources / "prefix-template"
    files = {
        "wine/bin/wine": b"#!/bin/sh\nexit 99\n",
        "wine/bin/wineserver": b"#!/bin/sh\nexit 99\n",
        "display-mode": b"#!/bin/sh\nexit 99\n",
        "prefix-template/drive_c/ra3-mtld3d/Data/game.bin": b"GAME-FIXTURE",
        "prefix-template/drive_c/ra3-mtld3d/Data/StaticStream.big": NEW,
        "prefix-template/drive_c/u60-mtld3d/Uprising60.exe": b"PAYLOAD-EXE",
        "prefix-template/drive_c/u60-mtld3d/uprising60.dll": b"PAYLOAD-DLL",
    }
    for name in TIMING_FILES:
        files["timing/" + name] = b"TIMING-FIXTURE-" + name.encode()
    for relative, data in files.items():
        target = resources / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        if relative in ("wine/bin/wine", "wine/bin/wineserver", "display-mode"):
            target.chmod(0o755)
    (template / "dosdevices").mkdir()
    (template / "dosdevices/c:").symlink_to("../drive_c")
    manifest = {
        "schema": "ra3mac-app-v1",
        "version": version,
        "runtime_pins": {"bin/wine": digest(resources / "wine/bin/wine")},
        "game_pins": {"Data/game.bin": digest(template / "drive_c/ra3-mtld3d/Data/game.bin"),
                      "Data/StaticStream.big": digest_bytes(NEW)},
        "payload_pins": {"Uprising60.exe": digest(template / "drive_c/u60-mtld3d/Uprising60.exe"),
                         "uprising60.dll": digest(template / "drive_c/u60-mtld3d/uprising60.dll")},
    }
    if data_version is not None:
        manifest["data_version"] = data_version
    if four_rate:
        manifest["timing_pins"] = {name: digest(resources / "timing" / name) for name in TIMING_FILES}
        manifest["frame_rate_profiles"] = {str(rate): {"dll": f"fps{rate}.dll", "log": "uprising120.log"}
                                           for rate in (60, 120)}
        manifest["default_frame_rate"] = 60
        manifest["resource_updates"] = {"Data/StaticStream.big": {
            "previous_sha256": [digest_bytes(OLD)], "sha256": digest_bytes(NEW)}}
    write_manifest(resources, manifest)
    plistlib.dump({"CFBundleIdentifier": identifier,
                   "CFBundleExecutable": "RA3Launcher", "CFBundlePackageType": "APPL"},
                  (app / "Contents/Info.plist").open("wb"))
    return app, resources, manifest


def build_old_prefix(support):
    prefix = support / "prefix"
    data_dir = prefix / "drive_c/ra3-mtld3d/Data"
    data_dir.mkdir(parents=True)
    stream = data_dir / "StaticStream.big"
    stream.write_bytes(OLD)
    profiles = prefix / "drive_c/users/player/AppData/Roaming/Red Alert 3 Uprising/Profiles/1"
    profiles.mkdir(parents=True)
    (profiles / "Options.ini").write_text("Quality = Preserved\nResolution = 800 600\n")
    (prefix / "drive_c/users/player/Saved Games").mkdir(parents=True)
    (prefix / "drive_c/users/player/Saved Games/ra3.save").write_bytes(b"SAVE-1")
    (prefix / ".ra3mac-ready.json").write_text(json.dumps({"version": "1.0", "created": "fixture"}))
    (prefix / "dosdevices").mkdir()
    (prefix / "dosdevices/z:").symlink_to("/")
    return prefix, stream


def run(launcher, arguments, code, message):
    result = subprocess.run([str(launcher), *arguments], capture_output=True, text=True, timeout=20)
    assert result.returncode == code, (arguments, result.returncode, result.stdout, result.stderr)
    assert message in result.stdout + result.stderr, (arguments, result.stdout, result.stderr)


def backup_dirs(support):
    return sorted((support / "Backups").glob("resources-v1.2-*")) if (support / "Backups").exists() else []


def real_game_running():
    probe = subprocess.run(["/bin/ps", "-axww", "-o", "comm="], capture_output=True, text=True)
    return "ra3ep1_1.0.game" in probe.stdout.lower()


def main():
    binary = Path(sys.argv[1]).resolve()
    checks = 0

    cli_cases = [
        (["--help"], 0, "Usage: RA3Launcher"),
        (["-h"], 0, "Usage: RA3Launcher"),
        (["--unknown"], 2, "启动参数无效"),
        (["--verify-only", "--prepare-only"], 2, "启动参数无效"),
        ([], 2, "请从应用程序打开"),
        (["--verify-only"], 1, "验证失败"),
        (["--fps"], 2, "启动参数无效"),
        (["--fps", "45"], 2, "启动参数无效"),
        (["--fps", "030"], 2, "启动参数无效"),
        (["--fps", "30", "--prepare-only"], 2, "启动参数无效"),
        (["--fps", "30"], 2, "启动参数无效"),
        (["--fps", "120"], 2, "请从应用程序打开"),
        (["--settings-only", "--fps", "60"], 2, "启动参数无效"),
    ]
    for arguments, expected, message in cli_cases:
        run(binary, arguments, expected, message)
        checks += 1

    with tempfile.TemporaryDirectory(prefix="ra3-frame-rate-") as temporary:
        base = Path(temporary)
        identifier = "org.ra3mac.FrameRateFixture." + uuid.uuid4().hex
        support = Path.home() / "Library/Application Support" / identifier
        assert not support.exists()
        app, resources, manifest = build_fixture_app(base, binary, identifier)
        launcher = app / "Contents/MacOS/RA3Launcher"
        aux_identifiers = []

        def aux_fixture(*args, **kwargs):
            aux = "org.ra3mac.FrameRateFixture." + uuid.uuid4().hex
            aux_identifiers.append(aux)
            return build_fixture_app(*args, identifier=aux, **kwargs)

        try:
            run(launcher, ["--verify-only"], 0, "PASS: standalone app resources")
            checks += 1

            invalid = [
                ("missing profiles", lambda m: m.pop("frame_rate_profiles")),
                ("wrong dll name", lambda m: m["frame_rate_profiles"].__setitem__(
                    "60", {"dll": "fps35.dll", "log": "uprising120.log"})),
                ("wrong log name", lambda m: m["frame_rate_profiles"].__setitem__(
                    "120", {"dll": "fps120.dll", "log": "uprising60.log"})),
                ("missing profile key", lambda m: m["frame_rate_profiles"].pop("120")),
                ("bad timing pin", lambda m: m["timing_pins"].__setitem__("fps60.dll", "0" * 64)),
                ("relative timing pin", lambda m: m["timing_pins"].__setitem__("../fps30.dll", "0" * 64)),
                ("bad default", lambda m: m.__setitem__("default_frame_rate", 45)),
                ("missing default", lambda m: m.pop("default_frame_rate")),
                ("foreign resource update", lambda m: m["resource_updates"].__setitem__(
                    "Data/Other.big", {"previous_sha256": [digest_bytes(OLD)], "sha256": digest_bytes(NEW)})),
                ("empty previous list", lambda m: m["resource_updates"].__setitem__(
                    "Data/StaticStream.big", {"previous_sha256": [], "sha256": digest_bytes(NEW)})),
            ]
            for label, mutate in invalid:
                broken = copy.deepcopy(manifest)
                mutate(broken)
                write_manifest(resources, broken)
                run(launcher, ["--verify-only"], 1, "验证失败")
                checks += 1
            write_manifest(resources, manifest)

            prefix, stream = build_old_prefix(support)
            options = prefix / "drive_c/users/player/AppData/Roaming/Red Alert 3 Uprising/Profiles/1/Options.ini"
            save = prefix / "drive_c/users/player/Saved Games/ra3.save"
            data_dir = stream.parent
            run(launcher, ["--verify-only"], 0, "PASS")
            assert stream.read_bytes() == OLD and not backup_dirs(support)
            checks += 2

            # Real gameRunning guard: a live process named like the game must
            # block the upgrade before anything is written, no matter what.
            # Copied system binaries are killed on exec here, so compile a
            # tiny named blocker instead (same toolchain requirement as the
            # launcher itself).
            fake_game = base / "ra3ep1_1.0.game"
            source = base / "blocker.c"
            source.write_text("#include <unistd.h>\nint main(void){ sleep(30); return 0; }\n")
            subprocess.run(["cc", "-o", str(fake_game), str(source)], check=True, timeout=60)
            blocker = subprocess.Popen([str(fake_game)], stdout=subprocess.DEVNULL)
            try:
                run(launcher, ["--prepare-only"], 1, "正在运行")
                assert stream.read_bytes() == OLD and not backup_dirs(support)
            finally:
                blocker.kill()
                blocker.wait()
            checks += 2

            # The resource-upgrade guard is machine-global by contract: while a
            # real RA3 process is active these end-to-end cases are covered by
            # test_frame_upgrade_driver (injected probe) instead of skipped logic.
            if real_game_running():
                print("SKIP: real RA3 process active; resource upgrade transactions "
                      "are verified by test_frame_upgrade_driver")
                shutil.rmtree(prefix)
            else:
                run(launcher, ["--prepare-only"], 0, "Prepared independent game data")
                assert stream.read_bytes() == NEW
                assert not list(data_dir.glob(".ra3-resource-*"))
                assert json.loads((prefix / ".ra3mac-ready.json").read_text())["version"] == "1.0"
                assert options.read_text() == "Quality = Preserved\nResolution = 800 600\n"
                assert save.read_bytes() == b"SAVE-1"
                backups = backup_dirs(support)
                assert len(backups) == 1, backups
                assert (backups[0] / "Data/StaticStream.big").read_bytes() == OLD
                metadata = json.loads((backups[0] / "metadata.json").read_text())
                assert metadata["relative"] == "Data/StaticStream.big"
                assert metadata["previous_sha256"] == digest_bytes(OLD)
                assert metadata["sha256"] == digest_bytes(NEW)
                checks += 8

                run(launcher, ["--prepare-only"], 0, "Prepared independent game data")
                assert len(backup_dirs(support)) == 1
                checks += 1

                stream.write_bytes(UNKNOWN)
                run(launcher, ["--prepare-only"], 1, "游戏资源版本未知")
                assert stream.read_bytes() == UNKNOWN and len(backup_dirs(support)) == 1
                checks += 1

                outside = base / "outside-OLD.big"
                outside.write_bytes(OLD)
                stream.unlink()
                stream.symlink_to(outside)
                run(launcher, ["--prepare-only"], 1, "已保留")
                assert stream.is_symlink() and outside.read_bytes() == OLD
                assert len(backup_dirs(support)) == 1
                checks += 1

                shutil.rmtree(prefix)
                run(launcher, ["--prepare-only"], 0, "Prepared independent game data")
                assert stream.read_bytes() == NEW
                assert json.loads((prefix / ".ra3mac-ready.json").read_text())["version"] == "1.0"
                assert len(backup_dirs(support)) == 1
                checks += 1

            legacy, _, _ = aux_fixture(base / "legacy", binary, version="test",
                                       data_version=None, four_rate=False)
            run(legacy / "Contents/MacOS/RA3Launcher", ["--verify-only"], 0, "PASS")
            checks += 1
            data_only, _, _ = aux_fixture(base / "dataonly", binary, four_rate=False)
            run(data_only / "Contents/MacOS/RA3Launcher", ["--verify-only"], 0, "PASS")
            checks += 1
        finally:
            for owner in [identifier, *aux_identifiers]:
                owner_support = Path.home() / "Library/Application Support" / owner
                if owner_support.exists():
                    assert owner.startswith("org.ra3mac.FrameRateFixture.")
                    shutil.rmtree(owner_support)

    print(f"PASS: {checks} two-rate launcher checks; CLI dispatch, strict manifest validation, "
          "resource upgrade backup/atomic replace and fail-preserve all fixture-only")


if __name__ == "__main__":
    main()
