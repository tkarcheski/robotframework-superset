/**
 * SVG renderer for the git-lane commit DAG.
 *
 * Nodes are placed on a (lane, row) grid by `layoutDag`; this component just
 * draws them. Each node is coloured by the pass rate of the runs at that
 * commit: green at/above the threshold, amber→red below, grey when no run
 * exists for that SHA. Merges are drawn as diamonds.
 *
 * Uses the automatic JSX runtime (Superset 6.0's build), so no `import React`.
 */
import { GitLaneProps, LaidOutNode } from './types';

const PASS_THRESHOLD = 95;

function nodeColor(passRate: number | null): string {
  if (passRate === null || Number.isNaN(passRate)) return '#8899aa'; // no runs
  if (passRate >= PASS_THRESHOLD) return '#2ecc71';
  if (passRate >= 75) return '#f1c40f';
  return '#e74c3c';
}

export default function GitLane(props: GitLaneProps): JSX.Element {
  const { width, height, nodes, edges, rowHeight, laneWidth } = props;

  const marginX = laneWidth;
  const marginY = rowHeight;
  const cx = (lane: number): number => marginX + lane * laneWidth;
  const cy = (row: number): number => marginY + row * rowHeight;

  const posOf = new Map<string, LaidOutNode>();
  nodes.forEach(n => posOf.set(n.sha, n));

  const contentHeight = marginY * 2 + nodes.length * rowHeight;

  return (
    <div style={{ width, height, overflow: 'auto' }}>
      <svg width={Math.max(width, 320)} height={Math.max(height, contentHeight)}>
        {/* Edges first so nodes sit on top. */}
        {edges.map(e => {
          const a = posOf.get(e.fromSha);
          const b = posOf.get(e.toSha);
          if (!a || !b) return null;
          const x1 = cx(a.lane);
          const y1 = cy(a.row);
          const x2 = cx(b.lane);
          const y2 = cy(b.row);
          // Curve when the lane changes (a merge/branch), straight otherwise.
          const midY = (y1 + y2) / 2;
          const d =
            a.lane === b.lane
              ? `M ${x1} ${y1} L ${x2} ${y2}`
              : `M ${x1} ${y1} C ${x1} ${midY}, ${x2} ${midY}, ${x2} ${y2}`;
          return (
            <path
              key={`${e.fromSha}-${e.toSha}`}
              d={d}
              stroke={e.isMerge ? '#9b59b6' : '#7f8c8d'}
              strokeWidth={e.isMerge ? 2 : 1.5}
              fill="none"
            />
          );
        })}

        {nodes.map(n => {
          const x = cx(n.lane);
          const y = cy(n.row);
          const color = nodeColor(n.passRate);
          const isMerge = n.decoration?.is_merge ?? false;
          const label =
            n.decoration?.subject ?? n.short;
          return (
            <g key={n.sha}>
              {isMerge ? (
                <rect
                  x={x - 6}
                  y={y - 6}
                  width={12}
                  height={12}
                  transform={`rotate(45 ${x} ${y})`}
                  fill={color}
                  stroke="#2c3e50"
                />
              ) : (
                <circle cx={x} cy={y} r={6} fill={color} stroke="#2c3e50" />
              )}
              <text x={x + 12} y={y + 4} fontSize={12} fill="#c8d0da">
                <title>{`${n.short} — ${label}`}</title>
                {n.short}
                {'  '}
                {label.length > 60 ? `${label.slice(0, 57)}…` : label}
                {n.passRate !== null ? `  (${n.passRate}%)` : ''}
              </text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}
