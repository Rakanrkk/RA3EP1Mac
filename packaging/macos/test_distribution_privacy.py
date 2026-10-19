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
        for path in (saves, replays, profiles, game_data):
            path.mkdir(parents=True)
        for filename, data in (("RA3UCampaign", b"campaign"), ("RA3UAutosave", b"autosave")):
            (saves / filename).write_bytes(data)
        (replays / "match.map").write_bytes(b"replay")
        (profiles / "Options.ini").write_text("Resolution = 800 600\n", encoding="utf-8")
        (game_data / "world.map").write_bytes(b"shipped-map")
        source_save = root / "source/drive_c/users/player/AppData/Roaming/Red Alert 3 Uprising/Saved Games/RA3UCampaign"
        source_save.parent.mkdir(parents=True)
        source_save.write_bytes(b"original")
        removed = []
        privacy.remove_player_archive_directories(users, root / "clone", removed)
        assert not saves.exists() and not replays.exists()
        assert len(removed) == 2
        assert (profiles / "Options.ini").is_file()
        assert (game_data / "world.map").read_bytes() == b"shipped-map"
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
