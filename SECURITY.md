# Security Policy

<!-- NOTE: 本文件的「Supported Versions」表由 release-please 在发版时自动同步版本号（见 release-please-config.json 的 extra-files）。手动修改版本号会被下次发版覆盖。 -->

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 0.9.x   | :white_check_mark: |
| < 0.9   | :x:                |

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

### Tushare Token Security

- Tushare token 通过系统 Keyring 加密存储，无 Keyring 环境回退到 AES-GCM 加密文件，不入数据库。
- `TushareClient` 的所有日志与异常消息中，token 必须经 `DataSanitizer` 脱敏后才输出（对应 CLAUDE.md §3.1 R9 红线）。
- token 认证失败时触发全局熔断（`_token_invalid` 标志置 True），所有 API 调用 fast-fail，避免无效 token 在日志中重复刷屏。
- `set_token()` 重置熔断标志并刷新 SDK 全局状态；token 更新路径不记录明文 token 到任何日志通道。
- 静态守护：`scripts/check_redlines.py` 的 `check_R_tushare_token_log` 检查 `data/external/tushare_client.py` 中是否存在直接打印 token 的 logger 调用。

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

`diskcache` 为传递依赖（非应用直接使用，源码无 `import diskcache`），仅出现在 `requirements*.txt` 与 `.security/audit-allowlist.yml`。该包存在 pickle 反序列化漏洞，因应用不反序列化不可信输入，风险可控，通过 allowlist 跟踪。

See [`.security/audit-allowlist.yml`](.security/audit-allowlist.yml) for details.

## Security Changelog

| Date | Description |
|------|-------------|
| 2026-05-22 | Initial security policy; implemented CodeQL, Gitleaks, SBOM, and attestation |
| 2026-06-18 | 升级 aiohttp 至 3.14.0 修复 CVE-2026-47265/CVE-2026-34993；litellm 升级传递依赖修复 |
