# Releasing `robotframework-superset`

Publishing to [PyPI](https://pypi.org/project/robotframework-superset/) is a
single, owner-gated action. Authentication uses **PyPI Trusted Publishing
(OIDC)**: GitHub Actions mints a short-lived OIDC token at publish time, so
**no long-lived API token is stored in repository secrets**. The release
pipeline lives in [`.github/workflows/release.yml`](../.github/workflows/release.yml).

## One-time owner setup

These steps are performed once by the repository owner. No secrets are added to
the repository — Trusted Publishing replaces the API token entirely.

### 1. Create the `pypi` GitHub environment

1. Open **Settings → Environments → New environment** and name it `pypi`.
2. Optional but recommended: add **Required reviewers** so the upload step waits
   for manual approval before it runs. This is the human gate on every publish.

The environment name must be exactly `pypi` — it is referenced by the publish
job in `release.yml`.

### 2. Register the Trusted Publisher on PyPI

Configure PyPI to trust this repository's release workflow.

- If the project does **not** exist on PyPI yet, add a **pending publisher**:
  PyPI → **Your account → Publishing → Add a new pending publisher**.
- If the project already exists, add the publisher under the project's
  **Settings → Publishing → Add a new publisher**.

Enter these values (all are public identifiers, not secrets):

| Field                | Value                              |
| -------------------- | ---------------------------------- |
| PyPI Project Name    | `robotframework-superset`          |
| Owner                | `tkarcheski`                       |
| Repository name      | `robotframework-superset`          |
| Workflow name        | `release.yml`                      |
| Environment name     | `pypi`                             |

That is the entire credential setup. Nothing is committed to the repository and
nothing is placed in GitHub Actions secrets.

## Cutting a release

1. Bump `version` in [`pyproject.toml`](../pyproject.toml) and land it on `main`
   through the normal PR flow.
2. Tag the release commit and push the tag:

   ```bash
   git tag v0.1.0
   git push origin v0.1.0
   ```

   Pushing a `v*` tag triggers `release.yml`. Alternatively, run it manually via
   **Actions → Release → Run workflow**.
3. If the `pypi` environment requires reviewers, approve the pending deployment
   when prompted. The `build` job (sdist + wheel + `twine check`) always runs
   first; the `publish` job runs only after approval.
4. Confirm the new version appears at
   <https://pypi.org/project/robotframework-superset/>.

The tag version and the `pyproject.toml` version must match. PyPI rejects a
re-upload of an existing version, so bump the version for every release.

## What is deliberately not automated

- **No token-based fallback.** Trusted Publishing is the only configured path,
  per the epic's "no secrets" requirement. A token-based publish would require
  storing `PYPI_API_TOKEN` in secrets and is intentionally omitted.
- **Coverage badge.** CI reports coverage on every run (see the `test` job) and
  uploads `coverage.xml` as a build artifact. A live coverage *badge* requires a
  reporting service (for example Codecov) whose upload needs owner-provisioned
  configuration; wiring that up is left to the owner and tracked as a follow-up.
