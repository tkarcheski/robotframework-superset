*** Settings ***
Documentation     Verify connectivity to the ``events`` database that Superset reads.
...
...               A public analogue of robotframework-chat's Superset connection
...               suite, retargeted to this package's ``events`` schema and its
...               own keyword library -- no ``rfc.*`` dependency. The suite
...               targets the docker-compose PostgreSQL stack but the keywords
...               are backend-agnostic (SQLite works for local runs).
Library           robotframework_superset.keywords.SupersetKeywords    WITH NAME    Superset
Test Tags         connection


*** Test Cases ***
Database URL Is Configured
    [Documentation]    DATABASE_URL must be set; it is logged with its password masked.
    ${url}=    Superset.Get Database URL
    Log    DATABASE_URL: ${url}
    Should Not Be Equal    ${url}    NOT SET
    ...    DATABASE_URL is not configured. Set it in .env.

Database Connection Is Alive
    [Documentation]    The events database is reachable and reports a version.
    ${version}=    Superset.Connect To Database
    Log    Connected: ${version}
    Should Not Be Empty    ${version}
    ...    The database did not report a version -- connection failed.

Events Table Is Present And Countable
    [Documentation]    The events table exists and its row count is non-negative.
    ${counts}=    Superset.Get Table Row Counts
    Log    Table row counts: ${counts}
    Should Be True    ${counts}[events] >= 0
    ...    events table is missing or inaccessible (count == -1).
