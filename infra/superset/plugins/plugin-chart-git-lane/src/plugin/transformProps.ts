/**
 * Map Superset's ChartProps (query results + form data) into the props the
 * GitLane SVG component renders. Builds the decoration map by SHA and runs the
 * pure lane layout.
 */
import { ChartProps } from '@superset-ui/core';
import { layoutDag } from '../layout';
import {
  CommitDecoration,
  EdgeRow,
  GitLaneFormData,
  GitLaneProps,
} from '../types';

export default function transformProps(chartProps: ChartProps): GitLaneProps {
  const { width, height, formData, queriesData } = chartProps;
  const fd = formData as GitLaneFormData;
  const records = (queriesData?.[0]?.data ?? []) as Record<string, unknown>[];

  const source = fd.sourceColumn || 'child_sha';
  const target = fd.targetColumn || 'parent_sha';
  const labelCol = fd.labelColumn || 'subject';
  const passRateCol = fd.passRateColumn || 'pass_rate_pct';

  const edgeRows: EdgeRow[] = [];
  const decorations = new Map<string, CommitDecoration>();

  const num = (v: unknown): number | null =>
    v === null || v === undefined || v === '' ? null : Number(v);

  records.forEach(r => {
    const childSha = String(r[source] ?? '');
    const parentSha = String(r[target] ?? '');
    if (childSha && parentSha) {
      edgeRows.push({
        child_sha: childSha,
        parent_sha: parentSha,
        child_short: r.child_short as string | undefined,
        parent_short: r.parent_short as string | undefined,
        parent_index: num(r.parent_index) ?? 0,
      });
    }
    // Decoration is carried per-child row when the dataset joins
    // commit_graph_decorated; record it once per SHA.
    const sha = (r.sha as string) || childSha;
    if (sha && !decorations.has(sha)) {
      decorations.set(sha, {
        sha,
        short_sha: (r.short_sha as string) ?? sha.slice(0, 8),
        subject: (r[labelCol] as string) ?? '',
        author: (r.author as string) ?? '',
        committed_at: (r.committed_at as string) ?? '',
        is_merge: Boolean(r.is_merge),
        pass_rate_pct: num(r[passRateCol]),
        run_count: num(r.run_count) ?? 0,
      });
    }
  });

  const { nodes, edges } = layoutDag(edgeRows, decorations);

  return {
    width,
    height,
    nodes,
    edges,
    rowHeight: fd.rowHeight ?? 22,
    laneWidth: fd.laneWidth ?? 22,
  };
}
