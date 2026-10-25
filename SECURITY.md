# Security Policy

<!-- NOTE: The "Supported Versions" table is maintained manually at each release and verified in CI by `scripts/verify_versions.py` (check 5), which requires the row to match the major.minor of pyproject.toml. SECURITY.md is listed in release-please-config.json extra-files, but its generic updater needs an `x-release-please-version` marker, which this file does not use, and a wildcard row such as `0.10.x` cannot be rewritten automatically. Update the table by hand when the supported major.minor changes. -->

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 0.10.x  | :white_check_mark: |
| < 0.10  | :x:                |

## Reporting a Vulnerability

We take security vulnerabilities seriously. If you discover a security vulnerability in AStockScreener, please report it responsibly.

### How to Report

**Please do NOT report security vulnerabilities through public GitHub issues.**

Instead, please report them via:

1. **GitHub Security Advisories** (Preferred): Use the [Security Advisories](https://github.com/shi00/qTrading/security/advisories) feature to report a vulnerability privately.

2. **Email**: Send details to `louis2sin@gmail.com` with the subject line `[SECURITY] AStockScreener Vulnerability Report`.

### What to Include

Please include the following information in your report:

- Description of the vulnerability
- Steps to reproduce
- Affected versions
- Potential impact
- Suggested fix (if any)

**Do not include real API tokens, database passwords, personal data, or other secrets in your report.** Redact them or replace them with placeholders; if a value must be shown, use a revoked or dummy one.

### Response Timeline

- **Initial Response**: Within 48 hours
- **Status Update**: Within 7 days
- **Resolution Target**: Critical vulnerabilities within 30 days

These timelines are best-effort targets, not contractual guarantees.

### Disclosure Policy

- We follow responsible disclosure practices
- We request 90 days to address vulnerabilities before public disclosure
- We will credit researchers who report valid vulnerabilities (unless you prefer to remain anonymous)

## Security Features

AStockScreener implements the following security measures:

### Credential Management

- API keys and tokens are stored securely using `keyring` (OS-level credential storage)
- Fallback encryption using AES-GCM for environments without keyring support
- No hardcoded secrets are allowed in source code (enforced by secret scanning and pre-commit red-line checks)

### Tushare Token Security

- The Tushare token is stored encrypted in the OS keyring; on systems without a working keyring it falls back to an AES-GCM-encrypted file and is never written to the database.
- All logs and exception messages from `TushareClient` pass the token through `DataSanitizer` before output (CLAUDE.md §3.1, rule R9).
- When token authentication fails, a global circuit breaker is armed (`_token_invalid` set to `True`) and all API calls fail fast, preventing repeated logging of the invalid token.
- `set_token()` / `set_token_async()` reset the circuit breaker and refresh the SDK's global state; the token update path never writes the plaintext token to any log channel.
- Static guard: `check_R_tushare_token_log` in `scripts/check_redlines.py` verifies that `data/external/tushare_client.py` contains no logger call that prints the token directly.

### Data Protection

- Database credentials are never logged
- Sensitive data is sanitized in logs using `DataSanitizer`
- Cache directories are not exposed to untrusted users; access control relies on OS-level user permissions

### CI/CD Security

- GitHub Actions are pinned to specific commit SHAs, except `dtolnay/rust-toolchain`, which follows the upstream `stable` tag
- CodeQL analysis for security vulnerabilities
- Gitleaks scanning for secret detection
- Dependency vulnerability scanning with pip-audit (source: PyPI)
- SLSA build provenance attestation for releases
- SBOM (Software Bill of Materials) for each release

## Security Contacts

- **Primary**: louis2sin@gmail.com

## Security Best Practices for Users

1. **Keep your installation updated** - Always use the latest release
2. **Protect your API keys** - Never share your Tushare or LLM API keys
3. **Secure your database** - Use strong passwords for PostgreSQL
4. **Review permissions** - Only grant necessary file system permissions

## Known Security Considerations

### diskcache (CVE-2025-69872)

`diskcache` is a transitive dependency (not used directly; the source contains no `import diskcache`) and appears only in `requirements*.txt` and `.security/audit-allowlist.yml`. The package is affected by a pickle deserialization vulnerability; because the application never deserializes untrusted input, the risk is contained and tracked through the allowlist.

See [`.security/audit-allowlist.yml`](.security/audit-allowlist.yml) for details.

## Security Changelog

| Date | Description |
|------|-------------|
| 2026-05-22 | Initial security policy; implemented CodeQL, Gitleaks, SBOM, and attestation |
| 2026-06-18 | Upgrade aiohttp to 3.14.0 (fixes CVE-2026-47265 / CVE-2026-34993); litellm transitive dependency upgrade |
| 2026-10-21 | Upgrade pillow to 13.0.0 (fixes 8 image-parsing CVEs), certifi to 2026.10.19 (PYSEC-2026-4320), and uv to 0.13.4 (PYSEC-2026-4321); restored the pip-audit gate |
| 2026-10-23 | Upgrade opentelemetry-api to 1.45.1 (fixes CVE-2026-114603, regex backtracking DoS in `TraceState.from_header` tracestate parsing); restored the pip-audit gate |
| 2026-10-23 | Upgrade brotlicffi to 1.2.1.0 (fixes PYSEC-2026-4323 / CVE-2026-111497, out-of-bounds write in the Brotli decoder, CVSS 9.2, RCE); restored the pip-audit gate |
