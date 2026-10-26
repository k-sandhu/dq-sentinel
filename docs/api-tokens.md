# Service API tokens

Admins can create a token in Settings with a viewer or editor role, at least one explicitly selected connection, and an expiry of at most 400 days. Copy the secret when it is shown; it cannot be retrieved later. Send it as `Authorization: Bearer dqs_...`. Store it in a secret manager and rotate by creating a replacement and revoking the old token. Revocation takes effect immediately.

Machine credentials support `GET /datasets`, `GET /datasets/{id}`, `GET /checks`, `GET /checks/{id}`, `POST /checks`, `POST /checks/{id}/run`, `GET /runs`, `GET /runs/{id}`, `GET /runs/{id}/exceptions`, `GET /exceptions`, and `POST /exceptions/triage` (all under `/api/v1`). `/auth/me` is human-only. Other endpoints require a human account. Tokens cannot manage credentials or gain admin privileges. Missing, malformed, or empty scopes grant no connection access. Machine changes are attributed in the audit log to the token name.

The last-used timestamp records successful authentication to an allowed machine endpoint; the endpoint can subsequently return 403 or 404. The timestamp is updated at most once per minute.
