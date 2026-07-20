*** Settings ***
Documentation     Superset observability smoke suites for robotframework-superset.
...
...               Verify that the ``events`` database Superset reads is reachable
...               and that provisioned dashboards render, using only this
...               package's own keyword libraries -- no dependency on any private
...               ``rfc.*`` module. Ported from robotframework-chat's
...               ``robot/20__tier2/superset/`` and genericized: the tier / verify
...               / axis taxonomy stays in robotframework-chat; plain tags here.
...
...               Structural gate: ``robot --dryrun robot/`` must parse clean.
Test Tags         superset
