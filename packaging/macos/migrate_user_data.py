"""Merge missing player data into a backed-up destination prefix without overwrites."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def inventory(users):
    result = {}
    for current, dirs, names in os.walk(users, followlinks=False):
        parent = Path(current)
        for name in list(dirs):
            entry = parent / name
            if entry.is_symlink():
                dirs.remove(name)
        for name in names:
            path = parent / name
            relative = path.relative_to(users)
            parts = relative.parts
            # Game-only player directories; do not migrate Windows profile internals.
            if not any(value in parts for value in ("Red Alert 3 Uprising", "紅色警戒3 - 起義時刻")):
                continue
            if path.is_symlink() or not path.is_file():
                raise RuntimeError("Unexpected player-data entry: " + str(relative))
            result[str(relative)] = {"bytes": path.stat().st_size, "sha256": digest(path)}
    return result


def migrate(source, target, backup, report):
    source, target, backup, report = map(lambda p: Path(p).resolve(), (source, target, backup, report))
    if source == target or source in target.parents or target in source.parents:
        raise RuntimeError("Source and destination must be separate user roots")
    if report.exists():
        raise RuntimeError("Migration report already exists")
    before_source, before_target = inventory(source), inventory(target)
    backup_files = inventory(backup)
    if backup_files != before_target:
        raise RuntimeError("Destination backup differs; take a new backup before migrating")
    copied, identical, conflicts = [], [], []
    for relative, identity in sorted(before_source.items()):
        src, dst = source / relative, target / relative
        if os.path.lexists(dst):
            if dst.is_file() and not dst.is_symlink() and digest(dst) == identity["sha256"]:
                identical.append(relative)
            else:
                conflicts.append(relative)
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        with src.open("rb") as stream, dst.open("xb") as output:
            shutil.copyfileobj(stream, output)
        shutil.copystat(src, dst, follow_symlinks=False)
        if digest(dst) != identity["sha256"]:
            raise RuntimeError("Migrated data differs: " + relative)
        copied.append(relative)
    after_source, after_target = inventory(source), inventory(target)
    if after_source != before_source:
        raise RuntimeError("Source changed while migrating")
    for relative, identity in before_target.items():
        if after_target.get(relative) != identity:
            raise RuntimeError("Existing destination data changed: " + relative)
    for relative in copied:
        if after_target.get(relative) != before_source[relative]:
            raise RuntimeError("Migrated file identity differs: " + relative)
    result = {"source": str(source), "destination": str(target), "backup": str(backup),
              "copied": copied, "identical": identical, "conflicts_kept_destination": conflicts,
              "source_unchanged": True, "existing_destination_unchanged": True,
              "copied_file_identities": {name: before_source[name] for name in copied}}
    report.parent.mkdir(parents=True, exist_ok=True)
    with report.open("x") as output:
        json.dump(result, output, ensure_ascii=False, indent=2)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-users", required=True, type=Path)
    parser.add_argument("--target-users", required=True, type=Path)
    parser.add_argument("--backup-users", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    listing = subprocess.check_output(["/bin/ps", "-axww", "-o", "comm="], text=True).lower()
    if "ra3ep1_1.0.game" in listing:
        raise RuntimeError("Game is active; migration refused")
    result = migrate(args.source_users, args.target_users, args.backup_users, args.report)
    print("Migrated", len(result["copied"]), "missing files; preserved",
          len(result["conflicts_kept_destination"]), "existing conflicting files.")


if __name__ == "__main__":
    main()
