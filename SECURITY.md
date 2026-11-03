# Security policy

## Reporting

Please report vulnerabilities privately — open a GitHub security advisory, or email the
maintainer listed in `pyproject.toml`. Do not open a public issue for a security bug.

Especially interesting: anything that could leak a stored Moodle token or API key,
anything that lets one account reach another's data, and anything that turns the file
download endpoint into an SSRF.

## Design invariants

These are deliberate and should never be "optimised" away:

1. **Passwords are never persisted.** They exist as a local variable during one login.
2. **Tokens are encrypted at rest** with Fernet; the key is a separate file with mode
   `0600`, not in the database.
3. **API keys are stored only as SHA-256 hashes.** The plaintext is shown once, at login.
4. **Session cookies from `--cookie` / `--browser` are never stored.** They are exchanged
   once for a Moodle token and discarded.
5. **Downloads are host-restricted** to the configured eClass host (SSRF guard).
6. **No telemetry.** Nothing phones home. This is a promise, not a default.
7. **MFA is never automated.** A human approves the second factor.
8. **Logs never contain secrets.** `TokenBundle.__repr__` redacts; login traces strip
   query strings that carry SAML payloads and CSRF values.

## If your token leaks

A Moodle token is a bearer credential for your account until revoked. Run:

```powershell
coursectl revoke <your-netid>
```

Then sign in again. Revocation wipes the stored token and invalidates the API key in one
step. If you used `--cookie`, also sign out of eClass in that browser.
