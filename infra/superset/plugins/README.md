# Custom Superset viz plugins

Custom chart types for the RFC Superset deployment. Superset has **no runtime
plugin-drop** for viz types — a custom `viz_type` must be compiled into the
frontend bundle — so shipping one means building a **custom Superset image**
off `apache/superset:6.0.0` with the plugin registered in the frontend's
`MainPreset`. See [`Dockerfile.gitlane`](../Dockerfile.gitlane).

## Plugins

### `plugin-chart-git-lane` — git-lane commit DAG

Renders the git commit graph as a proper `git log --graph`-style lane diagram
(branches in columns, merges joining lanes), with each commit node **coloured
by the pass/fail of the test runs that ran at that exact SHA**. This is the
Phase-3 replacement for the interim native ECharts *Graph* chart (a
force-directed blob) seeded by `bootstrap_dashboards.py`.

**Data contract.** The plugin consumes the two datasets the RFC bootstrap
already defines (see `robotframework-chat/superset/bootstrap_dashboards.py`):

- `commit_dag_edges` — the edge list: `child_sha`, `parent_sha`,
  `child_short`, `parent_short`, `parent_index`.
- `commit_graph_decorated` — per-commit decoration: `sha`, `short_sha`,
  `subject`, `author`, `committed_at`, `is_merge`, `pass_rate_pct`,
  `run_count`, `passed`, `failed`.

The chart reads edges from its own dataset and looks up decoration by SHA.

## ⚠️ Build & verification status

**The plugin source in this directory has NOT been compiled or runtime-tested
in CI.** Building it requires the full Superset frontend source tree and a
large Node/webpack build that does not run in the project's cloud CI (which
only exercises Python — see `.github/workflows/ci.yml`). Treat the TypeScript
here as reviewable, buildable source that a frontend build must validate before
deployment, not as a proven artifact.

### Building the custom image

```bash
# From infra/superset/
docker build -f Dockerfile.gitlane -t rfc-superset-gitlane:6.0.0 .
```

The Dockerfile: (1) checks out the Superset 6.0.0 frontend, (2) copies this
plugin in and adds it to `superset-frontend/package.json` + `MainPreset.js`,
(3) runs `npm ci && npm run build`, (4) layers the built assets onto the
runtime image.

### Registering the chart

Once the custom image is deployed, switch the seeded **"Commit DAG (interim)"**
chart from `viz_type: graph_chart` to `viz_type: git_lane_graph` (the value in
`GitLaneChartPlugin.ts` metadata) and point it at the `commit_dag_edges`
dataset. The interim chart is intentionally left on the stock type so the
default dashboard renders on a plain `apache/superset:6.0.0` image until the
custom image is built.
