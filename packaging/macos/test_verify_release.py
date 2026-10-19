#!/usr/bin/env python3
"""Synthetic-only regressions for local release auditing and data snapshots."""
from __future__ import annotations

import importlib.util
import json
import plistlib
from pathlib import Path
import tempfile
from unittest.mock import patch

HERE = Path(__file__).resolve().parent


def load_module(name, filename):
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


verify = load_module("release_audit", "verify_release.py")
snapshotter = load_module("user_snapshot", "snapshot_user_data.py")
signer = load_module("app_signing", "sign_app.py")
privacy = load_module("distribution_privacy", "prepare_distribution_app.py")


def write_app_fixture(root: Path):
    resources = root / "Contents/Resources"
    (root / "Contents/MacOS").mkdir(parents=True)
    (resources / "wine/lib/wine/x86_64-unix").mkdir(parents=True)
    (resources / "prefix-template/drive_c/ra3-mtld3d/Data").mkdir(parents=True)
    (resources / "prefix-template/drive_c/u60-mtld3d").mkdir(parents=True)
    (resources / "prefix-template/drive_c/windows/Fonts").mkdir(parents=True)
    (resources / "timing").mkdir()
    launcher = root / "Contents/MacOS/RA3Launcher"
    launcher.write_bytes(b"fixture-launcher")
    wine = resources / "wine/lib/wine/x86_64-unix/winecoreaudio.so"
    wine.write_bytes(b"fixture native binary")
    game = resources / "prefix-template/drive_c/ra3-mtld3d/Data/game.dat"
    game.write_bytes(b"commercial-fixture-bytes")
    payload = resources / "prefix-template/drive_c/u60-mtld3d/Uprising60.exe"
    payload.write_bytes(b"PE-fixture")
    timing = resources / "timing/fps60.dll"
    timing.write_bytes(b"timing-fixture")
    # Real-world catch: a diagnostic log in the payload tree outside users/.
    payload_log = resources / "prefix-template/drive_c/u60-mtld3d/uprising60.log"
    payload_log.write_bytes(b"LOG" * 66)
    pins = {
        "runtime_pins": {"lib/wine/x86_64-unix/winecoreaudio.so": verify.sha256_file(wine)},
        "game_pins": {"Data/game.dat": verify.sha256_file(game)},
        "payload_pins": {"Uprising60.exe": verify.sha256_file(payload)},
        "font_pins": {},
        "timing_pins": {"fps60.dll": verify.sha256_file(timing)},
    }
    manifest = {
        "schema": "ra3mac-app-v1",
        "version": "1.2.0",
        "data_version": "1.0",
        "bundle": {"id": "org.ra3mac.UprisingMetal.Adaptive", "short_version": "1.2.0"},
        "frame_rate_profiles": {
            "60": {"dll": "fps60.dll", "log": "uprising120.log"},
            "120": {"dll": "fps120.dll", "log": "uprising120.log"},
        },
        "default_frame_rate": 60,
        "signature_policy": "Ad-hoc integrity signature; component pins refreshed after signing",
        **pins,
    }
    (resources / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (root / "Contents/Info.plist").write_bytes(plistlib.dumps({
        "CFBundleIdentifier": manifest["bundle"]["id"],
        "CFBundleShortVersionString": "1.2.0",
        "CFBundleExecutable": "RA3Launcher",
    }))
    # These are the only native binaries that need the metadata-string scan in
    # this fixture. No executable is invoked by audit_app.
    return manifest, game


def test_public_report_has_no_paths_or_raw_signature_metadata():
    with tempfile.TemporaryDirectory(prefix="ra3-audit-fixture-") as temporary:
        base = Path(temporary)
        app = base / "fixture.app"
        manifest, game = write_app_fixture(app)
        secret_path = str(base / "private-machine-build-path")
        (app / "Contents/Resources/Notes.txt").write_text(
            f"known-local-marker {secret_path}\n", encoding="utf-8")
        with patch.object(verify, "_signature_metadata", return_value={"commands": [{
            "operation": "display", "exit_code": 0,
            "stderr": f"Authority=Developer ID Application: PRIVATE CERT {secret_path}",
        }]}):
            report = verify.audit_app(app, ["known-local-marker"])
        assert report["launched_app_or_game"] is False
        assert any(match["path"] == "Contents/Resources/Notes.txt"
                   for match in report["privacy_matches"])
        assert any(pin["matches"] for pin in report["pins"])
        public = verify._safe_public_report(report)
        serialized = json.dumps(public, ensure_ascii=False)
        assert secret_path not in serialized
        assert str(app) not in serialized
        assert "PRIVATE CERT" not in serialized
        assert "known-local-marker" not in serialized
        assert set(public) == {"schema", "counts", "sha256"}
        assert set(public["counts"]).isdisjoint({"target", "path_inventory", "local_indicators"})
        # A release audit hashes selected metadata/pins but never reads game.dat
        # into the text scanner or strings scanner.
        assert all(scan["path"] != "Contents/Resources/prefix-template/drive_c/ra3-mtld3d/Data/game.dat"
                   for scan in report["scans"])
        assert game.is_file()


def test_timing_contract_and_pin_tampering_are_reported():
    with tempfile.TemporaryDirectory(prefix="ra3-audit-pins-") as temporary:
        app = Path(temporary) / "fixture.app"
        manifest, _ = write_app_fixture(app)
        # The two-profile contract is the only accepted public 1.2 contract.
        assert not any(finding["kind"] == "frame_rate_profile_contract_invalid"
                       for finding in verify.audit_app(app)["path_findings"])
        dll = app / "Contents/Resources/timing/fps60.dll"
        dll.write_bytes(b"tampered")
        report = verify.audit_app(app)
        assert any(pin["group"] == "timing_pins" and not pin["matches"] for pin in report["pins"])
        manifest["frame_rate_profiles"]["90"] = {"dll": "fps90.dll", "log": "uprising120.log"}
        (app / "Contents/Resources/manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        report = verify.audit_app(app)
        assert any(finding["kind"] == "frame_rate_profile_contract_invalid"
                   for finding in report["path_findings"])


def test_tracked_tree_uses_index_paths_and_public_summary_is_path_free():
    with tempfile.TemporaryDirectory(prefix="ra3-tree-fixture-") as temporary:
        root = Path(temporary) / "checkout"
        root.mkdir()
        (root / "LICENSE").write_text("MIT License\n", encoding="utf-8")
        (root / "source.py").write_text("value = 1\n", encoding="utf-8")
        (root / "private-notes").mkdir()
        (root / "private-notes/scratch.txt").write_text("local notes", encoding="utf-8")
        (root / "empty-logs").mkdir()  # generic Wine-style dir name: not a finding
        report = verify.audit_tree(root, tracked_only=False)
        public = verify._safe_public_report(report)
        public_text = json.dumps(public)
        assert "private-notes" not in public_text
        assert str(root) not in public_text
        assert any(item["kind"] == "private_ci_or_notes_directory"
                   and item["path"] == "private-notes" for item in report["path_findings"])
        assert not any(item["path"] == "empty-logs" for item in report["path_findings"])
        # A clean license notice is inventory metadata, not a personal-data hit.
        assert not any(item.get("kind") == "license_notice" for item in report["path_findings"])


def test_player_save_cleanup_is_scoped_to_cloned_game_directories():
    with tempfile.TemporaryDirectory(prefix="ra3-player-fixture-") as temporary:
        base = Path(temporary)
        original = base / "source/drive_c/users/player"
        clone = base / "clone/drive_c/users/player"
        archive = clone / "AppData/Roaming/Red Alert 3 Uprising/Saved Games"
        replays = clone / "Documents/Red Alert 3 Uprising/Replays"
        profiles = clone / "AppData/Roaming/Red Alert 3 Uprising/Profiles/1"
        game_data = clone.parent.parent / "ra3-mtld3d/Data"
        for path in (archive, replays, profiles, game_data):
            path.mkdir(parents=True)
        (archive / "RA3UCampaign").write_bytes(b"campaign-save")
        (archive / "RA3UAutosave").write_bytes(b"auto-save")
        (replays / "match.map").write_bytes(b"replay")
        (profiles / "Options.ini").write_text("Resolution = 800 600\n", encoding="utf-8")
        (game_data / "normal.map").write_bytes(b"shipped-map")
        (original / "AppData/Roaming/Red Alert 3 Uprising/Saved Games").mkdir(parents=True)
        source_save = original / "AppData/Roaming/Red Alert 3 Uprising/Saved Games/RA3UCampaign"
        source_save.write_bytes(b"source-remains")
        removed = []
        privacy.remove_player_archive_directories(clone, base / "clone", removed)
        assert not archive.exists() and not replays.exists()
        assert len(removed) == 2
        assert (profiles / "Options.ini").is_file()
        assert (game_data / "normal.map").read_bytes() == b"shipped-map"
        assert source_save.read_bytes() == b"source-remains"


def test_snapshot_scope_and_non_overwriting_output():
    with tempfile.TemporaryDirectory(prefix="ra3-snapshot-fixture-") as temporary:
        base = Path(temporary)
        # Snapshots use the actual Wine profile root, drive_c/users, so profile
        # AppData trees are read as data rather than traversed as profiles.
        users = base / "drive_c/users"
        saves = users / "player/AppData/Roaming/Red Alert 3 Uprising/Saved Games/1"
        windows = users / "player/AppData/Local/Wine/Windows/System32"
        saves.mkdir(parents=True)
        windows.mkdir(parents=True)
        (saves / "RA3UCampaign").write_bytes(b"campaign")
        (saves / "RA3UAutosave").write_bytes(b"autosave")
        (saves / "battle.map").write_bytes(b"replay-map")
        (windows / "registry-like.dat").write_bytes(b"ignore")
        # The Saved Games ancestor must never be clipped: every file beneath the
        # game folder, at any depth under Saved Games, is inventoried.
        registry = base / "user.reg"
        registry.write_bytes(b"Wine registry")
        prefs = base / "org.ra3mac.UprisingMetal.Adaptive.plist"
        prefs.write_bytes(plistlib.dumps({"RA3PreferredFrameRate": 120}))
        result = snapshotter.snapshot(users, [registry], [prefs])
        player_entries = [item for item in result["entries"] if item["category"] == "player_data"]
        assert len(player_entries) == 3
        assert {item["path"].rsplit("/", 1)[-1] for item in player_entries} == {
            "RA3UCampaign", "RA3UAutosave", "battle.map"}
        assert all("Saved Games" in item["path"] for item in player_entries)
        assert all("registry-like.dat" not in item["path"] for item in result["entries"])
        before = (saves / "RA3UCampaign").read_bytes()
        out = base / "snapshot.json"
        snapshotter.write_absent(out, result)
        assert (out.stat().st_mode & 0o777) == 0o600
        try:
            snapshotter.write_absent(out, result)
        except FileExistsError:
            pass
        else:
            raise AssertionError("snapshot output was overwritten")
        assert (saves / "RA3UCampaign").read_bytes() == before


def test_snapshot_covers_mac_style_profile_saved_games_location():
    with tempfile.TemporaryDirectory(prefix="ra3-snapshot-sg-") as temporary:
        base = Path(temporary)
        users = base / "drive_c/users"
        # The launcher writes game data to <profile>/Saved Games/<GameFolder>:
        # campaign and autosave saves must be inventoried here too, without
        # walking generic Windows trees (System32-like files stay invisible).
        saves = users / "player/Saved Games/Red Alert 3 Uprising/1"
        other = users / "player/Saved Games/Other Game"
        system = users / "player/AppData/Local/Fake/Windows/System32"
        for path in (saves, other, system):
            path.mkdir(parents=True)
        (saves / "RA3UCampaign").write_bytes(b"campaign-real")
        (saves / "RA3UAutosave").write_bytes(b"autosave-real")
        (saves / "skirmish.map").write_bytes(b"player-map")
        (other / "foreign-save").write_bytes(b"not-ra3")
        (system / "kernel.dat").write_bytes(b"not-player-data")
        result = snapshotter.snapshot(users, [], [])
        player_entries = [item for item in result["entries"] if item["category"] == "player_data"]
        assert {item["path"].rsplit("/", 1)[-1] for item in player_entries} == {
            "RA3UCampaign", "RA3UAutosave", "skirmish.map"}
        assert all("/Red Alert 3 Uprising/" in item["path"] for item in player_entries)
        assert not any("Other Game" in item["path"] or "kernel.dat" in item["path"]
                       for item in player_entries)


def test_public_adhoc_signature_wording_and_identity_guard():
    neutral = signer.signature_policy("-", public=True)
    assert neutral == "Ad-hoc integrity signature; component pins refreshed after signing"
    try:
        signer.signature_policy("A" * 64, public=True)
    except ValueError:
        pass
    else:
        raise AssertionError("public signature wording accepted a private identity")


def test_payload_and_shipped_logs_flagged_anywhere_in_app():
    with tempfile.TemporaryDirectory(prefix="ra3-audit-logs-") as temporary:
        app = Path(temporary) / "fixture.app"
        write_app_fixture(app)
        game_log = (app / "Contents/Resources/prefix-template/drive_c/ra3-mtld3d/"
                    "ra3ep1_1.0.game_d3d9.log")
        game_log.write_bytes(b"d3d9-log")
        help_log = (app / "Contents/Resources/prefix-template/drive_c/ra3-mtld3d/"
                    "Support/EA Help/en-us/EA Help.log")
        help_log.parent.mkdir(parents=True)
        help_log.write_bytes(b"help-log")
        report = verify.audit_app(app)
        logged = {item["path"] for item in report["path_findings"]
                  if item["kind"] == "log_file_in_publish_scope"}
        assert ("Contents/Resources/prefix-template/drive_c/u60-mtld3d/uprising60.log") in logged
        assert ("Contents/Resources/prefix-template/drive_c/ra3-mtld3d/ra3ep1_1.0.game_d3d9.log") in logged
        assert ("Contents/Resources/prefix-template/drive_c/ra3-mtld3d/Support/EA Help/en-us/EA Help.log") in logged
        # The payload binary itself is never a log finding.
        assert not any("Uprising60.exe" in path for path in logged)


def test_internal_relative_symlinks_allowed_escapes_and_dangling_flagged():
    with tempfile.TemporaryDirectory(prefix="ra3-audit-links-") as temporary:
        base = Path(temporary)
        app = base / "fixture.app"
        manifest, _ = write_app_fixture(app)
        prefix = app / "Contents/Resources/prefix-template"
        # Real Wine prefix shapes: dosdevices links and lib-level relative hops.
        (prefix / "dosdevices").mkdir()
        (prefix / "dosdevices/c:").symlink_to("../drive_c")
        (app / "Contents/Resources/wine/lib/wine/x86_64-unix/link.so").symlink_to("../../../../bin/wine")
        escapes = prefix / "dosdevices/evil:"
        escapes.symlink_to("../../../../../../outside")
        internal_named = prefix / "dosdevices/inner:"
        internal_named.symlink_to("../../../outside")
        dangling = prefix / "dosdevices/broken:"
        dangling.symlink_to("../missing-target")
        (app / "Contents/Resources/wine/absolute").symlink_to("/etc")
        report = verify.audit_app(app)
        kinds = {(item["path"], item["kind"]) for item in report["path_findings"]}
        assert ("Contents/Resources/prefix-template/dosdevices/c:", "external_or_parent_symlink") not in kinds
        assert not any(path.endswith("link.so") and kind == "external_or_parent_symlink"
                       for path, kind in kinds)
        # inner: stays lexically inside the bundle, so it is not an escape; its
        # target does not exist, which is the dangling check's job.
        assert ("Contents/Resources/prefix-template/dosdevices/inner:", "dangling_symlink") in kinds
        assert ("Contents/Resources/prefix-template/dosdevices/evil:",
                "external_or_parent_symlink") in kinds
        assert ("Contents/Resources/prefix-template/dosdevices/broken:", "dangling_symlink") in kinds
        assert ("Contents/Resources/wine/absolute", "external_or_parent_symlink") in kinds


def main():
    tests = [
        test_public_report_has_no_paths_or_raw_signature_metadata,
        test_timing_contract_and_pin_tampering_are_reported,
        test_tracked_tree_uses_index_paths_and_public_summary_is_path_free,
        test_player_save_cleanup_is_scoped_to_cloned_game_directories,
        test_snapshot_scope_and_non_overwriting_output,
        test_snapshot_covers_mac_style_profile_saved_games_location,
        test_payload_and_shipped_logs_flagged_anywhere_in_app,
        test_public_adhoc_signature_wording_and_identity_guard,
        test_internal_relative_symlinks_allowed_escapes_and_dangling_flagged,
    ]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print("All synthetic release-audit fixtures passed; no app or game launched.")


if __name__ == "__main__":
    main()
