/**
 * Control panel for the git-lane chart. Lets the user map the edge columns and
 * the pass-rate decoration; defaults match the RFC `commit_dag_edges` /
 * `commit_graph_decorated` dataset column names.
 */
import { t } from '@superset-ui/core';
import { ControlPanelConfig } from '@superset-ui/chart-controls';

const config: ControlPanelConfig = {
  controlPanelSections: [
    {
      label: t('Commit graph'),
      expanded: true,
      controlSetRows: [
        [
          {
            name: 'sourceColumn',
            config: {
              type: 'SelectControl',
              label: t('Child SHA column'),
              description: t('Edge source — the commit (defaults to child_sha).'),
              mapStateToProps: state => ({
                choices: (state?.datasource?.columns ?? []).map((c: { column_name: string }) => [
                  c.column_name,
                  c.column_name,
                ]),
              }),
              default: 'child_sha',
              freeForm: true,
            },
          },
        ],
        [
          {
            name: 'targetColumn',
            config: {
              type: 'SelectControl',
              label: t('Parent SHA column'),
              description: t('Edge target — the parent (defaults to parent_sha).'),
              default: 'parent_sha',
              freeForm: true,
            },
          },
        ],
        [
          {
            name: 'labelColumn',
            config: {
              type: 'SelectControl',
              label: t('Commit subject column'),
              default: 'subject',
              freeForm: true,
              clearable: true,
            },
          },
        ],
        [
          {
            name: 'passRateColumn',
            config: {
              type: 'SelectControl',
              label: t('Pass-rate column'),
              description: t('Numeric %; colours the nodes (defaults to pass_rate_pct).'),
              default: 'pass_rate_pct',
              freeForm: true,
              clearable: true,
            },
          },
        ],
      ],
    },
    {
      label: t('Layout'),
      expanded: false,
      controlSetRows: [
        [
          {
            name: 'rowHeight',
            config: {
              type: 'SliderControl',
              label: t('Row height'),
              min: 12,
              max: 48,
              default: 22,
            },
          },
        ],
        [
          {
            name: 'laneWidth',
            config: {
              type: 'SliderControl',
              label: t('Lane width'),
              min: 12,
              max: 48,
              default: 22,
            },
          },
        ],
      ],
    },
  ],
};

export default config;
