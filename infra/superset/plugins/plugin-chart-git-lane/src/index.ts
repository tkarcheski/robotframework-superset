/**
 * @rfc/plugin-chart-git-lane — public entry point.
 *
 * Register in the Superset frontend's MainPreset:
 *
 *   import GitLaneChartPlugin from '@rfc/plugin-chart-git-lane';
 *   new GitLaneChartPlugin().configure({ key: 'git_lane_graph' }).register();
 */
export { default } from './GitLaneChartPlugin';
export { layoutDag } from './layout';
export * from './types';
