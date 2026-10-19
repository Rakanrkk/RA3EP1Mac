#!/usr/bin/env python3
"""Prepare and package the RA3EP1Mac 1.2.0 release assets.

The prepare phase APFS-clones a sanitized, signed two-rate app into isolated
Standalone and Runtime stages, exports tracked source, reuses the supplied Wine
source archive, and verifies both app copies.  The images phase creates and
mounts ULFO/APFS images, enforces four sub-2-GiB Standalone segments, then writes
portable release metadata and checksums.  It never starts Wine or the game.
"""
from __future__ import annotations

import argparse
import contextlib
import ctypes
import ctypes.util
import hashlib
import getpass
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import plistlib
import re
import shlex
import shutil
import subprocess
import sys

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
PREPARE_APP_PATH = HERE / "prepare_distribution_app.py"
SIGN_APP_PATH = HERE / "sign_app.py"
MAKE_DMG_PATH = HERE / "make_dmg.py"
VERSION = "1.2.0"
APP_BUNDLE_NAME = "红警3起义时刻.app"
RUNTIME_VOLUME = "RA3EP1Mac-1.2.0-Runtime"
STANDALONE_VOLUME = "RA3EP1Mac-1.2.0-Standalone"
MAX_SEGMENT_BYTES = 2 * 1024**3
SEGMENT_SIZE = "1800m"
RATES = (60, 120)
TIMING_FILES = ("Uprising60.exe", "fps60.dll", "fps120.dll")
TOOLS_FILES = ("RA3Launcher", "display-mode", "display-plan", "AppIcon.icns")
PRIVATE_SUFFIXES = {".ra3save", ".ra3ureplay", ".log", ".dmp", ".mdmp"}
PRIVATE_USER_DIRS = {"logs", "log", "saves", "saved games", "savedgames", "replays",
                    "autosaves", "auto saves", "recordings"}
SOURCE_EXTENSIONS = {
    ".c", ".h", ".m", ".mm", ".swift", ".py", ".patch", ".sh",
    ".md", ".txt", ".rst", ".json", ".toml", ".yaml", ".yml",
    ".xml", ".xsd", ".plist", ".entitlements", ".xcconfig", ".strings",
    ".xcstrings", ".fx", ".fxh", ".gitignore", ".gitattributes", ".license", ".licence",
}
SOURCE_BASENAMES = {"license", "license.txt", "copying", "makefile", "readme", "readme.txt"}
EXCLUDED_SOURCE_PARTS = {
    ".git", ".svn", ".hg", "build", "rawbuild", "outputs", "local-evidence",
    "audit", "history", "caches", "cache", "logs", "saves", "savedgames",
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", "deriveddata",
    "test-results", "node_modules",
}
GENERIC_PATHS = (b"/Users/<user>", b"/Users/username", b"/Users/USER", b"/Volumes/<volume>",
                 b"/Volumes/NAME", b"/private/tmp/<temporary-directory>")
# Generic, non-personal release guards: an uppercase 40-hex token is the shape of
# a signing-identity (SHA-1) hash and is never legitimate in public source or
# metadata (sha256 pins are 64 lowercase hex and do not match). Team-ID-like
# tokens are uppercase alnum with boundaries and a digit/letter mix.
IDENTITY_HASH_RE = re.compile(rb"(?<![0-9A-Fa-f])[0-9A-F]{40}(?![0-9A-Fa-f])")
TEAM_ID_RE = re.compile(rb"(?<![0-9A-Za-z])[A-Z0-9]{10}(?![0-9A-Za-z])")
# Token-shaped words this pipeline itself emits into public metadata.
BENIGN_TOKENS = {b"SHA256SUMS"}


class DistributionError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def run_checked(command: list[str], *, capture: bool = True) -> subprocess.CompletedProcess:
    try:
        result = subprocess.run(command, check=False, capture_output=capture, text=True)
    except OSError as error:
        raise DistributionError(f"could not start {command[0]}: {error}") from error
    if result.returncode:
        detail = ((result.stdout or "") + (result.stderr or "")).strip()
        raise DistributionError(f"command failed ({result.returncode}): {shlex.join(command)}\n{detail}")
    return result


def load_prepare_module():
    spec = importlib.util.spec_from_file_location("ra3_distribution_prepare_app", PREPARE_APP_PATH)
    if spec is None or spec.loader is None:
        raise DistributionError(f"cannot load existing App privacy preparer: {PREPARE_APP_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def clone_directory(source: Path, destination: Path) -> str:
    """Require a recursive APFS clone; never silently duplicate multi-GB inputs."""
    libc_name = ctypes.util.find_library("c")
    if not libc_name:
        raise DistributionError("APFS clonefile is unavailable on this host")
    libc = ctypes.CDLL(libc_name, use_errno=True)
    clone = getattr(libc, "clonefile", None)
    if clone is None:
        raise DistributionError("APFS clonefile is unavailable on this host")
    clone.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int]
    clone.restype = ctypes.c_int
    if clone(os.fsencode(source), os.fsencode(destination), 0) != 0:
        code = ctypes.get_errno()
        raise DistributionError(
            f"APFS clone failed ({os.strerror(code)}): {source} -> {destination}; "
            "place the source and new cache on the same APFS volume"
        )
    return "apfs-clonefile"


def clone_file(source: Path, destination: Path) -> str:
    libc_name = ctypes.util.find_library("c")
    if not libc_name:
        raise DistributionError("APFS clonefile is unavailable for Wine source reuse")
    libc = ctypes.CDLL(libc_name, use_errno=True)
    clone = getattr(libc, "clonefile", None)
    if clone is None:
        raise DistributionError("APFS clonefile is unavailable for Wine source reuse")
    clone.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int]
    clone.restype = ctypes.c_int
    if clone(os.fsencode(source), os.fsencode(destination), 0) != 0:
        code = ctypes.get_errno()
        raise DistributionError(
            f"Wine source APFS clone failed ({os.strerror(code)}); put the existing archive "
            "and new output cache on the same APFS volume"
        )
    return "apfs-clonefile"


def resolve_new_output(path: Path) -> Path:
    expanded = path.expanduser()
    resolved = expanded.parent.resolve() / expanded.name
    if os.path.lexists(resolved):
        raise DistributionError(f"--output must be a new absent cache directory: {resolved}")
    if "Caches" not in resolved.parts or "Application Support" in resolved.parts:
        raise DistributionError("--output must be a new directory under a Caches folder")
    if resolved.suffix == ".app":
        raise DistributionError("--output is a cache directory, not an app bundle")
    return resolved


def local_terms(values) -> tuple[bytes, ...]:
    """Caller-declared private terms (e.g. a real signing identity) from the CLI."""
    return tuple(str(value).encode("utf-8") for value in values or () if str(value))


def validate_version(value: str) -> str:
    if value != VERSION:
        raise DistributionError(f"this pipeline targets version {VERSION}, got {value!r}")
    return value


def validate_app_bundle(app: Path, *, verify_signature=True) -> dict:
    app = app.expanduser().resolve()
    if not app.is_dir() or app.suffix != ".app":
        raise DistributionError(f"--app must be an existing .app bundle: {app}")
    manifest_path = app / "Contents/Resources/manifest.json"
    launcher = app / "Contents/MacOS/RA3Launcher"
    if not manifest_path.is_file() or not launcher.is_file():
        raise DistributionError("input app is missing its resource manifest or RA3Launcher")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        plist = plistlib.loads((app / "Contents/Info.plist").read_bytes())
    except (OSError, UnicodeError, json.JSONDecodeError, plistlib.InvalidFileException) as error:
        raise DistributionError(f"input app metadata is invalid: {error}") from error
    if manifest.get("schema") != "ra3mac-app-v1":
        raise DistributionError("input app uses an unsupported manifest schema")
    if manifest.get("bundle", {}).get("id") != "org.ra3mac.UprisingMetal.Adaptive":
        raise DistributionError("input app must keep the Adaptive bundle id for save compatibility")
    if plist.get("CFBundleIdentifier") != manifest["bundle"]["id"]:
        raise DistributionError("Info.plist bundle id disagrees with the resource manifest")
    if plist.get("CFBundleExecutable") != "RA3Launcher":
        raise DistributionError("Info.plist executable must be RA3Launcher")
    pins = manifest.get("timing_pins")
    profiles = manifest.get("frame_rate_profiles")
    expected_timing = set(TIMING_FILES)
    if not isinstance(pins, dict) or set(pins) != expected_timing:
        raise DistributionError("input app must carry exactly the 60/120 timing component pins")
    if not isinstance(profiles, dict) or set(profiles) != {"60", "120"}:
        raise DistributionError("input app must carry exactly the 60 and 120 FPS profiles")
    for rate in RATES:
        profile = profiles[str(rate)]
        if profile != {"dll": f"fps{rate}.dll", "log": "uprising120.log"}:
            raise DistributionError(f"input app has an invalid {rate} FPS profile")
    if manifest.get("default_frame_rate") != 60:
        raise DistributionError("input app default frame rate must be 60 FPS")
    for relative, expected in pins.items():
        if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise DistributionError(f"input app has an invalid timing pin: {relative}")
        if not (app / "Contents/Resources/timing" / relative).is_file():
            raise DistributionError(f"input app is missing timing component: {relative}")
    for relative in ("wine/bin/wine", "wine/bin/wineserver", "display-mode", "display-plan",
                     "prefix-template/system.reg", "prefix-template/drive_c/u60-mtld3d/Uprising60.exe"):
        path = app / "Contents/Resources" / relative
        if not path.is_file():
            raise DistributionError(f"input app is missing required runtime/import component: {relative}")
    if verify_signature:
        run_checked(["/usr/bin/codesign", "--verify", "--deep", "--strict", str(app)])
    return {"manifest": manifest, "plist": plist, "launcher": launcher}


def verify_app(app: Path, *, runner=run_checked, launcher_runner=None) -> None:
    runner(["/usr/bin/codesign", "--verify", "--deep", "--strict", str(app)])
    launcher = app / "Contents/MacOS/RA3Launcher"
    if launcher_runner is None:
        runner([str(launcher), "--verify-only"])
    else:
        launcher_runner(launcher)


def sign_app(app: Path, reports: Path, label: str, *, runner=run_checked) -> Path:
    reports.mkdir(parents=True, exist_ok=True)
    report = reports / f"sign-{label}.json"
    if os.path.lexists(report):
        raise DistributionError(f"refusing to overwrite signing report: {report}")
    runner([sys.executable, "-I", str(SIGN_APP_PATH), str(app), "--identity", "-",
            "--public", "--report", str(report)])
    if not report.is_file():
        raise DistributionError(f"signing command did not create its report: {report}")
    return report


def validate_source_checkout(root: Path, *, runner=run_checked) -> list[str]:
    root = root.expanduser().resolve()
    if not root.is_dir():
        raise DistributionError(f"--source-repo must be an existing clean Git checkout: {root}")
    top = runner(["/usr/bin/git", "-C", str(root), "rev-parse", "--show-toplevel"]).stdout.strip()
    if top and Path(top).resolve() != root:
        raise DistributionError("--source-repo must name the Git worktree root")
    status = runner(["/usr/bin/git", "-C", str(root), "status", "--porcelain=v1",
                     "--untracked-files=all"]).stdout
    if status.strip():
        raise DistributionError("--source-repo is not clean; commit or remove local changes first")
    raw = runner(["/usr/bin/git", "-C", str(root), "ls-files", "-z"]).stdout
    paths = [p for p in raw.split("\0") if p]
    if not paths:
        raise DistributionError("--source-repo has no tracked files")
    return paths


def actual_private_path_patterns() -> tuple[re.Pattern[bytes], ...]:
    username = getpass.getuser().encode("utf-8", errors="ignore")
    home = Path.home()
    patterns = []
    if username and username not in {b"root", b"user", b"username"}:
        patterns.append(re.compile(rb"/Users/" + re.escape(username) + rb"(?:/|\\b)"))
    if str(home).startswith("/Users/"):
        patterns.append(re.compile(re.escape(str(home).encode("utf-8"))))
    return tuple(patterns)


def source_privacy_issue(data: bytes, extra_terms: tuple[bytes, ...] = ()) -> str | None:
    if IDENTITY_HASH_RE.search(data):
        return "signing-identity-shaped hash"
    for candidate in TEAM_ID_RE.findall(data):
        if candidate in BENIGN_TOKENS:
            continue
        token = candidate.decode("ascii", errors="ignore")
        digits = sum(1 for ch in token if ch.isdigit())
        letters = sum(1 for ch in token if ch.isalpha())
        if digits >= 3 and letters >= 5:
            return "signing team-id-like token"
    for term in extra_terms:
        if term and term in data:
            return "caller-declared private term"
    for pattern in actual_private_path_patterns():
        match = pattern.search(data)
        if match:
            return "host-local user path"
    # Literal split so this public source file never matches its own guard.
    marker = b"clau" b"de-"
    if re.search(rb"/private/tmp/" + re.escape(marker) + rb"[^/\\s]+", data):
        return "temporary host path"
    if re.search(rb"/private/var/folders/[a-z0-9]+/[a-z0-9]+/T/", data):
        return "temporary host path"
    recovery = b"/Volumes/" b"Rec" b"overy/"  # split: public source must not match its own guard
    if recovery in data:
        return "host-specific mounted-volume path"
    return None


def reject_local_path_content(data: bytes, relative: str,
                              extra_terms: tuple[bytes, ...] = ()) -> None:
    issue = source_privacy_issue(data, extra_terms)
    if issue:
        raise DistributionError(f"tracked source contains {issue} ({relative})")


def has_private_player_data(root: Path) -> list[str]:
    """Return relative paths that look like player saves, replays, or logs."""
    found = []
    for current, dirs, files in os.walk(root, followlinks=False):
        parent = Path(current)
        for name in list(dirs):
            child = parent / name
            if child.is_symlink():
                dirs.remove(name)
                continue
            lower = name.casefold()
            if lower in PRIVATE_USER_DIRS or lower.startswith(("ra3ucampaign", "ra3campaign")):
                found.append(str(child.relative_to(root)))
        for name in files:
            path = parent / name
            lower = name.casefold()
            is_player_area = any(part.casefold() in {"users", "saved games", "saves", "replays", "autosaves"}
                                 for part in path.relative_to(root).parts)
            if (path.suffix.casefold() in PRIVATE_SUFFIXES
                    or (is_player_area and lower.startswith(("stats_", "skirmish", "movies.ini")))
                    or lower in {"ra3ucampaign.ini", "ra3campaign.ini", "ra3u.ini"}):
                found.append(str(path.relative_to(root)))
    return sorted(set(found))


def source_path_is_excluded(relative: str) -> bool:
    path = PurePosixPath(relative)
    lower_parts = {part.lower() for part in path.parts}
    if lower_parts & EXCLUDED_SOURCE_PARTS:
        return True
    if path.suffix.lower() not in SOURCE_EXTENSIONS and path.name.lower() not in SOURCE_BASENAMES:
        return True
    if path.name.lower() in {".ds_store", "thumbs.db"}:
        return True
    return False


def export_tracked_source(source_repo: Path, destination: Path, *, runner=run_checked,
                          extra_terms: tuple[bytes, ...] = ()) -> dict:
    source_repo = source_repo.expanduser().resolve()
    if os.path.lexists(destination):
        raise DistributionError(f"Source destination already exists: {destination}")
    tracked = validate_source_checkout(source_repo, runner=runner)
    destination.mkdir(parents=True)
    copied, excluded = [], []
    try:
        for raw in tracked:
            rel = PurePosixPath(raw)
            if rel.is_absolute() or ".." in rel.parts or "\\" in raw:
                raise DistributionError(f"unsafe tracked source path: {raw!r}")
            if source_path_is_excluded(raw):
                excluded.append(raw)
                continue
            source = source_repo.joinpath(*rel.parts)
            target = destination.joinpath(*rel.parts)
            if source.is_symlink():
                link = os.readlink(source)
                resolved = (source.parent / link).resolve()
                if not resolved.is_relative_to(source_repo):
                    raise DistributionError(f"tracked source symlink escapes checkout: {raw}")
                if not resolved.exists():
                    raise DistributionError(f"tracked source symlink target is missing: {raw}")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.symlink_to(link)
                copied.append(raw)
                continue
            if not source.is_file():
                excluded.append(raw)
                continue
            data = source.read_bytes()
            reject_local_path_content(data, raw, extra_terms)
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as stream:
                stream.write(data)
            os.chmod(target, source.stat().st_mode & 0o777)
            copied.append(raw)
        if not copied:
            raise DistributionError("tracked source export contains no allowlisted source files")
    except BaseException:
        shutil.rmtree(destination, ignore_errors=True)
        raise
    if (destination / ".git").exists() or (destination / ".git").is_symlink():
        raise DistributionError("Source export unexpectedly contains Git metadata")
    return {"tracked_files_copied": len(copied), "tracked_files_excluded": len(excluded),
            "git_metadata_included": False, "host_local_paths_rejected": True,
            "copied_paths": copied, "excluded_paths": excluded}


def validate_wine_archive(path: Path, version: str) -> dict:
    path = path.expanduser().resolve()
    if not path.is_file() or path.suffixes[-2:] != [".tar", ".zst"]:
        raise DistributionError("--wine-source must be an existing .tar.zst Wine source archive")
    match = re.fullmatch(r"RA3EP1Mac-v([0-9]+(?:\.[0-9]+){2})-wine-source\.tar\.zst", path.name)
    if not match:
        raise DistributionError("Wine source must use a prior release filename so reuse is traceable")
    if match.group(1) == version:
        raise DistributionError("--wine-source must be the existing prior-release archive")
    return {"path": path, "filename": path.name, "source_version": match.group(1)}


def write_text_new(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(text)


def write_runtime_layout(full_app: Path, runtime_root: Path, *, clone_dir_fn=clone_directory,
                         clone_file_fn=clone_file) -> dict:
    """Stage COW-cloned Wine/prefix/timing components without duplicating an App bundle."""
    resources = full_app / "Contents/Resources"
    try:
        source_manifest = json.loads((resources / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise DistributionError(f"runtime source manifest is invalid: {error}") from error
    runtime_root.mkdir(parents=True, exist_ok=False)
    clone_methods = {}
    for name in ("wine", "prefix-template"):
        source = resources / name
        destination = runtime_root / ("prefix" if name == "prefix-template" else name)
        clone_methods[name] = clone_dir_fn(source, destination)
    # Runtime prefix has no proprietary game tree or player artifacts. Preserve
    # its import payload, font/system files, and all timing-critical template files.
    game = runtime_root / "prefix/drive_c/ra3-mtld3d"
    if game.exists() or game.is_symlink():
        if game.is_symlink():
            raise DistributionError("Runtime game path must be an ordinary directory before removal")
        shutil.rmtree(game)
    remaining_private = has_private_player_data(runtime_root / "prefix")
    if remaining_private:
        raise DistributionError("Runtime prefix retains player data: " + ", ".join(remaining_private[:8]))
    payload = runtime_root / "prefix/drive_c/u60-mtld3d"
    required_payload = {"Uprising60.exe", "uprising60.dll"}
    if not required_payload <= {path.name for path in payload.iterdir() if path.is_file()}:
        raise DistributionError("Runtime prefix lost required import payload files")
    timing_source = resources / "timing"
    timing_dest = runtime_root / "timing"
    clone_methods["timing"] = clone_dir_fn(timing_source, timing_dest)
    if {path.name for path in timing_dest.iterdir() if path.is_file()} != set(TIMING_FILES):
        raise DistributionError("Runtime timing component set is incomplete or contains extra files")
    manifest = {key: value for key, value in source_manifest.items()
                if key not in {"_runtime_root", "source_app", "output"}}
    manifest["game_pins"] = {}
    manifest.pop("resource_updates", None)
    (runtime_root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    tools_root = runtime_root / "Tools"
    tools_root.mkdir()
    for name in TOOLS_FILES:
        source = full_app / "Contents/MacOS/RA3Launcher" if name == "RA3Launcher" else resources / name
        if not source.is_file():
            raise DistributionError(f"Runtime Tools input is missing: {name}")
        shutil.copy2(source, tools_root / name)
    return {"clone_methods": clone_methods, "source_manifest_schema": source_manifest.get("schema"),
            "game_directory_present": False, "game_pins": 0,
            "timing_rates": sorted(manifest.get("frame_rate_profiles", {}).keys()),
            "tools": list(TOOLS_FILES)}


def runtime_manifest_for_distribution(app: Path) -> dict:
    """Remove game payload from an isolated validation App clone only."""
    manifest_path = app / "Contents/Resources/manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    game = app / "Contents/Resources/prefix-template/drive_c/ra3-mtld3d"
    if not game.is_dir() or game.is_symlink():
        raise DistributionError("Runtime validation App must contain a game tree before isolated stripping")
    shutil.rmtree(game)
    manifest["game_pins"] = {}
    manifest.pop("resource_updates", None)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + "\n",
                             encoding="utf-8")
    if game.exists():
        raise DistributionError("game tree remained in Runtime validation App")
    return manifest


def prepare_phase(args, *, services=None) -> dict:
    services = services or {}
    clone_dir_fn = services.get("clone_directory", clone_directory)
    clone_file_fn = services.get("clone_file", clone_file)
    runner = services.get("runner", run_checked)
    prepare_app_fn = services.get("prepare_app")
    sign_app_fn = services.get("sign_app")
    verify_app_fn = services.get("verify_app")
    validate_fn = services.get("validate_app", validate_app_bundle)
    app_input = Path(args.app).expanduser().resolve()
    source_repo = Path(args.source_repo).expanduser().resolve()
    wine = validate_wine_archive(Path(args.wine_source), args.version)
    app_info = validate_fn(app_input, verify_signature=False) if "validate_app" in services else validate_app_bundle(app_input, verify_signature=True)
    tracked = validate_source_checkout(source_repo, runner=runner)
    del tracked
    output = resolve_new_output(Path(args.output))
    for item in (app_input, source_repo, wine["path"]):
        if item == output or item in output.parents or output in item.parents:
            raise DistributionError("--output must stay separate from the app, source checkout, and Wine archive")
    output.mkdir(parents=True, exist_ok=False)
    stage = output / "stage"
    standalone_root = stage / "Standalone"
    runtime_root = stage / "Runtime"
    validation_root = output / "validation"
    reports = output / "reports"
    assets = output / "release-assets"
    standalone_root.mkdir(parents=True)
    validation_root.mkdir()
    reports.mkdir()
    assets.mkdir()
    standalone_app = standalone_root / APP_BUNDLE_NAME
    runtime_app = validation_root / "Runtime.app"
    if prepare_app_fn is None:
        prepare_module = load_prepare_module()
        prepare_app_fn = prepare_module.prepare
    if sign_app_fn is None:
        sign_app_fn = lambda app, directory, label: sign_app(app, directory, label, runner=runner)
    if verify_app_fn is None:
        verify_app_fn = lambda app: verify_app(app, runner=runner)
    privacy_report = prepare_app_fn(app_input, standalone_app)
    (reports / "standalone-privacy.json").write_text(
        json.dumps(privacy_report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    standalone_sign_report = sign_app_fn(standalone_app, reports, "standalone")
    verify_app_fn(standalone_app)
    standalone = validate_fn(standalone_app, verify_signature=False)
    validation_clone_method = clone_dir_fn(standalone_app, runtime_app)
    runtime_manifest = runtime_manifest_for_distribution(runtime_app)
    runtime_sign_report = sign_app_fn(runtime_app, reports, "runtime-validation")
    verify_app_fn(runtime_app)
    runtime_validation = validate_fn(runtime_app, verify_signature=False)
    if runtime_manifest.get("game_pins") != {}:
        raise DistributionError("Runtime validation manifest still claims game files")
    if (runtime_app / "Contents/Resources/prefix-template/drive_c/ra3-mtld3d").exists():
        raise DistributionError("Runtime validation app unexpectedly contains game content")
    runtime_stage = stage / "Runtime"
    runtime_report = write_runtime_layout(standalone_app, runtime_stage,
                                          clone_dir_fn=clone_dir_fn,
                                          clone_file_fn=clone_file_fn)
    runtime_report["validation_app"] = str(runtime_app)
    runtime_report["validation_signature_report"] = str(runtime_sign_report)

    source_report = export_tracked_source(source_repo, runtime_stage / "Source", runner=runner,
                                          extra_terms=local_terms(getattr(args, "known_local_terms", [])))
    wine_destination = assets / f"RA3EP1Mac-v{args.version}-wine-source.tar.zst"
    if os.path.lexists(wine_destination):
        raise DistributionError(f"refusing existing Wine source destination: {wine_destination}")
    wine_clone_method = clone_file_fn(wine["path"], wine_destination)

    write_text_new(standalone_root / "README.txt", (
        "RA3EP1Mac 1.2.0 完整版\n\n"
        "本版本包含《红色警戒 3：起义时刻》游戏、Wine/Metal 运行时和导入载荷。\n"
        "将“红警3起义时刻.app”拖入“应用程序”文件夹后打开即可开始游戏。\n\n"
        "启动时可选择 60 或 120 FPS，默认 60 FPS。首次启动会准备独立的用户游戏数据。\n\n"
        "RA3EP1Mac 1.2.0 Standalone\n\n"
        "This edition includes Red Alert 3: Uprising, the Wine/Metal runtime and the import payload.\n"
        "Copy “红警3起义时刻.app” to Applications and open it to play.\n\n"
        "Choose 60 or 120 FPS at launch; the default is 60 FPS. The first launch prepares separate user game data.\n"
    ))
    write_text_new(runtime_stage / "README.txt", (
        "RA3EP1Mac 1.2.0 运行环境\n\n"
        "本镜像提供 Wine 运行时、prefix 模板、60/120 FPS 时序载荷、原生工具和源码，不含《红色警戒 3：起义时刻》游戏文件，也不是可直接运行的游戏 App。\n"
        "请先准备自己的游戏副本和兼容资源，再从 Source/ 目录中的文档按步骤导入并构建 App。\n\n"
        "资源校验与构建说明见 Source/README.md。所有组件许可见 Source/THIRD-PARTY-NOTICES.md。\n\n"
        "RA3EP1Mac 1.2.0 Runtime\n\n"
        "This image contains the Wine runtime, prefix template, 60/120 FPS timing payloads, native tools and source.\n"
        "It does not include Red Alert 3: Uprising game files and is not a playable app.\n"
        "Prepare your own game copy and compatibility resources, then follow the instructions in Source/ to import the runtime and build an app.\n\n"
        "For resource verification and build steps, see Source/README.en.md. Component licenses are listed in Source/THIRD-PARTY-NOTICES.md.\n"
    ))
    if has_private_player_data(standalone_app / "Contents/Resources/prefix-template"):
        raise DistributionError("Standalone privacy stage still contains player saves, replays, or logs")

    plan = {
        "schema": "ra3mac-distribution-v12-prepare-v1",
        "version": args.version,
        "phase": "prepared",
        "output": str(output),
        "stages": {"standalone": str(standalone_root), "runtime": str(runtime_stage)},
        "validation_app": str(runtime_app),
        "assets": str(assets),
        "source": source_report,
        "wine_source": {"reused_from": wine["filename"], "source_version": wine["source_version"],
                        "staged_as": wine_destination.name, "clone_method": wine_clone_method},
        "apps": {
            "standalone": {"path": str(standalone_app), "bundle_id": standalone["manifest"]["bundle"]["id"],
                           "timing_rates": sorted(standalone["manifest"]["frame_rate_profiles"].keys()),
                           "verify_only": "passed"},
            "runtime_validation": {"path": str(runtime_app),
                                   "bundle_id": runtime_validation["manifest"]["bundle"]["id"],
                                   "game_directory_present": False, "game_pins": 0,
                                   "timing_rates": sorted(runtime_validation["manifest"]["frame_rate_profiles"].keys()),
                                   "verify_only": "passed"},
        },
        "runtime_layout": runtime_report,
        "privacy": privacy_report,
        "clone": {"runtime_validation_app": validation_clone_method},
        "images_created": False,
        "large_payload_hashes_computed": False,
        "next_command": shlex.join([
            sys.executable, "-I", str(HERE / "distribution_v12.py"),
            "--output", str(output), "--version", args.version, "--images-only",
        ]),
    }
    report_path = reports / "prepare-phase.json"
    report_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return plan


def discover_segments(base: Path) -> list[tuple[int, Path]]:
    """Recognize hdiutil's segmented output variants without assuming its first suffix."""
    candidates: dict[int, Path] = {}
    parent = base.parent
    for path in parent.iterdir():
        if not path.is_file():
            continue
        name = path.name
        if name == base.name:
            candidates[1] = path
            continue
        patterns = (
            (rf"{re.escape(base.stem)}\.(\d{{3}})\.dmg", 1),
            (rf"{re.escape(base.stem)}\.(\d{{3}})\.dmgpart", 1),
            (rf"{re.escape(base.name)}\.(\d{{3}})\.dmgpart", 1),
        )
        for pattern, _ in patterns:
            match = re.fullmatch(pattern, name)
            if match:
                index = int(match.group(1))
                if index in candidates:
                    raise DistributionError(f"duplicate hdiutil segment number {index}: {path}")
                candidates[index] = path
                break
    if not candidates or sorted(candidates) != list(range(1, len(candidates) + 1)):
        raise DistributionError(f"hdiutil produced a missing or non-contiguous segment set for {base}")
    return sorted(candidates.items())


def normalize_segments(base: Path, destination: Path, stem: str) -> list[Path]:
    found = discover_segments(base)
    if len(found) != 4:
        raise DistributionError(f"Standalone must have exactly four native segments; found {len(found)}")
    normalized = []
    for index, source in found:
        target = destination / (f"{stem}.{index:03d}.dmg" if index == 1
                                else f"{stem}.{index:03d}.dmgpart")
        if os.path.lexists(target):
            raise DistributionError(f"refusing existing segment: {target}")
        if source.stat().st_size >= MAX_SEGMENT_BYTES:
            raise DistributionError(f"Standalone segment reaches the 2 GiB limit: {source.name}")
        os.replace(source, target)
        normalized.append(target)
    return normalized


def create_runtime_image(source: Path, output: Path, *, runner=run_checked) -> None:
    if os.path.lexists(output):
        raise DistributionError(f"refusing existing Runtime DMG: {output}")
    runner([sys.executable, "-I", str(MAKE_DMG_PATH), "--source", str(source),
            "--output", str(output), "--volume-name", RUNTIME_VOLUME, "--format", "ULFO"])


def create_standalone_image(source: Path, scratch: Path, destination: Path, stem: str,
                            *, runner=run_checked) -> list[Path]:
    scratch.mkdir(parents=True, exist_ok=False)
    whole = scratch / "standalone-raw.dmg"
    runner([sys.executable, "-I", str(MAKE_DMG_PATH), "--source", str(source),
            "--output", str(whole), "--volume-name", STANDALONE_VOLUME, "--format", "ULFO"])
    base = scratch / "standalone.dmg"
    runner(["/usr/bin/hdiutil", "segment", "-segmentCount", "4", "-segmentSize", SEGMENT_SIZE,
            "-o", str(base), str(whole)])
    segments = normalize_segments(base, destination, stem)
    runner(["/usr/bin/hdiutil", "verify", str(segments[0])])
    return segments


def mounted_image(image: Path, mount_root: Path, label: str, *, runner=run_checked):
    @contextlib.contextmanager
    def context():
        mountpoint = mount_root / label
        mountpoint.mkdir(parents=True, exist_ok=False)
        attached = False
        try:
            runner(["/usr/bin/hdiutil", "attach", "-readonly", "-nobrowse", "-mountpoint",
                    str(mountpoint), str(image)])
            attached = True
            yield mountpoint
        finally:
            if attached:
                runner(["/usr/bin/hdiutil", "detach", str(mountpoint)])
            try:
                mountpoint.rmdir()
            except OSError:
                pass
    return context()


def verify_mounted_product(root: Path, product: str, *, runner=run_checked,
                           launcher_runner=None) -> dict:
    if product == "runtime":
        expected = {"wine", "prefix", "timing", "Tools", "Source", "README.txt"}
        actual = {path.name for path in root.iterdir()}
        missing = expected - actual
        if missing:
            raise DistributionError(f"Runtime volume is missing components: {sorted(missing)}")
        apps = [path for path in root.iterdir() if path.is_dir() and path.suffix == ".app"]
        if apps:
            raise DistributionError("Runtime volume must not ship a user-facing app bundle")
        game = root / "prefix/drive_c/ra3-mtld3d"
        if game.exists():
            raise DistributionError("mounted Runtime unexpectedly contains game content")
        if (root / "Source/.git").exists() or (root / "Source/.git").is_symlink():
            raise DistributionError("mounted Runtime Source contains Git metadata")
        for name in TOOLS_FILES:
            if not (root / "Tools" / name).is_file():
                raise DistributionError(f"mounted Runtime is missing Tools/{name}")
        if {path.name for path in (root / "timing").iterdir() if path.is_file()} != set(TIMING_FILES):
            raise DistributionError("mounted Runtime timing payload set is invalid")
        for name in ("wine/bin/wine", "wine/bin/wineserver", "prefix/system.reg",
                     "prefix/drive_c/u60-mtld3d/Uprising60.exe"):
            if not (root / name).is_file():
                raise DistributionError(f"mounted Runtime is missing {name}")
        return {"app": None, "codesign_verify": "n/a (no user-facing app in Runtime volume)",
                "runtime_component_checks": "passed", "game_started": False, "wine_started": False}

    apps = sorted(path for path in root.iterdir() if path.is_dir() and path.suffix == ".app")
    if len(apps) != 1:
        raise DistributionError(f"mounted {product} volume must contain exactly one app bundle")
    app = apps[0]
    validate_app_bundle(app, verify_signature=False)
    verify_app(app, runner=runner, launcher_runner=launcher_runner)
    return {"app": app.name, "codesign_verify": "passed", "launcher_verify_only": "passed",
            "game_started": False, "wine_started": False}


def verify_image(image: Path, mount_root: Path, label: str, product: str, *, runner=run_checked,
                 launcher_runner=None) -> dict:
    runner(["/usr/bin/hdiutil", "verify", str(image)])
    context = mounted_image(image, mount_root, label, runner=runner)
    with context as mountpoint:
        return verify_mounted_product(mountpoint, product, runner=runner,
                                      launcher_runner=launcher_runner)


def attachment_name(version: str, role: str) -> str:
    prefix = f"RA3EP1Mac-v{version}-macos-arm64-"
    return prefix + role


def list_payload_assets(assets: Path, version: str) -> list[Path]:
    runtime = assets / attachment_name(version, "runtime.dmg")
    standalone_stem = attachment_name(version, "standalone")
    standalone = [assets / f"{standalone_stem}.{index:03d}.{'dmg' if index == 1 else 'dmgpart'}"
                 for index in range(1, 5)]
    wine = assets / f"RA3EP1Mac-v{version}-wine-source.tar.zst"
    paths = [runtime, *standalone, wine]
    missing = [str(path) for path in paths if not path.is_file()]
    if missing:
        raise DistributionError("required release payload is missing:\n  " + "\n  ".join(missing))
    return paths


def assert_portable_metadata(data: bytes, *, report_paths: tuple[Path, ...] = (),
                             extra_terms: tuple[bytes, ...] = ()) -> None:
    text = data.decode("utf-8", errors="strict")
    for path in report_paths:
        literal = str(path).encode("utf-8")
        if literal and literal in data:
            raise DistributionError(f"public metadata contains a local path: {path}")
    reject_local_path_content(data, "release metadata", extra_terms)
    if "file://" in text:
        raise DistributionError("public metadata must not contain local file URLs")


def write_release_metadata(assets: Path, version: str, wine_source_version: str,
                           *, report_paths=(), extra_terms: tuple[bytes, ...] = ()) -> dict:
    payloads = list_payload_assets(assets, version)
    attachment_records = []
    for path in payloads:
        attachment_records.append({"name": path.name, "bytes": path.stat().st_size,
                                   "sha256": sha256_file(path)})
    attachment_records.sort(key=lambda item: item["name"].casefold())
    manifest_path = assets / "release-manifest.json"
    sums_path = assets / "SHA256SUMS.txt"
    manifest = {
        "schema": "ra3mac-release-manifest-v1",
        "product": "RA3EP1Mac",
        "version": version,
        "platform": "macos-arm64",
        "images": {
            "runtime": {"filesystem": "APFS", "format": "ULFO", "volume_name": RUNTIME_VOLUME,
                         "contains_complete_game": False},
            "standalone": {"filesystem": "APFS", "format": "ULFO", "volume_name": STANDALONE_VOLUME,
                            "segment_count": 4, "segment_size_argument": SEGMENT_SIZE,
                            "maximum_segment_bytes_exclusive": MAX_SEGMENT_BYTES},
        },
        "wine_source_reuse": {"source_release_version": wine_source_version,
                               "published_filename": f"RA3EP1Mac-v{version}-wine-source.tar.zst"},
        "attachments": attachment_records,
        "attachment_order": [item["name"] for item in attachment_records]
        + ["release-manifest.json", "SHA256SUMS.txt"],
        "checksum_file": "SHA256SUMS.txt",
    }
    manifest_bytes = (json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    assert_portable_metadata(manifest_bytes, report_paths=tuple(report_paths), extra_terms=extra_terms)
    manifest_path.write_bytes(manifest_bytes)
    checksum_targets = sorted([*payloads, manifest_path], key=lambda path: path.name.casefold())
    sums = "".join(f"{sha256_file(path)}  {path.name}\n" for path in checksum_targets)
    sums_bytes = sums.encode("utf-8")
    assert_portable_metadata(sums_bytes, report_paths=tuple(report_paths), extra_terms=extra_terms)
    sums_path.write_bytes(sums_bytes)
    return {"manifest": str(manifest_path), "checksum_file": str(sums_path),
            "payloads": attachment_records, "checksum_entries": len(checksum_targets),
            "sha256sums_sha256": hashlib.sha256(sums_bytes).hexdigest()}


def images_phase(args, *, services=None) -> dict:
    services = services or {}
    runner = services.get("runner", run_checked)
    verify_app_fn = services.get("verify_app")
    launcher_runner = services.get("launcher_runner")
    output = Path(args.output).expanduser().resolve()
    if not output.is_dir():
        raise DistributionError(f"--images-only requires a prepared cache directory: {output}")
    report_path = output / "reports/prepare-phase.json"
    if not report_path.is_file():
        raise DistributionError(f"prepared phase report is missing: {report_path}")
    try:
        prepare_report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise DistributionError(f"prepared phase report is invalid: {error}") from error
    if prepare_report.get("version") != args.version or prepare_report.get("schema") != "ra3mac-distribution-v12-prepare-v1":
        raise DistributionError("prepared phase report version/schema does not match this invocation")
    stage = output / "stage"
    standalone_root, runtime_root = stage / "Standalone", stage / "Runtime"
    validation_app = output / "validation/Runtime.app"
    assets, reports = output / "release-assets", output / "reports"
    standalone_app = standalone_root / APP_BUNDLE_NAME
    for path in (standalone_app, validation_app, runtime_root):
        if not path.is_dir():
            raise DistributionError(f"prepared runtime product stage is missing: {path}")
    if verify_app_fn is None:
        verify_app_fn = lambda app: verify_app(app, runner=runner, launcher_runner=launcher_runner)
    validate_fn = services.get("validate_app", validate_app_bundle)
    verify_app_fn(standalone_app)
    verify_app_fn(validation_app)
    standalone_info = validate_fn(standalone_app, verify_signature=False)
    runtime_validation_info = validate_fn(validation_app, verify_signature=False)
    if runtime_validation_info["manifest"].get("game_pins") != {}:
        raise DistributionError("prepared Runtime validation manifest is not game-free")
    if (validation_app / "Contents/Resources/prefix-template/drive_c/ra3-mtld3d").exists():
        raise DistributionError("prepared Runtime validation app still contains game content")
    runtime_manifest = json.loads((runtime_root / "manifest.json").read_text(encoding="utf-8"))
    if runtime_manifest.get("game_pins") not in ({}, None):
        raise DistributionError("prepared Runtime manifest is not game-free")
    if (runtime_root / "prefix/drive_c/ra3-mtld3d").exists():
        raise DistributionError("prepared Runtime still contains game content")
    if standalone_info["manifest"].get("version") != args.version:
        raise DistributionError("prepared Standalone manifest version does not match")

    runtime_dmg = assets / attachment_name(args.version, "runtime.dmg")
    if not os.path.lexists(runtime_dmg):
        create_runtime_image(runtime_root, runtime_dmg, runner=runner)
    scratch_parent = output / "image-work"
    scratch_parent.mkdir(exist_ok=True)
    standalone_stem = attachment_name(args.version, "standalone")
    existing_standalone = sorted(assets.glob(standalone_stem + ".*"))
    if existing_standalone:
        segments = [assets / f"{standalone_stem}.{index:03d}.{'dmg' if index == 1 else 'dmgpart'}"
                    for index in range(1, 5)]
        if not all(path.is_file() for path in segments):
            raise DistributionError("existing standalone payload is incomplete; remove it before re-running images")
    else:
        segments = create_standalone_image(standalone_root, scratch_parent / "standalone", assets,
                                           standalone_stem, runner=runner)
    if len(segments) != 4 or any(path.stat().st_size >= MAX_SEGMENT_BYTES for path in segments):
        raise DistributionError("Standalone segment count/size violates the four-volume release limit")

    mounts = output / "mountpoints"
    mounts.mkdir(exist_ok=True)
    runtime_mount_result = verify_image(runtime_dmg, mounts, "runtime", "runtime", runner=runner,
                                        launcher_runner=launcher_runner)
    standalone_mount_result = verify_image(segments[0], mounts, "standalone", "standalone",
                                           runner=runner, launcher_runner=launcher_runner)
    metadata = write_release_metadata(assets, args.version,
                                     prepare_report["wine_source"]["source_version"],
                                     report_paths=(output, args.app if args.app else output),
                                     extra_terms=local_terms(getattr(args, "known_local_terms", [])))
    report = {
        "schema": "ra3mac-distribution-v12-images-v1", "version": args.version,
        "output": str(output), "attachments_directory": str(assets),
        "runtime_mount_verification": runtime_mount_result,
        "standalone_mount_verification": standalone_mount_result,
        "segments": [{"name": path.name, "bytes": path.stat().st_size} for path in segments],
        "metadata": metadata,
        "game_started": False, "wine_started": False,
    }
    final_report = reports / "images-phase.json"
    final_report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", type=Path, help="sanitized, signed 1.2.0 two-rate app bundle")
    parser.add_argument("--source-repo", type=Path, help="clean Git checkout used for tracked Source export")
    parser.add_argument("--output", type=Path, required=True, help="new cache directory; images phase reuses it")
    parser.add_argument("--version", default=VERSION, help=f"release version (this pipeline requires {VERSION})")
    parser.add_argument("--wine-source", type=Path, help="existing prior-release matching .tar.zst Wine source")
    parser.add_argument("--known-local-term", dest="known_local_terms", action="append", default=[],
                        metavar="TERM",
                        help="private term (repeatable) refused in Source and metadata, e.g. your signing identity")
    phases = parser.add_mutually_exclusive_group()
    phases.add_argument("--prepare-only", action="store_true",
                        help="stage and verify apps, Tools, Source, and Wine source; do not create images or hash large assets")
    phases.add_argument("--images-only", action="store_true",
                        help="create/verify DMGs and generate release-manifest/SHA from a prepared cache")
    return parser


def main(argv=None) -> int:
    args = make_parser().parse_args(argv)
    try:
        args.version = validate_version(args.version)
        if args.images_only:
            result = images_phase(args)
        else:
            for name in ("app", "source_repo", "wine_source"):
                if getattr(args, name) is None:
                    raise DistributionError(f"--{name.replace('_', '-')} is required in the prepare phase")
            result = prepare_phase(args)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (DistributionError, OSError, ValueError, subprocess.SubprocessError) as error:
        print(f"DISTRIBUTION ABORTED: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
