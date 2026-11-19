# Releasing the FDPneo Index

The server and the client are released together under one version `X.Y.Z`:
images `ghcr.io/<owner>/fdpneo-index-server` and `…-client`, tagged `X.Y.Z`,
`X.Y` and `latest`, for amd64 and arm64, signed with Sigstore keyless
signing and with build provenance attested.

## Steps

1. On a branch, set the version in both places:
   ```sh
   (cd server && uv version X.Y.Z && uv lock)
   (cd client && npm version X.Y.Z --no-git-tag-version)
   ```
2. Refresh the label pack (`cd server && uv run fdpindex vocab update`) and
   look over the diff of `search/vocab.json`; it changes when the EU
   authority tables, SPDX or GeoNames' country list gain entries.
3. Run the end-to-end tests against a local stack with some data (the
   Compose stack and `server/bench/seed_demo.py`): the smoke test and the
   accessibility pass (axe on every public page in light and dark mode, and
   the keyboard paths through the search):
   ```sh
   (cd client && FDPINDEX_E2E_URL=http://127.0.0.1:8090 npx playwright test)
   ```
4. In `CHANGELOG.md`, rename `[Unreleased]` to `[X.Y.Z] - YYYY-MM-DD` and
   start a new empty `[Unreleased]`. When operators must act on upgrade,
   add an **Upgrading** subsection.
5. Commit `chore(release): X.Y.Z` (signed off), open a pull request, merge.
6. Tag the merge commit on main and push the tag:
   ```sh
   git tag -a vX.Y.Z -m "FDPneo Index X.Y.Z" <merge commit>
   git push origin vX.Y.Z
   ```

The `release` workflow then checks that the tag, both versions and the
CHANGELOG agree and that the tag is on main; builds and pushes the images;
signs them and verifies the signature; and creates the GitHub release with
the CHANGELOG section as notes. A failed release is fixed with a new patch
version, not by moving the tag.

## PyPI

Publishing the server package (`fdpneo-index`) is off until PyPI trusts
this repository: add a trusted publisher on PyPI (workflow `release.yml`,
environment `pypi`), create the `pypi` environment in the repository, and
set the repository variable `PUBLISH_PYPI` to `true`. A version on PyPI can
never be uploaded again.

## Verifying an image

```sh
cosign verify ghcr.io/<owner>/fdpneo-index-server:X.Y.Z \
  --certificate-identity "https://github.com/<owner>/fdpneo-index/.github/workflows/release.yml@refs/tags/vX.Y.Z" \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com
```
