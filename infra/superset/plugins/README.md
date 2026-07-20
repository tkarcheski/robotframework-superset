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

## Build & verification status

**The frontend build is validated.** The plugin was compiled into a real
Superset 6.0.0 frontend production build (`npm ci` + drop the pre-built plugin
into `node_modules/@rfc` + register in `MainPreset` + `npm run build`): webpack
exited 0 and the emitted bundle contains `git_lane_graph` (the `viz_type`) and
the "Git Commit DAG (lanes)" chart metadata. It also type-checks clean against
`@superset-ui/core` + `@superset-ui/chart-controls` (`tsc --noEmit`).

**Still not automated:** none of this runs in CI (which is Python-only — see
`.github/workflows/ci.yml`), and the **Docker image build** in
`Dockerfile.gitlane` (which packages the validated recipe) has not itself been
run end-to-end here. So: the plugin code and the frontend build recipe are
proven; the containerised image build is the remaining unrun step.

> Gotcha the build surfaced, encoded in `Dockerfile.gitlane`: do **not** add the
> plugin as a workspace / `file:` dependency and re-run `npm install` — that
> re-resolves Superset's pinned lockfile and drops optional deps (deck.gl,
> react-spring, …). Use pristine `npm ci`, then drop the pre-built plugin into
> `node_modules/@rfc`.

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
