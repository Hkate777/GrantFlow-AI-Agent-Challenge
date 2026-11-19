"""Check Git-tracked and new project prose against the repository's OpenCC standard."""
import os
import subprocess
from pathlib import Path

from opencc import OpenCC


ROOT = Path(__file__).resolve().parents[1]
TEXT_SUFFIXES = {".css", ".csv", ".html", ".js", ".json", ".md", ".py", ".toml", ".txt", ".yaml", ".yml"}

# Legacy simplified-Chinese inputs kept on purpose: they exercise the backward
# compatibility path (app002's simplified attachment vs. traditional form drives
# the cross-source "inconsistent" finding; app003 is a fully simplified legacy
# application; reviews_demo.json carries simplified criteria keys that must keep
# matching legacy score files). Mark any other intentional line with `lang:keep`.
EXEMPT_PREFIXES = (
    "data/demo/applications/app002/",
    "data/demo/applications/app003/",
)
EXEMPT_FILES = {
    "data/demo/reviews/reviews_demo.json",
}
KEEP_MARK = "lang:keep"


def project_text_files():
    result = subprocess.check_output(
        ["git", "-C", str(ROOT), "ls-files", "--cached", "--others", "--exclude-standard", "-z"])
    for name in sorted(set(result.split(b"\0")) - {b""}):
        path = ROOT / os.fsdecode(name)
        if path.is_file() and (path.suffix.lower() in TEXT_SUFFIXES or path.name in {".gitignore", "LICENSE"}):
            yield path


def exempted(relative: str) -> bool:
    return (relative in EXEMPT_FILES
            or any(relative.startswith(prefix) for prefix in EXEMPT_PREFIXES))


def main() -> int:
    converter = OpenCC("s2hk")
    issues = 0
    for path in project_text_files():
        relative = path.relative_to(ROOT).as_posix()
        if exempted(relative):
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
            if converter.convert(line) != line and KEEP_MARK not in line:
                print(f"{relative}:{number} requires Hong Kong Traditional Chinese")
                issues += 1
    if issues:
        print(f"Language check failed ({issues} lines).")
        return 1
    print("Language check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
