"""Synthetic registry cleanup regressions; no app, prefix or game access."""
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("privacy", Path(__file__).with_name("prepare_distribution_app.py"))
privacy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(privacy)


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
    print("PASS: hardware/audio/session caches removed, host name neutral, runtime settings preserved")


if __name__ == "__main__":
    main()
