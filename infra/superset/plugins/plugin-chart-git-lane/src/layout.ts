/**
 * Lane layout for the commit DAG — the `git log --graph` column assignment.
 *
 * Pure and framework-free so it can be unit-tested without React/Superset.
 * The algorithm is the classic single-pass lane allocator:
 *
 *   - Commits are ordered newest-first (row 0 at the top), by `committed_at`
 *     when available, else by input order.
 *   - Each lane is a vertical column that "expects" a particular SHA next.
 *     When a commit is reached it takes the lane that reserved it (or the
 *     first free lane), then hands that lane to its FIRST parent (the branch
 *     continues straight down). Additional parents (a merge) open new lanes.
 *
 * This reproduces the familiar branch-in-a-column / merge-joins-lanes picture.
 * It is an approximation — it does not minimise lane crossings the way git's
 * own renderer does — but it is stable and O(commits · lanes).
 */
import { CommitDecoration, EdgeRow, LaidOutEdge, LaidOutNode } from './types';

export interface LayoutResult {
  nodes: LaidOutNode[];
  edges: LaidOutEdge[];
  laneCount: number;
}

export function layoutDag(
  edgeRows: EdgeRow[],
  decorations: Map<string, CommitDecoration>,
): LayoutResult {
  // Parents in parent_index order (first parent = mainline).
  const parentsOf = new Map<string, string[]>();
  const shortOf = new Map<string, string>();
  const allShas = new Set<string>();

  const sortedEdges = [...edgeRows].sort(
    (a, b) => (a.parent_index ?? 0) - (b.parent_index ?? 0),
  );
  for (const e of sortedEdges) {
    allShas.add(e.child_sha);
    allShas.add(e.parent_sha);
    if (e.child_short) shortOf.set(e.child_sha, e.child_short);
    if (e.parent_short) shortOf.set(e.parent_sha, e.parent_short);
    const list = parentsOf.get(e.child_sha) ?? [];
    list.push(e.parent_sha);
    parentsOf.set(e.child_sha, list);
  }
  // Decoration may include commits with no edges (root/orphan) — include them.
  decorations.forEach((_d, sha) => allShas.add(sha));

  // Newest-first ordering. Missing timestamps sink to the bottom stably.
  const order = [...allShas].sort((a, b) => {
    const ta = decorations.get(a)?.committed_at ?? '';
    const tb = decorations.get(b)?.committed_at ?? '';
    if (ta === tb) return 0;
    return ta < tb ? 1 : -1;
  });

  const laneOf = new Map<string, number>();
  const activeLanes: (string | null)[] = [];

  const firstFree = (): number => {
    const idx = activeLanes.indexOf(null);
    if (idx !== -1) return idx;
    activeLanes.push(null);
    return activeLanes.length - 1;
  };
  const reserve = (sha: string): void => {
    // Only reserve if no lane already expects this SHA (avoid duplicate lanes
    // when two children share a parent).
    if (activeLanes.indexOf(sha) !== -1) return;
    activeLanes[firstFree()] = sha;
  };

  order.forEach(sha => {
    let lane = activeLanes.indexOf(sha);
    if (lane === -1) lane = firstFree();
    laneOf.set(sha, lane);
    activeLanes[lane] = null; // consumed

    const parents = parentsOf.get(sha) ?? [];
    if (parents.length > 0) {
      // First parent continues this commit's lane when free.
      if (activeLanes.indexOf(parents[0]) === -1) {
        activeLanes[lane] = parents[0];
      }
      for (let i = 1; i < parents.length; i += 1) reserve(parents[i]);
    }
  });

  const nodes: LaidOutNode[] = order.map((sha, row) => {
    const dec = decorations.get(sha);
    return {
      sha,
      short: shortOf.get(sha) ?? sha.slice(0, 8),
      lane: laneOf.get(sha) ?? 0,
      row,
      passRate: dec?.pass_rate_pct ?? null,
      decoration: dec,
    };
  });

  const edges: LaidOutEdge[] = [];
  parentsOf.forEach((parents, child) => {
    parents.forEach(parent => {
      if (laneOf.has(child) && laneOf.has(parent)) {
        edges.push({
          fromSha: child,
          toSha: parent,
          isMerge: parents.length > 1,
        });
      }
    });
  });

  const laneCount = activeLanes.length || 1;
  return { nodes, edges, laneCount };
}
