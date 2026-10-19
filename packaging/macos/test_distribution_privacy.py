"""Synthetic registry cleanup regressions; no app, prefix or game access."""
import importlib.util
from pathlib import Path
import shutil
import tempfile

spec = importlib.util.spec_from_file_location("privacy", Path(__file__).with_name("prepare_distribution_app.py"))
privacy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(privacy)


def test_player_archive_cleanup():
    with tempfile.TemporaryDirectory(prefix="ra3-distribution-fixture-") as temporary:
        root = Path(temporary)
        users = root / "clone/drive_c/users/player"
        saves = users / "AppData/Roaming/Red Alert 3 Uprising/Saved Games"
        replays = users / "Documents/Red Alert 3 Uprising/Replays"
        profiles = users / "AppData/Roaming/Red Alert 3 Uprising/Profiles/1"
        game_data = root / "clone/drive_c/ra3-mtld3d/Data"
        payload = root / "clone/drive_c/u60-mtld3d"
        help_dir = root / "clone/drive_c/ra3-mtld3d/Support/EA Help/en-uk"
        for path in (saves, replays, profiles, game_data, payload, help_dir):
            path.mkdir(parents=True)
        for filename, data in (("RA3UCampaign", b"campaign"), ("RA3UAutosave", b"autosave")):
            (saves / filename).write_bytes(data)
        (replays / "match.map").write_bytes(b"replay")
        (profiles / "Options.ini").write_text("Resolution = 800 600\n", encoding="utf-8")
        (game_data / "world.map").write_bytes(b"shipped-map")
        # The four real-world log shapes: payload, game engine and shipped help.
        (payload / "uprising60.log").write_bytes(b"LOG" * 66)
        (game_data / "ra3ep1_1.0.game_d3d9.log").write_bytes(b"d3d9" * 2101)
        (help_dir / "EA_Help_UK.log").write_bytes(b"help" * 1052)
        # Non-log shipped files in the same trees must survive.
        (payload / "Uprising60.exe").write_bytes(b"PE-fixture")
        source_save = root / "source/drive_c/users/player/AppData/Roaming/Red Alert 3 Uprising/Saved Games/RA3UCampaign"
        source_save.parent.mkdir(parents=True)
        source_save.write_bytes(b"original")
        removed = []
        privacy.sanitize_template_files(root / "clone/drive_c", users, root / "clone", removed)
        assert not (payload / "uprising60.log").exists()
        assert not (game_data / "ra3ep1_1.0.game_d3d9.log").exists()
        assert not (help_dir / "EA_Help_UK.log").exists()
        assert (payload / "Uprising60.exe").is_file()
        assert (game_data / "world.map").is_file()
        privacy.remove_player_archive_directories(users, root / "clone", removed)
        assert not saves.exists() and not replays.exists()
        # Non-log removals: the three archive files (dir rule) plus the two
        # emptied Saved Games/Replays directories themselves.
        assert len([item for item in removed if not item.endswith(".log")]) == 5
        assert (profiles / "Options.ini").is_file()
        assert source_save.read_bytes() == b"original"


def main():
    text = ('WINE REGISTRY Version2\n'
            '[System\\\\ControlSet001\\\\Enum\\\\DISPLAY\\\\PRIVATE-MONITOR]\n"EDID"="private"\n'
            '[System\\\\ControlSet001\\\\Enum\\\\PCI\\\\PRIVATE-GPU]\n"Model"="private"\n'
            '[Software\\\\Microsoft\\\\Windows\\\\CurrentVersion\\\\MMDevices\\\\Audio]\n"Device"="private"\n'
            '[Volatile Environment]\n"COMPUTERNAME"="private"\n'
            '[System\\\\ControlSet001\\\\Control\\\\ComputerName\\\\ComputerName]\n"ComputerName"="private-host"\n'
            '[Software\\\\Wine\\\\Mac Driver]\n"RetinaMode"="Y"\n'
            '[Software\\\\Wine\\\\X11 Driver]\n"EmulateModeset"="Y"\n')
    result, removed = privacy.sanitize_registry(text)
    assert removed == 4
    assert 'PRIVATE-MONITOR' not in result and 'PRIVATE-GPU' not in result
    assert 'private-host' not in result and '"ComputerName"="RA3EP1MAC"' in result
    assert '"RetinaMode"="Y"' in result and '"EmulateModeset"="Y"' in result
    assert privacy.sanitize_registry(result)[0] == result
    test_player_archive_cleanup()
    print("PASS: entire Saved Games/Replays directories removed from clone; profiles, game maps and source preserved")
    print("PASS: hardware/audio/session caches removed, host name neutral, runtime settings preserved")


if __name__ == "__main__":
    main()
