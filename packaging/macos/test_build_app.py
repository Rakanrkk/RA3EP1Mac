#!/usr/bin/env python3
"""Self-tests for build_app.py using only pure temp/mock paths.

Mirrors the real source shapes at toy scale: wine tree with an internal-relative
symlink and the ORIGINAL runtime winecoreaudio module, prefix with dosdevices
(internal, z:/, stale volume/device links), test-owned/u60 session dirs,
escaped-dos registry font refs, and the candidate manifest pin contract. The
built disabled-capture audio fixture carries a Mach-O magic plus the
RA3_NO_AUDIO_CAPTURE marker, exactly what build_app validates. Candidate
packaging is covered offline too: the default fixed build must keep every
piece of existing metadata unchanged (and stage no display-plan helper), the
safe-area candidate must use exactly org.ra3mac.UprisingMetal.Adaptive, emit
presentation safe-area-v1, stage an executable display-plan copy without
mutating its source or the module-level Info.plist template, and every invalid
contract (missing helper, wrong/arbitrary/reused id, non-Mach-O, not
executable, path-character id) must abort before any output directory exists.
Run with:
python3 -I test_build_app.py
"""
import hashlib, json, os, plistlib, shutil, subprocess, sys, tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_app as B  # noqa: E402

AUDIO_BYTES = b"\xcf\xfa\xed\xfe" + b"RA3_NO_AUDIO_CAPTURE-DISABLED-CAPTURE-BIN"
ORIGINAL_AUDIO_BYTES = b"ORIGINAL-WINECOREAUDIO"
DPLAN_BYTES = b"\xcf\xfa\xed\xfe" + b"RA3-NATIVE-DISPLAY-PLAN-HELPER-FAKE"


def pe32_fixture(payload: bytes) -> bytes:
    return (b"MZ" + b"\0" * 0x3A + (0x40).to_bytes(4, "little") + b"PE\0\0"
            + (0x014C).to_bytes(2, "little") + b"\1\0" + payload)


def timing_fixture(root: Path, rate: int) -> Path:
    path = root / f"timing-{rate}"
    path.mkdir()
    data = pe32_fixture(f"release-payload-{rate}".encode())
    (path / "uprising120.dll").write_bytes(data)
    (path / "build-result.json").write_text(json.dumps({
        "target_updates_hz": rate, "dll_sha256": hashlib.sha256(data).hexdigest()}))
    B.KNOWN_TIMING_SHA256[rate] = hashlib.sha256(data).hexdigest()
    return path

def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()

def esc(dos_logical):
    return dos_logical.replace("\\", "\\\\")

def make_source(root: Path, home: Path):
    wine = root / "wine"
    (wine / "bin").mkdir(parents=True)
    (wine / "bin/wine").write_text("WINEBIN")
    (wine / "share/wine/fonts").mkdir(parents=True)
    (wine / "share/wine/fonts/marlett.ttf").write_text("MARLETT-DATA")
    (wine / "share/wine/fonts/symbol.ttf").write_text("SYMBOL-DATA")
    (wine / "lib").mkdir(parents=True)
    (wine / "lib/internal.so").write_text("MOD")
    (wine / "lib/link.so").symlink_to("../bin/wine")  # internal relative: keep
    (wine / "lib/wine/x86_64-unix").mkdir(parents=True)
    (wine / "lib/wine/x86_64-unix/winecoreaudio.so").write_bytes(ORIGINAL_AUDIO_BYTES)
    pre = root / "prefix"
    (pre / "dosdevices").mkdir(parents=True)
    (pre / "dosdevices/c:").symlink_to("../drive_c")
    (pre / "dosdevices/z:").symlink_to("/")
    (pre / "dosdevices/d:").symlink_to("/Volumes/StaleVol")
    (pre / "dosdevices/e::").symlink_to("/dev/rdisk9s2")
    dc = pre / "drive_c"
    for d, f in (("owned-top", "A.txt"), ("u60-metal-x", "B.txt"), ("u60-viewport-y", "C.txt")):
        (dc / d).mkdir(parents=True)
        (dc / d / f).write_text("TEST-ONLY")
    (dc / "u60-mtld3d").mkdir(parents=True)
    loader_bytes = pe32_fixture(b"PAYLOAD-EXE")
    (dc / "u60-mtld3d/Uprising60.exe").write_bytes(loader_bytes)
    (dc / "u60-mtld3d/uprising60.dll").write_text("PAYLOAD-DLL")
    data = dc / "ra3-mtld3d/Data"
    data.mkdir(parents=True)
    (data / "StaticStream.big").write_text("FIXTURE-STATIC")
    (data / "mtld3d-logs").mkdir(parents=True)
    (data / "mtld3d-logs/run.log").write_text("LOG")
    (data / "ra3ep1_1.0.game").write_text("GAME-DATA")
    (data / "ra3ep1_1.0.game.dxvk-cache").write_text("DXVK-OLD")
    (data / "mtld3d_shaders.bin").write_text("WARM-SHADERS")
    (data / "mtld3d_shaders.bin.lock").write_text("L")
    (dc / "windows/Fonts").mkdir(parents=True)
    wf = ("Z:" + str(wine / "share/wine/fonts")).replace("/", "\\")
    lf = ("Z:" + str(home / "Library/Fonts")).replace("/", "\\")
    reg = (f'"Marlett (TrueType)"="{esc(wf)}\\\\marlett.ttf"\n'
           f'"Noto (Regular)"="{esc(lf)}\\\\NotoSansCJKsc-Regular.otf"\n'
           '"Sys (Geneva)"="Z:\\\\System\\\\Library\\\\Fonts\\\\Geneva.ttf"\n').encode()
    (pre / "system.reg").write_bytes(reg)
    (pre / "system 2.reg").write_bytes(reg)
    (pre / "user.reg").write_bytes(
        f'"Symbol (TrueType)"="{esc(wf)}\\\\symbol.ttf"\n'.encode())
    (pre / "userdef.reg").write_bytes(b"[Key]\\n")
    (home / "Library/Fonts").mkdir(parents=True)
    (home / "Library/Fonts/NotoSansCJKsc-Regular.otf").write_text("NOTO-REG-DATA")
    manifest = {"schema": "ra3-fixed-viewport-candidate-v1", "wine_source": "abc",
                "runtime_pins": {"bin/wine": sha(wine / "bin/wine"),
                                 "share/wine/fonts/marlett.ttf": sha(wine / "share/wine/fonts/marlett.ttf")},
                "game_pins": {"Data/ra3ep1_1.0.game": sha(data / "ra3ep1_1.0.game"),
                              B.STATIC_REL: sha(data / "StaticStream.big")},
                "payload_pins": {"Uprising60.exe": sha(dc / "u60-mtld3d/Uprising60.exe"),
                                 "uprising60.dll": sha(dc / "u60-mtld3d/uprising60.dll")}}
    (root / "candidate-manifest.json").write_text(json.dumps(manifest))
    return manifest

def args_for(root, out, launcher, icon, helper, prepare=False, audio=None, patch=None,
             bundle_id=None, presentation=None, display_plan=None, timing_60=None,
             timing_120=None, timing_dir=None, runtime_only=False):
    import argparse
    ns = argparse.Namespace(output=str(out), icon=str(icon), runtime_root=str(root),
                            launcher=str(launcher), display_helper=str(helper),
                            prepare_layout=prepare, audio_module=str(audio),
                            audio_patch=str(patch))
    # The candidate options are attached only when explicitly requested, so
    # default-path callers keep a Namespace WITHOUT these attributes and
    # exercise build_app's getattr defaults (compat contract).
    if bundle_id is not None:
        ns.bundle_id = bundle_id
    if presentation is not None:
        ns.presentation = presentation
    if display_plan is not None:
        ns.display_plan = str(display_plan)
    if timing_60 is not None:
        ns.timing_60 = str(timing_60)
    if timing_120 is not None:
        ns.timing_120 = str(timing_120)
    if timing_dir is not None:
        ns.timing_dir = str(timing_dir)
    if runtime_only:
        ns.runtime_only = True
    return ns

def main() -> int:
    base = Path(tempfile.mkdtemp(prefix="ra3build-test-"))
    src, home, reports = base / "src", base / "home", base / "reports"
    tools, icon = base / "tools", base / "icon.icns"
    tools.mkdir()
    (tools / "RA3Launcher").write_text("LAUNCHER-BIN")
    (tools / "RA3Launcher").chmod(0o755)
    (tools / "display-mode").write_text("HELPER-BIN")
    icon.write_text("ICON-DATA")
    fixtures = base / "fixtures"
    fixtures.mkdir()
    amod, apatch = fixtures / "winecoreaudio.so", fixtures / "wine-no-audio-capture.patch"
    amod.write_bytes(AUDIO_BYTES)
    apatch.write_text("PATCH-PROVENANCE-BYTES")
    # Fake native display-plan helper: fake bytes, but a real Mach-O magic plus
    # the executable bit, exactly what build_app's exe/Mach-O checks accept.
    dplan = fixtures / "display-plan-helper"
    dplan.write_bytes(DPLAN_BYTES)
    dplan.chmod(0o755)
    timing = {rate: timing_fixture(fixtures, rate) for rate in (60, 120)}
    pins = make_source(src.resolve(), home.resolve())
    loader_bytes = (src / "prefix/drive_c/u60-mtld3d/Uprising60.exe").read_bytes()
    flat_timing = fixtures / "flat-timing"
    flat_timing.mkdir()
    (flat_timing / "Uprising60.exe").write_bytes(loader_bytes)
    for rate in (60, 120):
        shutil.copy2(timing[rate] / "uprising120.dll", flat_timing / f"fps{rate}.dll")
    audio_sha, patch_sha = hashlib.sha256(AUDIO_BYTES).hexdigest(), sha(apatch)
    orig_sha = hashlib.sha256(ORIGINAL_AUDIO_BYTES).hexdigest()

    def source_digest():
        h = hashlib.sha256()
        for p in sorted(src.rglob("*")):
            h.update(str(p.relative_to(src)).encode())
            if p.is_symlink():
                h.update(os.readlink(p).encode())
            elif p.is_dir():
                pass
            else:
                h.update(p.read_bytes())
        return h.hexdigest()

    B.REPORTS = reports  # keep reports inside the temp area
    before = source_digest()

    # -- full build ---------------------------------------------------------
    out = base / "RA3.app"
    ns = args_for(src, out, tools / "RA3Launcher", icon, tools / "display-mode",
                  audio=amod, patch=apatch)
    rp = B.build_app(ns, home=home)
    assert out.is_dir() and rp.is_file() and reports in rp.parents, "bundle/report missing"
    res = out / "Contents/Resources"
    assert (out / "Contents/MacOS/RA3Launcher").read_text() == "LAUNCHER-BIN"
    assert os.access(out / "Contents/MacOS/RA3Launcher", os.X_OK), "launcher not executable"
    assert (res / "display-mode").read_text() == "HELPER-BIN" and (res / "AppIcon.icns").read_text() == "ICON-DATA"
    m = json.loads((res / "manifest.json").read_text())
    assert m["schema"] == B.SCHEMA and m["version"] == "1.0" and m["local_only"] is True
    assert "data_version" not in m and "timing_pins" not in m and "frame_rate_profiles" not in m
    assert m["bundle"]["id"] == B.BUNDLE_ID and m["bundle"]["executable"] == "RA3Launcher"
    assert m["runtime_pins"] == {**pins["runtime_pins"], B.AUDIO_PIN: audio_sha}, \
        "runtime pins must equal candidate pins plus the audio overlay pin"
    assert m["game_pins"] == pins["game_pins"]
    assert m["payload_pins"] == pins["payload_pins"], "flat pin contract"
    assert "_runtime_root" not in m, "audit-only key leaked into manifest"
    # -- default fixed build: existing metadata untouched -------------------
    assert "presentation" not in m and "display_plan" not in m, \
        "default fixed build must not gain candidate metadata"
    assert "display_plan" not in m["layout"] and not (res / "display-plan").exists(), \
        "default fixed build must not stage a display-plan helper"
    mtext = (res / "manifest.json").read_text()
    assert str(src) not in mtext and str(base) not in mtext, "manifest not portable"
    pt = res / "prefix-template"
    dc = pt / "drive_c"
    for gone in ("owned-top", "u60-metal-x", "u60-viewport-y"):
        assert not (dc / gone).exists(), f"{gone} not excluded"
    assert (dc / "u60-mtld3d/Uprising60.exe").is_file(), "accepted payload home dropped"
    data = dc / "ra3-mtld3d/Data"
    assert not (data / "mtld3d-logs").exists() and not (data / "ra3ep1_1.0.game.dxvk-cache").exists()
    assert not (data / "mtld3d_shaders.bin.lock").exists() and (data / "mtld3d_shaders.bin").is_file()
    fonts = dc / "windows/Fonts"
    assert (fonts / "marlett.ttf").read_text() == "MARLETT-DATA"
    assert (fonts / "symbol.ttf").read_text() == "SYMBOL-DATA"
    assert (fonts / "NotoSansCJKsc-Regular.otf").read_text() == "NOTO-REG-DATA"
    assert m["font_pins"] == {"NotoSansCJKsc-Regular.otf": sha(fonts / "NotoSansCJKsc-Regular.otf"),
                              "marlett.ttf": sha(fonts / "marlett.ttf"),
                              "symbol.ttf": sha(fonts / "symbol.ttf")}
    links = sorted(p.name for p in (pt / "dosdevices").iterdir())
    assert links == ["c:"], f"dosdevices not cleaned for signed bundle: {links}"
    assert not (pt / "system 2.reg").exists(), "duplicate registry backup kept"
    sysreg = (pt / "system.reg").read_bytes()
    assert esc("C:\\windows\\Fonts\\marlett.ttf").encode() in sysreg, "font ref not rewritten"
    assert b"Z:\\\\System\\\\Library\\\\Fonts\\\\Geneva.ttf" in sysreg, "portable system font ref lost"
    assert str(src).encode() not in sysreg and b"Z:\\\\Users" not in sysreg, "machine path leaked"
    assert (res / "wine/lib/link.so").is_symlink(), "wine internal symlink not preserved"
    # -- audio overlay: exact built module, pin, provenance, build 2 ---------
    audio_dst = res / "wine/lib/wine/x86_64-unix/winecoreaudio.so"
    assert audio_dst.is_file() and audio_dst.read_bytes() == AUDIO_BYTES, "audio overlay not exact"
    assert m["runtime_pins"][B.AUDIO_PIN] == audio_sha, "audio pin missing/wrong in manifest"
    assert m["audio"]["sha256"] == audio_sha and m["audio"]["bytes"] == len(AUDIO_BYTES)
    assert m["audio"]["patch_sha256"] == patch_sha and m["audio"]["env"] == "RA3_NO_AUDIO_CAPTURE=1"
    assert m["audio"]["marker"] == "RA3_NO_AUDIO_CAPTURE"
    assert str(base) not in json.dumps(m["audio"]), "audio provenance not portable"
    assert m["audio_capture"] == B.AUDIO_CAPTURE_FIELD, "audio_capture field missing"
    assert m["privacy_usage_requests"] == B.PRIVACY_FIELD, "privacy field missing"
    assert m["bundle"]["build_version"] == "2", "manifest build_version not build 2"
    pl = plistlib.load((out / "Contents/Info.plist").open("rb"))
    assert pl["CFBundleVersion"] == "2", "Info.plist CFBundleVersion not build 2"
    assert pl["CFBundleIdentifier"] == B.BUNDLE_ID, "default Info.plist id changed"
    rep = json.loads(rp.read_text())
    ov = [a for a in rep["actions"] if a["action"] == "audio-module-overlay"]
    assert ov and ov[0]["replaced_sha256"] == orig_sha, "overlay did not replace the original module"
    assert ov[0]["sha256"] == audio_sha and rep["audio"]["source_patch"].endswith("wine-no-audio-capture.patch")
    assert rep["clone"]["prefix"] in ("apfs-clonefile", "copytree-symlinks")
    assert any(a["action"] == "audit" and "clean" in a["result"] for a in rep["actions"]), "no audit entry"
    assert before == source_digest(), "SOURCE TREE WAS MUTATED"

    # -- prepare-layout without launcher -----------------------------------
    out2 = base / "RA3-stage.app"
    ns2 = args_for(src, out2, base / "absent-launcher", icon, tools / "display-mode",
                   prepare=True, audio=amod, patch=apatch)
    B.build_app(ns2, home=home)
    assert not (out2 / "Contents/MacOS").exists() and (out2 / "Contents/Resources/manifest.json").is_file()
    assert (out2 / "Contents/Resources/wine/lib/wine/x86_64-unix/winecoreaudio.so"
            ).read_bytes() == AUDIO_BYTES, "prepare-layout must still overlay the audio module"

    # -- adaptive safe-area candidate ---------------------------------------
    dplan_before = (sha(dplan), oct(dplan.stat().st_mode & 0o777))
    outa = base / "RA3-Adaptive.app"
    nsa = args_for(src, outa, tools / "RA3Launcher", icon, tools / "display-mode",
                   audio=amod, patch=apatch, bundle_id=B.ADAPTIVE_BUNDLE_ID,
                   presentation="safe-area", display_plan=dplan,
                   timing_60=timing[60], timing_120=timing[120])
    rpa = B.build_app(nsa, home=home,
                      known_static=(sha(src / "prefix/drive_c/ra3-mtld3d/Data/StaticStream.big"),
                                    B.KNOWN_NEW_STATIC))
    resa = outa / "Contents/Resources"
    ma = json.loads((resa / "manifest.json").read_text())
    pla = plistlib.load((outa / "Contents/Info.plist").open("rb"))
    assert ma["data_version"] == "1.0" and ma["version"] == "1.2.0"
    assert ma["bundle"]["short_version"] == "1.2.0" and ma["bundle"]["build_version"] == "3"
    assert ma["timing_pins"] == {
        "Uprising60.exe": sha(resa / "timing/Uprising60.exe"),
        "fps60.dll": sha(resa / "timing/fps60.dll"),
        "fps120.dll": sha(resa / "timing/fps120.dll"),
    }
    assert set(ma["frame_rate_profiles"]) == {"60", "120"}
    assert ma["default_frame_rate"] == 60
    assert pla["CFBundleShortVersionString"] == "1.2.0" and pla["CFBundleVersion"] == "3"
    assert ma["bundle"]["id"] == B.ADAPTIVE_BUNDLE_ID, "adaptive manifest bundle id wrong"
    assert set(ma["frame_rate_profiles"]) == {"60", "120"}
    assert ma["frame_rate_profiles"] == {
        "60": {"dll": "fps60.dll", "log": "uprising120.log"},
        "120": {"dll": "fps120.dll", "log": "uprising120.log"},
    }
    assert "resource_updates" not in ma, "unrecognized source StaticStream must not claim an upgrade"
    assert pla["CFBundleIdentifier"] == B.ADAPTIVE_BUNDLE_ID, "adaptive Info.plist id wrong"
    assert B.BUNDLE_ID != B.ADAPTIVE_BUNDLE_ID and pl["CFBundleIdentifier"] != pla["CFBundleIdentifier"], \
        "candidate must carry a unique bundle id (separate Application Support container)"
    assert ma["layout"]["display_plan"] == "Resources/display-plan", "layout display_plan missing"
    dp = resa / "display-plan"
    assert dp.is_file() and dp.read_bytes() == DPLAN_BYTES, "display-plan helper not copied exactly"
    assert os.access(dp, os.X_OK), "staged display-plan helper not executable"
    assert ma["display_plan"] == {"resource": "Resources/display-plan",
                                  "sha256": sha(dplan), "bytes": len(DPLAN_BYTES)}, \
        "display-plan provenance wrong"
    assert (sha(dplan), oct(dplan.stat().st_mode & 0o777)) == dplan_before, \
        "display-plan SOURCE was mutated"
    assert B.INFO_PLIST["CFBundleIdentifier"] == B.BUNDLE_ID, \
        "module-level Info.plist template was globally mutated"
    ma_text = (resa / "manifest.json").read_text()
    assert str(base) not in ma_text and str(dplan) not in ma_text, "adaptive manifest not portable"
    repa = json.loads(rpa.read_text())
    assert repa["presentation"] == {"mode": "safe-area", "bundle_id": B.ADAPTIVE_BUNDLE_ID}, \
        "report presentation record wrong"

    runtime_out = base / "RA3-Runtime.app"
    runtime_ns = args_for(src, runtime_out, tools / "RA3Launcher", icon, tools / "display-mode",
                          audio=amod, patch=apatch, bundle_id=B.ADAPTIVE_BUNDLE_ID,
                          presentation="safe-area", display_plan=dplan,
                          timing_60=timing[60], timing_120=timing[120], runtime_only=True)
    runtime_report = B.build_app(runtime_ns, home=home)
    runtime_resources = runtime_out / "Contents/Resources"
    runtime_manifest = json.loads((runtime_resources / "manifest.json").read_text())
    assert not (runtime_resources / "prefix-template/drive_c/ra3-mtld3d").exists()
    assert runtime_manifest["game_pins"] == {}
    assert runtime_manifest["timing_pins"] == {
        "Uprising60.exe": sha(runtime_resources / "timing/Uprising60.exe"),
        "fps60.dll": sha(runtime_resources / "timing/fps60.dll"),
        "fps120.dll": sha(runtime_resources / "timing/fps120.dll"),
    }
    assert "resource_updates" not in runtime_manifest
    assert runtime_report.is_file()

    flat_out = base / "RA3-FlatRuntime.app"
    flat_ns = args_for(src, flat_out, tools / "RA3Launcher", icon, tools / "display-mode",
                       audio=amod, patch=apatch, bundle_id=B.ADAPTIVE_BUNDLE_ID,
                       presentation="safe-area", display_plan=dplan,
                       timing_dir=flat_timing, runtime_only=True)
    B.build_app(flat_ns, home=home)
    flat_manifest = json.loads((flat_out / "Contents/Resources/manifest.json").read_text())
    assert flat_manifest["game_pins"] == {}
    assert flat_manifest["timing_pins"] == {
        name: sha(flat_out / "Contents/Resources/timing" / name)
        for name in ("Uprising60.exe", "fps60.dll", "fps120.dll")}
    assert (flat_out / "Contents/Resources/timing/Uprising60.exe").read_bytes() == loader_bytes

    mixed_ns = args_for(src, base / "RA3-MixedTiming.app", tools / "RA3Launcher", icon,
                        tools / "display-mode", audio=amod, patch=apatch,
                        timing_dir=flat_timing, timing_60=timing[60], timing_120=timing[120])
    expect_abort = False
    try:
        B.build_app(mixed_ns, home=home)
    except B.BuildError as error:
        expect_abort = "cannot be combined" in str(error)
    assert expect_abort and not Path(mixed_ns.output).exists()

    # -- refusals and aborts ------------------------------------------------
    def expect_abort(ns, home_=home, cleaned=True):
        try:
            B.build_app(ns, home=home_)
        except B.BuildError:
            if cleaned:
                assert not Path(ns.output).exists(), "aborted output not cleaned up"
            return
        raise AssertionError("expected BuildError")

    expect_abort(args_for(src, out, tools / "RA3Launcher", icon, tools / "display-mode",
                          audio=amod, patch=apatch),
                 cleaned=False)  # refuse existing target; it must be left alone
    (src / "prefix/drive_c/stray.txt").symlink_to("/etc/hosts")
    expect_abort(args_for(src, base / "X1.app", tools / "RA3Launcher", icon, tools / "display-mode",
                          audio=amod, patch=apatch))
    (src / "prefix/drive_c/stray.txt").unlink()
    (src / "prefix/userdef.reg").write_bytes(b'"X"="Z:\\\\Users\\\\tester\\\\Documents\\\\secret.bin"\\n')
    expect_abort(args_for(src, base / "X2.app", tools / "RA3Launcher", icon, tools / "display-mode",
                          audio=amod, patch=apatch))
    (src / "prefix/userdef.reg").write_bytes(b"[Key]\\n")
    (home / "Library/Fonts/NotoSansCJKsc-Regular.otf").unlink()  # referenced font missing
    expect_abort(args_for(src, base / "X3.app", tools / "RA3Launcher", icon, tools / "display-mode",
                          audio=amod, patch=apatch))
    # audio inputs: absent module, unpatched module content, absent patch
    expect_abort(args_for(src, base / "X4.app", tools / "RA3Launcher", icon, tools / "display-mode",
                          audio=fixtures / "absent.so", patch=apatch))
    bad = fixtures / "unpatched.so"
    bad.write_bytes(b"\xcf\xfa\xed\xfe" + b"ORIGINAL-BINARY-WITHOUT-MARKER")
    expect_abort(args_for(src, base / "X5.app", tools / "RA3Launcher", icon, tools / "display-mode",
                          audio=bad, patch=apatch))
    bad.unlink()
    expect_abort(args_for(src, base / "X6.app", tools / "RA3Launcher", icon, tools / "display-mode",
                          audio=amod, patch=fixtures / "absent.patch"))

    # -- candidate contract guards: must abort BEFORE any output exists ------
    expect_abort(args_for(src, base / "A1.app", tools / "RA3Launcher", icon, tools / "display-mode",
                          audio=amod, patch=apatch,
                          presentation="safe-area"))  # safe-area without display-plan
    expect_abort(args_for(src, base / "A2.app", tools / "RA3Launcher", icon, tools / "display-mode",
                          audio=amod, patch=apatch, presentation="safe-area",
                          bundle_id=B.BUNDLE_ID, display_plan=dplan))  # default id reused for adaptive
    expect_abort(args_for(src, base / "A3.app", tools / "RA3Launcher", icon, tools / "display-mode",
                          audio=amod, patch=apatch, presentation="safe-area",
                          bundle_id="com.example.Other", display_plan=dplan))  # arbitrary id refused
    expect_abort(args_for(src, base / "A4.app", tools / "RA3Launcher", icon, tools / "display-mode",
                          audio=amod, patch=apatch,
                          bundle_id=B.ADAPTIVE_BUNDLE_ID))  # adaptive id with fixed presentation
    expect_abort(args_for(src, base / "A5.app", tools / "RA3Launcher", icon, tools / "display-mode",
                          audio=amod, patch=apatch, presentation="safe-area",
                          bundle_id=B.ADAPTIVE_BUNDLE_ID,
                          display_plan=fixtures / "absent-plan-helper"))  # helper missing
    badplan = fixtures / "notmacho-plan-helper"
    badplan.write_bytes(b"NOT-A-MACHO-IMAGE")
    badplan.chmod(0o755)
    expect_abort(args_for(src, base / "A6.app", tools / "RA3Launcher", icon, tools / "display-mode",
                          audio=amod, patch=apatch, presentation="safe-area",
                          bundle_id=B.ADAPTIVE_BUNDLE_ID, display_plan=badplan))  # not Mach-O
    badplan.unlink()
    noexe = fixtures / "noexec-plan-helper"
    noexe.write_bytes(DPLAN_BYTES)
    noexe.chmod(0o644)
    expect_abort(args_for(src, base / "A7.app", tools / "RA3Launcher", icon, tools / "display-mode",
                          audio=amod, patch=apatch, presentation="safe-area",
                          bundle_id=B.ADAPTIVE_BUNDLE_ID, display_plan=noexe))  # not executable
    noexe.unlink()
    expect_abort(args_for(src, base / "A8.app", tools / "RA3Launcher", icon, tools / "display-mode",
                          audio=amod, patch=apatch,
                          bundle_id="org.ra3mac../evil"))  # path characters in bundle id
    assert before == source_digest(), "SOURCE TREE WAS MUTATED during aborts"

    # -- CLI parses under -I ------------------------------------------------
    subprocess.run([sys.executable, "-I", str(Path(__file__).resolve().parent / "build_app.py"), "--help"],
                   check=True, capture_output=True)
    shutil.rmtree(base)
    print("ALL SELF-TESTS PASSED")
    return 0

if __name__ == "__main__":
    sys.exit(main())
