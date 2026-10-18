"""Fixture tests for prepared-input import; never run Wine or read game assets."""
import importlib.util
from pathlib import Path
import shutil
import tempfile
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("import_runtime", Path(__file__).with_name("import_runtime.py"))
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def main():
    with tempfile.TemporaryDirectory(prefix="ra3-import-fixture-") as temporary:
        base = Path(temporary)
        base = base.resolve()
        wine, prefix, output = base / "wine", base / "prefix", base / "result"
        for relative in MODULE.RUNTIME_FILES:
            path = wine / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(relative)
        (prefix / "drive_c/ra3-mtld3d/Data").mkdir(parents=True)
        (prefix / "drive_c/u60-mtld3d").mkdir(parents=True)
        original = MODULE.windows_registry_path(wine) + b"\\\\share\\\\fonts"
        (prefix / "system.reg").write_bytes(original)
        for relative in MODULE.GAME_FILES:
            (prefix / "drive_c/ra3-mtld3d" / relative).write_text(relative)
        for relative in MODULE.PAYLOAD_FILES:
            (prefix / "drive_c/u60-mtld3d" / relative).write_text(relative)
        real_digest = MODULE.digest
        def fixture_digest(path):
            if Path(path).name == "ra3ep1_1.0.game":
                return "19b3542a7d2f7a050b16b2ea5e4ba0231b13c3fb7fcdb2c3e89767d4d354430b"
            return real_digest(path)
        with patch.object(MODULE, "digest", fixture_digest):
            result = MODULE.prepare(wine, prefix, output,
                clone=lambda src, dst: shutil.copytree(src, dst, symlinks=True))
        assert result["schema"] == "ra3mac-release-input-v1"
        assert (prefix / "system.reg").read_bytes() == original
        assert MODULE.windows_registry_path(output / "wine") in (output / "prefix/system.reg").read_bytes()
        assert (output / "candidate-manifest.json").is_file()
        try:
            MODULE.prepare(wine, prefix, output)
        except RuntimeError:
            pass
        else:
            raise AssertionError("Existing output must be refused")
        assert (prefix / "drive_c/u60-mtld3d/Uprising60.exe").is_file()
    print("PASS: prepared-runtime import, source immutability, path rebasing and no-overwrite fixtures")


if __name__ == "__main__":
    main()
