# ADR-0003: Administrators authenticate with OIDC or static tokens

- Status: accepted
- Date: 2026-11-17

## Context

The reference index uses the FDP's own users and its `ADMIN` role. A
standalone index has no user database, and should not grow one: its only
privileged users are the few people who administer the directory.

## Decision

Two ways to be an administrator, either or both configured:

1. **OIDC bearer tokens.** With `FDPINDEX_OIDC_ISSUER` (and an audience), the
   server validates JWTs against the issuer's JWKS, like FDPneo does. A token
   whose roles claim (default `realm_access.roles`, Keycloak's) contains the
   admin role (default `admin`) is an administrator. The client signs in with
   the authorization code flow and PKCE. An FDPneo stack's Keycloak realm can
   serve both.
2. **Static admin tokens.** `FDPINDEX_ADMIN_TOKENS` holds SHA-256 hashes of
   bearer tokens, for scripts and small deployments without an identity
   provider. `fdpindex token new` prints a token and its hash.

Everything else is public and read-only, as in the reference: anonymous
callers see accepted entries only, and the `permit` filter is ignored for
them. Ping intake is public by design.

## Consequences

- No passwords, sessions or user management in the index.
- Without either option configured, the administration API is unreachable;
  the server logs a warning at start-up.
