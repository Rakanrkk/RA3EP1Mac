#!/usr/bin/env python3
"""Assemble the v1.2.0 60/120 FPS candidate from the installed app.

Clones the verified installed Adaptive app into a NEW destination under a Caches
directory (APFS clonefile, fallback copytree symlinks=True), then mutates the
DESTINATION ONLY:
  - replaces Contents/MacOS/RA3Launcher with the explicit --launcher Mach-O input,
  - stages exactly the 60 and 120 PE timing payloads as fps60.dll and fps120.dll,
    plus the accepted Uprising60.exe loader under Resources/timing/,
  - replaces ONLY the template Data/StaticStream.big with the user-accepted light
    variant (bound to the ra3-light-diagnostic-v1 report, the known accepted source
    hash and the known accepted variant hash),
  - refreshes Info.plist to CFBundleShortVersionString 1.2.0 / CFBundleVersion 3,
    the Adaptive bundle id (and executable name) unchanged for save compatibility,
  - writes the launcher manifest contract: version 1.2.0, data_version 1.0,
    exactly these timing pins: Uprising60.exe, fps60.dll and fps120.dll;
    profiles 60/120 (each logs to uprising120.log), default_frame_rate 60,
    resource_updates for Data/StaticStream.big, and game_pins refreshed ONLY for
    StaticStream.big (payload_pins and all other pins stay byte-identical, so the
    legacy template prefix contract remains valid).

Every input is validated BEFORE the clone; any failure leaves the source app
untouched and creates no destination. The source is only ever read (no in-place
mutation, no hardlinks); user data under Application Support is never read,
cloned or written. Signing stays with the existing sign_app.py step, so the
report sets signature_refresh_required=true and the stale signature_policy claim
is removed from the manifest until that step re-signs. Source paths are recorded
only in the external report; the manifest keeps portable metadata only.

Usage:
python3 -I packaging/macos/build_multirate_app.py \
  --output ~/Library/Caches/<org>/Candidate.app --launcher <compiled RA3Launcher> \
  --timing-60 DIR --timing-120 DIR \
  --light-variant <ra3-light-diagnostic-v1 report> \
  --reports-dir ~/Library/Caches/<org>/reports
"""
import argparse, ctypes, ctypes.util, errno, hashlib, json, os, plistlib, shutil, stat, subprocess, sys, time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DEFAULT_SOURCE_APP = Path("/Applications/红警3起义时刻.app")
DEFAULT_LOADER = None
MANIFEST_SCHEMA = "ra3mac-app-v1"          # unknown source manifest schemas abort
LIGHT_SCHEMA = "ra3-light-diagnostic-v1"   # unknown light-variant report schemas abort
REPORT_SCHEMA = "ra3mac-multirate-build-v1"
ADAPTIVE_BUNDLE_ID = "org.ra3mac.UprisingMetal.Adaptive"  # save-compat identity, unchanged
LAUNCHER_NAME = "RA3Launcher"
VERSION, DATA_VERSION, BUILD_VERSION = "1.2.0", "1.0", "3"
RATES = (60, 120)                         # only the two release profiles are accepted
DEFAULT_RATE = 60
TIMING_LOG = "uprising120.log"             # every profile logs to the same name
STATIC_REL = "Data/StaticStream.big"
# User-accepted resource identity (ACCEPTED-120-20261019): the installed app's
# StaticStream.big must be exactly KNOWN_OLD_STATIC, the light variant exactly
# KNOWN_NEW_STATIC. Tests may inject fixture hashes via build_multirate(known_static=...).
KNOWN_OLD_STATIC = "0daeb2d3aedfe7a83d75dc61732af8232de3ffb13603882aa5e216f0b9853834"
KNOWN_NEW_STATIC = "785f42ad271e6da824c86a938ef79baf1ae99f7bba9aab12afba6c3a9f13a634"
KNOWN_TIMING_SHA256 = {
    60: "9017171020424cf6f040b8ea8802016ccaada70049b147fed25d48afe681c84b",
    120: "1ede43dc679bcf2f91c19dd64c8695f8898aa75574c04e25921f4c93c9d0c7c3",
}
MACHO_MAGICS = (b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe", b"\xfe\xed\xfa\xcf",
                b"\xfe\xed\xfa\xce", b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca",
                b"\xca\xfe\xba\xbf", b"\xbf\xba\xfe\xca")
PE_MACHINE_I386 = 0x014C
REL_TIMING = "Contents/Resources/timing"
REL_MANIFEST = "Contents/Resources/manifest.json"
REL_TEMPLATE = "Contents/Resources/prefix-template"
REL_GAME = REL_TEMPLATE + "/drive_c/ra3-mtld3d"
REL_PAYLOAD = REL_TEMPLATE + "/drive_c/u60-mtld3d"
REL_WINE = "Contents/Resources/wine"
REL_FONTS = REL_TEMPLATE + "/drive_c/windows/Fonts"
# The only byte deltas a successful build may leave versus the source app.
EXPECTED_REPLACED = ("Contents/Info.plist", "Contents/MacOS/" + LAUNCHER_NAME,
                     REL_MANIFEST, REL_GAME + "/" + STATIC_REL)
EXPECTED_ADDED = ("Contents/Resources/timing",
                  *(REL_TIMING + "/" + n for n in ("Uprising60.exe", "fps60.dll", "fps120.dll")))

class BuildError(RuntimeError):
    pass

def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()

def clone_dir(src: Path, dst: Path) -> str:
    """Recursive APFS COW clone of the source app; fail if cloning is unavailable.

    clonefile is a copy-on-write clone, not a hardlink. Later destination writes
    never touch the source; release assembly must not silently copy a huge app.
    """
    try:
        libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
        cf = libc.clonefile
        cf.restype, cf.argtypes = ctypes.c_int, [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int]
        if cf(os.fsencode(src), os.fsencode(dst), 0) == 0:
            return "apfs-clonefile"
        err = ctypes.get_errno()
    except AttributeError as error:
        raise BuildError("APFS clonefile is unavailable; refusing a full app copy") from error
    if err in (errno.EXDEV, errno.ENOTSUP, errno.EPERM, errno.EMLINK):
        raise BuildError(f"APFS clonefile unavailable: {os.strerror(err)}; refusing a full app copy")
    raise BuildError(f"clonefile failed: {os.strerror(err)} for {src} -> {dst}")

def codesign_verify_deep(app: Path) -> None:
    """Deep strict signature verification of the source app before cloning."""
    r = subprocess.run(["/usr/bin/codesign", "--verify", "--deep", "--strict", str(app)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise BuildError(f"source app fails deep codesign verification: {app}\n{r.stderr.strip()}")

def is_macho(data: bytes) -> bool:
    return data[:4] in MACHO_MAGICS

def pe32_machine(data: bytes):
    """Return 'i386' for a valid 32-bit PE image, else None (never raises)."""
    if len(data) < 0x46 or data[:2] != b"MZ":
        return None
    off = int.from_bytes(data[0x3C:0x40], "little")
    if off < 0x40 or off + 6 > len(data) or data[off:off + 4] != b"PE\x00\x00":
        return None
    if int.from_bytes(data[off + 4:off + 6], "little") == PE_MACHINE_I386:
        return "i386"
    return None

def pin_errors(pins: dict, base: Path) -> list:
    errs = []
    for rel, want in sorted(pins.items()):
        p = base / rel
        if not p.is_file():
            errs.append(f"missing pin {p}")
        elif sha256(p) != want:
            errs.append(f"hash mismatch {p}")
    return errs

def inside(target: Path, root: Path) -> bool:
    try:
        target.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False

def tree_snapshot(root: Path) -> dict:
    """Structural identity of a tree: rel path -> (kind, size-or-linktarget)."""
    snap = {}
    for dp, dns, fns in os.walk(root, followlinks=False):
        for n in dns + fns:
            p = Path(dp) / n
            st = os.lstat(p)
            rel = str(p.relative_to(root))
            if stat.S_ISLNK(st.st_mode):
                snap[rel] = ("link", os.readlink(p))
            elif stat.S_ISDIR(st.st_mode):
                snap[rel] = ("dir", None)
            else:
                snap[rel] = ("file", st.st_size)
    return snap

def validate_output_path(out: Path, src: Path) -> None:
    if out.suffix != ".app":
        raise BuildError(f"--output must end in .app: {out}")
    if out.exists() or out.is_symlink():
        raise BuildError(f"refusing existing target (destination must be absent): {out}")
    if not out.is_absolute():
        raise BuildError(f"--output must be absolute: {out}")
    if not out.parent.is_dir():
        raise BuildError(f"--output parent must exist: {out.parent}")
    if not any(part == "Caches" for part in out.parts):
        raise BuildError(f"--output must live under a Caches directory: {out}")
    if any(part == "Application Support" for part in out.parts):
        raise BuildError("--output must never live under Application Support (user data stays untouched)")
    if inside(out, src) or inside(src, out) or out.resolve() == src.resolve():
        raise BuildError("--output must be disjoint from the source app")

def check_template_links(pt: Path) -> None:
    """Signed-template symlink contract: only internal c:, z: stays omitted."""
    dd = pt / "dosdevices"
    if not dd.is_dir():
        raise BuildError(f"prefix template has no dosdevices: {dd}")
    for name in sorted(os.listdir(dd)):
        p = dd / name
        t = os.readlink(p) if p.is_symlink() else None
        if name != "c:" or t is None or os.path.isabs(t) or not inside(p.parent / t, pt):
            raise BuildError(f"unexpected template dosdevices entry {name} -> {t}")
    for dp, dns, fns in os.walk(pt, followlinks=False):
        if dp == str(dd):
            dns[:] = []
        for n in list(dns) + list(fns):
            p = Path(dp) / n
            if p.is_symlink():
                t = os.readlink(p)
                if os.path.isabs(t) or not inside(p.parent / t, pt):
                    raise BuildError(f"template symlink escapes bundle: {p.relative_to(pt)} -> {t}")

def validate_timing_dir(rate: int, d: Path, expected_sha: str) -> dict:
    """PE payload + build report: exact target rate and self-consistent pin."""
    if not d.is_dir():
        raise BuildError(f"--timing-{rate} artifact directory missing: {d}")
    dll, rep = d / "uprising120.dll", d / "build-result.json"
    for p in (dll, rep):
        if not p.is_file():
            raise BuildError(f"--timing-{rate} artifact incomplete, missing {p}")
    try:
        build = json.loads(rep.read_text())
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise BuildError(f"--timing-{rate} build-result.json is not valid JSON: {e}")
    if not isinstance(build, dict) or "target_updates_hz" not in build or "dll_sha256" not in build:
        raise BuildError(f"--timing-{rate} build-result.json lacks target_updates_hz/dll_sha256")
    hz = build["target_updates_hz"]
    if not isinstance(hz, int) or isinstance(hz, bool) or hz != rate:
        raise BuildError(f"--timing-{rate} build report target_updates_hz={hz!r} != {rate}")
    data = dll.read_bytes()
    if pe32_machine(data) != "i386":
        raise BuildError(f"--timing-{rate} payload is not a 32-bit PE image: {dll}")
    sha = hashlib.sha256(data).hexdigest()
    if sha != expected_sha:
        raise BuildError(f"--timing-{rate} DLL SHA-256 is not the accepted release payload: {sha}")
    if sha != build["dll_sha256"]:
        raise BuildError(f"--timing-{rate} DLL differs from its build report pin: {dll}")
    return {"rate": rate, "artifact_dir": str(d), "target_updates_hz": hz,
            "dll_sha256": sha, "bytes": len(data), "staged_as": f"fps{rate}.dll"}

def validate_light_variant(report_path: Path, src_pins: dict, known: tuple) -> dict:
    """Bind the accepted light variant: schema, known pins, and the real file."""
    old_static, new_static = known
    if not report_path.is_file():
        raise BuildError(f"light-variant report missing: {report_path}")
    try:
        rep = json.loads(report_path.read_text())
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise BuildError(f"light-variant report is not valid JSON: {e}")
    if not isinstance(rep, dict) or rep.get("schema") != LIGHT_SCHEMA:
        raise BuildError(f"unknown light-variant report schema "
                         f"{rep.get('schema')!r}, expected {LIGHT_SCHEMA}: {report_path}")
    for key in ("game_root", "game_pins", "accepted_game_pins"):
        if key not in rep:
            raise BuildError(f"light-variant report lacks {key}: {report_path}")
    if rep["game_pins"].get(STATIC_REL) != new_static:
        raise BuildError(f"light-variant report StaticStream pin is not the accepted "
                         f"variant hash: {rep['game_pins'].get(STATIC_REL)}")
    if rep["accepted_game_pins"].get(STATIC_REL) != src_pins.get(STATIC_REL):
        raise BuildError("light-variant source StaticStream identity differs from the verified source app")
    if rep["accepted_game_pins"] != src_pins:
        raise BuildError("light-variant report was not prepared from the verified source resources")
    for name, sha in rep["game_pins"].items():
        if name != STATIC_REL and rep["accepted_game_pins"].get(name) != sha:
            raise BuildError(f"light-variant report changes unrelated resource {name}")
    game_root = Path(rep["game_root"])
    big = game_root / STATIC_REL
    if not big.is_file():
        raise BuildError(f"light-variant StaticStream.big missing at report game_root: {big}")
    if sha256(big) != new_static:
        raise BuildError(f"light-variant StaticStream.big does not hash to the accepted "
                         f"variant identity: {big}")
    return {"report": str(report_path), "schema": rep.get("schema"), "game_root": str(game_root),
            "sha256": new_static, "previous_sha256": old_static, "source_big": str(big)}

def load_source(src: Path, known: tuple) -> dict:
    """Read and pin-verify the source app; never mutate it."""
    if not src.is_dir() or src.suffix != ".app":
        raise BuildError(f"source app is not an .app bundle: {src}")
    man_path = src / REL_MANIFEST
    if not man_path.is_file():
        raise BuildError(f"source manifest missing: {man_path}")
    try:
        man = json.loads(man_path.read_text())
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise BuildError(f"source manifest is not valid JSON: {e}")
    if man.get("schema") != MANIFEST_SCHEMA:
        raise BuildError(f"unknown source manifest schema {man.get('schema')!r}, "
                         f"expected {MANIFEST_SCHEMA}")
    if man.get("bundle", {}).get("id") != ADAPTIVE_BUNDLE_ID:
        raise BuildError(f"source bundle id must stay {ADAPTIVE_BUNDLE_ID} for save "
                         f"compatibility, got {man.get('bundle', {}).get('id')!r}")
    plist = plistlib.load((src / "Contents/Info.plist").open("rb"))
    if plist.get("CFBundleIdentifier") != man["bundle"]["id"]:
        raise BuildError("source Info.plist bundle id disagrees with the manifest")
    if plist.get("CFBundleExecutable") != LAUNCHER_NAME:
        raise BuildError(f"source CFBundleExecutable must be {LAUNCHER_NAME}")
    res = src / "Contents/Resources"
    groups = (("game_pins", src / REL_GAME), ("payload_pins", src / REL_PAYLOAD),
              ("runtime_pins", src / REL_WINE), ("font_pins", src / REL_FONTS))
    for group, base in groups:
        errs = pin_errors(man.get(group, {}), base)
        if errs:
            raise BuildError(f"source {group} validation failed:\n  " + "\n  ".join(errs))
    source_static = man["game_pins"].get(STATIC_REL)
    if source_static not in known:
        raise BuildError(f"installed StaticStream.big is not a recognized accepted identity: {source_static}")
    dp = man.get("display_plan", {})
    if dp.get("resource"):
        helper = res / dp["resource"].removeprefix("Resources/")
        if not helper.is_file() or sha256(helper) != dp.get("sha256"):
            raise BuildError(f"source display-plan helper pin mismatch: {helper}")
    codesign_verify_deep(src)
    return {"manifest": man, "plist": plist, "game_pins": man["game_pins"],
            "payload_pins": man["payload_pins"], "source_static": source_static}

def validate_inputs(args, known: tuple, timing_pins: dict = None) -> dict:
    """Validate every external input; called strictly before the clone."""
    timing_pins = timing_pins or KNOWN_TIMING_SHA256
    src = Path(args.source_app).resolve()
    out = Path(args.output).absolute()
    validate_output_path(out, src)
    loaded = load_source(src, known)
    launcher = Path(args.launcher).resolve()
    if not launcher.is_file():
        raise BuildError(f"launcher input missing: {launcher}")
    ldata = launcher.read_bytes()
    if not is_macho(ldata):
        raise BuildError(f"launcher input is not a Mach-O image: {launcher}")
    loader_arg = getattr(args, "loader", None)
    loader = Path(loader_arg).resolve() if loader_arg else src / REL_PAYLOAD / "Uprising60.exe"
    if not loader.is_file():
        raise BuildError(f"accepted loader missing: {loader}")
    lddata = loader.read_bytes()
    if pe32_machine(lddata) != "i386":
        raise BuildError(f"accepted loader is not a 32-bit PE image: {loader}")
    loader_sha = hashlib.sha256(lddata).hexdigest()
    if loader_sha != loaded["payload_pins"]["Uprising60.exe"]:
        raise BuildError(f"loader does not match the accepted payload pin "
                         f"Uprising60.exe in the source manifest: {loader}")
    sidecar = loader.parent / "SHA256.json"
    if sidecar.is_file():
        recorded = json.loads(sidecar.read_text()).get("Uprising60.exe")
        if recorded is not None and recorded != loader_sha:
            raise BuildError(f"loader disagrees with recorded identity {sidecar}")
    timing = {r: validate_timing_dir(r, Path(getattr(args, f"timing_{r}")).resolve(),
                                      timing_pins[r]) for r in RATES}
    light = validate_light_variant(Path(args.light_variant).resolve(),
                                   loaded["game_pins"], known)
    return {"src": src, "out": out, "manifest": loaded["manifest"],
            "launcher": {"path": str(launcher), "sha256": hashlib.sha256(ldata).hexdigest(),
                         "bytes": len(ldata)},
            "loader": {"path": str(loader), "sha256": loader_sha, "bytes": len(lddata)},
            "timing": timing, "light": light, "source_static": loaded["source_static"]}

def apply_manifest(man: dict, inputs: dict, known: tuple) -> dict:
    """Write the versioned two-rate launcher contract."""
    old_static, new_static = known
    man["version"] = VERSION
    man["data_version"] = DATA_VERSION
    man["bundle"]["short_version"] = VERSION
    man["bundle"]["build_version"] = BUILD_VERSION
    man["timing_pins"] = {"Uprising60.exe": inputs["loader"]["sha256"],
                          **{f"fps{r}.dll": inputs["timing"][r]["dll_sha256"] for r in RATES}}
    man["frame_rate_profiles"] = {str(r): {"dll": f"fps{r}.dll", "log": TIMING_LOG}
                                  for r in RATES}
    man["default_frame_rate"] = DEFAULT_RATE
    man["resource_updates"] = {STATIC_REL: {"previous_sha256": [old_static],
                                            "sha256": new_static}}
    if man["game_pins"].get(STATIC_REL) != inputs["source_static"]:
        raise BuildError("source game_pins drifted before the StaticStream refresh")
    man["game_pins"] = {**man["game_pins"], STATIC_REL: new_static}
    # The old claim referred to the replaced launcher bytes; sign_app.py writes a
    # fresh signature_policy when it re-signs this bundle.
    man.pop("signature_policy", None)
    return man

def delta_audit(src_snap: dict, dst_snap: dict) -> dict:
    """Every byte-level delta must be one of the known, expected changes.

    Structural identity (paths, types, link targets, sizes) plus the pin
    verification cover the untouched tree; the small replaced files get an
    explicit byte-level proof below and StaticStream.big is bound by the
    OLD/NEW identity pins on both sides.
    """
    removed = sorted(set(src_snap) - set(dst_snap))
    added = sorted(set(dst_snap) - set(src_snap))
    changed = sorted(r for r in set(src_snap) & set(dst_snap) if src_snap[r] != dst_snap[r])
    if removed or added != sorted(EXPECTED_ADDED) or not set(changed) <= set(EXPECTED_REPLACED):
        raise BuildError("unexpected destination delta versus source:\n"
                         f"  removed: {removed}\n  added: {added}\n  changed: {changed}")
    return {"removed": removed, "added": added, "changed": changed,
            "result": "exactly the expected resource delta"}

def build_multirate(args, known_static: tuple = None, timing_pins: dict = None) -> Path:
    known = known_static or (KNOWN_OLD_STATIC, KNOWN_NEW_STATIC)
    inputs = validate_inputs(args, known, timing_pins or KNOWN_TIMING_SHA256)
    if getattr(args, "report", None) and Path(args.report).exists():
        raise BuildError(f"report already exists: {args.report}")
    src, out, man = inputs["src"], inputs["out"], dict(inputs["manifest"])
    created = False
    try:
        method = clone_dir(src, out)
        created = True
        if tree_snapshot(src) != tree_snapshot(out):
            raise BuildError("cloned destination differs structurally from the source app")
        dst_man_path = out / REL_MANIFEST
        if dst_man_path.read_bytes() != (src / REL_MANIFEST).read_bytes():
            raise BuildError("cloned manifest differs from the source manifest")
        # Destination pin verification on the fresh clone (source/dest pins).
        for group, base in (("game_pins", out / REL_GAME), ("payload_pins", out / REL_PAYLOAD),
                            ("runtime_pins", out / REL_WINE), ("font_pins", out / REL_FONTS)):
            errs = pin_errors(man.get(group, {}), base)
            if errs:
                raise BuildError(f"destination {group} validation failed:\n  " + "\n  ".join(errs))
        check_template_links(out / REL_TEMPLATE)
        actions = []
        # Stage the accepted 60 and 120 PE payloads plus the pinned loader.
        timing_dir = out / REL_TIMING
        timing_dir.mkdir()
        shutil.copy2(Path(inputs["loader"]["path"]), timing_dir / "Uprising60.exe")
        actions.append({"action": "stage", "path": "Resources/timing/Uprising60.exe",
                        "sha256": inputs["loader"]["sha256"]})
        for r in RATES:
            staged = timing_dir / f"fps{r}.dll"
            shutil.copy2(Path(inputs["timing"][r]["artifact_dir"]) / "uprising120.dll", staged)
            actions.append({"action": "stage", "path": f"Resources/timing/fps{r}.dll",
                            "sha256": inputs["timing"][r]["dll_sha256"],
                            "target_updates_hz": inputs["timing"][r]["target_updates_hz"]})
        # 2. Replace the native launcher with the explicit compiled Mach-O input.
        macos = out / "Contents/MacOS" / LAUNCHER_NAME
        replaced_launcher = sha256(macos)
        shutil.copy2(Path(inputs["launcher"]["path"]), macos)
        os.chmod(macos, 0o755)
        if sha256(macos) != inputs["launcher"]["sha256"]:
            raise BuildError("staged launcher differs from the verified input")
        actions.append({"action": "replace", "path": "MacOS/" + LAUNCHER_NAME,
                        "previous_sha256": replaced_launcher,
                        "sha256": inputs["launcher"]["sha256"]})
        # 3. Replace ONLY template StaticStream.big with the accepted variant.
        big = out / REL_GAME / STATIC_REL
        replaced_static = sha256(big)
        if replaced_static != inputs["source_static"]:
            raise BuildError("cloned StaticStream.big differs from the verified source pin")
        shutil.copy2(Path(inputs["light"]["source_big"]), big)
        if sha256(big) != known[1]:
            raise BuildError("staged StaticStream.big does not match the accepted variant")
        actions.append({"action": "replace", "path": "prefix-template/drive_c/ra3-mtld3d/"
                        + STATIC_REL, "previous_sha256": replaced_static, "sha256": known[1]})
        # Refresh version metadata while preserving the save-compatible bundle id.
        plist_path = out / "Contents/Info.plist"
        plist = plistlib.load(plist_path.open("rb"))
        plist["CFBundleShortVersionString"] = VERSION
        plist["CFBundleVersion"] = BUILD_VERSION
        if plist.get("CFBundleIdentifier") != ADAPTIVE_BUNDLE_ID:
            raise BuildError("bundle id drifted; save compatibility requires it unchanged")
        with plist_path.open("wb") as f:
            plistlib.dump(plist, f)
        # 5. Manifest contract; portable metadata only, no source paths.
        man = apply_manifest(man, inputs, known)
        man_bytes = (json.dumps(man, ensure_ascii=False, indent=1) + "\n").encode()
        banned = [str(src), inputs["light"]["game_root"], str(Path.home()), str(REPO)]
        if [b for b in banned if b.encode() in man_bytes]:
            raise BuildError("manifest would leak source-machine paths; refusing to write")
        dst_man_path.write_bytes(man_bytes)
        # Final verification: written manifest pins all hold; delta is exactly known.
        final_man = json.loads(dst_man_path.read_text())
        for group, base in (("game_pins", out / REL_GAME), ("payload_pins", out / REL_PAYLOAD),
                            ("runtime_pins", out / REL_WINE), ("font_pins", out / REL_FONTS)):
            errs = pin_errors(final_man[group], base)
            if errs:
                raise BuildError(f"final {group} verification failed:\n  " + "\n  ".join(errs))
        errs = pin_errors(final_man["timing_pins"], out / "Contents/Resources/timing")
        if errs:
            raise BuildError(f"final timing_pins verification failed:\n  " + "\n  ".join(errs))
        delta = delta_audit(tree_snapshot(src), tree_snapshot(out))
        # Byte-level proof for the small replaced files (StaticStream.big is
        # already byte-bound by the OLD source pin and the NEW final pin).
        for rel in ("Contents/Info.plist", "Contents/MacOS/" + LAUNCHER_NAME, REL_MANIFEST):
            if sha256(src / rel) == sha256(out / rel):
                raise BuildError(f"expected byte change not present: {rel}")
        byte_changes = [a for a in actions if a["action"] == "replace"]
        for name in ("Uprising60.exe", "fps60.dll", "fps120.dll"):
            p = timing_dir / name
            byte_changes.append({"action": "add", "path": "Resources/timing/" + name,
                                 "sha256": sha256(p), "bytes": p.stat().st_size})
        report = {"schema": REPORT_SCHEMA, "started": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "mode": "clone-and-restage (unsigned v1.2.0 60/120 candidate)",
                  "source_app": str(src), "output": str(out),
                  "signature_refresh_required": True,
                  "clone": {"method": method},
                  "inputs": {"launcher": inputs["launcher"], "loader": inputs["loader"],
                             "timing": {str(r): inputs["timing"][r] for r in RATES},
                             "light_variant": inputs["light"],
                             "known_static": {"previous": known[0], "new": known[1]}},
                  "byte_changes": byte_changes, "delta_audit": delta,
                  "manifest_fields": {"version": VERSION, "data_version": DATA_VERSION,
                                      "bundle": final_man["bundle"],
                                      "timing_pins": final_man["timing_pins"],
                                      "frame_rate_profiles": final_man["frame_rate_profiles"],
                                      "default_frame_rate": DEFAULT_RATE,
                                      "resource_updates": final_man["resource_updates"],
                                      "game_pins_refreshed": [STATIC_REL],
                                      "payload_pins_unchanged": True,
                                      "signature_policy_removed_until_signing": True}}
        rp = resolve_report_path(args, out)
        rp.write_text(json.dumps(report, ensure_ascii=False, indent=1) + "\n")
        return rp
    except BaseException:
        if created:
            shutil.rmtree(out, ignore_errors=True)
        raise

def resolve_report_path(args, out: Path) -> Path:
    if args.reports_dir:
        rd = Path(args.reports_dir)
        rd.mkdir(parents=True, exist_ok=True)
        rp = rd / f"{out.stem}-multirate-{time.strftime('%Y%m%d-%H%M%S')}.json"
        while rp.exists():
            rp = rp.with_name(rp.stem + "-r.json")
        return rp
    rp = Path(args.report)
    if rp.exists():
        raise BuildError(f"report already exists: {rp}")
    rp.parent.mkdir(parents=True, exist_ok=True)
    return rp

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Clone the installed Adaptive app into a new v1.2.0 two-rate candidate.")
    ap.add_argument("--source-app", default=str(DEFAULT_SOURCE_APP),
                    help="verified installed Adaptive .app (cloned, never mutated)")
    ap.add_argument("--output", required=True, help="absent .app path under a Caches directory")
    ap.add_argument("--launcher", required=True,
                    help=f"compiled {LAUNCHER_NAME} Mach-O input (replaces Contents/MacOS/{LAUNCHER_NAME})")
    ap.add_argument("--loader", default=str(DEFAULT_LOADER) if DEFAULT_LOADER else None,
                    help="accepted Uprising60.exe PE loader (defaults to the source app's pinned legacy payload)")
    for r in RATES:
        ap.add_argument(f"--timing-{r}", required=True, dest=f"timing_{r}",
                        help=f"artifact dir with uprising120.dll + build-result.json "
                             f"(target_updates_hz {r}); staged as fps{r}.dll")
    ap.add_argument("--light-variant", required=True,
                    help="accepted ra3-light-diagnostic-v1 report binding the new StaticStream.big")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--report", help="exact absent report file path")
    g.add_argument("--reports-dir", help="directory for a new timestamped report")
    args = ap.parse_args(argv)
    try:
        rp = build_multirate(args)
    except BuildError as e:
        print(f"BUILD ABORTED: {e}", file=sys.stderr)
        return 2
    print(f"OK [v1.2.0 60/120 FPS candidate, unsigned] report: {rp}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
