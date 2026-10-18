#!/usr/bin/env python3
"""Assemble the user-local RA3 Uprising Mac app bundle (org.ra3mac.UprisingMetal).

Stages Contents/Resources/{wine,prefix-template} from the accepted fixed-viewport
runtime root (build/compat-fixed-viewport) using APFS recursive COW clones
(clonefile, fallback copytree symlinks=True), applies DEST-ONLY prefix cleanup,
validates candidate-manifest pins before and after the clone, and writes the
NativeLauncher contract files: Resources/wine, Resources/prefix-template,
Resources/display-mode, Resources/manifest.json. Source tree is never mutated.

Audio: the accepted runtime root keeps the ORIGINAL winecoreaudio module; the
exact locally built disabled-capture module (RA3_NO_AUDIO_CAPTURE patch,
build/compat-fixed-viewport/wine-build/dlls/winecoreaudio.drv/winecoreaudio.so)
is overlayed into the staged wine copy destination-only, pinned as
runtime_pins["lib/wine/x86_64-unix/winecoreaudio.so"], and its provenance
(module+patch sha256, marker, env) is recorded in the manifest. The launcher
sets RA3_NO_AUDIO_CAPTURE=1. Bundle metadata is build 2 (CFBundleVersion /
build_version "2"), consistent with the installed app.

Prefix-template cleanup (destination copy only): drop test-owned drive_c dirs
(owned-*, u60-metal-*, u60-viewport-*; u60-mtld3d is the accepted payload home
and is kept), Data/mtld3d-logs, the old unsupported DXVK cache, the shader
.lock (warm mtld3d_shaders.bin is kept), a byte-identical "system 2.reg"
backup, and stale external volume/device dosdevices links (only c: is kept; z: is
removed from the signed template and created by the launcher in the writable user
prefix on first launch).
Registry values pointing at this machine's fonts are made portable: referenced
Wine-bundled (marlett/symbol) and user-Library Noto CJK fonts are materialized
into prefix drive_c/windows/Fonts and rewritten to C:\\windows\\Fonts\\<name>;
Z:\\System\\... host font refs stay (portable across macOS); any other
Z:\\Users ref aborts. Unknown external symlinks abort. Launcher compilation and
codesign are out of scope; pins record pre-codesign identity and are refreshed
by the packaging sign step before outer codesign.

Candidate packaging options: --presentation fixed (default) keeps every piece
of existing metadata unchanged. --presentation safe-area builds the adaptive
candidate: it emits manifest presentation "safe-area-v1", requires the
dedicated bundle id org.ra3mac.UprisingMetal.Adaptive (separate Application
Support container from the default bundle), and copies the native display-plan
helper (--display-plan, required) to Resources/display-plan as an executable
Mach-O. The helper is staged unsigned and unlinked here; the packaging sign
step owns linking and codesigning it. Only two launchable bundle ids exist
(the compiled launchers hard-code exactly these), so every other id is refused
before any output directory is created. --audio-module stays a per-build input
passed through unchanged; no audio behavior differs between candidates.

Usage: python3 -I packaging/macos/build_app.py --output /tmp/X.app --icon X.icns
"""
import argparse, ctypes, ctypes.util, errno, fnmatch, hashlib, json, os, plistlib, re, shutil, stat, sys, time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BUILD = REPO / "build"
DEFAULT_RUNTIME = BUILD / "compat-fixed-viewport"
DEFAULT_LAUNCHER = BUILD / "app-package-tools" / "RA3Launcher"
DEFAULT_HELPER = BUILD / "compat-display-recovery" / "display-mode"
REPORTS = BUILD / "app-package-reports"
SCHEMA, BUNDLE_ID, APP_NAME = "ra3mac-app-v1", "org.ra3mac.UprisingMetal", "红警3：起义时刻"
ADAPTIVE_BUNDLE_ID = "org.ra3mac.UprisingMetal.Adaptive"  # safe-area candidate identity
PRESENTATION_SAFE_AREA = "safe-area-v1"  # manifest presentation value for adaptive builds
BUNDLE_ID_RE = re.compile(r"[A-Za-z][A-Za-z0-9-]*(?:\.[A-Za-z][A-Za-z0-9-]*)+")
APP_BUILD = "2"  # matches the installed build-2 Info.plist (CFBundleVersion)
AUDIO_PIN = "lib/wine/x86_64-unix/winecoreaudio.so"
DEFAULT_AUDIO_MODULE = (BUILD / "compat-fixed-viewport" / "wine-build" /
                        "dlls" / "winecoreaudio.drv" / "winecoreaudio.so")
DEFAULT_AUDIO_PATCH = Path(__file__).resolve().parent / "wine-no-audio-capture.patch"
AUDIO_MARKER = b"RA3_NO_AUDIO_CAPTURE"
MACHO_MAGICS = (b"\xcf\xfa\xed\xfe", b"\xce\xfa\xed\xfe", b"\xfe\xed\xfa\xcf",
                b"\xfe\xed\xfa\xce", b"\xca\xfe\xba\xbe", b"\xbe\xba\xfe\xca",
                b"\xca\xfe\xba\xbf", b"\xbf\xba\xfe\xca")
AUDIO_CAPTURE_FIELD = "Disabled in Wine backend before native input calls; RA3_NO_AUDIO_CAPTURE=1"
PRIVACY_FIELD = "No microphone request"
EXCLUDE_GLOBS = ("owned-*", "u60-metal-*", "u60-viewport-*")
REG_FILES = ("system.reg", "user.reg", "userdef.reg", "system 2.reg")
STALE_VOL = ("/Volumes/", "/dev/")
WIN_FONTS_ESC = ("C:\\windows\\Fonts\\").replace("\\", "\\\\").encode()  # file-byte form

class BuildError(RuntimeError):
    pass

def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()

def esc_dos(posix_or_dos: str) -> bytes:
    """Windows-escape a logical DOS path into the byte form stored in .reg files."""
    return posix_or_dos.replace("\\", "\\\\").encode()

def tree_stats(root: Path) -> dict:
    files = dirs = links = nbytes = 0
    for dp, dns, fns in os.walk(root, followlinks=False):
        for n in dns + fns:
            st = os.lstat(os.path.join(dp, n))
            if stat.S_ISLNK(st.st_mode):
                links += 1
            elif stat.S_ISDIR(st.st_mode):
                dirs += 1
            else:
                files += 1
                nbytes += st.st_size
    return {"files": files, "dirs": dirs, "symlinks": links, "bytes": nbytes}

def clone_dir(src: Path, dst: Path) -> str:
    """Recursive APFS COW clone; falls back to a byte/symlink-preserving copy."""
    try:
        libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
        cf = libc.clonefile
        cf.restype, cf.argtypes = ctypes.c_int, [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int]
        if cf(os.fsencode(src), os.fsencode(dst), 0) == 0:
            return "apfs-clonefile"
        err = ctypes.get_errno()
        if err not in (errno.EXDEV, errno.ENOTSUP, errno.EPERM, errno.EMLINK):
            raise BuildError(f"clonefile failed: {os.strerror(err)} for {src} -> {dst}")
    except AttributeError:
        pass
    shutil.copytree(src, dst, symlinks=True)
    return "copytree-symlinks"

def pin_errors(pins: dict, base: Path) -> list:
    errs = []
    for rel, want in sorted(pins.items()):
        p = base / rel
        if not p.is_file():
            errs.append(f"missing pin {base / rel}")
        elif sha256(p) != want:
            errs.append(f"hash mismatch {base / rel}")
    return errs

def inside(target: Path, root: Path) -> bool:
    try:
        target.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False

def audio_provenance(module: Path, patch: Path) -> dict:
    """Validate the locally built disabled-capture winecoreaudio module.

    The manifest pin is the authority, so the module must exist, be a Mach-O
    image, and carry the no-capture patch marker (an original, unpatched
    winecoreaudio.so is refused). The patch file hash is recorded as provenance.
    """
    if not module.is_file():
        raise BuildError(
            f"disabled-capture audio module missing: {module}\n"
            f"build it with: make -C build/compat-fixed-viewport/wine-build "
            f"dlls/winecoreaudio.drv/winecoreaudio.so (source patch: "
            f"packaging/macos/wine-no-audio-capture.patch)")
    data = module.read_bytes()
    if data[:4] not in MACHO_MAGICS:
        raise BuildError(f"audio module is not a Mach-O image: {module}")
    if AUDIO_MARKER not in data:
        raise BuildError(f"audio module lacks the no-capture patch marker "
                         f"{AUDIO_MARKER.decode()}; refusing possible original "
                         f"winecoreaudio.so: {module}")
    if not patch.is_file():
        raise BuildError(f"audio patch provenance missing: {patch}")
    return {"module": "Resources/wine/" + AUDIO_PIN,
            "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data),
            "patch": "packaging/macos/wine-no-audio-capture.patch",
            "patch_sha256": sha256(patch), "marker": AUDIO_MARKER.decode(),
            "env": "RA3_NO_AUDIO_CAPTURE=1"}

def overlay_audio_module(res: Path, module: Path, prov: dict, log: list) -> None:
    """Copy the exact built module over the staged wine copy (DEST only)."""
    dst = res / "wine" / AUDIO_PIN
    replaced = sha256(dst) if dst.is_file() else None
    shutil.copy2(module, dst)
    log.append({"action": "audio-module-overlay", "path": prov["module"],
                "sha256": prov["sha256"], "bytes": prov["bytes"],
                "replaced_sha256": replaced, "env": prov["env"],
                "note": "exact locally built disabled-capture module; "
                        "accepted runtime root keeps its original"})

def validate_options(bundle_id: str, presentation: str, display_plan) -> None:
    """Candidate option contract, enforced before any output directory exists.

    The compiled launchers hard-code exactly two bundle identities, so the
    builder refuses every other id rather than emitting an unlaunchable bundle:
    org.ra3mac.UprisingMetal with --presentation fixed, and
    org.ra3mac.UprisingMetal.Adaptive with --presentation safe-area (which also
    requires --display-plan). bundle-id is additionally checked as a strict
    reverse-DNS name (letter-started dot segments, no path or special
    characters).
    """
    if presentation not in ("fixed", "safe-area"):
        raise BuildError(f"--presentation must be fixed or safe-area, got {presentation!r}")
    if not BUNDLE_ID_RE.fullmatch(bundle_id):
        raise BuildError(f"--bundle-id is not a strict reverse-DNS name (letters, digits "
                         f"and hyphens only, no path characters): {bundle_id!r}")
    if presentation == "safe-area":
        if bundle_id != ADAPTIVE_BUNDLE_ID:
            raise BuildError(f"--presentation safe-area requires --bundle-id "
                             f"{ADAPTIVE_BUNDLE_ID}; the only accepted ids are "
                             f"{BUNDLE_ID} (fixed) and {ADAPTIVE_BUNDLE_ID} (safe-area)")
        if display_plan is None:
            raise BuildError("--presentation safe-area requires --display-plan "
                             "<native display-plan helper binary>")
    elif bundle_id != BUNDLE_ID:
        raise BuildError(f"--presentation fixed requires --bundle-id {BUNDLE_ID}; the only "
                         f"accepted ids are {BUNDLE_ID} (fixed) and "
                         f"{ADAPTIVE_BUNDLE_ID} (safe-area)")

def display_plan_provenance(src) -> dict:
    """Validate the safe-area native display-plan helper before staging.

    The source is only read, never mutated: it must exist, carry the
    executable bit, and be a Mach-O image (fixtures may be fake bytes as long
    as the magic is real, matching the audio-module contract). Linking and
    codesigning stay with the packaging sign step.
    """
    if src is None or not src.is_file():
        raise BuildError(f"display-plan helper missing: {src}")
    data = src.read_bytes()
    if data[:4] not in MACHO_MAGICS:
        raise BuildError(f"display-plan helper is not a Mach-O image: {src}")
    if not os.access(src, os.X_OK):
        raise BuildError(f"display-plan helper is not executable: {src}")
    return {"resource": "Resources/display-plan",
            "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}

def remediate_fonts(pt: Path, wine_src: Path, home: Path, log: list) -> dict:
    """Materialize registry-referenced fonts into C:\\windows\\Fonts; rewrite refs.

    Wine-bundled share fonts and the user's own Library fonts are copied in
    (subset only); Z:\\System host refs are portable and kept; other Z:\\Users
    refs abort. Returns {name: {"sha256", "bytes"}} for the final font set.
    """
    fonts = pt / "drive_c/windows/Fonts"
    fonts.mkdir(parents=True, exist_ok=True)
    sources = ((("Z:" + str(wine_src / "share/wine/fonts")).replace("/", "\\"), wine_src / "share/wine/fonts"),
               (("Z:" + str(home / "Library/Fonts")).replace("/", "\\"), home / "Library/Fonts"))
    plans, seen = [], set()
    for name in REG_FILES:
        p = pt / name
        if not p.is_file():
            continue
        data, orig, hits = p.read_bytes(), None, {}
        orig = data
        for dos_dir, src_dir in sources:
            needle = esc_dos(dos_dir + "\\")
            n = data.count(needle)
            if not n:
                continue
            for m in re.findall(re.escape(needle) + rb'([^"\\\x00]+)', data):
                fname = m.decode(errors="replace")
                if fname not in seen:
                    seen.add(fname)
                    plans.append((fname, src_dir))
            data = data.replace(needle, WIN_FONTS_ESC)
            hits[dos_dir[:44]] = n
        if data != orig:
            p.write_bytes(data)
        log.append({"action": "font-registry-rewrite", "path": name,
                    "replacements": sum(hits.values()), "detail": hits})
    pins = {}
    for fname, src_dir in sorted(plans):
        dst, src = fonts / fname, src_dir / fname
        if dst.is_file():
            origin = "already-present"
        elif src.is_file():
            shutil.copy2(src, dst)
            origin = "materialized"
        else:
            raise BuildError(f"registry-referenced font missing on host: {src_dir / fname}")
        pins[fname] = {"sha256": sha256(dst), "bytes": dst.stat().st_size, "origin": origin}
    return pins

def audit_registry(pt: Path, wine_src: Path, log: list) -> None:
    """No token placeholders: any residual build-machine path ref aborts."""
    sources = (esc_dos("Z:" + str(wine_src).replace("/", "\\")),
               esc_dos("Z:" + str(wine_src.parent / "prefix").replace("/", "\\")),
               str(wine_src).encode(), str(wine_src.parent / "prefix").encode(),
               b"Z:\\\\Users", b"Z:\\Users")
    for name in REG_FILES:
        p = pt / name
        if not p.is_file():
            continue
        data = p.read_bytes()
        bad = [s.decode(errors="replace") for s in sources if s in data]
        if bad:
            raise BuildError(f"registry {name} still references build-machine paths: {bad[:2]}")
    log.append({"action": "registry-audit", "result": "no residual build-machine paths"})

def remediate_prefix(pt: Path, wine_src: Path, home: Path, log: list) -> dict:
    """DEST-ONLY prefix-template cleanup; raises BuildError on unknown shapes."""
    dc = pt / "drive_c"
    for name in sorted(os.listdir(dc)):
        p = dc / name
        if p.is_dir() and not p.is_symlink() and any(fnmatch.fnmatch(name, g) for g in EXCLUDE_GLOBS):
            log.append({"action": "exclude", "path": f"drive_c/{name}", **tree_stats(p)})
            shutil.rmtree(p)
    for rel in ("ra3-mtld3d/Data/mtld3d-logs",
                "ra3-mtld3d/Data/ra3ep1_1.0.game.dxvk-cache",
                "ra3-mtld3d/Data/mtld3d_shaders.bin.lock"):
        p = pt / "drive_c" / rel
        if p.is_dir():
            log.append({"action": "remove-logs", "path": f"drive_c/{rel}", **tree_stats(p)})
            shutil.rmtree(p)
        elif p.is_file():
            log.append({"action": "remove-known", "path": f"drive_c/{rel}", "bytes": p.stat().st_size})
            p.unlink()
    dup, main = pt / "system 2.reg", pt / "system.reg"
    if dup.is_file():
        if main.is_file() and sha256(dup) == sha256(main):
            log.append({"action": "drop-duplicate", "path": "system 2.reg", "bytes": dup.stat().st_size})
            dup.unlink()
        else:
            log.append({"action": "keep-differing", "path": "system 2.reg", "bytes": dup.stat().st_size})
    dd = pt / "dosdevices"
    for name in sorted(os.listdir(dd)):
        p = dd / name
        if not p.is_symlink():
            continue
        t = os.readlink(p)
        if name == "c:":
            if os.path.isabs(t) or not inside(p.parent / t, pt):
                raise BuildError(f"dosdevices c: is not internal-relative: {t}")
            log.append({"action": "keep", "path": "dosdevices/c:", "target": t})
        elif name == "z:" and t == "/":
            log.append({"action": "defer-z-drive-to-first-launch", "path": "dosdevices/z:",
                        "note": "Created only in writable user prefix; signed bundle has no root-escaping links"})
            p.unlink()
        elif os.path.isabs(t) and t.startswith(STALE_VOL):
            log.append({"action": "drop-stale-vol", "path": f"dosdevices/{name}", "target": t})
            p.unlink()
        else:
            raise BuildError(f"unknown external symlink dosdevices/{name} -> {t}")
    for dp, dns, fns in os.walk(pt, topdown=True, followlinks=False):
        if dp == str(dd):
            dns[:] = []
        for n in list(dns) + list(fns):
            p = Path(dp) / n
            if not p.is_symlink():
                continue
            t = os.readlink(p)
            tgt = p.parent / t
            if not os.path.isabs(t) and inside(tgt, pt):
                continue  # internal relative symlink: keep
            if str(p.relative_to(pt)).startswith("drive_c/users/"):
                log.append({"action": "users-shell-symlink-to-empty-dir",
                            "path": str(p.relative_to(pt)), "target": t})
                p.unlink()
                p.mkdir()
            else:
                raise BuildError(f"unexpected non-internal symlink {p.relative_to(pt)} -> {t}")
    font_pins = remediate_fonts(pt, wine_src, home, log)
    audit_registry(pt, wine_src, log)
    return font_pins

def audit(pt: Path, out: Path, manifest: dict, log: list) -> None:
    res = out / "Contents/Resources"
    errs = pin_errors(manifest["runtime_pins"], res / "wine")
    errs += pin_errors(manifest["game_pins"], pt / "drive_c/ra3-mtld3d")
    errs += pin_errors(manifest["payload_pins"], pt / "drive_c/u60-mtld3d")
    errs += pin_errors(manifest["font_pins"], pt / "drive_c/windows/Fonts")
    wine = res / "wine"
    for dp, dns, fns in os.walk(wine, followlinks=False):
        for n in dns + fns:
            p = Path(dp) / n
            if p.is_symlink() and not inside(p.parent / os.readlink(p), wine):
                errs.append(f"wine symlink escapes bundle: {p.relative_to(wine)}")
    for dp, dns, fns in os.walk(pt, followlinks=False):
        for n in dns + fns:
            p = Path(dp) / n
            if p.is_symlink() and p.parent != pt / "dosdevices":
                errs.append(f"unexpected symlink in prefix-template: {p.relative_to(pt)}")
    rt = Path(manifest["_runtime_root"])
    banned = {str(REPO), str(rt), str(rt / "wine"), str(rt / "prefix"),
              "Z:\\Users", r"Z:\Users", "__RA3MAC_"}
    for p in [pt / n for n in REG_FILES] + [res / "manifest.json", out / "Contents/Info.plist",
                                            res / "Delivery.txt"]:
        if p.is_file() and any(b.encode() in p.read_bytes() for b in banned):
            errs.append(f"banned source/template string remains in {p.name}")
    if errs:
        raise BuildError("audit failed:\n  " + "\n  ".join(errs))
    log.append({"action": "audit", "result": "pins+symlinks+registry clean"})

INFO_PLIST = {
    "CFBundleIdentifier": BUNDLE_ID, "CFBundleName": APP_NAME, "CFBundleDisplayName": APP_NAME,
    "CFBundleExecutable": "RA3Launcher", "CFBundleIconFile": "AppIcon.icns",
    "CFBundleVersion": APP_BUILD, "CFBundleShortVersionString": "1.0",
    "CFBundlePackageType": "APPL", "CFBundleInfoDictionaryVersion": "6.0",
    "LSMinimumSystemVersion": "15.0",
    "LSApplicationCategoryType": "public.app-category.strategy-games",
    "LSMultipleInstancesProhibited": True, "NSHighResolutionCapable": True,
}

def info_plist(bundle_id: str) -> dict:
    """Fresh per-build Info.plist copy; never mutates the module-level template."""
    pl = dict(INFO_PLIST)
    pl["CFBundleIdentifier"] = bundle_id
    return pl

DELIVERY = """红警3：起义时刻（本地交付）
Stack: Rosetta/WoW64 + Wine (cx-26.3.0-7, fixed-viewport patches) + mtld3d v0.13.0 D3D9->Metal.
Original Windows engine and payload identity preserved; RGBA8 Bloom/Glow-fix-v1 resources kept.
Pins: Resources/manifest.json (schema ra3mac-app-v1). Wine runtime: Resources/wine.
Prefix template: Resources/prefix-template; the launcher clones it on first launch and
preserves the user copy (profiles/saves) in Application Support.
Fonts referenced by the prefix registry are materialized in C:\\windows\\Fonts inside the
template (Noto Sans CJK SC + Wine marlett/symbol); Z:\\System host font refs are kept as
normal macOS system dependencies.
Scope: user-local build. Commercial game assets and Apple GPTK components are restricted:
do not redistribute this bundle. Pins are pre-codesign identity; refreshed by the packaging
sign step before outer codesign.
"""

def build_app(args, home: Path = None) -> Path:
    out, rt = Path(args.output).resolve(), Path(args.runtime_root).resolve()
    home = Path(home or Path.home()).resolve()
    amod = Path(args.audio_module).resolve()
    apatch = Path(args.audio_patch).resolve()
    # Candidate options are read through getattr so callers/tests that predate
    # them keep building the unchanged default (fixed) bundle.
    bundle_id = getattr(args, "bundle_id", BUNDLE_ID)
    presentation = getattr(args, "presentation", "fixed")
    dplan = getattr(args, "display_plan", None)
    dplan_src = Path(dplan).resolve() if dplan else None
    # Refuse invalid candidate contracts before creating anything on disk.
    validate_options(bundle_id, presentation, dplan_src)
    dprov = display_plan_provenance(dplan_src) if presentation == "safe-area" else None
    prov = audio_provenance(amod, apatch)
    report = {"schema": SCHEMA, "started": time.strftime("%Y-%m-%dT%H:%M:%S"), "output": str(out),
              "runtime_root": str(rt),
              "presentation": {"mode": presentation, "bundle_id": bundle_id},
              "actions": [], "clone": {}, "pins": {}, "fonts": {},
              "audio": {**prov, "source_module": str(amod), "source_patch": str(apatch)}}
    if dprov is not None:
        report["display_plan_source"] = str(dplan_src)
    if out.suffix != ".app":
        raise BuildError(f"--output must end in .app: {out}")
    if out.exists():
        raise BuildError(f"refusing existing target: {out}")
    if str(out).startswith(str(rt)):
        raise BuildError("--output must live outside the runtime root")
    wine_src, prefix_src, man = rt / "wine", rt / "prefix", rt / "candidate-manifest.json"
    for p in (wine_src, prefix_src, man):
        if not p.exists():
            raise BuildError(f"runtime root incomplete, missing {p}")
    icon = Path(args.icon).resolve() if args.icon else None
    if icon is None and not args.prepare_layout:
        raise BuildError("--icon is required for a full build")
    if icon is not None and not icon.is_file():
        raise BuildError(f"icon missing: {icon}")
    helper = Path(args.display_helper).resolve()
    for p in (helper,):
        if not p.is_file():
            raise BuildError(f"required input missing: {p}")
    launcher = Path(args.launcher).resolve()
    if not args.prepare_layout and not launcher.is_file():
        raise BuildError(f"launcher binary missing: {launcher} (use --prepare-layout to stage without it)")
    cm = json.loads(man.read_text())
    pins = {k: cm[k] for k in ("runtime_pins", "game_pins", "payload_pins")}
    # The runtime root keeps the ORIGINAL audio module; the built module is
    # authoritative for the destination pin, so any stale candidate audio pin
    # is excluded from the source-side validation and replaced below.
    src_pins = {g: dict(pins[g]) for g in pins}
    src_pins["runtime_pins"].pop(AUDIO_PIN, None)
    for group, base in (("runtime_pins", wine_src), ("game_pins", prefix_src / "drive_c/ra3-mtld3d"),
                        ("payload_pins", prefix_src / "drive_c/u60-mtld3d")):
        e = pin_errors(src_pins[group], base)
        report["pins"][f"{group}-before"] = "ok" if not e else e
        if e:
            raise BuildError(f"source pin validation failed: {e}")
    res = out / "Contents/Resources"
    res.mkdir(parents=True)
    try:
        report["clone"]["wine"] = clone_dir(wine_src, res / "wine")
        report["clone"]["prefix"] = clone_dir(prefix_src, res / "prefix-template")
        overlay_audio_module(res, amod, prov, report["actions"])
        pt = res / "prefix-template"
        report["source_stats"] = {"wine": tree_stats(wine_src), "prefix": tree_stats(prefix_src)}
        font_pins = remediate_prefix(pt, wine_src, home, report["actions"])
        report["fonts"] = font_pins
        layout = {"wine": "Resources/wine", "prefix_template": "Resources/prefix-template",
                  "display_helper": "Resources/display-mode"}
        if dprov is not None:
            layout["display_plan"] = "Resources/display-plan"
        mres = {"schema": SCHEMA, "version": "1.0",
                "bundle": {"id": bundle_id, "name": APP_NAME, "executable": "RA3Launcher",
                           "short_version": "1.0", "build_version": APP_BUILD},
                "layout": layout,
                "pin_roots": {"runtime": "Resources/wine",
                              "game": "Resources/prefix-template/drive_c/ra3-mtld3d",
                              "payload": "Resources/prefix-template/drive_c/u60-mtld3d",
                              "fonts": "Resources/prefix-template/drive_c/windows/Fonts"},
                "runtime": {"accepted_source": cm.get("schema", "fixed"),
                            "wine_source": cm.get("wine_source"), "viewport": cm.get("viewport")},
                "runtime_pins": {**pins["runtime_pins"], AUDIO_PIN: prov["sha256"]},
                "game_pins": pins["game_pins"],
                "payload_pins": pins["payload_pins"],
                "font_pins": {k: v["sha256"] for k, v in sorted(font_pins.items())},
                "pins_note": "pre-codesign identity; refreshed by packaging sign step before outer codesign",
                "local_only": True,
                "audio_capture": AUDIO_CAPTURE_FIELD,
                "privacy_usage_requests": PRIVACY_FIELD,
                "audio": prov}
        if presentation == "safe-area":
            mres["presentation"] = PRESENTATION_SAFE_AREA
            mres["display_plan"] = dprov
        mres["_runtime_root"] = str(rt)  # audit input only; removed before write
        audit(pt, out, mres, report["actions"])
        del mres["_runtime_root"]
        (res / "manifest.json").write_text(json.dumps(mres, ensure_ascii=False, indent=1) + "\n")
        plistlib.dump(info_plist(bundle_id), (out / "Contents/Info.plist").open("wb"))
        (res / "Delivery.txt").write_text(DELIVERY, encoding="utf-8")
        shutil.copy2(helper, res / "display-mode")
        os.chmod(res / "display-mode", 0o755)
        if dprov is not None:
            # Native display-plan helper for the safe-area candidate only:
            # staged as an executable copy, unsigned and unlinked; the
            # packaging sign step owns linking and codesigning it.
            shutil.copy2(dplan_src, res / "display-plan")
            os.chmod(res / "display-plan", 0o755)
        if icon is not None:
            shutil.copy2(icon, res / "AppIcon.icns")
        if launcher.is_file():
            (out / "Contents/MacOS").mkdir(parents=True)
            shutil.copy2(launcher, out / "Contents/MacOS/RA3Launcher")
            os.chmod(out / "Contents/MacOS/RA3Launcher", 0o755)
        elif not args.prepare_layout:
            raise BuildError(f"launcher binary vanished: {launcher}")
        report["dest_stats"] = {"prefix_template": tree_stats(pt)}
        report["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        report["mode"] = "prepare-layout (no launcher; not launchable)" if args.prepare_layout else "full"
        REPORTS.mkdir(parents=True, exist_ok=True)
        rp = REPORTS / f"{out.stem}-{time.strftime('%Y%m%d-%H%M%S')}.json"
        while rp.exists():
            rp = rp.with_name(rp.stem + "-r.json")
        rp.write_text(json.dumps(report, ensure_ascii=False, indent=1))
        return rp
    except BaseException:
        shutil.rmtree(out, ignore_errors=True)
        raise

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Build the local RA3 Uprising Mac app bundle.")
    ap.add_argument("--output", required=True, help="absent .app path to create")
    ap.add_argument("--icon", default=None, help="path to AppIcon.icns (required for full build)")
    ap.add_argument("--runtime-root", default=str(DEFAULT_RUNTIME),
                    help="accepted fixed-viewport root (wine/, prefix/, candidate-manifest.json)")
    ap.add_argument("--launcher", default=str(DEFAULT_LAUNCHER),
                    help="compiled RA3Launcher artifact (copied, not built here)")
    ap.add_argument("--display-helper", default=str(DEFAULT_HELPER), help="display-mode helper binary")
    ap.add_argument("--audio-module", default=str(DEFAULT_AUDIO_MODULE),
                    help="locally built disabled-capture winecoreaudio.so, overlayed into Resources/wine")
    ap.add_argument("--audio-patch", default=str(DEFAULT_AUDIO_PATCH),
                    help="no-capture patch file recorded as audio provenance")
    ap.add_argument("--prepare-layout", action="store_true",
                    help="stage the bundle without the launcher; not launchable")
    ap.add_argument("--bundle-id", default=BUNDLE_ID,
                    help=f"bundle identity; only {BUNDLE_ID} (fixed) and "
                         f"{ADAPTIVE_BUNDLE_ID} (safe-area) are accepted")
    ap.add_argument("--presentation", choices=("fixed", "safe-area"), default="fixed",
                    help="fixed = existing metadata unchanged; safe-area = adaptive "
                         f"candidate with manifest presentation {PRESENTATION_SAFE_AREA}")
    ap.add_argument("--display-plan", default=None,
                    help="native display-plan helper binary (required with --presentation "
                         "safe-area; copied to Resources/display-plan, linked by the sign step)")
    args = ap.parse_args(argv)
    try:
        rp = build_app(args)
    except BuildError as e:
        print(f"BUILD ABORTED: {e}", file=sys.stderr)
        return 2
    mode = "prepare-layout (rerun full build with the launcher to ship)" if args.prepare_layout else "full bundle"
    print(f"OK [{mode}] report: {rp}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
