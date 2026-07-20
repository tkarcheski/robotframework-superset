*** Settings ***
Documentation     Framework-agnostic Superset dashboard smoke test.
...
...               Discovers dashboards via the Superset REST API and confirms
...               each renders without a 404/error. No browser, no hard-coded
...               dashboard IDs, and no ``rfc.*`` dependency -- a reduction of
...               robotframework-chat's browser + LLM ``dashboards.robot``.
...
...               LLM-based dashboard evaluation (via the OpenAI/Ollama feeds) is
...               intentionally deferred until those feeds land; see the PR body
...               for the follow-up ticket.
Library           robotframework_superset.keywords.SupersetDashboardKeywords    WITH NAME    Superset
Test Tags         dashboards
Suite Setup       Skip Unless Superset Reachable


*** Test Cases ***
Superset Health Endpoint Responds
    [Documentation]    The Superset /health endpoint reports the service is up.
    ${status}=    Superset.Get Health Status
    Should Be Equal As Strings    ${status}    OK
    ...    Superset /health did not return OK.

Dashboards Are Discoverable Via The API
    [Documentation]    The Superset REST API returns a dashboard list without error.
    ${ids}=    Superset.List Dashboard Ids
    Log    Discovered dashboard ids: ${ids}
    # An empty list is valid on a freshly-provisioned stack; the assertion here
    # is simply that the authenticated API call succeeded.

All Discovered Dashboards Render Without Error
    [Documentation]    Every discovered dashboard loads with no 404/error.
    ${ids}=    Superset.List Dashboard Ids
    ${count}=    Get Length    ${ids}
    IF    ${count} == 0
        Skip    No dashboards provisioned yet -- nothing to smoke test.
    END
    FOR    ${id}    IN    @{ids}
        ${ok}=    Superset.Dashboard Renders    ${id}
        Should Be True    ${ok}
        ...    Dashboard ${id} returned a 404/error.
    END


*** Keywords ***
Skip Unless Superset Reachable
    [Documentation]    Skip the whole suite unless Superset answers on /health.
    ${up}=    Superset.Superset Is Reachable
    IF    not ${up}
        Skip    Superset is not reachable -- set SUPERSET_URL/SUPERSET_PORT and start the stack.
    END
