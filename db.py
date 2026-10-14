"""SQLite storage for everything the tracker owns (the PCE stays read-only).

Schema conventions: plural snake_case tables, surrogate integer `id` primary keys,
`<entity>_id` foreign keys that are always declared and indexed, natural keys kept
as UNIQUE constraints, lookup tables instead of free-text enums, associative tables
for many-to-many relationships, and no stored values that can be derived.
The ERD is in README.md.
"""
import os
import sqlite3

DB_PATH = os.environ.get(
    "TRACKER_DB", os.path.join(os.path.dirname(os.path.abspath(__file__)), "tracker.db")
)

SCHEMA = """
-- Lookup tables -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS statuses (
    id               INTEGER PRIMARY KEY,
    name             TEXT    NOT NULL UNIQUE,
    sort_order       INTEGER NOT NULL UNIQUE,
    default_progress INTEGER NOT NULL CHECK (default_progress BETWEEN 0 AND 100)
);
CREATE TABLE IF NOT EXISTS priorities (
    id         INTEGER PRIMARY KEY,
    name       TEXT    NOT NULL UNIQUE,
    sort_order INTEGER NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS roles (
    id   INTEGER PRIMARY KEY,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL UNIQUE
);

-- Core entities -------------------------------------------------------------
CREATE TABLE IF NOT EXISTS pces (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL UNIQUE,           -- block name in config.yaml
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS people (
    id    INTEGER PRIMARY KEY,
    name  TEXT NOT NULL UNIQUE COLLATE NOCASE,
    email TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS person_roles (      -- people M:N roles
    person_id INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE,
    role_id   INTEGER NOT NULL REFERENCES roles(id)  ON DELETE CASCADE,
    PRIMARY KEY (person_id, role_id)
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS applications (      -- one per PCE "app" label
    id                 INTEGER PRIMARY KEY,
    pce_id             INTEGER NOT NULL REFERENCES pces(id)       ON DELETE CASCADE,
    label_href         TEXT    NOT NULL,
    name               TEXT    NOT NULL,
    description        TEXT    NOT NULL DEFAULT '',
    rank               INTEGER NOT NULL CHECK (rank > 0),
    priority_id        INTEGER NOT NULL REFERENCES priorities(id) ON DELETE RESTRICT,
    status_id          INTEGER NOT NULL REFERENCES statuses(id)   ON DELETE RESTRICT,
    progress           INTEGER NOT NULL DEFAULT 0 CHECK (progress BETWEEN 0 AND 100),
    start_date         TEXT,
    end_date           TEXT,
    assigned_ps_id     INTEGER REFERENCES people(id) ON DELETE SET NULL,
    project_manager_id INTEGER REFERENCES people(id) ON DELETE SET NULL,
    app_owner_id       INTEGER REFERENCES people(id) ON DELETE SET NULL,
    wl_full            INTEGER NOT NULL DEFAULT 0 CHECK (wl_full >= 0),
    wl_selective       INTEGER NOT NULL DEFAULT 0 CHECK (wl_selective >= 0),
    wl_visibility      INTEGER NOT NULL DEFAULT 0 CHECK (wl_visibility >= 0),
    wl_idle            INTEGER NOT NULL DEFAULT 0 CHECK (wl_idle >= 0),
    wl_unmanaged       INTEGER NOT NULL DEFAULT 0 CHECK (wl_unmanaged >= 0),
    in_pce             INTEGER NOT NULL DEFAULT 1 CHECK (in_pce IN (0, 1)),
    hidden             INTEGER NOT NULL DEFAULT 0 CHECK (hidden IN (0, 1)),   -- removed from the tracker
    excluded           INTEGER NOT NULL DEFAULT 0 CHECK (excluded IN (0, 1)), -- listed but out of scope
    created_at         TEXT    NOT NULL,
    updated_at         TEXT    NOT NULL,
    UNIQUE (pce_id, label_href),
    CHECK (start_date IS NULL OR end_date IS NULL OR end_date >= start_date)
);
CREATE TABLE IF NOT EXISTS environments (      -- one per PCE "env" label
    id         INTEGER PRIMARY KEY,
    pce_id     INTEGER NOT NULL REFERENCES pces(id) ON DELETE CASCADE,
    label_href TEXT    NOT NULL,
    name       TEXT    NOT NULL,
    UNIQUE (pce_id, label_href)
);
CREATE TABLE IF NOT EXISTS application_environments (   -- applications M:N environments
    id             INTEGER PRIMARY KEY,
    application_id INTEGER NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
    environment_id INTEGER NOT NULL REFERENCES environments(id) ON DELETE CASCADE,
    excluded       INTEGER NOT NULL DEFAULT 0 CHECK (excluded IN (0, 1)),
    in_pce         INTEGER NOT NULL DEFAULT 1 CHECK (in_pce IN (0, 1)),  -- 0: no longer in the PCE for this application
    status_id      INTEGER NOT NULL REFERENCES statuses(id) ON DELETE RESTRICT,
    progress       INTEGER NOT NULL DEFAULT 0 CHECK (progress BETWEEN 0 AND 100),
    priority_id    INTEGER REFERENCES priorities(id) ON DELETE RESTRICT,  -- NULL: follows the application's priority
    start_date     TEXT,
    end_date       TEXT,
    wl_full        INTEGER NOT NULL DEFAULT 0 CHECK (wl_full >= 0),
    wl_selective   INTEGER NOT NULL DEFAULT 0 CHECK (wl_selective >= 0),
    wl_visibility  INTEGER NOT NULL DEFAULT 0 CHECK (wl_visibility >= 0),
    wl_idle        INTEGER NOT NULL DEFAULT 0 CHECK (wl_idle >= 0),
    wl_unmanaged   INTEGER NOT NULL DEFAULT 0 CHECK (wl_unmanaged >= 0),
    UNIQUE (application_id, environment_id),
    CHECK (start_date IS NULL OR end_date IS NULL OR end_date >= start_date)
);

-- Activity ------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS comments (
    id             INTEGER PRIMARY KEY,
    application_id INTEGER NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
    application_environment_id INTEGER REFERENCES application_environments(id) ON DELETE CASCADE,  -- set for a comment on one environment
    author_id      INTEGER REFERENCES people(id) ON DELETE SET NULL,
    body           TEXT    NOT NULL CHECK (length(body) BETWEEN 1 AND 500),
    created_at     TEXT    NOT NULL
);
CREATE TABLE IF NOT EXISTS events (            -- audit trail; NULL author = system
    id             INTEGER PRIMARY KEY,
    application_id INTEGER NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
    author_id      INTEGER REFERENCES people(id) ON DELETE SET NULL,
    kind           TEXT    NOT NULL CHECK (kind IN
                       ('sync', 'status', 'progress', 'priority', 'edit', 'comment', 'environment')),
    message        TEXT    NOT NULL,
    created_at     TEXT    NOT NULL
);
CREATE TABLE IF NOT EXISTS workloads (          -- snapshot of the PCE's workloads, replaced on every sync
    id                INTEGER PRIMARY KEY,
    pce_id            INTEGER NOT NULL REFERENCES pces(id) ON DELETE CASCADE,
    href              TEXT    NOT NULL,
    hostname          TEXT    NOT NULL DEFAULT '',
    application_id    INTEGER REFERENCES applications(id) ON DELETE SET NULL,
    environment_id    INTEGER REFERENCES environments(id) ON DELETE SET NULL,
    managed           INTEGER NOT NULL DEFAULT 0 CHECK (managed IN (0, 1)),   -- 1: has a VEN
    online            INTEGER NOT NULL DEFAULT 0 CHECK (online IN (0, 1)),
    enforcement_mode  TEXT    NOT NULL DEFAULT '',
    ven_status        TEXT    NOT NULL DEFAULT '',
    ven_version       TEXT    NOT NULL DEFAULT '',
    ven_type          TEXT    NOT NULL DEFAULT '',   -- server, endpoint, containerized
    policy_sync_state TEXT    NOT NULL DEFAULT '',
    last_heartbeat    TEXT,
    os                TEXT    NOT NULL DEFAULT '',
    os_detail         TEXT    NOT NULL DEFAULT '',   -- e.g. "... (Windows 10 Enterprise LTSC 2024)"
    location          TEXT    NOT NULL DEFAULT '',
    role              TEXT    NOT NULL DEFAULT '',
    app_label         TEXT    NOT NULL DEFAULT '',   -- value of the app label, tracked or not
    env_label         TEXT    NOT NULL DEFAULT '',
    label_keys        TEXT,                          -- label types on the workload, comma-separated ("": no labels, NULL: not read yet)
    UNIQUE (pce_id, href)
);
CREATE INDEX IF NOT EXISTS idx_workloads_application ON workloads(application_id);
CREATE INDEX IF NOT EXISTS idx_workloads_environment ON workloads(environment_id);

-- Policy and traffic snapshots, replaced on every sync ------------------------
CREATE TABLE IF NOT EXISTS rule_sets (
    id            INTEGER PRIMARY KEY,
    pce_id        INTEGER NOT NULL REFERENCES pces(id) ON DELETE CASCADE,
    href          TEXT    NOT NULL,              -- without the draft/active part, so both versions share it
    name          TEXT    NOT NULL,
    enabled       INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    rules_draft   INTEGER NOT NULL DEFAULT 0 CHECK (rules_draft >= 0),    -- rules in the draft policy
    rules_active  INTEGER NOT NULL DEFAULT 0 CHECK (rules_active >= 0),   -- rules in the provisioned policy
    rules_pending INTEGER NOT NULL DEFAULT 0 CHECK (rules_pending >= 0),  -- draft changes not provisioned yet
    UNIQUE (pce_id, href)
);
CREATE TABLE IF NOT EXISTS rule_set_scopes (    -- one row per application and environment a ruleset scope covers
    id             INTEGER PRIMARY KEY,
    rule_set_id    INTEGER NOT NULL REFERENCES rule_sets(id)    ON DELETE CASCADE,
    scope          TEXT    NOT NULL,                                        -- the scope as written in the PCE
    application_id INTEGER REFERENCES applications(id) ON DELETE CASCADE,  -- NULL: the scope names no tracked application
    environment_id INTEGER REFERENCES environments(id) ON DELETE CASCADE,  -- NULL: every environment
    scope_type     TEXT    NOT NULL DEFAULT ''   -- none (scopeless), app (application only), app_env, other; '' = not read yet
);
CREATE TABLE IF NOT EXISTS traffic_summaries (  -- blocked and potentially blocked flows seen by the PCE
    id                              INTEGER PRIMARY KEY,
    pce_id                          INTEGER NOT NULL REFERENCES pces(id) ON DELETE CASCADE,
    application_id                  INTEGER REFERENCES applications(id) ON DELETE CASCADE,  -- NULL: the whole PCE
    environment_id                  INTEGER REFERENCES environments(id) ON DELETE CASCADE,  -- NULL: the whole application
    direction                       TEXT    NOT NULL DEFAULT 'all' CHECK (direction IN ('all', 'in', 'out')),  -- to the scope
    potentially_blocked_flows       INTEGER NOT NULL DEFAULT 0 CHECK (potentially_blocked_flows >= 0),
    potentially_blocked_connections INTEGER NOT NULL DEFAULT 0 CHECK (potentially_blocked_connections >= 0),
    blocked_flows                   INTEGER NOT NULL DEFAULT 0 CHECK (blocked_flows >= 0),
    blocked_connections             INTEGER NOT NULL DEFAULT 0 CHECK (blocked_connections >= 0),
    -- The same flows judged against the draft rules: still potentially blocked / blocked, or allowed once provisioned.
    draft_potentially_blocked_flows       INTEGER NOT NULL DEFAULT 0,
    draft_potentially_blocked_connections INTEGER NOT NULL DEFAULT 0,
    draft_blocked_flows                   INTEGER NOT NULL DEFAULT 0,
    draft_blocked_connections             INTEGER NOT NULL DEFAULT 0,
    draft_allowed_flows                   INTEGER NOT NULL DEFAULT 0,
    draft_allowed_connections             INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS traffic_ports (     -- blocked and potentially blocked traffic per port, for the whole PCE
    id           INTEGER PRIMARY KEY,
    pce_id       INTEGER NOT NULL REFERENCES pces(id) ON DELETE CASCADE,
    port         INTEGER,                         -- NULL for protocols without ports (ICMP)
    proto        INTEGER NOT NULL,                -- IP protocol number: 6 TCP, 17 UDP
    decision     TEXT    NOT NULL,                -- potentially_blocked or blocked, as the VENs reported it
    connections  INTEGER NOT NULL DEFAULT 0,      -- source, destination, port and protocol
    flows        INTEGER NOT NULL DEFAULT 0,      -- times those connections were seen
    sources      INTEGER NOT NULL DEFAULT 0,      -- different sources
    destinations INTEGER NOT NULL DEFAULT 0       -- different destinations
);
CREATE INDEX IF NOT EXISTS idx_traffic_ports_pce ON traffic_ports(pce_id);
CREATE TABLE IF NOT EXISTS labels (             -- every label in the PCE and where it is used
    id             INTEGER PRIMARY KEY,
    pce_id         INTEGER NOT NULL REFERENCES pces(id) ON DELETE CASCADE,
    href           TEXT    NOT NULL,
    key            TEXT    NOT NULL,              -- label type: app, env, loc, role, ...
    value          TEXT    NOT NULL,
    workload_count INTEGER NOT NULL DEFAULT 0 CHECK (workload_count >= 0),
    used_by        TEXT    NOT NULL DEFAULT '',   -- what the PCE says uses it, comma-separated; empty: unused
    UNIQUE (pce_id, href)
);
CREATE INDEX IF NOT EXISTS idx_rule_set_scopes_rule_set    ON rule_set_scopes(rule_set_id);
CREATE INDEX IF NOT EXISTS idx_rule_set_scopes_application ON rule_set_scopes(application_id);
CREATE INDEX IF NOT EXISTS idx_rule_set_scopes_environment ON rule_set_scopes(environment_id);
CREATE INDEX IF NOT EXISTS idx_traffic_summaries_pce         ON traffic_summaries(pce_id);
CREATE INDEX IF NOT EXISTS idx_traffic_summaries_application ON traffic_summaries(application_id);
CREATE INDEX IF NOT EXISTS idx_traffic_summaries_environment ON traffic_summaries(environment_id);

-- Project timeline ----------------------------------------------------------
CREATE TABLE IF NOT EXISTS task_statuses (
    id         INTEGER PRIMARY KEY,
    name       TEXT    NOT NULL UNIQUE,
    sort_order INTEGER NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS projects (
    id          INTEGER PRIMARY KEY,
    pce_id      INTEGER NOT NULL REFERENCES pces(id) ON DELETE CASCADE,
    name        TEXT    NOT NULL,
    description TEXT    NOT NULL DEFAULT '',
    created_at  TEXT    NOT NULL,
    UNIQUE (pce_id, name)
);
CREATE TABLE IF NOT EXISTS tasks (             -- self-referencing: a task with children is a phase
    id                 INTEGER PRIMARY KEY,
    project_id         INTEGER NOT NULL REFERENCES projects(id)      ON DELETE CASCADE,
    parent_id          INTEGER          REFERENCES tasks(id)         ON DELETE CASCADE,
    application_id     INTEGER          REFERENCES applications(id)  ON DELETE SET NULL,
    environment_id     INTEGER          REFERENCES environments(id)  ON DELETE SET NULL,
    name               TEXT    NOT NULL,
    description        TEXT    NOT NULL DEFAULT '',
    sort_order         INTEGER NOT NULL,
    priority_id        INTEGER NOT NULL REFERENCES priorities(id)    ON DELETE RESTRICT,
    status_id          INTEGER NOT NULL REFERENCES task_statuses(id) ON DELETE RESTRICT,
    progress           INTEGER NOT NULL DEFAULT 0 CHECK (progress BETWEEN 0 AND 100),
    start_date         TEXT,
    end_date           TEXT,
    assigned_ps_id     INTEGER REFERENCES people(id) ON DELETE SET NULL,
    project_manager_id INTEGER REFERENCES people(id) ON DELETE SET NULL,
    app_owner_id       INTEGER REFERENCES people(id) ON DELETE SET NULL,
    baseline_start     TEXT,
    baseline_end       TEXT,
    created_at         TEXT    NOT NULL,
    updated_at         TEXT    NOT NULL,
    CHECK (parent_id IS NULL OR parent_id <> id),
    CHECK (start_date IS NULL OR end_date IS NULL OR end_date >= start_date)
);
CREATE TABLE IF NOT EXISTS task_dependencies ( -- finish-to-start: predecessor before successor
    predecessor_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    successor_id   INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    PRIMARY KEY (predecessor_id, successor_id),
    CHECK (predecessor_id <> successor_id)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS task_resources (    -- tasks M:N people
    task_id   INTEGER NOT NULL REFERENCES tasks(id)  ON DELETE CASCADE,
    person_id INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE,
    PRIMARY KEY (task_id, person_id)
) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS milestones (
    id        INTEGER PRIMARY KEY,
    task_id   INTEGER NOT NULL REFERENCES tasks(id)         ON DELETE CASCADE,
    name      TEXT    NOT NULL,
    due_date  TEXT    NOT NULL,
    status_id INTEGER NOT NULL REFERENCES task_statuses(id) ON DELETE RESTRICT
);
CREATE TABLE IF NOT EXISTS task_comments (
    id         INTEGER PRIMARY KEY,
    task_id    INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    author_id  INTEGER REFERENCES people(id) ON DELETE SET NULL,
    body       TEXT    NOT NULL CHECK (length(body) BETWEEN 1 AND 500),
    created_at TEXT    NOT NULL
);
CREATE TABLE IF NOT EXISTS task_events (       -- change log
    id         INTEGER PRIMARY KEY,
    task_id    INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    author_id  INTEGER REFERENCES people(id) ON DELETE SET NULL,
    message    TEXT    NOT NULL,
    created_at TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS consultant_reports (  -- what the consultant writes for the Word report; one per PCE
    id         INTEGER PRIMARY KEY,
    pce_id     INTEGER NOT NULL UNIQUE REFERENCES pces(id) ON DELETE CASCADE,
    content    TEXT    NOT NULL,   -- JSON: engagement details, executive summary, recommendations, risks, next steps, notes
    updated_at TEXT    NOT NULL
);
CREATE TABLE IF NOT EXISTS sync_runs (
    id                INTEGER PRIMARY KEY,
    pce_id            INTEGER NOT NULL REFERENCES pces(id) ON DELETE CASCADE,
    completed_at      TEXT    NOT NULL,
    application_count INTEGER NOT NULL,
    environment_count INTEGER NOT NULL,
    workload_count    INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (          -- standalone key/value preferences
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
) WITHOUT ROWID;

-- Every foreign key is indexed (UNIQUE constraints already cover the leading ones).
CREATE INDEX IF NOT EXISTS idx_person_roles_role        ON person_roles(role_id);
CREATE INDEX IF NOT EXISTS idx_applications_priority    ON applications(priority_id);
CREATE INDEX IF NOT EXISTS idx_applications_status      ON applications(status_id);
CREATE INDEX IF NOT EXISTS idx_applications_ps          ON applications(assigned_ps_id);
CREATE INDEX IF NOT EXISTS idx_applications_pm          ON applications(project_manager_id);
CREATE INDEX IF NOT EXISTS idx_applications_owner       ON applications(app_owner_id);
CREATE INDEX IF NOT EXISTS idx_app_envs_environment     ON application_environments(environment_id);
CREATE INDEX IF NOT EXISTS idx_app_envs_status          ON application_environments(status_id);
CREATE INDEX IF NOT EXISTS idx_comments_application     ON comments(application_id);
CREATE INDEX IF NOT EXISTS idx_comments_author          ON comments(author_id);
CREATE INDEX IF NOT EXISTS idx_events_application       ON events(application_id);
CREATE INDEX IF NOT EXISTS idx_events_author            ON events(author_id);
CREATE INDEX IF NOT EXISTS idx_sync_runs_pce            ON sync_runs(pce_id);
CREATE INDEX IF NOT EXISTS idx_tasks_project            ON tasks(project_id);
CREATE INDEX IF NOT EXISTS idx_tasks_parent             ON tasks(parent_id);
CREATE INDEX IF NOT EXISTS idx_tasks_application        ON tasks(application_id);
CREATE INDEX IF NOT EXISTS idx_tasks_environment        ON tasks(environment_id);
CREATE INDEX IF NOT EXISTS idx_tasks_priority           ON tasks(priority_id);
CREATE INDEX IF NOT EXISTS idx_tasks_status             ON tasks(status_id);
CREATE INDEX IF NOT EXISTS idx_tasks_ps                 ON tasks(assigned_ps_id);
CREATE INDEX IF NOT EXISTS idx_tasks_pm                 ON tasks(project_manager_id);
CREATE INDEX IF NOT EXISTS idx_tasks_owner              ON tasks(app_owner_id);
CREATE INDEX IF NOT EXISTS idx_task_deps_successor      ON task_dependencies(successor_id);
CREATE INDEX IF NOT EXISTS idx_task_resources_person    ON task_resources(person_id);
CREATE INDEX IF NOT EXISTS idx_milestones_task          ON milestones(task_id);
CREATE INDEX IF NOT EXISTS idx_milestones_status        ON milestones(status_id);
CREATE INDEX IF NOT EXISTS idx_task_comments_task       ON task_comments(task_id);
CREATE INDEX IF NOT EXISTS idx_task_comments_author     ON task_comments(author_id);
CREATE INDEX IF NOT EXISTS idx_task_events_task         ON task_events(task_id);
CREATE INDEX IF NOT EXISTS idx_task_events_author       ON task_events(author_id);
"""

STATUSES = [  # (name, default progress) in workflow order
    ("Not Started", 0),
    ("In Progress", 25),
    ("Selective Enforcement", 60),
    ("Fully Enforced", 100),
]
PRIORITIES = ["Critical", "High", "Medium", "Low"]
TASK_STATUSES = ["Not Started", "In Progress", "Completed"]
ROLES = [("ps", "Assigned PS"), ("pm", "Project Manager"), ("owner", "App Owner")]


def connect():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")  # SQLite only enforces FKs when asked, per connection
    return con


def init():
    con = connect()
    con.executescript(SCHEMA)
    # Columns added after the first release; CREATE TABLE IF NOT EXISTS does not alter existing tables.
    if "excluded" not in {r["name"] for r in con.execute("PRAGMA table_info(applications)")}:
        con.execute("ALTER TABLE applications ADD COLUMN excluded INTEGER NOT NULL DEFAULT 0 CHECK (excluded IN (0, 1))")
    if "in_pce" not in {r["name"] for r in con.execute("PRAGMA table_info(application_environments)")}:
        con.execute("ALTER TABLE application_environments ADD COLUMN in_pce INTEGER NOT NULL DEFAULT 1 CHECK (in_pce IN (0, 1))")
    have = {r["name"] for r in con.execute("PRAGMA table_info(application_environments)")}
    for col, ddl in (("priority_id", "INTEGER REFERENCES priorities(id) ON DELETE RESTRICT"), ("start_date", "TEXT"), ("end_date", "TEXT")):
        if col not in have:
            con.execute(f"ALTER TABLE application_environments ADD COLUMN {col} {ddl}")
    have = {r["name"] for r in con.execute("PRAGMA table_info(workloads)")}
    for col in ("ven_type", "app_label", "env_label", "os_detail"):
        if col not in have:
            con.execute(f"ALTER TABLE workloads ADD COLUMN {col} TEXT NOT NULL DEFAULT ''")
    if "label_keys" not in have:
        con.execute("ALTER TABLE workloads ADD COLUMN label_keys TEXT")
    if "scope_type" not in {r["name"] for r in con.execute("PRAGMA table_info(rule_set_scopes)")}:
        con.execute("ALTER TABLE rule_set_scopes ADD COLUMN scope_type TEXT NOT NULL DEFAULT ''")
    have = {r["name"] for r in con.execute("PRAGMA table_info(traffic_summaries)")}
    if "direction" not in have:
        con.execute("ALTER TABLE traffic_summaries ADD COLUMN direction TEXT NOT NULL DEFAULT 'all' "
                    "CHECK (direction IN ('all', 'in', 'out'))")
    for col in ("draft_potentially_blocked_flows", "draft_potentially_blocked_connections", "draft_blocked_flows",
                "draft_blocked_connections", "draft_allowed_flows", "draft_allowed_connections"):
        if col not in have:
            con.execute(f"ALTER TABLE traffic_summaries ADD COLUMN {col} INTEGER NOT NULL DEFAULT 0")
    if "application_environment_id" not in {r["name"] for r in con.execute("PRAGMA table_info(comments)")}:
        con.execute("ALTER TABLE comments ADD COLUMN application_environment_id INTEGER "
                    "REFERENCES application_environments(id) ON DELETE CASCADE")
    con.execute("CREATE INDEX IF NOT EXISTS idx_comments_app_env ON comments(application_environment_id)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_app_envs_priority ON application_environments(priority_id)")
    # Comments written on application tasks before the timeline shared the application's thread:
    # move them onto that thread so Application Tracking shows them too. Nothing to do once moved.
    con.execute(
        "INSERT INTO comments (application_id, application_environment_id, author_id, body, created_at) "
        "SELECT t.application_id, (SELECT ae.id FROM application_environments ae WHERE ae.application_id = t.application_id "
        "AND ae.environment_id = t.environment_id), tc.author_id, tc.body, tc.created_at "
        "FROM task_comments tc JOIN tasks t ON t.id = tc.task_id WHERE t.application_id IS NOT NULL ORDER BY tc.id")
    con.execute("DELETE FROM task_comments WHERE task_id IN (SELECT id FROM tasks WHERE application_id IS NOT NULL)")
    # Keep ranks contiguous with excluded applications after the active ones (and removed ones last).
    # Idempotent; it also tidies databases written before excluded applications left the ranking.
    con.execute(
        "UPDATE applications SET rank = (SELECT n FROM (SELECT id, ROW_NUMBER() OVER "
        "(PARTITION BY pce_id ORDER BY hidden, excluded, rank, id) AS n FROM applications) o WHERE o.id = applications.id)")
    for order, (name, pct) in enumerate(STATUSES, start=1):
        con.execute("INSERT OR IGNORE INTO statuses (name, sort_order, default_progress) VALUES (?,?,?)",
                    (name, order, pct))
    for order, name in enumerate(PRIORITIES, start=1):
        con.execute("INSERT OR IGNORE INTO priorities (name, sort_order) VALUES (?,?)", (name, order))
    for order, name in enumerate(TASK_STATUSES, start=1):
        con.execute("INSERT OR IGNORE INTO task_statuses (name, sort_order) VALUES (?,?)", (name, order))
    for code, name in ROLES:
        con.execute("INSERT OR IGNORE INTO roles (code, name) VALUES (?,?)", (code, name))
        con.execute("UPDATE roles SET name = ? WHERE code = ? AND name <> ?", (name, code, name))  # label renames
    con.commit()
    con.close()


def get_setting(con, key, default=""):
    row = con.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(con, key, value):
    con.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, str(value)),
    )
