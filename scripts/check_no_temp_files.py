"""Pre-commit hook: block compiled/binary/archive artifact files from being committed.

Usage: python scripts/check_no_temp_files.py <files...>

Guards against accidentally committing build artifacts unpacked by E2E tests
(e.g. embedded-postgres dlls/exes). Unlike .gitignore directory-name rules,
this hook is directory-agnostic: it blocks any staged file whose extension is
a compiled/binary/archive artifact, regardless of which directory it lives in
(e.g. a dll staged from `log_tmp/` or `pytest_e2e_tmp2/` is still blocked).

The hook inspects every file pre-commit passes to it (i.e. all staged files).
Extensions are matched case-insensitively. Files with an explicit allowlist
entry are skipped. This hook is the sole dedicated guard: .gitignore's
directory-name rules (e.g. tmp_*/) only cover in-repo temp dirs, whereas real
E2E temp dirs live in the system temp dir (outside the repo) where such rules
cannot reach them.
"""

import sys

# Compiled/binary/archive artifact extensions that should NEVER be tracked.
# NOTE(lazy): .wasm is deliberately excluded - it is a valid E2E mock asset.
#   ceiling: an exotic compiled artifact type not listed here would slip through.
#   upgrade: extend this set when a new artifact type leaks in practice.
# NOTE(lazy): the check is extension-based only - it does not sniff content
#   magic bytes, so a no-extension executable or a renamed artifact bypasses it.
#   ceiling: extension check only; no-extension/renamed binaries are not caught.
#   upgrade: sniff magic bytes (read first 4 bytes) if a bypass incident occurs.
_FORBIDDEN_EXTENSIONS = {
    ".dll",
    ".exe",
    ".pyd",
    ".so",
    ".o",
    ".a",
    ".lib",
    ".dylib",
    ".bin",
    ".class",
    ".jar",
    ".zip",
    ".7z",
    ".tar",
    ".gz",
    ".rar",
    ".pyc",
    ".pyo",
}

# Explicit allowlist for files that legitimately carry a forbidden extension.
# Currently empty: the repo tracks ZERO files with a forbidden extension, so no
# legitimate resource needs an exemption. Add an entry (repo-relative path)
# ONLY when a genuinely required binary/archive asset is introduced.
_ALLOWLIST: set[str] = set()


def main() -> None:
    violations: list[str] = []
    for filepath in sys.argv[1:]:
        if filepath in _ALLOWLIST:
            continue
        ext = filepath.rsplit(".", 1)[1].lower() if "." in filepath else ""
        full = f".{ext}" if ext else ""
        if full in _FORBIDDEN_EXTENSIONS:
            violations.append(filepath)

    if violations:
        print("Blocked staged files with compiled/binary artifact extensions:")
        for v in violations:
            print(f"  {v}")
        print(
            "These should never be committed. If this file is genuinely required, "
            "add it to the _ALLOWLIST in scripts/check_no_temp_files.py."
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
