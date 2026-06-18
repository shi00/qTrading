"""Pre-commit hook: forbid unittest.IsolatedAsyncioTestCase in tests.

Usage: python scripts/check_no_isolated_asyncio.py <files...>
Exit 1 if any file contains IsolatedAsyncioTestCase (excluding comments).
"""

import sys


def main() -> None:
    violations: list[str] = []
    for filepath in sys.argv[1:]:
        try:
            with open(filepath, encoding="utf-8") as f:
                for i, line in enumerate(f, 1):
                    if "IsolatedAsyncioTestCase" not in line:
                        continue
                    stripped = line.strip()
                    # Allow comments and docstring references
                    if stripped.startswith("#") or stripped.startswith('"""') or stripped.startswith("'''"):
                        continue
                    if "manual loop management" in line:
                        continue
                    violations.append(f"{filepath}:{i}: {stripped}")
        except OSError:
            continue

    if violations:
        print("IsolatedAsyncioTestCase is forbidden (use pytest-asyncio style instead):")
        for v in violations:
            print(f"  {v}")
        sys.exit(1)


if __name__ == "__main__":
    main()
