# api provider

## Handles
Claims about HTTP endpoints: a route exists, returns the right status and
body, enforces auth, validates input, has the right side effects.

## Requires
1. A running server, or a command to start one locally (`package.json`
   scripts, `Makefile`, `docker compose`, the project's launch config).
2. Credentials for authenticated routes from the project's seed data,
   fixtures, or example env. Never real production credentials.

If 1 is unmet, fall through to generic (handler or integration tests). If
only 2 is unmet, verify unauthenticated behavior and mark authenticated
claims ⚠️.

## lite
The route is registered (grep the router) and matches the OpenAPI / schema
file if one exists. Typecheck the handler.

## full
Hit the running server with `curl` (or the project's HTTP client):
- The request the claim describes, with realistic input.
- Assert status code and the fields in the body the claim promises.
- For writes, read back the result (a GET, or a DB query) to prove the side
  effect happened, not just that the response said so.

## ultra
Everything in full, plus:
- Falsifiability: revert the change, restart, repeat the request, confirm
  it fails, restore.
- Auth: no token → 401, wrong user's token → 403 or 404, never another
  user's data.
- Validation: missing, malformed, and oversized input → 4xx with a useful
  error, never 500.
- Idempotency for retries on writes, where the API promises it.
- Response matches the OpenAPI / schema file, if one exists.

## Observation
The exact request (method, path, body, with secrets redacted), status code,
the relevant body excerpt, and the read-back result for writes.
