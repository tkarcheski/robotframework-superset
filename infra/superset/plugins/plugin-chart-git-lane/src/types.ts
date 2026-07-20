/**
 * Shared types for the git-lane commit DAG chart.
 */
import { QueryFormData } from '@superset-ui/core';

/** One row from the `commit_dag_edges` dataset (the chart's datasource). */
export interface EdgeRow {
  child_sha: string;
  parent_sha: string;
  child_short?: string;
  parent_short?: string;
  parent_index?: number;
}

/**
 * Per-commit decoration, looked up by SHA. In the current build this is
 * carried inline on the edge rows when the dataset joins
 * `commit_graph_decorated`; kept as a distinct type so a future control can
 * source it from a second dataset.
 */
export interface CommitDecoration {
  sha: string;
  short_sha: string;
  subject: string;
  author: string;
  committed_at: string;
  is_merge: boolean;
  pass_rate_pct: number | null;
  run_count: number;
}

/** A node after lane layout: a commit placed on the (lane, row) grid. */
export interface LaidOutNode {
  sha: string;
  short: string;
  lane: number;
  row: number;
  passRate: number | null;
  decoration?: CommitDecoration;
}

export interface LaidOutEdge {
  fromSha: string;
  toSha: string;
  isMerge: boolean;
}

export interface GitLaneFormData extends QueryFormData {
  sourceColumn: string;
  targetColumn: string;
  labelColumn?: string;
  passRateColumn?: string;
  rowHeight?: number;
  laneWidth?: number;
}

export interface GitLaneProps {
  width: number;
  height: number;
  nodes: LaidOutNode[];
  edges: LaidOutEdge[];
  rowHeight: number;
  laneWidth: number;
}
