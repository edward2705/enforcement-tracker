# Illumio Enforcement Tracker

Plan, track and report the move of each application from visibility to full enforcement.
Applications and environments come from the Illumio PCE (`app` and `env` labels, plus the
enforcement mode of their workloads); everything else — priority, owners, dates, progress,
comments — is tracked locally.

## Run

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python3 app.py            # http://127.0.0.1:5055 and http://<hostname>:5055  (PORT=xxxx to change)
```

Run `source .venv/bin/activate` again in each new shell before starting the app; without it
`python3 app.py` fails with `ModuleNotFoundError: No module named 'flask'`.

Open the app and press **Sync from PCE**. The PCE connection is read from `config.yaml`
and can be edited under **Settings → PCE Configuration**.

## Stack

| Layer | Choice |
|---|---|
| API | Flask (`app.py`, `timeline.py`, `policy.py`, `pce.py`), SQLite (`db.py`; `tracker.db` is created on first run) |
| UI | React 18 + TypeScript + Bootstrap 5, in `static/app/*.tsx` |
| Build | None. No Node.js: Babel (vendored in `static/vendor`) compiles the TSX in the browser. Types are stripped, not type-checked. |

All front-end libraries are vendored, so the app works without internet access.

## How the PCE is used

The tracker reads `/labels` (with usage), `/label_dimensions`, `/workloads`, `/product_version`, `/health`, and the draft and provisioned (`active`) versions of
`/sec_policy/.../rule_sets` and `/sec_policy/.../label_groups` with `GET` requests (switching to an
async job when a collection reaches the 500-item sync limit). It never changes labels, workloads or policy.

Blocked traffic is the one exception to `GET`: the PCE only answers traffic questions through an
Explorer query, so each sync posts one to `/traffic_flows/async_queries` (blocked and potentially
blocked flows of the traffic period: 30 days by default, changed under **Settings → Sync behaviour**), asks the
PCE to judge those flows against the draft rules too (`PUT .../async_queries/<id>/update_rules`, the same thing
Explorer's Draft view does), downloads the result and deletes the query again.

Every sync first **clears the stored traffic counts** (`traffic_summaries`) and then stores only what this sync
read, so old traffic never lingers. If the traffic query fails, the sync still completes and warns, and traffic
shows no counts until the next successful sync. If the API key may not read rulesets, the sync also completes,
warns and keeps the previous rulesets.

Traffic uses Explorer's terms: a **connection** is one source, destination, port and protocol; its **flows** are
the number of times it was seen, all of the PCE's records for that connection added up (the PCE returns separate
records per reporting VEN, time range and boundary decision). The counts match Explorer's rows for the same
period, though Explorer can leave out some records that the tracker includes.

Managed workloads are grouped by app label (and app + env label) and summarised:

| Workload enforcement modes | Shown as "Enforcement in PCE" | Implied tracker status |
|---|---|---|
| all `full` | Fully Enforced | Fully Enforced |
| some `full` or `selective` | Partially Enforced | Selective Enforcement |
| otherwise some `visibility_only` | Visibility Only | In Progress |
| all `idle` | Idle | Not Started |
| none managed | No Managed Workloads | Not Started |

A sync never overwrites tracker settings. For applications and environments it already knows, it
only refreshes what the PCE owns: label names, workload counts and whether the label still exists.
Status, progress, priority, dates, people, comments and exclusions are kept. New applications and
environments get a starting status from the table above. An application whose label was deleted in
the PCE is kept and marked red. So is an environment that is gone for an application (its label was
deleted, or no workload carries both labels any more): its row turns red on the Project Timeline and
in Application Tracking. **Reports → Tracker vs PCE**
lists every application where the two disagree.

## Calculated application dates

Once any tracked environment of an application has a start or end date, the application's own
dates are calculated: start = the earliest environment start, end = the latest environment end.
They are then read-only on the application (grid, edit dialog and API) and recalculated whenever an
environment's dates change or an environment is excluded or included. With no environment dates the
application's dates are entered by hand as before.

## Project Timeline

A project is a tree of tasks shown as a grid next to a Gantt chart.

- A task that has tasks underneath it is a **phase**: its dates, duration, % complete (weighted by
  task duration) and status are rolled up from its tasks and are not stored.
- Dependencies are finish-to-start. They are drawn as arrows and flagged in red when a task starts
  before its predecessor ends; dates are never moved automatically. Loops are rejected.
- **Baseline** saves the current dates so later slips show as a thin grey bar under each task.
- The timeline follows Application Tracking: every application that is not excluded is listed in each
  project that has an Application Enforcement phase. Including an application or environment again,
  restoring one, or importing a new one in a sync adds its task automatically; excluding hides it.
- **Application and environment tasks stay in sync with Application Tracking.** A task that stands
  for an application mirrors its status, progress, priority, dates and people; an environment
  sub-task mirrors that environment's status, progress, priority and dates. An edit on either page
  is written to the tracking record and copied to every task for it, in every project. Comments are
  one shared thread. (A task has three statuses and tracking has four: Completed = Fully Enforced,
  and In Progress covers both In Progress and Selective Enforcement.)
- **Add applications & environments** creates one task per tracked application with a sub-task
  for each of its tracked environments. A sync adds sub-tasks for environments that
  are new in the PCE. These tasks keep their PCE names and cannot be deleted; excluding the
  application or environment in Application Tracking takes them off the timeline.
- **Save Project → Save as a new project** copies a project (tasks, dependencies, milestones, resources,
  baseline) under a new name, so several plans can be kept side by side. The **Projects** picker loads
  any combination of saved projects at once. Edits are saved automatically.
- New projects can start from the Illumio implementation plan (Discovery & Planning, Application
  Enforcement, Validation & Closure).

## Reports

**Reports → Illumio Enforcement Summary** is a one-page report: KPIs, applications by status and
priority, a per-phase overview of the project plan, and the task table with its Gantt chart.
**Download Excel** (`reports.py`, built with XlsxWriter) produces a workbook with six sheets:

- **Dashboard** - banner, six tinted tiles, three native Excel charts (Applications by Status,
  Applications by Priority, Completion by Environment) and one row per application and environment
  with priority, people, dates, duration, % complete, an in-cell progress bar, status and comments.
- **Policy & Traffic** - one row per scope (application and environment, as on the Dashboard): the
  rulesets scoped to it, their rules in the draft policy, rules provisioned, draft changes not yet
  provisioned, and potentially blocked and blocked flows and connections. Rulesets whose scope names
  no tracked application (for example All Applications) follow with their own scope, and a last row
  totals the whole PCE.
- **Application Tracker**, **People**, **Environments** and **Project Timeline** - the underlying data.

The workbook is live: tiles and the status and priority charts are formulas over the Application
Tracker sheet; Completion by Environment, durations and progress bars follow the Dashboard table;
colours are conditional formatting; Status and Priority cells have dropdowns. Edits made in Excel
are not written back to the tracker.

**Reports → Enforcement Summary Report** is the Word document for the customer. The tab has the
**Download Word** button and a **Recommendations** form for the consultant: engagement details, executive
summary, recommendations (area, observation, recommendation, priority, owner, target date, status),
risks and dependencies, next steps and notes. The form saves by itself a moment after typing stops
(`consultant_reports` table, one document per PCE) and is printed in the Word document; whatever is
left empty becomes a blank form to fill in by hand.

The document (python-docx, charts drawn with matplotlib) is Illumio-themed and landscape. It opens
with a cover page and a table of contents. The contents are a Word field: Word asks to update
fields when the document is opened, and answering Yes fills in the page numbers. The cover shows
`static/logo.png` when that file exists (put the official logo artwork there); otherwise it draws
the tracker's own mark. The cover page is white with the logo and accents in Illumio orange and navy type (colours in `COVER` in `reports.py`). Each section starts on a new page:

- **Executive Summary** - headline numbers, applications by status, VENs by enforcement mode,
  completion by environment, the consultant's summary and the key findings.
- **1. PCE Information Summary** - charts of workloads, VENs, labels, policy and blocked traffic, then
  address, version and cluster health.
- **2. VENs & Workloads** - deployed VENs by type (server, endpoint, containerized), online, offline,
  suspended, unmanaged workloads and enforcement modes as measure tables; charts of VENs by type,
  VEN status by type, enforcement mode, VEN versions and operating systems; then one table by
  application and one by environment; then **Unsupported VEN Deployments**: Windows clients (Windows 10/11,
  from the OS the PCE reports) that have the server VEN installed are flagged **Critical** and listed with OS,
  VEN version and their app, env, loc and role labels (they need the endpoint VEN); then **Workload Labelling**: how many workloads have no labels,
  only a role label or only an application label, and the list of each by hostname (up to 1,000 per list).
  Label assignments are read at sync, so sync once after upgrading to fill these lists.
- **3. Label Summary** - labels by type and the full list, with unused labels highlighted. A label is
  unused when no workload carries it and the PCE reports nothing else using it.
- **4. Policy and Blocked Traffic by Scope** - rulesets, rules in draft and provisioned, and draft changes not yet
  provisioned per scope, with the scopes with most rules charted. Traffic counts are left out of the Word report;
  they are on the Reports tab and in the Excel workbook. Each scope has a **Scope Type**: scopeless rulesets (no scope, so
  they apply to every workload) and rulesets scoped to an application only (every environment of it) are highlighted,
  named in a callout above the table, and application-only scopes get a row of their own.
- **5. Application Enforcement Status** - status, priority and completion charts, and one row per
  application and environment with priority, status, % complete, dates and the latest comment.
- **6. Assessment Summary** - one finding per area (enforcement coverage, VEN connectivity and versions,
  unmanaged workloads, labelling, unused labels, policy provisioning and coverage,
  project status vs PCE, project plan, VEN type) rated Critical, Action, Attention or Good with a recommendation. The
  ratings are fixed rules in `assess()` in `reports.py`, not a manual review.
- **7. Recommendations** - what the consultant entered on the Reports tab, or the empty form, and a sign-off table.
  It also has **Suggested Selective Enforcement Ports**: the high-risk ports usually closed first with an
  enforcement boundary (RDP, SMB, MS-RPC, WinRM, NetBIOS, SSH, VNC, Telnet, FTP; the list is `SELECTIVE_PORTS` in
  `reports.py`), always listed as **Core**, plus other risky ports the last sync **found in traffic** (blocked or
  potentially blocked): databases, Redis, MongoDB, Elasticsearch, Docker, Kubernetes, LDAP, SNMP, TFTP, LLMNR, rsh
  and more; the list is `RISKY_PORTS`. A callout counts them and lists which are ready and which need review.
  For each, the potentially blocked connections on that port from the last sync decide the
  suggestion: **Ready** (nothing would be blocked, add it to the boundary) or **Review first** (write allow rules
  for the needed connections, then add it). Sync stores these per-port counts in `traffic_ports`, cleared on every sync.
  **Services and Ports That Need Attention** follows: services on by default but rarely needed (Windows Delivery
  Optimization TCP 7680, Multicast DNS UDP 5353; the list is `ATTENTION_PORTS`), each with what it is, why it needs
  attention and how to deal with it at the source.

Sections 1 to 3 describe the whole PCE as of the last sync, including applications excluded from tracking.

**Policy and Blocked Traffic by Scope** shows, per application and environment, the rulesets, the rules in
draft and provisioned, the draft changes not yet provisioned, and the potentially blocked and blocked flows
and connections, with PCE totals. **Count flows by** switches the flow numbers between:

- **Provisioned Rules** - the decision the VENs reported under the rules provisioned today.
- **Draft Rules** - the PCE's decision for the same flows if the draft were provisioned, with the change
  against the provisioned count next to each number and an **Allowed by Draft** column for the flows the draft
  would allow. Only flows reported as blocked or potentially blocked are re-checked; a flow the PCE cannot judge
  against the draft keeps its reported decision.

**Direction** splits the flows of each scope into two categories (a note on the tab explains them):

- **Inbound** - traffic coming into the scope: the destination workload is in the scope, the source can be anywhere.
- **Outbound** - traffic leaving the scope: the source workload is in the scope, the destination can be anywhere.
- **All Directions** - either way, each flow once per scope. A flow between two workloads of the same scope is
  both inbound and outbound to it, so Inbound + Outbound can be more than All Directions. The PCE total row
  always counts every flow once.

The table can be searched, filtered to tracked applications or to scopes with blocked traffic, and sorted.
**Download Excel** exports it with the flows counted the way the toggles are set; with All Directions the
workbook also has an Inbound and an Outbound sheet.

**Roll-ups & Exceptions** keeps the grouped summary, the overdue list and Tracker vs PCE.

## Data model

```mermaid
erDiagram
    pces ||--o{ applications : "has"
    pces ||--o{ environments : "has"
    pces ||--o{ sync_runs : "records"
    priorities ||--o{ applications : "ranks"
    statuses ||--o{ applications : "describes"
    statuses ||--o{ application_environments : "describes"
    applications ||--o{ application_environments : "runs in"
    environments ||--o{ application_environments : "hosts"
    applications ||--o{ comments : "has"
    applications ||--o{ events : "has"
    people ||--o{ person_roles : "holds"
    roles ||--o{ person_roles : "granted to"
    people |o--o{ applications : "assigned PS"
    people |o--o{ applications : "project manager"
    people |o--o{ applications : "app owner"
    people |o--o{ comments : "writes"
    people |o--o{ events : "performs"

    pces { int id PK  text name UK }
    statuses { int id PK  text name UK  int sort_order UK  int default_progress }
    priorities { int id PK  text name UK  int sort_order UK }
    roles { int id PK  text code UK  text name UK }
    people { int id PK  text name UK  text email  text title }
    person_roles { int person_id PK,FK  int role_id PK,FK }
    applications {
        int id PK
        int pce_id FK
        text label_href "UK with pce_id"
        text name
        int rank
        int priority_id FK
        int status_id FK
        int progress
        date start_date
        date end_date
        int assigned_ps_id FK
        int project_manager_id FK
        int app_owner_id FK
        int wl_full "plus selective, visibility, idle, unmanaged"
        bool in_pce
        bool hidden
    }
    environments { int id PK  int pce_id FK  text label_href "UK with pce_id"  text name }
    application_environments {
        int id PK
        int application_id FK
        int environment_id FK
        bool excluded
        int status_id FK
        int progress
        int wl_full "plus selective, visibility, idle, unmanaged"
    }
    comments { int id PK  int application_id FK  int author_id FK  text body  datetime created_at }
    events { int id PK  int application_id FK  int author_id FK  text kind  text message  datetime created_at }
    sync_runs { int id PK  int pce_id FK  datetime completed_at  int application_count }
    rule_sets { int id PK  int pce_id FK  text href "UK with pce_id"  text name  int rules_draft  int rules_active  int rules_pending }
    rule_set_scopes { int id PK  int rule_set_id FK  text scope  int application_id FK  int environment_id FK }
    labels { int id PK  int pce_id FK  text href "UK with pce_id"  text key  text value  int workload_count  text used_by }
    consultant_reports { int id PK  int pce_id FK "UK"  text content "JSON"  datetime updated_at }
    traffic_summaries { int id PK  int pce_id FK  int application_id FK  int environment_id FK  int potentially_blocked_flows  int blocked_flows }

    pces ||--o{ projects : "has"
    projects ||--o{ tasks : "contains"
    tasks |o--o{ tasks : "parent of"
    applications |o--o{ tasks : "referenced by"
    environments |o--o{ tasks : "referenced by"
    priorities ||--o{ tasks : "ranks"
    task_statuses ||--o{ tasks : "describes"
    task_statuses ||--o{ milestones : "describes"
    tasks ||--o{ task_dependencies : "predecessor"
    tasks ||--o{ task_dependencies : "successor"
    tasks ||--o{ task_resources : "staffed by"
    people ||--o{ task_resources : "works on"
    tasks ||--o{ milestones : "has"
    tasks ||--o{ task_comments : "has"
    tasks ||--o{ task_events : "has"

    task_statuses { int id PK  text name UK  int sort_order UK }
    projects { int id PK  int pce_id FK  text name "UK with pce_id"  text description }
    tasks {
        int id PK
        int project_id FK
        int parent_id FK "self reference"
        int application_id FK
        int environment_id FK
        text name
        int sort_order
        int priority_id FK
        int status_id FK
        int progress
        date start_date
        date end_date
        int assigned_ps_id FK
        int project_manager_id FK
        int app_owner_id FK
        date baseline_start
        date baseline_end
    }
    task_dependencies { int predecessor_id PK,FK  int successor_id PK,FK }
    task_resources { int task_id PK,FK  int person_id PK,FK }
    milestones { int id PK  int task_id FK  text name  date due_date  int status_id FK }
    task_comments { int id PK  int task_id FK  int author_id FK  text body }
    task_events { int id PK  int task_id FK  int author_id FK  text message }
```

Conventions (see `db.py`):

- Surrogate integer `id` primary keys; natural keys kept as `UNIQUE` constraints (`pce_id + label_href`).
- Every relationship is a declared foreign key with an explicit `ON DELETE` rule, enforced with
  `PRAGMA foreign_keys = ON` on every connection, and every foreign key column is indexed.
- Lookup tables (`statuses`, `priorities`, `roles`) instead of free-text values.
- Many-to-many relationships use associative tables (`person_roles`, `application_environments`).
- `CHECK` constraints guard ranges and booleans (progress 0–100, end date ≥ start date, flags 0/1).
- No derived data is stored: "Enforcement in PCE" and the latest comment are computed on read.
- `settings` is a standalone key/value table and intentionally has no relationships.

## Security notes

- The server listens on `127.0.0.1` and on the address of the machine's hostname, and has no login: anyone who can
  reach that address on the network can read and change the tracker and its PCE settings. Restrict the port with a
  firewall or put the app behind an authenticating proxy.
- **The PCE is read-only to this app: it only reads (the equivalent of a SELECT).** Every request to the PCE is
  checked by `ReadOnlySession` in `pce.py` at the lowest level (`send()`, so also prepared requests), before it
  leaves the machine. Allowed:
  - `GET` to the configured PCE over HTTPS, with no body;
  - the Explorer traffic query, which the PCE only offers as a `POST` to `/traffic_flows/async_queries`; the
    body may hold query fields only;
  - `PUT .../async_queries/<id>/update_rules` with an empty body, only for a query this app created (the PCE
    judges that query's flows against draft policy; it changes the query's own result, not the policy);
  - `DELETE` of a query this app created, once its result is downloaded.

  Anything else - `POST`, `PUT`, `PATCH` or `DELETE` to workloads, labels, rulesets, rules or any other
  object, other hosts, plain HTTP, unexpected bodies - is refused unsent, and redirects are never followed.
  A test also fails if any other module sends HTTP requests itself instead of going through `pce.py`.
  `python -m unittest discover tests` checks all of this.
  For a second layer, give the tracker an API key with a read-only role in the PCE.
- Write requests require a custom header, so other websites open in the same browser cannot call the API.
- API secrets stay in `config.yaml` (mode `600`) and are never returned to the browser.
- **Encryption at rest** (`vault.py`): AES-256-GCM, a random nonce per value, authenticated so a changed or moved
  value is refused.
  - `config.yaml`: `api_key` and `api_secret` are always stored encrypted (`enc:v1:...`), and `pce_fqdn` too when
    it is an IP address. A file with plain-text credentials (hand-edited, or from before encryption) is rewritten
    encrypted the next time it is read.
  - `tracker.db`: workload names that are IP addresses (unmanaged workloads) are stored encrypted; host names stay
    readable so they can be sorted and searched. Existing rows are encrypted at startup and the file is vacuumed;
    `secure_delete` overwrites deleted values. Traffic is stored as counts only, with no addresses.
  - The 256-bit key is `secret.key` next to the app (created on first run, mode `600`, in `.gitignore`), or the
    `TRACKER_SECRET_KEY` environment variable (base64url) to keep it off that disk. Each machine has its own key.
    Keep the key out of backups of `config.yaml` and `tracker.db`. If it is lost, re-enter the API key and secret
    on the PCE Configuration page and run a sync.
  - This protects copies of the files (backups, accidental commits, a copied database). It does not protect
    against someone who can read the key on the running machine.
- `config.yaml`, `tracker.db` and `secret.key` are listed in `.gitignore`.
