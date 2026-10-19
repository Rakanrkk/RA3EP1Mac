#!/usr/bin/env python3
"""Self-tests for the v1.2.0 two-rate packager with toy bundles and DLLs.

The source app, clone operation, and signature check are fixture-backed. No real
app, Caches directory, game, Wine, or user data is opened.
Run with: python3 -I packaging/macos/test_multirate_app.py
"""
import argparse, hashlib, json, plistlib, shutil, subprocess, sys, tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_multirate_app as M  # noqa: E402

OLD_STATIC_BYTES = b"STATIC-STREAM-ACCEPTED-SOURCE-IDENTITY"
NEW_STATIC_BYTES = b"STATIC-STREAM-USER-ACCEPTED-LIGHT-VARIANT"
LAUNCHER_BYTES = b"\xcf\xfa\xed\xfe" + b"RA3LAUNCHER-V1.2-CONTRACT-FIXTURE"
DISPLAY_PLAN_BYTES = b"\xcf\xfa\xed\xfeDISPLAY-PLAN-FIXTURE"
WINE_BYTES = b"WINE-BIN-FIXTURE"
GAME_FILES = {"Data/ra3ep1_1.0.game": b"GAME-EXE", "Data/Shaders.big": b"SHADERS",
              "Data/Misc.big": b"MISC", "Data/StaticStream.big": OLD_STATIC_BYTES}
PAYLOAD_FILES = {"Uprising60.exe": None, "uprising60.dll": b"PAYLOAD-DLL-60"}  # exe set by caller

def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()

def hsh(b):
    return hashlib.sha256(b).hexdigest()

def pe32_fixture(payload: bytes) -> bytes:
    """Minimal but genuinely parsed i386 PE image header (MZ, e_lfanew, PE\\0\\0, 0x14c)."""
    return (b"MZ" + b"\x00" * 0x3A + (0x40).to_bytes(4, "little")
            + b"PE\x00\x00" + (0x014C).to_bytes(2, "little") + b"\x01\x00" + payload)

def timing_bytes(rate: int) -> bytes:
    return pe32_fixture(b"TIMING-PAYLOAD-RATE-%d" % rate)

KNOWN = (hsh(OLD_STATIC_BYTES), hsh(NEW_STATIC_BYTES))  # injected fixture binding

def make_timing_dir(base: Path, rate: int, reported_hz=None, name=None) -> Path:
    d = base / (name or f"rate{rate}")
    d.mkdir(parents=True, exist_ok=True)
    data = timing_bytes(rate)
    (d / "uprising120.dll").write_bytes(data)
    (d / "build-result.json").write_text(json.dumps(
        {"target_updates_hz": rate if reported_hz is None else reported_hz,
         "logic_hz": 15, "dll_sha256": hashlib.sha256(data).hexdigest()}))
    return d

def make_source_app(root: Path, loader_bytes: bytes) -> dict:
    """Toy-scale installed Adaptive app; returns pin bookkeeping."""
    app = root / "红警3起义时刻.app"
    res = app / "Contents/Resources"
    (app / "Contents/MacOS").mkdir(parents=True)
    (res / "wine/bin").mkdir(parents=True)
    (res / "wine/bin/wine").write_bytes(WINE_BYTES)
    game = res / "prefix-template/drive_c/ra3-mtld3d"
    for rel, data in GAME_FILES.items():
        (game / rel).parent.mkdir(parents=True, exist_ok=True)
        (game / rel).write_bytes(data)
    payload = res / "prefix-template/drive_c/u60-mtld3d"
    payload.mkdir(parents=True)
    (payload / "uprising60.dll").write_bytes(PAYLOAD_FILES["uprising60.dll"])
    fonts = res / "prefix-template/drive_c/windows/Fonts"
    fonts.mkdir(parents=True)
    (fonts / "marlett.ttf").write_bytes(b"MARLETT")
    dd = res / "prefix-template/dosdevices"
    dd.mkdir(parents=True)
    (dd / "c:").symlink_to("../drive_c")  # internal; z: stays omitted
    (res / "AppIcon.icns").write_bytes(b"ICNS")
    (res / "Delivery.txt").write_bytes(b"DELIVERY")
    (res / "display-mode").write_bytes(b"\xcf\xfa\xed\xfeDISPLAY-MODE-FIXTURE")
    (res / "display-plan").write_bytes(DISPLAY_PLAN_BYTES)
    (app / "Contents/MacOS/RA3Launcher").write_bytes(b"\xcf\xfa\xed\xfeOLD-LAUNCHER")
    (payload / "Uprising60.exe").write_bytes(loader_bytes)
    manifest = {
        "schema": M.MANIFEST_SCHEMA, "version": "1.0",
        "bundle": {"id": M.ADAPTIVE_BUNDLE_ID, "name": "红警3：起义时刻",
                   "executable": "RA3Launcher", "short_version": "1.0", "build_version": "2"},
        "layout": {"wine": "Resources/wine", "prefix_template": "Resources/prefix-template",
                   "display_helper": "Resources/display-mode",
                   "display_plan": "Resources/display-plan"},
        "pin_roots": {"runtime": "Resources/wine", "game": "Resources/prefix-template/drive_c/ra3-mtld3d",
                      "payload": "Resources/prefix-template/drive_c/u60-mtld3d",
                      "fonts": "Resources/prefix-template/drive_c/windows/Fonts"},
        "runtime": {"accepted_source": "fixed-viewport"}, "presentation": "safe-area-v1",
        "runtime_pins": {"bin/wine": hsh(WINE_BYTES)},
        "game_pins": {rel: hsh(data) for rel, data in GAME_FILES.items()},
        "payload_pins": {"Uprising60.exe": hsh(loader_bytes),
                         "uprising60.dll": hsh(PAYLOAD_FILES["uprising60.dll"])},
        "font_pins": {"marlett.ttf": hsh(b"MARLETT")},
        "display_plan": {"resource": "Resources/display-plan", "sha256": hsh(DISPLAY_PLAN_BYTES),
                         "bytes": len(DISPLAY_PLAN_BYTES)},
        "pins_note": "pre-codesign identity; refreshed by packaging sign step before outer codesign",
        "local_only": True, "audio_capture": "Disabled in Wine backend",
        "privacy_usage_requests": "No microphone request",
        "signature_policy": "Local signing identity -; pins refreshed after signing"}
    (res / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + "\n")
    plistlib.dump({"CFBundleIdentifier": M.ADAPTIVE_BUNDLE_ID, "CFBundleName": "红警3：起义时刻",
                   "CFBundleExecutable": "RA3Launcher", "CFBundleShortVersionString": "1.0",
                   "CFBundleVersion": "2", "CFBundlePackageType": "APPL"},
                  (app / "Contents/Info.plist").open("wb"))
    return {"app": app, "loader_sha": hsh(loader_bytes),
            "game_pins": manifest["game_pins"], "payload_pins": manifest["payload_pins"]}

def make_light_variant(root: Path, src_pins: dict, tamper_file=False, wrong_pin=False,
                       name="variant") -> Path:
    game_root = root / ("light-zero-gap" if name == "variant" else name)
    (game_root / "Data").mkdir(parents=True, exist_ok=True)
    (game_root / "Data/StaticStream.big").write_bytes(
        b"STATIC-STREAM-TAMPERED" if tamper_file else NEW_STATIC_BYTES)
    pins = {**src_pins, "Data/StaticStream.big": hsh(NEW_STATIC_BYTES)}
    report = {"schema": M.LIGHT_SCHEMA, "status": "prepared-not-visually-verified",
              "game_root": str(game_root), "windows_game_root": "C:\\light-zero-gap",
              "game_pins": pins, "accepted_game_pins": src_pins,
              "accepted_source_unchanged": True, "installed_app_or_saves_modified": False}
    if wrong_pin:
        report["game_pins"]["Data/StaticStream.big"] = "f" * 64
    rp = root / f"{name}.json"
    rp.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n")
    return rp

def snapshot(root: Path) -> dict:
    got = {}
    for p in sorted(root.rglob("*")):
        rel = str(p.relative_to(root))
        got[rel] = ("link", p.readlink()) if p.is_symlink() else \
                   ("dir",) if p.is_dir() else ("file", sha(p))
    return got

def main() -> int:
    # Contract constants are exactly the user-accepted identities.
    assert M.KNOWN_OLD_STATIC == "0daeb2d3aedfe7a83d75dc61732af8232de3ffb13603882aa5e216f0b9853834"
    assert M.KNOWN_NEW_STATIC == "785f42ad271e6da824c86a938ef79baf1ae99f7bba9aab12afba6c3a9f13a634"
    assert M.RATES == (60, 120) and M.DEFAULT_RATE == 60 and M.VERSION == "1.2.0"
    assert M.KNOWN_TIMING_SHA256 == {
        60: "9017171020424cf6f040b8ea8802016ccaada70049b147fed25d48afe681c84b",
        120: "1ede43dc679bcf2f91c19dd64c8695f8898aa75574c04e25921f4c93c9d0c7c3",
    }
    assert M.DATA_VERSION == "1.0" and M.BUILD_VERSION == "3" and M.TIMING_LOG == "uprising120.log"

    # Header validators accept the fixture shapes and reject fakes.
    assert M.pe32_machine(pe32_fixture(b"x")) == "i386"
    assert M.pe32_machine(b"MZ" + b"\x00" * 100) is None
    assert M.pe32_machine(pe32_fixture(b"x").replace(b"PE\x00\x00", b"PE\x01\x00")) is None
    assert M.is_macho(LAUNCHER_BYTES) and not M.is_macho(pe32_fixture(b"x"))

    base = Path(tempfile.mkdtemp(prefix="multirate-fixture-"))
    mocks = {"clone": [], "verify": []}
    real_clone, real_verify = M.clone_dir, M.codesign_verify_deep
    real_timing_pins = M.KNOWN_TIMING_SHA256

    def mock_clone(s, d):
        mocks["clone"].append((str(s), str(d)))
        shutil.copytree(s, d, symlinks=True)
        return "mock-copytree"

    M.clone_dir = mock_clone
    M.codesign_verify_deep = lambda app: mocks["verify"].append(str(app))
    try:
        loader_bytes = pe32_fixture(b"ACCEPTED-LOADER-PAYLOAD")
        fixtures = base / "fixtures"
        fixtures.mkdir()
        loader = fixtures / "Uprising60.exe"
        loader.write_bytes(loader_bytes)
        (loader.parent / "SHA256.json").write_text(json.dumps(
            {"Uprising60.exe": hsh(loader_bytes), "unrelated": "0" * 64}))
        src_info = make_source_app(base / "source", loader_bytes)
        src = src_info["app"]
        light = make_light_variant(base, src_info["game_pins"])
        timing = {r: make_timing_dir(fixtures, r) for r in M.RATES}
        fixture_timing_pins = {r: hsh(timing_bytes(r)) for r in M.RATES}
        M.KNOWN_TIMING_SHA256 = fixture_timing_pins
        launcher_in = fixtures / "launcher-input"
        launcher_in.write_bytes(LAUNCHER_BYTES)
        caches = base / "Caches"
        caches.mkdir()

        def make_args(out, report=None, reports_dir=None, loader_p=None, light_p=None, timing_p=None):
            return argparse.Namespace(
                source_app=str(src), output=str(out), launcher=str(launcher_in),
                loader=str(loader_p or loader), timing_60=str((timing_p or timing)[60]),
                timing_120=str((timing_p or timing)[120]),
                light_variant=str(light_p or light),
                report=str(report) if report else None,
                reports_dir=str(reports_dir) if reports_dir else None)

        def fixture_build(args, known_static=KNOWN):
            return M.build_multirate(args, known_static=known_static, timing_pins=fixture_timing_pins)

        def rerun(outname, **kw):
            target = caches / outname
            err_args = make_args(target, report=kw.pop("report", base / "never.json"), **kw)
            err = None
            try:
                M.build_multirate(err_args, known_static=KNOWN, timing_pins=fixture_timing_pins)
            except M.BuildError as e:
                err = str(e)
            assert err, f"expected BuildError for {outname}"
            assert not target.exists(), f"failed build left a destination: {target}"
            return err

        # -- successful build --------------------------------------------------
        out = caches / "Candidate.app"
        report_path = base / "report.json"
        rp = M.build_multirate(make_args(out, report=report_path), known_static=KNOWN,
                               timing_pins=fixture_timing_pins)
        assert rp == report_path and report_path.is_file()
        assert mocks["verify"] == [str(src.resolve())], "codesign must run on the source app"
        assert mocks["clone"] == [(str(src.resolve()), str(out))], "exactly one clone, source -> dest"
        rep = json.loads(report_path.read_text())
        assert rep["schema"] == M.REPORT_SCHEMA and rep["signature_refresh_required"] is True
        assert rep["delta_audit"]["result"].startswith("exactly")
        assert sorted(c["path"] for c in rep["byte_changes"]) == sorted(
            ["MacOS/RA3Launcher", "prefix-template/drive_c/ra3-mtld3d/Data/StaticStream.big"]
            + ["Resources/timing/" + n for n in ("Uprising60.exe", "fps60.dll", "fps120.dll")])
        assert rep["inputs"]["timing"]["120"]["target_updates_hz"] == 120
        assert rep["inputs"]["light_variant"]["sha256"] == hsh(NEW_STATIC_BYTES)

        # Known only resource delta versus the untouched source.
        a, b = snapshot(src), snapshot(out)
        added = sorted(set(b) - set(a))
        removed = sorted(set(a) - set(b))
        changed = sorted(r for r in set(a) & set(b) if a[r] != b[r])
        assert not removed
        assert added == sorted(M.EXPECTED_ADDED), added
        assert changed == sorted(M.EXPECTED_REPLACED), changed
        assert snapshot(src) == a, "SOURCE TREE WAS MUTATED"

        # Manifest contract fields, pins, version/data_version, Info.plist sync.
        man_bytes = (out / "Contents/Resources/manifest.json").read_bytes()
        man = json.loads(man_bytes)
        assert man["version"] == "1.2.0" and man["data_version"] == "1.0"
        assert man["bundle"]["short_version"] == "1.2.0" and man["bundle"]["build_version"] == "3"
        assert man["bundle"]["id"] == M.ADAPTIVE_BUNDLE_ID
        assert man["bundle"]["executable"] == "RA3Launcher"
        assert man["timing_pins"] == {"Uprising60.exe": hsh(loader_bytes), **{
            f"fps{r}.dll": fixture_timing_pins[r] for r in M.RATES}}
        assert man["frame_rate_profiles"] == {str(r): {"dll": f"fps{r}.dll",
                                                       "log": "uprising120.log"}
                                              for r in M.RATES}
        assert man["default_frame_rate"] == 60
        assert man["resource_updates"] == {"Data/StaticStream.big": {
            "previous_sha256": [hsh(OLD_STATIC_BYTES)], "sha256": hsh(NEW_STATIC_BYTES)}}
        assert man["game_pins"]["Data/StaticStream.big"] == hsh(NEW_STATIC_BYTES)
        assert {k: v for k, v in man["game_pins"].items() if k != "Data/StaticStream.big"} == \
               {k: v for k, v in src_info["game_pins"].items() if k != "Data/StaticStream.big"}
        assert man["payload_pins"] == src_info["payload_pins"], "legacy payload pins unchanged"
        assert man["runtime_pins"] == {"bin/wine": hsh(WINE_BYTES)}
        assert "signature_policy" not in man, "stale signature claim removed until re-sign"
        assert str(base).encode() not in man_bytes and b"/Users/" not in man_bytes
        plist = plistlib.load((out / "Contents/Info.plist").open("rb"))
        assert plist["CFBundleShortVersionString"] == "1.2.0" and plist["CFBundleVersion"] == "3"
        assert plist["CFBundleIdentifier"] == M.ADAPTIVE_BUNDLE_ID
        assert plist["CFBundleExecutable"] == "RA3Launcher"
        # Staged bytes equal the verified inputs; written pins hold on disk.
        assert (out / "Contents/MacOS/RA3Launcher").read_bytes() == LAUNCHER_BYTES
        assert (out / "Contents/Resources/timing/Uprising60.exe").read_bytes() == loader_bytes
        for r in M.RATES:
            assert (out / f"Contents/Resources/timing/fps{r}.dll").read_bytes() == timing_bytes(r)
        assert (out / "Contents/Resources/prefix-template/drive_c/ra3-mtld3d/"
                "Data/StaticStream.big").read_bytes() == NEW_STATIC_BYTES
        game_root = out / "Contents/Resources/prefix-template/drive_c/ra3-mtld3d"
        for rel, want in man["game_pins"].items():
            assert sha(game_root / rel) == want
        for rel, want in man["payload_pins"].items():
            assert sha(out / "Contents/Resources/prefix-template/drive_c/u60-mtld3d" / rel) == want
        for rel, want in man["timing_pins"].items():
            assert sha(out / "Contents/Resources/timing" / rel) == want
        good_snapshot = snapshot(out)

        # -- meaningful failures BEFORE any clone ------------------------------
        clones_after_success = len(mocks["clone"])
        bad_timing = dict(timing)
        bad_timing[60] = make_timing_dir(fixtures, 60, reported_hz=90, name="rate60-bad")
        err = rerun("F1.app", timing_p=bad_timing)
        assert "target_updates_hz" in err and "60" in err, err
        assert len(mocks["clone"]) == clones_after_success, \
            "validation failures must never reach the clone step"
        stray = make_timing_dir(fixtures, 99)
        err = rerun("F2.app", loader_p=stray / "uprising120.dll")
        assert "accepted payload pin" in err, err
        wrong_sha = make_timing_dir(fixtures, 60, name="rate60-sha")
        bad_sha_pins = {**fixture_timing_pins, 60: "f" * 64}
        bad_sha_args = make_args(caches / "F12.app", report=base / "never.json",
                                 timing_p={**timing, 60: wrong_sha})
        try:
            M.build_multirate(bad_sha_args, known_static=KNOWN, timing_pins=bad_sha_pins)
            raise AssertionError("expected BuildError for unapproved DLL SHA")
        except M.BuildError as e:
            assert "not the accepted release payload" in str(e)
        assert not (caches / "F12.app").exists()
        tampered = make_light_variant(base, src_info["game_pins"], tamper_file=True,
                                      name="variant-tampered")
        err = rerun("F3.app", light_p=tampered)
        assert "does not hash" in err, err
        wrongpin = make_light_variant(base, src_info["game_pins"], wrong_pin=True,
                                      name="variant-wrongpin")
        err = rerun("F4.app", light_p=wrongpin)
        assert "not the accepted variant hash" in err, err
        bad_loader = fixtures / "other-loader.exe"
        bad_loader.write_bytes(pe32_fixture(b"NOT-THE-ACCEPTED-LOADER"))
        err = rerun("F5.app", loader_p=bad_loader)
        assert "accepted payload pin" in err, err
        nonmacho = fixtures / "nonmacho"
        nonmacho.write_bytes(b"NOT-MACHO-AT-ALL")
        target = caches / "F6.app"
        err_args = make_args(target, report=base / "never.json")
        err_args.launcher = str(nonmacho)
        try:
            M.build_multirate(err_args, known_static=KNOWN)
            raise AssertionError("expected BuildError for non-Mach-O launcher")
        except M.BuildError as e:
            assert "Mach-O" in str(e)
        assert not target.exists()
        # Stale known-static binding must abort (light report pins the real value).
        err_args = make_args(caches / "F7.app", report=base / "never.json")
        try:
            M.build_multirate(err_args, known_static=(KNOWN[0], "e" * 64))
            raise AssertionError("expected BuildError for stale binding")
        except M.BuildError as e:
            assert "not the accepted variant hash" in str(e)
        assert not (caches / "F7.app").exists()
        assert len(mocks["clone"]) == clones_after_success, \
            "no clone may happen for any pre-validation failure"

        # -- unknown source schema fails before clone --------------------------
        src_man = src / "Contents/Resources/manifest.json"
        good_man = src_man.read_text()
        try:
            src_man.write_text(json.dumps({**json.loads(good_man), "schema": "ra3mac-app-v2"}))
            err = rerun("F8.app")
            assert "unknown source manifest schema" in err, err
        finally:
            src_man.write_text(good_man)
        assert len(mocks["clone"]) == clones_after_success

        # -- template z: symlink must stay omitted (fails after clone, cleans up)
        zlink = src / "Contents/Resources/prefix-template/dosdevices/z:"
        zlink.symlink_to("/")
        try:
            err = rerun("F9.app")
            assert "dosdevices" in err, err
            assert len(mocks["clone"]) == clones_after_success + 1, \
                "this failure is expected to occur after the clone"
        finally:
            zlink.unlink()
        mocks["clone"].clear()
        assert snapshot(src) == a, "SOURCE TREE WAS MUTATED during aborts"

        # -- strict inputs and output rules ------------------------------------
        incomplete = dict(timing)
        incomplete[120] = base / "absent-dir"
        err = rerun("F10.app", timing_p=incomplete)
        assert "artifact directory missing" in err, err
        err_args = make_args(base / "Application Support" / "X.app", report=base / "never.json")
        try:
            M.build_multirate(err_args, known_static=KNOWN)
            raise AssertionError("expected BuildError for Application Support output")
        except M.BuildError as e:
            assert "Application Support" in str(e)
        (base / "notcaches").mkdir()
        err_args = make_args(base / "notcaches" / "X.app", report=base / "never.json")
        try:
            M.build_multirate(err_args, known_static=KNOWN)
            raise AssertionError("expected BuildError for non-Caches output")
        except M.BuildError as e:
            assert "Caches" in str(e)
        err_args = make_args(out, report=base / "never.json")  # existing destination
        try:
            M.build_multirate(err_args, known_static=KNOWN)
            raise AssertionError("expected BuildError for existing target")
        except M.BuildError as e:
            assert "refusing existing target" in str(e)
        err_args = make_args(caches / "F11.app", report=report_path)  # existing report
        try:
            M.build_multirate(err_args, known_static=KNOWN)
            raise AssertionError("expected BuildError for existing report")
        except M.BuildError as e:
            assert "report already exists" in str(e)
        assert not (caches / "F11.app").exists() and snapshot(src) == a

        # --reports-dir writes a timestamped report; result is deterministic.
        rd = base / "reports"
        rp2 = fixture_build(make_args(caches / "Candidate2.app", reports_dir=rd))
        assert rp2.parent == rd and rp2.is_file() and rp2.name.startswith("Candidate2-multirate-")
        assert snapshot(caches / "Candidate2.app") == good_snapshot

        # -- CLI behavior -------------------------------------------------------
        rc = M.main(["--source-app", str(src), "--output", str(caches / "X.app"),
                     "--launcher", str(launcher_in), "--timing-60", str(timing[60]),
                     "--timing-120", str(timing[120]), "--light-variant", str(light),
                     "--report", str(base / "cli-report.json")])
        # The unoverridden CLI path enforces the real accepted constants; the
        # fixture binding differs, so main() must abort with exit 2, no output.
        assert rc == 2, "default known-static constants must be enforced on the CLI path"
        assert not (caches / "X.app").exists() and not (base / "cli-report.json").exists()
        try:
            M.main(["--source-app", str(src), "--output", str(caches / "Y.app"),
                    "--launcher", str(launcher_in), "--timing-60", str(timing[60]),
                    "--timing-120", str(timing[120]), "--light-variant", str(light)])
            raise AssertionError("missing --report/--reports-dir must be a usage error")
        except SystemExit as e:
            assert e.code == 2
        assert not (caches / "Y.app").exists()
        # Only 60/120 are legal command-line rate inputs.
        for rejected in ("--timing-30", "--timing-90"):
            try:
                M.main(["--source-app", str(src), "--output", str(caches / f"reject-{rejected[9:]}.app"),
                        "--launcher", str(launcher_in), rejected, str(timing[60]),
                        "--light-variant", str(light), "--report", str(base / "reject.json")])
                raise AssertionError(f"unexpected accepted legacy rate option: {rejected}")
            except SystemExit as error:
                assert error.code == 2
        assert not any((caches / name).exists() for name in ("reject-30.app", "reject-90.app"))
        subprocess.run([sys.executable, "-I", str(Path(__file__).resolve().parent /
                                                  "build_multirate_app.py"), "--help"],
                       check=True, capture_output=True)
        # CLI end-to-end on the fixtures succeeds via the injected fixture binding.
        cli_args = argparse.Namespace(
            source_app=str(src), output=str(caches / "Candidate3.app"),
            launcher=str(launcher_in), loader=str(loader),
            timing_60=str(timing[60]), timing_120=str(timing[120]),
            light_variant=str(light), report=str(base / "cli-report.json"),
            reports_dir=None)
        # The public CLI requires canonical identities; fixture success goes
        # through the exact same builder with injectable fixture pins.
        assert fixture_build(cli_args).name == "cli-report.json"
        assert snapshot(caches / "Candidate3.app") == good_snapshot

        print("ALL SELF-TESTS PASSED")
        return 0
    finally:
        M.clone_dir, M.codesign_verify_deep = real_clone, real_verify
        M.KNOWN_TIMING_SHA256 = real_timing_pins
        shutil.rmtree(base, ignore_errors=True)

if __name__ == "__main__":
    sys.exit(main())
