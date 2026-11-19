# Deploying the FDPneo Index

The index is three containers: PostgreSQL, the server (Python), and the
client (nginx), which serves the web app and passes the API and FDP pings to
the server on one origin. `deploy/compose.yaml` runs them.

## Requirements

- Docker with Compose v2; 2 CPUs and 4 GB of memory for hundreds of FDPs
  (see [search-performance.md](search-performance.md) for 1 000 and more).
- A public HTTPS address that FDPs can reach, since they ping it, and from
  which the index can reach the FDPs.

## First start

```sh
git clone https://github.com/luizbonino/fdpneo-index.git && cd fdpneo-index
cp deploy/.env.example deploy/.env
```

In `deploy/.env`:

1. Set `POSTGRES_PASSWORD`.
2. Create an admin token and put its digest in `FDPINDEX_ADMIN_TOKENS`:
   ```sh
   docker compose -f deploy/compose.yaml --env-file deploy/.env build server
   docker compose -f deploy/compose.yaml --env-file deploy/.env run --rm server fdpindex token new
   ```
3. Set `FDPINDEX_PUBLIC_ORIGIN` and `FDPINDEX_PUBLIC_URL` to the public
   address, for example `https://index.example.org`.

Then:

```sh
docker compose -f deploy/compose.yaml --env-file deploy/.env up -d --build
```

The app is at `http://127.0.0.1:8090` (`FDPINDEX_PORT`). The server applies
database migrations when it starts.

## Going public

Put a TLS-terminating reverse proxy in front of port 8090 and set
`FDPINDEX_TRUSTED_PROXY` to its address, so that rate limits see the
clients' addresses. Details, backups and upgrades are in
[operations.md](operations.md).

## Sign-in

Admin tokens are enough to start. For an identity provider, set
`FDPINDEX_OIDC_ISSUER`, `FDPINDEX_OIDC_AUDIENCE` and
`FDPINDEX_OIDC_CLIENT_ID` (a public client with PKCE whose redirect URI is
`<public origin>/auth/callback`); administrators need the `admin` role in
`realm_access.roles` (configurable). The Compose stack's `keycloak` profile
shows a working setup (README, "Trying OIDC sign-in with Keycloak").

## Choices to make

| Choice | Setting | Default |
|---|---|---|
| Who gets listed | Settings → Registration | automatically |
| Concept labels from Wikidata, MeSH, ROR … (requests to those services) | `FDPINDEX_LABELS_RESOLVE` | off |
| Expiry of FDPs out of contact | Settings → FDPs out of contact | off |
| Search and SPARQL | `FDPINDEX_SEARCH_ENABLED`, `FDPINDEX_SPARQL_ENABLED` | on |

Next: register FDPs ([with-fdpneo.md](with-fdpneo.md) for FDPneo, the
**Register** page for any FDP) and see [administering.md](administering.md).
