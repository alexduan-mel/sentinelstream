# 0005: Signed-cookie sessions and in-memory login rate limiting

Status: accepted, 25 Nov 2026

## Context

The web UI needs a sign-in before it shows task runs or lets anyone queue work. There is one user, one `api` container and no public endpoint: the UI is reached through SSM port forwarding. The prototype already had working PBKDF2 password hashing, HMAC-signed session cookies and a per-client login lockout. Its auth was tied to the Holdings unlock flow and a row-level-security role, both of which are left in the prototype.

## Decision

**Passwords.** PBKDF2-HMAC-SHA256 with 600,000 iterations and a 16-byte salt, stored as `pbkdf2_sha256$iterations$salt$digest` in `app_users.password_hash`. `001_baseline.sql` seeds an `admin` row with no hash. The first login that presents `AUTH_ADMIN_PASSWORD` installs the hash, so no password ever appears in SQL or git. After that, the stored hash is the only check. An unknown username still runs one PBKDF2 check, so it takes as long as a wrong password.

**Sessions.** The cookie `ss_session` holds `{id, exp}` signed with HMAC-SHA256 under `AUTH_SESSION_SECRET`. It is HttpOnly, SameSite=Strict, scoped to `/api`, and valid for 12 hours from login, with no sliding renewal. Each request re-reads the user, so deactivating a user ends their sessions at once. There is no session table, which keeps the schema unchanged. Logging out clears the cookie but cannot revoke a copied one. Rotating `AUTH_SESSION_SECRET` signs everyone out.

**Default deny.** Every `/api` router except login and logout is mounted with the signed-in-user dependency. A test walks the OpenAPI schema and asserts that every other `/api` path answers 401 without a session.

**Rate limiting.** These are the prototype's values: 5 failed logins from one client address within 5 minutes lock that address out for 5 minutes. A locked-out request gets 429 with `Retry-After` and `retry_after_seconds`, even if the password is right. Counts live in the API process.

## Consequences

- Counts reset when the API restarts, and they would not be shared if the API ran more than one process. Both are fine for one container. A shared store (a Postgres table) is the fix if the API scales out.
- Behind SSM port forwarding every request arrives from the same address, so a lockout applies to every browser at once. With one user, that is the intended effect.
- `AUTH_COOKIE_SECURE` stays false while the UI is plain `http://localhost` through the tunnel, and must be set to true if the UI is ever served over HTTPS.
