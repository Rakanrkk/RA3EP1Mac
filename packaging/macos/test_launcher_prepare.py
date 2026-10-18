"""Exercise native --prepare-only with tiny, uniquely named fixture bundles."""
import hashlib
import json
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
import tempfile
import uuid


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    binary = Path(sys.argv[1]).resolve()
    checks = 0
    with tempfile.TemporaryDirectory(prefix="ra3-native-prepare-") as temporary:
        base = Path(temporary)
        identifier = "org.ra3mac.PrepareFixture." + uuid.uuid4().hex
        support = Path.home() / "Library/Application Support" / identifier
        assert not support.exists()
        app = base / "Fixture.app"
        resources = app / "Contents/Resources"
        template = resources / "prefix-template"
        macos = app / "Contents/MacOS"
        macos.mkdir(parents=True)
        shutil.copy2(binary, macos / "RA3Launcher")
        files = {
            "wine/bin/wine": b"#!/bin/sh\nexit 99\n",
            "wine/bin/wineserver": b"#!/bin/sh\nexit 99\n",
            "display-mode": b"#!/bin/sh\nexit 99\n",
            "prefix-template/drive_c/ra3-mtld3d/Data/game.bin": b"GAME-FIXTURE",
            "prefix-template/drive_c/u60-mtld3d/Uprising60.exe": b"PAYLOAD-EXE",
            "prefix-template/drive_c/u60-mtld3d/uprising60.dll": b"PAYLOAD-DLL",
            "prefix-template/drive_c/users/player/AppData/Roaming/Red Alert 3 Uprising/Profiles/1/Options.ini":
                b"Quality = UserValue\r\nResolution = 3024 1900\r\n",
        }
        for relative, data in files.items():
            target = resources / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            if relative in ("wine/bin/wine", "wine/bin/wineserver", "display-mode"):
                target.chmod(0o755)
        (template / "dosdevices").mkdir()
        (template / "dosdevices/c:").symlink_to("../drive_c")
        plan = {"environment": {"RA3_SAFE_VIEWPORT": "3840x2160:0,0,3840,2160"},
                "screen_width": 3840, "screen_height": 2160, "left": 0, "top": 0,
                "right": 3840, "bottom": 2160, "viewport_width": 3840,
                "viewport_height": 2160, "drawable_width": 1920,
                "drawable_height": 1080, "display_id": 123}
        planner = resources / "display-plan"
        planner.write_text("#!/bin/sh\nprintf '%s\\n' '" + json.dumps(plan) + "'\n")
        planner.chmod(0o755)
        manifest = {"schema": "ra3mac-app-v1", "version": "test",
                    "presentation": "safe-area-v1",
                    "display_plan": {"sha256": digest(planner)},
                    "runtime_pins": {"bin/wine": digest(resources / "wine/bin/wine")},
                    "game_pins": {"Data/game.bin": digest(resources / "prefix-template/drive_c/ra3-mtld3d/Data/game.bin")},
                    "payload_pins": {name: digest(template / "drive_c/u60-mtld3d" / name)
                                     for name in ("Uprising60.exe", "uprising60.dll")}}
        (resources / "manifest.json").write_text(json.dumps(manifest))
        plistlib.dump({"CFBundleIdentifier": identifier, "CFBundleExecutable": "RA3Launcher",
                       "CFBundlePackageType": "APPL"}, (app / "Contents/Info.plist").open("wb"))
        source_options = template / "drive_c/users/player/AppData/Roaming/Red Alert 3 Uprising/Profiles/1/Options.ini"
        options_relative = source_options.relative_to(template)

        def invoke(success):
            nonlocal checks
            result = subprocess.run([str(macos / "RA3Launcher"), "--prepare-only"],
                                    capture_output=True, text=True, timeout=15)
            assert (result.returncode == 0) == success, (result.returncode, result.stdout, result.stderr)
            checks += 1

        try:
            invoke(True)
            prefix = support / "prefix"
            options = prefix / options_relative
            assert "Resolution = 1920 1080" in options.read_text()
            assert "Quality = UserValue" in options.read_text()
            assert "Resolution = 3024 1900" in source_options.read_text()
            assert (prefix / "dosdevices/z:").is_symlink()
            checks += 4
            options.write_text("Quality = Preserved\nResolution = 800 600\n")
            invoke(True)
            assert options.read_text() == "Quality = Preserved\nResolution = 800 600\n"
            checks += 1
            original_planner = planner.read_bytes()
            planner.write_bytes(original_planner + b"# changed\n")
            invoke(False)
            assert options.read_text() == "Quality = Preserved\nResolution = 800 600\n"
            planner.write_bytes(original_planner)
            checks += 1
            marker = prefix / ".ra3mac-ready.json"
            marker.write_text('{"version":"wrong"}')
            invoke(False)
            assert options.read_text() == "Quality = Preserved\nResolution = 800 600\n"
            checks += 1
            shutil.rmtree(prefix)
            source_options.write_text("Resolution = 800 600\nResolution = 640 480\n")
            invoke(False)
            assert not prefix.exists() and not list(support.glob("prefix-preparing-*"))
            checks += 1
            planner.write_text("#!/bin/sh\nexit 7\n")
            manifest["display_plan"]["sha256"] = digest(planner)
            (resources / "manifest.json").write_text(json.dumps(manifest))
            invoke(False)
            assert not prefix.exists() and not list(support.glob("prefix-preparing-*"))
            checks += 1
        finally:
            if support.exists():
                assert support.name == identifier and identifier.startswith("org.ra3mac.PrepareFixture.")
                shutil.rmtree(support)
    print(f"PASS: {checks} native preparation checks; fixture prefix only, no Wine/game/UI")


if __name__ == "__main__":
    main()
