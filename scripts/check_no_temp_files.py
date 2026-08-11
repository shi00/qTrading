"""Pre-commit hook: forbid committing known temp/build artifact directories.

Usage: python scripts/check_no_temp_files.py <files...>
Exit 1 if any staged file lives under a known artifact directory
(e.g. pytest_xdist E2E work dirs that embed-postgres unpacks dlls into).
"""

import sys

# Keep this minimal & explicit: only dirs that are known to leak build/test
# artifacts and are NOT legitimately tracked. Extend only when a real leak occurs.
_FORBIDDEN_DIR_PARTS = {"pytest_e2e_tmp", "pytest_e2e_tmp2"}


def main() -> None:
    violations: list[str] = []
    for filepath in sys.argv[1:]:
        norm = filepath.replace("\\", "/")
        parts = norm.split("/")
        if any(part in _FORBIDDEN_DIR_PARTS for part in parts):
            violations.append(filepath)

    if violations:
        print("Found files under forbidden artifact directories (likely E2E runtime temp output):")
        for v in violations:
            print(f"  {v}")
        print("Remove them from the index and add the directory to .gitignore before committing.")
        sys.exit(1)


if __name__ == "__main__":
    main()
