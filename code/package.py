"""Build code.zip for submission: README.md + code/, without caches, secrets, data or nested archives.

Usage (from the repository root): python code/package.py [--out code.zip]
code/.cache holds cached model replies, OCR text and per-case results derived from the dataset (they contain
dataset personal data), so it is never packaged (security review F8).
"""
import argparse
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import CODE_DIR, REPO_ROOT  # noqa: E402

EXCLUDED_DIRS = {".cache", "out", "__pycache__", ".pytest_cache", ".venv", "venv", "node_modules", ".git"}
EXCLUDED_SUFFIXES = {".pyc", ".pyo", ".zip", ".tar", ".gz", ".tgz", ".7z", ".rar", ".db", ".sqlite"}
EXCLUDED_NAMES = {".env", ".DS_Store"}


def included_files(code_dir=CODE_DIR):
    for path in sorted(Path(code_dir).rglob("*")):
        rel = path.relative_to(code_dir)
        if not path.is_file() or set(rel.parts[:-1]) & EXCLUDED_DIRS:
            continue
        if path.suffix.lower() in EXCLUDED_SUFFIXES or path.name in EXCLUDED_NAMES or path.name.startswith(".env"):
            continue
        yield path


def build(out, repo_root=REPO_ROOT, code_dir=CODE_DIR):
    names = []
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        readme = Path(repo_root) / "README.md"
        if readme.exists():
            zf.write(readme, "README.md")
            names.append("README.md")
        for path in included_files(code_dir):
            arc = "code/" + path.relative_to(code_dir).as_posix()
            zf.write(path, arc)
            names.append(arc)
    return names


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=str(REPO_ROOT / "code.zip"))
    args = ap.parse_args(argv)
    names = build(args.out, REPO_ROOT, CODE_DIR)
    print(f"wrote {args.out}: {len(names)} files")
    missing = 0
    for required in ("README.md", "code/main.py", "code/evaluation/evaluation_report.md",
                     "code/evaluation/usage_report.md"):
        print(("ok      " if required in names else "MISSING ") + required)
        missing += required not in names
    return 1 if missing else 0  # a submission without the required reports must not look successful


if __name__ == "__main__":
    raise SystemExit(main())
