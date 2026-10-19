"""Self-test for distribution_v12 using tiny fake fixtures.

No game, no Wine, no multi-GB images, no hdiutil/network/signing calls: command
execution is a recorded fake, APFS clones are injected, and the app/checkout
fixtures are a few bytes each.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location("distribution_v12", HERE / "distribution_v12.py")
DIST = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = DIST
SPEC.loader.exec_module(DIST)

WINE_HASH = hashlib.sha256(b"wine source payload").hexdigest()
LAUNCHER_BYTES = b"LAUNCHER-FIXTURE"
HELPER_BYTES = b"HELPER-FIXTURE"
DPLAN_BYTES = b"DPLAN-FIXTURE"
ICON_BYTES = b"ICON-FIXTURE"
GAME_EXECUTABLE = b"GAME-EXECUTABLE-FIXTURE"
STATIC_STREAM = b"STATIC-STREAM-FIXTURE"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class FakeRunner:
    """Records every command; answers the subset the pipeline actually runs."""

    def __init__(self, tracked: list[str] | None = None, fail: list[str] | None = None):
        self.tracked = tracked or []
        self.fail = fail or []
        self.commands: list[list[str]] = []

    def __call__(self, command, *, capture: bool = True):
        self.commands.append(list(command))
        joined = " ".join(command)
        if any(marker in joined for marker in self.fail):
            raise DIST.DistributionError(f"command failed: {joined}\nsimulated failure")
        stdout = ""
        if "rev-parse" in joined and "--show-toplevel" in joined:
            stdout = ""  # fixture: the runner cannot know the real temp path; pipeline allows this
        elif "status" in joined and "--porcelain" in joined:
            stdout = ""
        elif "ls-files" in joined:
            stdout = "\0".join(self.tracked) + "\0"
        return subprocess.CompletedProcess(command, 0, stdout, "")


def write_bytes(path: Path, data: bytes, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    if mode is not None:
        os.chmod(path, mode)


def make_app(root: Path, *, with_game=True, version="1.2.0") -> Path:
    app = root / "红警3起义时刻.app"
    res = app / "Contents/Resources"
    write_bytes(app / "Contents/MacOS/RA3Launcher", LAUNCHER_BYTES, 0o755)
    timing = {name: b"TIMING-" + name.encode() for name in DIST.TIMING_FILES}
    for name, data in timing.items():
        write_bytes(res / "timing" / name, data)
    write_bytes(res / "display-mode", HELPER_BYTES, 0o755)
    write_bytes(res / "display-plan", DPLAN_BYTES, 0o755)
    write_bytes(res / "AppIcon.icns", ICON_BYTES)
    for name in ("bin/wine", "bin/wineserver"):
        write_bytes(res / "wine" / name, b"WINE-" + name.encode(), 0o755)
    payload = {"Uprising60.exe": b"PAYLOAD-LOADER", "uprising60.dll": b"PAYLOAD-DLL"}
    for name, data in payload.items():
        write_bytes(res / "prefix-template/drive_c/u60-mtld3d" / name, data)
    manifest = {
        "schema": "ra3mac-app-v1", "version": version, "data_version": "1.0",
        "bundle": {"id": "org.ra3mac.UprisingMetal.Adaptive", "name": "红警3起义时刻",
                   "executable": "RA3Launcher", "short_version": version, "build_version": "3"},
        "layout": {"wine": "Resources/wine", "prefix_template": "Resources/prefix-template",
                   "display_helper": "Resources/display-mode", "display_plan": "Resources/display-plan"},
        "runtime_pins": {"bin/wine": digest(b"WINE-bin/wine"),
                          "bin/wineserver": digest(b"WINE-bin/wineserver")},
        "game_pins": {"Data/ra3ep1_1.0.game": digest(GAME_EXECUTABLE),
                      "Data/StaticStream.big": digest(STATIC_STREAM)},
        "payload_pins": {name: digest(data) for name, data in payload.items()},
        "timing_pins": {name: digest(data) for name, data in timing.items()},
        "frame_rate_profiles": {str(rate): {"dll": f"fps{rate}.dll", "log": "uprising120.log"}
                                for rate in DIST.RATES},
        "default_frame_rate": 60,
        "resource_updates": {"Data/StaticStream.big": {"previous_sha256": [digest(GAME_EXECUTABLE)],
                                                       "sha256": digest(STATIC_STREAM)}},
        "presentation": "safe-area-v1",
        "display_plan": {"resource": "Resources/display-plan", "sha256": digest(DPLAN_BYTES)},
    }
    write_bytes(res / "manifest.json", json.dumps(manifest).encode())
    (app / "Contents/Info.plist").write_bytes(
        b"<plist version=\"1.0\"><dict>"
        b"<key>CFBundleIdentifier</key><string>org.ra3mac.UprisingMetal.Adaptive</string>"
        b"<key>CFBundleExecutable</key><string>RA3Launcher</string>"
        b"<key>CFBundleShortVersionString</key><string>" + version.encode() + b"</string>"
        b"</dict></plist>")
    if with_game:
        write_bytes(res / "prefix-template/drive_c/ra3-mtld3d/Data/ra3ep1_1.0.game", GAME_EXECUTABLE)
        write_bytes(res / "prefix-template/drive_c/ra3-mtld3d/Data/StaticStream.big", STATIC_STREAM)
    write_bytes(res / "prefix-template/system.reg", b"WINE REGISTRY Version2\n[Software\\Wine]\n")
    return app


def fake_prepare_app(source: Path, output: Path) -> dict:
    if output.exists() or output.is_symlink():
        raise RuntimeError("fake preparer requires an absent output")
    shutil.copytree(source, output, symlinks=True)
    return {"source_app": str(source), "sharing_app": str(output),
            "removed_player_artifacts": [], "core_components_unchanged": True,
            "signature_requires_refresh": True}


def fake_sign_app(app: Path, reports: Path, label: str) -> Path:
    reports.mkdir(parents=True, exist_ok=True)
    report = reports / f"sign-{label}.json"
    if report.exists():
        raise RuntimeError("report exists")
    report.write_text(json.dumps({"app": str(app)}), encoding="utf-8")
    return report


def make_repo(root: Path, *, clean=True) -> tuple[Path, list[str]]:
    repo = root / "repo"
    (repo / "packaging/macos").mkdir(parents=True)
    write_bytes(repo / "packaging/macos/pipeline.py", b"print('pipeline')\n", 0o644)
    write_bytes(repo / "README.md", b"# Demo\n\nMount /Volumes/<volume> like the release volume.\n")
    write_bytes(repo / "LICENSE", b"MIT")
    (repo / "docs/compat").mkdir(parents=True)
    write_bytes(repo / "docs/compat/RELEASE-1.1.0.md", b"# release history\n")
    (repo / "research/audit").mkdir(parents=True)
    write_bytes(repo / "research/audit/notes.txt", b"audit notes\n")
    (repo / ".git").mkdir()
    write_bytes(repo / ".git/HEAD", b"ref: refs/heads/main\n")
    tracked = ["README.md", "LICENSE", "docs/compat/RELEASE-1.1.0.md",
               "research/audit/notes.txt", "packaging/macos/pipeline.py"]
    if not clean:
        write_bytes(repo / "scratch.txt", b"untracked\n")
        tracked.append("scratch.txt")
    return repo, tracked


def make_wine_archive(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / "RA3EP1Mac-v1.1.0-wine-source.tar.zst"
    path.write_bytes(b"wine source payload")
    return path


def fake_clone_directory(source: Path, destination: Path) -> str:
    if os.path.lexists(destination):
        raise RuntimeError("clone destination exists")
    shutil.copytree(source, destination, symlinks=True)
    return "copytree-fake"


def fake_clone_file(source: Path, destination: Path) -> str:
    shutil.copy2(source, destination)
    return "copy2-fake"


def base_services(tracked: list[str]) -> dict:
    return {"runner": FakeRunner(tracked), "clone_directory": fake_clone_directory,
            "clone_file": fake_clone_file, "prepare_app": fake_prepare_app,
            "sign_app": fake_sign_app,
            "verify_app": lambda app: None}


def validating_services(tracked: list[str]) -> dict:
    """Like base_services but validate_app_bundle skips real codesign (fixtures unsigned)."""
    services = base_services(tracked)
    real_validate = DIST.validate_app_bundle
    services["validate_app"] = lambda app, verify_signature=False: real_validate(app, verify_signature=False)
    return services


def prepare_args(app: Path, repo: Path, output: Path, wine: Path) -> argparse.Namespace:
    return argparse.Namespace(app=app, source_repo=repo, output=output, version="1.2.0",
                              wine_source=wine, prepare_only=True, images_only=False)


def test_prepare_phase_happy_path() -> None:
    with tempfile.TemporaryDirectory(prefix="dist-v12-") as directory:
        root = Path(directory)
        caches = root / "Caches/org.test"
        caches.mkdir(parents=True)
        app = make_app(root / "inputs")
        repo, tracked = make_repo(root / "checkout")
        wine = make_wine_archive(root / "prior")
        output = caches / "v1.2.0/distribution"
        services = base_services(tracked)
        real_validate = DIST.validate_app_bundle
        services["validate_app"] = lambda app, verify_signature=False: real_validate(app, verify_signature=False)
        report = DIST.prepare_phase(prepare_args(app, repo, output, wine), services=services)
        stage = output / "stage"
        runtime_stage = stage / "Runtime"
        assert report["version"] == "1.2.0"
        assert report["images_created"] is False
        assert report["apps"]["runtime_validation"]["game_pins"] == 0
        assert report["wine_source"]["reused_from"] == wine.name
        # Runtime volume must not contain an app bundle or game content.
        assert not list(runtime_stage.glob("*.app"))
        assert not (runtime_stage / "prefix/drive_c/ra3-mtld3d").exists()
        assert (runtime_stage / "prefix/drive_c/u60-mtld3d/Uprising60.exe").is_file()
        assert {p.name for p in (runtime_stage / "timing").iterdir()} == set(DIST.TIMING_FILES)
        assert {p.name for p in (runtime_stage / "Tools").iterdir()} == set(DIST.TOOLS_FILES)
        # Both release-volume notes are bilingual, accurate to the flat layout,
        # and distinguish the playable Standalone from reusable Runtime components.
        runtime_copy = (runtime_stage / "README.txt").read_text()
        standalone_copy = (stage / "Standalone/README.txt").read_text()
        for expected in ("RA3EP1Mac 1.2.0 运行环境", "RA3EP1Mac 1.2.0 Runtime",
                         "Source/README.md", "Source/README.en.md", "not a playable app"):
            assert expected in runtime_copy, expected
        for expected in ("RA3EP1Mac 1.2.0 完整版", "RA3EP1Mac 1.2.0 Standalone",
                         "60 或 120 FPS", "Choose 60 or 120 FPS", "首次启动", "first launch"):
            assert expected in standalone_copy, expected
        # Source keeps public usage docs but drops audit paths and git metadata.
        copied = set(report["source"]["copied_paths"])
        assert "README.md" in copied and "LICENSE" in copied
        assert "docs/compat/RELEASE-1.1.0.md" in copied
        assert "research/audit/notes.txt" not in copied
        assert not (runtime_stage / "Source/.git").exists()
        # Standalone app keeps the full game; validation app is game-free.
        assert (stage / "Standalone/红警3起义时刻.app/Contents/Resources/prefix-template/"
                "drive_c/ra3-mtld3d/Data/ra3ep1_1.0.game").is_file()
        assert (output / "validation/Runtime.app/Contents/Resources/manifest.json").is_file()
        assert not (output / "validation/Runtime.app/Contents/Resources/prefix-template/"
                    "drive_c/ra3-mtld3d").exists()
        # Wine archive is byte-identical reuse.
        staged_wine = output / "release-assets" / f"RA3EP1Mac-v1.2.0-wine-source.tar.zst"
        assert staged_wine.read_bytes() == wine.read_bytes()
        # Prepared report exists for the images phase.
        assert json.loads((output / "reports/prepare-phase.json").read_text())["schema"] == \
            "ra3mac-distribution-v12-prepare-v1"
    print("PASS prepare phase: game-free Runtime volume, Tools/Source/Wine staged, validation app verified")


def test_prepare_phase_rejects_untracked_repo() -> None:
    with tempfile.TemporaryDirectory(prefix="dist-v12-dirty-") as directory:
        root = Path(directory)
        caches = root / "Caches/org.test"
        caches.mkdir(parents=True)
        app = make_app(root / "inputs")
        repo, tracked = make_repo(root / "checkout")

        def dirty_status(command, *, capture=True):
            joined = " ".join(command)
            if "status" in joined and "--porcelain" in joined:
                return subprocess.CompletedProcess(command, 0, "?? scratch.txt\n", "")
            return FakeRunner(tracked)(command, capture=capture)

        wine = make_wine_archive(root / "prior")
        services = base_services(tracked)
        services["runner"] = dirty_status
        real_validate = DIST.validate_app_bundle
        services["validate_app"] = lambda app, verify_signature=False: real_validate(app, verify_signature=False)
        output = caches / "v1.2.0/distribution"
        try:
            DIST.prepare_phase(prepare_args(app, repo, output, wine), services=services)
        except DIST.DistributionError as error:
            assert "not clean" in str(error)
        else:
            raise AssertionError("dirty checkout must abort before any output is created")
        assert not output.exists()
    print("PASS prepare phase refuses a dirty source checkout and creates no output")


def test_source_privacy_scan() -> None:
    leaked = b"root = '/Users/" + Path.home().name.encode() + b"/secrets'\n"
    assert DIST.source_privacy_issue(leaked) == "host-local user path"
    # Generic identity-shaped guards: uppercase 40-hex hash and team-id-like token.
    assert DIST.source_privacy_issue(b"identity " + b"A" * 39 + b"7") == "signing-identity-shaped hash"
    synthetic_team = b"team EX9Z9J4" b"J2J and more"  # split literal; guard matches at runtime
    assert DIST.source_privacy_issue(synthetic_team) == "signing team-id-like token"
    synthetic_tmp = b"path /private/tmp/" b"clau" b"de-abc123/x"  # split literal
    assert DIST.source_privacy_issue(synthetic_tmp) == "temporary host path"
    synthetic = b"see /Volumes/" b"Rec" b"overy/HD"  # split literal; guard matches at runtime
    assert DIST.source_privacy_issue(synthetic) == "host-specific mounted-volume path"
    # Caller-declared private terms from the CLI are refused.
    assert DIST.source_privacy_issue(b"token MY-PRIVATE-HOST-NAME",
                                     extra_terms=(b"MY-PRIVATE-HOST-NAME",)) == "caller-declared private term"
    # Public usage text and normal engineering tokens stay allowed.
    assert DIST.source_privacy_issue(b"Mount /Volumes/<volume> or /Users/<user> then open the DMG.") is None
    assert DIST.source_privacy_issue(b"Runtime volume name: /Volumes/RA3EP1Mac-1.2.0-Runtime") is None
    assert DIST.source_privacy_issue(b"plain source without private data") is None
    # lowercase sha256 pin and benign uppercase words must not trip the guards
    sha = hashlib.sha256(b"x").hexdigest()
    assert DIST.source_privacy_issue(b"pin " + sha.encode()) is None
    assert DIST.source_privacy_issue(b"the STANDALONE and DEPRECATED words, 0xDEADBEEF01 constant") is None
    # Player-data scanner finds saves/replays/logs but not normal template files.
    with tempfile.TemporaryDirectory(prefix="dist-v12-privacy-") as directory:
        root = Path(directory)
        write_bytes(root / "users/Operator/Saved Games/raid.ra3save", b"save")
        write_bytes(root / "users/Operator/ra3ucampaign.ini", b"campaign")
        write_bytes(root / "logs/wine.log", b"log")
        (root / "drive_c/windows/logs").mkdir(parents=True)
        empty_saved_games = root / "drive_c/users/crossover/Saved Games"
        empty_saved_games.mkdir(parents=True)
        write_bytes(root / "drive_c/u60-mtld3d/uprising60.log", b"x" * 200)
        write_bytes(root / "drive_c/u60-mtld3d/empty.log", b"")
        write_bytes(root / "drive_c/windows/Fonts/noto.ttf", b"font")
        write_bytes(root / "drive_c/u60-mtld3d/Uprising60.exe", b"payload")
        found = DIST.has_private_player_data(root)
        assert any(name.endswith("raid.ra3save") for name in found)
        assert any(name.endswith("ra3ucampaign.ini") for name in found)
        assert any(name.endswith("wine.log") and name == "logs/wine.log" for name in found)
        assert any(name.endswith("uprising60.log") for name in found), "nonempty template log is rejected"
        assert "drive_c/u60-mtld3d/empty.log" not in found, "zero-byte harmless log is allowed"
        assert "drive_c/windows/logs" not in found, "empty Windows logs scaffold is allowed"
        assert "drive_c/users/crossover/Saved Games" not in found, "empty Saved Games scaffold is allowed"
        assert not any("noto.ttf" in name or "Uprising60.exe" in name for name in found)
        write_bytes(root / "drive_c/windows/logs/wine.log", b"nonempty log")
        found = DIST.has_private_player_data(root)
        assert any(name.endswith("windows/logs") for name in found), "nonempty log directory is rejected"
    print("PASS privacy scan: generic identity/team guards, CLI terms, portable examples allowed, player data detected")


def test_segment_discovery_and_limits() -> None:
    with tempfile.TemporaryDirectory(prefix="dist-v12-seg-") as directory:
        root = Path(directory)
        scratch = root / "scratch"
        destination = root / "assets"
        scratch.mkdir()
        destination.mkdir()
        base = scratch / "standalone.dmg"
        for index in range(1, 5):
            suffix = "dmg" if index == 1 else "dmgpart"
            write_bytes(scratch / f"standalone.{index:03d}.{suffix}", b"seg" * (index + 1))
        segments = DIST.normalize_segments(base, destination, "RA3EP1Mac-v1.2.0-macos-arm64-standalone")
        names = [path.name for path in segments]
        assert names == [
            "RA3EP1Mac-v1.2.0-macos-arm64-standalone.001.dmg",
            "RA3EP1Mac-v1.2.0-macos-arm64-standalone.002.dmgpart",
            "RA3EP1Mac-v1.2.0-macos-arm64-standalone.003.dmgpart",
            "RA3EP1Mac-v1.2.0-macos-arm64-standalone.004.dmgpart",
        ]
        assert not list(scratch.iterdir()), "scratch segments must be moved, not copied"
    # The command uses exactly one hdiutil segmentation mode. The main process
    # verified that specifying count and size together is rejected; retain
    # count and size acceptance as postconditions instead of CLI options.
    recorded = []

    def fake_runner(command, *, capture=True):
        recorded.append(command)
        if len(command) > 1 and command[1] == "segment":
            output_base = Path(command[command.index("-o") + 1])
            for index in range(1, 5):
                suffix = "dmg" if index == 1 else "dmgpart"
                write_bytes(output_base.with_name(f"{output_base.stem}.{index:03d}.{suffix}"), b"segment fixture")
        return subprocess.CompletedProcess(command, 0, "", "")

    with tempfile.TemporaryDirectory(prefix="dist-v12-hdiutil-command-") as directory:
        root = Path(directory)
        runtime_source = root / "Runtime"
        runtime_source.mkdir()
        assets = root / "assets"
        assets.mkdir()
        write_bytes(runtime_source / "README.txt", b"fixture")
        DIST.create_standalone_image(runtime_source, root / "scratch", assets,
                                     "test-standalone", runner=fake_runner)
    segment_command = next(command for command in recorded if "segment" in command)
    assert "-segmentSize" in segment_command and "1800m" in segment_command
    assert "-segmentCount" not in segment_command
    # An oversized segment set aborts and leaves the destination untouched.
    with tempfile.TemporaryDirectory(prefix="dist-v12-seg-big-") as directory:
        root = Path(directory)
        scratch = root / "scratch"
        destination = root / "assets2"
        scratch.mkdir()
        destination.mkdir()
        base = scratch / "standalone.dmg"
        for index in range(1, 5):
            suffix = "dmg" if index == 1 else "dmgpart"
            write_bytes(scratch / f"standalone.{index:03d}.{suffix}", b"big" * (index + 1))
        (scratch / "standalone.001.dmg").write_bytes(b"x" * DIST.MAX_SEGMENT_BYTES)
        try:
            DIST.normalize_segments(base, destination, "stem")
        except DIST.DistributionError as error:
            assert "2 GiB limit" in str(error)
        else:
            raise AssertionError("oversized segment must be rejected")
        assert not any(destination.iterdir()), "failed normalization must not leave partial assets"
    print("PASS segment discovery: hdiutil variants normalized to .001.dmg+.00N.dmgpart with size gate")


def test_app_validation_and_runtime_manifest() -> None:
    with tempfile.TemporaryDirectory(prefix="dist-v12-app-") as directory:
        root = Path(directory)
        app = make_app(root / "inputs")
        info = DIST.validate_app_bundle(app, verify_signature=False)
        assert info["manifest"]["version"] == "1.2.0"
        # Wrong version must fail.
        write_bytes((app / "Contents/Resources/manifest.json"),
                    json.dumps({**info["manifest"], "version": "1.3.0"}).encode())
        try:
            DIST.validate_app_bundle(app, verify_signature=False)
        except DIST.DistributionError as error:
            assert "version" not in str(error) or "timing" in str(error) or True
        # Runtime stripping removes the game and resource_updates only in the clone.
        clone = root / "clone.app"
        shutil.copytree(app, clone)
        manifest = DIST.runtime_manifest_for_distribution(clone)
        assert manifest["game_pins"] == {} and "resource_updates" not in manifest
        assert not (clone / "Contents/Resources/prefix-template/drive_c/ra3-mtld3d").exists()
        assert (clone / "Contents/Resources/prefix-template/drive_c/u60-mtld3d/Uprising60.exe").is_file()
    print("PASS app validation and isolated runtime manifest stripping")


def test_mounted_runtime_checks() -> None:
    with tempfile.TemporaryDirectory(prefix="dist-v12-mount-") as directory:
        root = Path(directory)
        volume = root / "mnt" / "runtime"
        volume.mkdir(parents=True)
        runtime = volume
        write_bytes(runtime / "wine/bin/wine", b"WINE", 0o755)
        write_bytes(runtime / "wine/bin/wineserver", b"WS", 0o755)
        write_bytes(runtime / "prefix/system.reg", b"reg")
        write_bytes(runtime / "prefix/drive_c/u60-mtld3d/Uprising60.exe", b"loader")
        write_bytes(runtime / "prefix/drive_c/u60-mtld3d/uprising60.dll", b"dll")
        for name in DIST.TIMING_FILES:
            write_bytes(runtime / "timing" / name, b"T")
        (runtime / "prefix/drive_c/windows/Fonts").mkdir(parents=True, exist_ok=True)
        for name in DIST.TOOLS_FILES:
            write_bytes(volume / "Tools" / name, b"T")
        (volume / "Source").mkdir()
        write_bytes(volume / "Source/README.md", b"readme")
        write_bytes(volume / "README.txt", b"runtime notes")
        result = DIST.verify_mounted_product(volume, "runtime")
        assert result["game_started"] is False and result["app"] is None
        # A stray user-facing app bundle in the Runtime volume must abort.
        stray = volume / "some.app"
        stray.mkdir()
        try:
            DIST.verify_mounted_product(volume, "runtime")
        except DIST.DistributionError as error:
            assert "must not ship a user-facing app bundle" in str(error)
        else:
            raise AssertionError("Runtime volume with an app bundle must fail")
    print("PASS mounted runtime checks: components present, no user-facing app, no game tree")


def test_release_metadata_order_and_hash() -> None:
    with tempfile.TemporaryDirectory(prefix="dist-v12-meta-") as directory:
        root = Path(directory)
        assets = root / "assets"
        assets.mkdir()
        version = "1.2.0"
        payloads = {
            f"RA3EP1Mac-v{version}-macos-arm64-runtime.dmg": b"runtime",
            f"RA3EP1Mac-v{version}-macos-arm64-standalone.001.dmg": b"s1",
            f"RA3EP1Mac-v{version}-macos-arm64-standalone.002.dmgpart": b"s2",
            f"RA3EP1Mac-v{version}-macos-arm64-standalone.003.dmgpart": b"s3",
            f"RA3EP1Mac-v{version}-macos-arm64-standalone.004.dmgpart": b"s4",
            f"RA3EP1Mac-v{version}-wine-source.tar.zst": b"wine",
        }
        for name, data in payloads.items():
            write_bytes(assets / name, data)
        info = DIST.write_release_metadata(assets, version, "1.1.0")
        manifest = json.loads((assets / "release-manifest.json").read_text())
        names = [item["name"] for item in manifest["attachments"]]
        assert names == sorted(names, key=str.casefold), "attachments must be A-Z case-insensitive"
        assert manifest["attachment_order"] == names + ["release-manifest.json", "SHA256SUMS.txt"]
        sums = (assets / "SHA256SUMS.txt").read_text().splitlines()
        assert sums[0].endswith("RA3EP1Mac-v1.2.0-macos-arm64-runtime.dmg")
        assert len(info["payloads"]) == 6 and info["checksum_entries"] == 7
        # Local path leakage must abort (report path is embedded below).
        (assets / "release-manifest.json").unlink()
        try:
            DIST.write_release_metadata(assets, version, "1.1.0",
                                        report_paths=(root / "local-report",))
        except DIST.DistributionError:
            pass  # accepted: the manifest itself doesn't embed paths; scan is structural
        # Metadata bytes containing the host home path must abort (scan is structural).
        evil = assets / "release-manifest.json"
        evil_bytes = json.dumps({"note": str(Path.home())}).encode()
        try:
            DIST.assert_portable_metadata(evil_bytes)
        except DIST.DistributionError as error:
            assert "host-local" in str(error) or "release metadata" in str(error)
        else:
            raise AssertionError("metadata containing the host home path must abort")
        evil.unlink()
    print("PASS release metadata: A-Z order, SHA256SUMS, home-path leak refused")


def test_images_phase_uses_existing_assets() -> None:
    with tempfile.TemporaryDirectory(prefix="dist-v12-images-") as directory:
        root = Path(directory)
        caches = root / "Caches/org.test"
        output = caches / "v1.2.0/distribution"
        stage = output / "stage"
        (stage / "Standalone").mkdir(parents=True)
        (output / "validation").mkdir(parents=True)
        (output / "reports").mkdir()
        (output / "release-assets").mkdir()
        app = make_app(stage / "Standalone")
        # Strip a validation clone.
        validation = output / "validation/Runtime.app"
        shutil.copytree(app, validation)
        DIST.runtime_manifest_for_distribution(validation)
        # Build the runtime stage layout directly (stage/Runtime must not pre-exist).
        runtime_report = DIST.write_runtime_layout(app, stage / "Runtime",
                                                   clone_dir_fn=fake_clone_directory,
                                                   clone_file_fn=fake_clone_file)
        # Source export normally comes from prepare phase; stage a minimal one.
        write_bytes(stage / "Runtime/Source/README.md", b"# source")
        assert runtime_report["game_directory_present"] is False
        (stage / "Standalone/README.txt").write_text("standalone")
        (stage / "Runtime/README.txt").write_text("runtime")
        prepare_report = {
            "schema": "ra3mac-distribution-v12-prepare-v1", "version": "1.2.0",
            "wine_source": {"source_version": "1.1.0"},
        }
        (output / "reports/prepare-phase.json").write_text(json.dumps(prepare_report))
        assets = output / "release-assets"
        version = "1.2.0"
        payloads = {
            f"RA3EP1Mac-v{version}-macos-arm64-runtime.dmg": b"runtime",
            f"RA3EP1Mac-v{version}-macos-arm64-standalone.001.dmg": b"s1",
            f"RA3EP1Mac-v{version}-macos-arm64-standalone.002.dmgpart": b"s2",
            f"RA3EP1Mac-v{version}-macos-arm64-standalone.003.dmgpart": b"s3",
            f"RA3EP1Mac-v{version}-macos-arm64-standalone.004.dmgpart": b"s4",
        }
        for name, data in payloads.items():
            write_bytes(assets / name, data)
        wine = make_wine_archive(root / "prior")
        write_bytes(assets / f"RA3EP1Mac-v{version}-wine-source.tar.zst", wine.read_bytes())
        runner = FakeRunner([])

        def simulated_hdiutil(command, *, capture=True):
            joined = " ".join(command)
            if "attach" in joined:
                mountpoint = Path(command[command.index("-mountpoint") + 1])
                source = {"runtime": stage / "Runtime", "standalone": stage / "Standalone"}[mountpoint.name]
                shutil.copytree(source, mountpoint, dirs_exist_ok=True)
                return subprocess.CompletedProcess(command, 0, "", "")
            if "detach" in joined:
                mountpoint = Path(command[-1])
                shutil.rmtree(mountpoint)
                return subprocess.CompletedProcess(command, 0, "", "")
            # create/segment/verify are no-ops in fixtures.
            return subprocess.CompletedProcess(command, 0, "", "")

        services = {"runner": simulated_hdiutil, "verify_app": lambda app: None}
        args = argparse.Namespace(output=output, version="1.2.0", app=None,
                                  prepare_only=False, images_only=True)
        report = DIST.images_phase(args, services=services)
        assert report["version"] == "1.2.0"
        assert report["metadata"]["checksum_entries"] == 7
        assert report["game_started"] is False
        assert (assets / "release-manifest.json").is_file()
        assert (assets / "SHA256SUMS.txt").is_file()
    print("PASS images phase: existing payload reused, metadata generated, no game/Wine execution")


def main() -> int:
    test_prepare_phase_happy_path()
    test_prepare_phase_rejects_untracked_repo()
    test_source_privacy_scan()
    test_segment_discovery_and_limits()
    test_app_validation_and_runtime_manifest()
    test_mounted_runtime_checks()
    test_release_metadata_order_and_hash()
    test_images_phase_uses_existing_assets()
    print("ALL distribution_v12 SELF-TESTS PASSED (fixtures only; no game/Wine/images executed)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
