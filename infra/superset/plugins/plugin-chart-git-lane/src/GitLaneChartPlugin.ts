/**
 * ChartPlugin registration for the git-lane commit DAG.
 *
 * `viz_type: git_lane_graph` — reference this value from the Superset bootstrap
 * once the custom image (with this plugin compiled in) is deployed.
 */
import { ChartMetadata, ChartPlugin, t } from '@superset-ui/core';
import buildQuery from './plugin/buildQuery';
import controlPanel from './plugin/controlPanel';
import transformProps from './plugin/transformProps';
import { GitLaneFormData } from './types';

export default class GitLaneChartPlugin extends ChartPlugin<GitLaneFormData> {
  constructor() {
    super({
      buildQuery,
      controlPanel,
      transformProps,
      loadChart: () => import('./GitLane'),
      metadata: new ChartMetadata({
        name: t('Git Commit DAG (lanes)'),
        description: t(
          'A git log --graph-style commit tree: branches in lanes, merges ' +
            'joining lanes, each commit coloured by the pass rate of the ' +
            'test runs at that SHA.',
        ),
        thumbnail: '',
        tags: [t('Graph'), t('RFC'), t('Git')],
      }),
    });
  }
}
