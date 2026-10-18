"""Create a read-only DMG from a prepared folder without modifying its contents."""
import argparse
from pathlib import Path
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--volume-name", required=True)
    parser.add_argument("--format", choices=["UDZO", "ULFO", "ULMO"], default="ULFO")
    args = parser.parse_args()
    source, output = args.source.resolve(), args.output.absolute()
    if not source.is_dir() or output.exists() or output.is_symlink() or output.suffix != ".dmg":
        parser.error("Use an existing source folder and a new absent .dmg output")
    if source in output.parents:
        parser.error("DMG output cannot be inside its source folder")
    output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["/usr/bin/hdiutil", "create", "-srcfolder", str(source), "-volname", args.volume_name,
                    "-fs", "APFS", "-format", args.format, "-nospotlight", str(output)], check=True)
    subprocess.run(["/usr/bin/hdiutil", "verify", str(output)], check=True)
    print("Created and verified read-only DMG:", output.name)


if __name__ == "__main__":
    main()
