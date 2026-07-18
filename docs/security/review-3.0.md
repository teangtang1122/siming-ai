# Siming 3.0 Security Review

Reviewed for `3.0.1`. This review covers repository code and deterministic tests; it is not a third-party penetration test.

## Closed Findings

| Priority | Finding | Resolution |
| --- | --- | --- |
| P0 | Browser pages could attempt state-changing requests to the loopback API | Added non-loopback Origin rejection and strict Host validation |
| P0 | SQLite file copy could omit committed WAL data | Replaced file copy with SQLite online backup plus `integrity_check` |
| P1 | SPA fallback did not explicitly prove path containment | Added resolved-root containment and traversal tests |
| P1 | Standalone migration classification depended on unrelated import order | Bootstrap now explicitly loads the complete model metadata |
| P1 | Remote Google Fonts created an unnecessary network request | Removed remote font loading and use local system font fallbacks |
| P2 | Browser responses lacked a consistent hardening policy | Added CSP, frame denial, no-sniff, referrer and permissions headers |
| P1 | Backend runtime dependencies contained published vulnerabilities | Upgraded FastAPI/Starlette, multipart parsing, cryptography, aiohttp and dotenv to audited fix versions |
| P1 | Frontend runtime dependencies contained published vulnerabilities | Upgraded Axios, React Router, markdown-it and linkify-it to audited fix versions |

## Residual Risks

1. **Unsigned Windows executable.** Automatic installation requires a valid Authenticode signature and therefore fails closed for current unsigned releases. Manual downloads may still trigger antivirus reputation warnings. SignPath or another trusted certificate remains the correct fix.
2. **Local-process authority.** A process already running as the author can call a no-Origin loopback API or edit application files. Preventing this requires OS sandboxing or local authentication and is outside the current single-user desktop model.
3. **Third-party providers and CLIs.** Prompts and selected story context sent to them are governed by their privacy, retention and account-security policies.
4. **CSP permits inline styles.** Ant Design currently requires `style-src 'unsafe-inline'`. Scripts remain self-only; removing inline style permission requires a separate frontend nonce/style migration.
5. **Local backups share the same disk.** Migration backups protect against software failure, not device loss or ransomware. External backups are still recommended.

## Verification

Run the focused controls:

```powershell
cd backend
.venv\Scripts\python.exe -m pytest -q tests\test_http_security.py tests\test_sqlite_backup.py tests\test_database_bootstrap.py tests\test_migration_rehearsal.py
cd ..
backend\.venv\Scripts\python.exe scripts\run-performance-baseline.py
backend\.venv\Scripts\python.exe -m pip_audit -r backend\requirements.txt
npm --prefix frontend audit --omit=dev --registry=https://registry.npmjs.org
```

Release validation additionally runs backend tests, frontend lint/test/build, browser E2E, exact-tag asset verification and executable smoke tests.
