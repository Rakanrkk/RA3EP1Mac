"""Check standalone launcher CLI dispatch without an app bundle or game data."""
from pathlib import Path
import subprocess
import sys


def main():
    executable = Path(sys.argv[1]).resolve()
    cases = [
        (["--help"], 0, "Usage: RA3Launcher"),
        (["-h"], 0, "Usage: RA3Launcher"),
        (["--unknown"], 2, "启动参数无效"),
        (["--verify-only", "--prepare-only"], 2, "启动参数无效"),
        ([], 2, "独立启动器文件不能直接运行"),
        (["--verify-only"], 1, "验证失败"),
    ]
    for arguments, expected, message in cases:
        result = subprocess.run([str(executable), *arguments], capture_output=True,
                                text=True, timeout=10)
        assert result.returncode == expected, (arguments, result.returncode,
                                               result.stdout, result.stderr)
        assert message in result.stdout + result.stderr, (arguments, result.stdout,
                                                          result.stderr)
    print(f"PASS: {len(cases)} unbundled CLI cases completed without UI or game startup")


if __name__ == "__main__":
    main()
