/**
 * Query context for the git-lane chart.
 *
 * The chart is a raw edge/decoration dump, not an aggregation: it asks for the
 * columns it lays out and lets Superset apply the dataset's own row limit.
 */
import { buildQueryContext } from '@superset-ui/core';
import { GitLaneFormData } from '../types';

export default function buildQuery(formData: GitLaneFormData) {
  const { sourceColumn, targetColumn, labelColumn, passRateColumn } = formData;
  const columns = [
    sourceColumn,
    targetColumn,
    'child_short',
    'parent_short',
    'parent_index',
    'sha',
    'short_sha',
    'committed_at',
    'is_merge',
    labelColumn,
    passRateColumn,
    'run_count',
  ].filter(Boolean) as string[];

  return buildQueryContext(formData, baseQueryObject => [
    {
      ...baseQueryObject,
      // Deduplicate while preserving order.
      columns: Array.from(new Set(columns)),
      is_timeseries: false,
    },
  ]);
}
