# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 1.x.x   | :white_check_mark: |

## Reporting a Vulnerability

We take security vulnerabilities seriously. If you discover a security vulnerability in AStockScreener, please report it responsibly.

### How to Report

**Please do NOT report security vulnerabilities through public GitHub issues.**

Instead, please report them via:

1. **GitHub Security Advisories** (Preferred): Use the [Security Advisories](https://github.com/louis2sin/AStockScreener/security/advisories) feature to report a vulnerability privately.

2. **Email**: Send details to `louis2sin@gmail.com` with the subject line `[SECURITY] AStockScreener Vulnerability Report`.

### What to Include

Please include the following information in your report:

- Description of the vulnerability
- Steps to reproduce
- Affected versions
- Potential impact
- Suggested fix (if any)

### Response Timeline

- **Initial Response**: Within 48 hours
- **Status Update**: Within 7 days
- **Resolution Target**: Critical vulnerabilities within 30 days

### Disclosure Policy

- We follow responsible disclosure practices
- We request 90 days to address vulnerabilities before public disclosure
- We will credit researchers who report valid vulnerabilities (unless you prefer to remain anonymous)

## Security Features

AStockScreener implements the following security measures:

### Credential Management

- API keys and tokens are stored securely using `keyring` (OS-level credential storage)
- Fallback encryption using AES-GCM for environments without keyring support
- No hardcoded secrets in source code

### Data Protection

- Database credentials are never logged
- Sensitive data is sanitized in logs using `DataSanitizer`
- Local cache is protected from unauthorized access

### CI/CD Security

- All GitHub Actions are pinned to specific commit SHAs
- CodeQL analysis for security vulnerabilities
- Gitleaks scanning for secret detection
- Dependency vulnerability scanning with pip-audit (dual source: PyPI + OSV)
- SLSA build provenance attestation for releases
- SBOM (Software Bill of Materials) for each release

### Security Contacts

- **Primary**: louis2sin@gmail.com

## Security Best Practices for Users

1. **Keep your installation updated** - Always use the latest release
2. **Protect your API keys** - Never share your Tushare or LLM API keys
3. **Secure your database** - Use strong passwords for PostgreSQL
4. **Review permissions** - Only grant necessary file system permissions

## Known Security Considerations

### diskcache (CVE-2025-69872)

The application uses `diskcache` for performance optimization. This package has a known vulnerability related to pickle deserialization. We mitigate this risk by:

- Not exposing the cache directory to untrusted users
- Using the cache only for internal performance optimization
- Monitoring for upstream fixes

See [`.security/audit-allowlist.yml`](.security/audit-allowlist.yml) for details.

## Security Changelog

| Date | Description |
|------|-------------|
| 2026-05-22 | Initial security policy; implemented CodeQL, Gitleaks, SBOM, and attestation |
