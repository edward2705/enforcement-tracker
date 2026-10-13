"""Project Timeline API: projects, a task hierarchy, dependencies, milestones and baselines.

Tasks are tracked independently of the application grid. A task may reference a PCE
application (and environment); "generate from applications" copies the application's
current plan into new tasks, after which the two are edited separately.
"""
import datetime

from flask import Blueprint, jsonify

import db

bp = Blueprint("timeline", __name__)
core = None  # the app module; set by register() so helpers are shared without a circular import

TASK_STATUSES = db.TASK_STATUSES
APP_TO_TASK_STATUS = {
    "Not Started": "Not Started", "In Progress": "In Progress",
    "Selective Enforcement": "In Progress", "Fully Enforced": "Completed",
}
TASK_COLUMNS = {
    "name": "name", "description": "description", "parent_id": "parent_id",
    "application_id": "application_id", "environment_id": "environment_id",
    "priority": "priority_id", "status": "status_id", "progress": "progress",
    "start_date": "start_date", "end_date": "end_date",
    "ps_id": "assigned_ps_id", "pm_id": "project_manager_id", "owner_id": "app_owner_id",
}
TASK_SELECT = """
SELECT t.id, t.project_id, t.parent_id, t.application_id, t.environment_id, t.name, t.description, t.sort_order,
       p.name AS priority, s.name AS status, t.progress, t.start_date, t.end_date,
       t.assigned_ps_id AS ps_id, t.project_manager_id AS pm_id, t.app_owner_id AS owner_id,
       t.baseline_start, t.baseline_end, t.updated_at, a.name AS application, e.name AS environment
FROM tasks t
JOIN priorities p ON p.id = t.priority_id
JOIN task_statuses s ON s.id = t.status_id
LEFT JOIN applications a ON a.id = t.application_id
LEFT JOIN environments e ON e.id = t.environment_id
"""
# (phase, [(task, start offset in days, duration in days)]) laid out from the project start date
TEMPLATE_PLANNING = ("Discovery & Planning", [
    ("Application Discovery", 0, 10), ("Stakeholder Alignment", 5, 10), ("Enforcement Planning", 12, 8)])
TEMPLATE_CLOSURE = ("Validation & Closure", ["Policy Validation", "Final Review", "Project Closure"])
APP_PHASE = "Application Enforcement"


def register(app_module):
    global core
    core = app_module
    core.app.register_blueprint(bp)


def con():
    return core.con()


def active_pce_id(create=False):
    _cfg, name = core.active_pce_name()
    return core.pce_id_for(name, create=create)


def get_project(project_id):
    row = con().execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    if row is None:
        raise core.ApiError("Project not found", 404)
    return row


def get_task(task_id):
    row = con().execute(TASK_SELECT + " WHERE t.id = ?", (task_id,)).fetchone()
    if row is None:
        raise core.ApiError("Task not found", 404)
    return row


def log(task_id, message):
    con().execute("INSERT INTO task_events (task_id, author_id, message, created_at) VALUES (?,?,?,?)",
                  (task_id, core.current_user_id(), message, core.now()))


def next_order(project_id, parent_id):
    return con().execute(
        "SELECT COALESCE(MAX(sort_order), 0) + 1 n FROM tasks WHERE project_id = ? AND parent_id IS ?",
        (project_id, parent_id)).fetchone()["n"]


def insert_task(project_id, parent_id, name, **fields):
    ts = core.now()
    values = {
        "project_id": project_id, "parent_id": parent_id, "name": name,
        "sort_order": next_order(project_id, parent_id),
        "priority_id": core.lookup("priorities")[fields.pop("priority", "Medium")],
        "status_id": core.lookup("task_statuses")[fields.pop("status", "Not Started")],
        "created_at": ts, "updated_at": ts, **fields,
    }
    cols = ", ".join(values)
    task_id = con().execute(f"INSERT INTO tasks ({cols}) VALUES ({', '.join('?' * len(values))})",
                            tuple(values.values())).lastrowid
    log(task_id, "Task created")
    return task_id


def clean_name(value, what="Name"):
    name = core.clean_text(value, 120)
    if not name:
        raise core.ApiError(f"{what} is required")
    return name


def clean_ref(value, table, what, pce_id=None):
    if value in (None, "", 0):
        return None
    sql = f"SELECT id FROM {table} WHERE id = ?" + (" AND pce_id = ?" if pce_id else "")
    row = con().execute(sql, (value, pce_id) if pce_id else (value,)).fetchone()
    if row is None:
        raise core.ApiError(f"Unknown {what}")
    return row["id"]


# ------------------------------------------------------------------------------ read

@bp.get("/api/timeline")
def timeline():
    c = con()
    pid = active_pce_id()
    in_pce = "IN (SELECT t2.id FROM tasks t2 JOIN projects p2 ON p2.id = t2.project_id WHERE p2.pce_id = ?)"
    # Tasks for applications that are excluded in Application Tracking are left out, along with
    # anything nested under them and any dependency or milestone that points at them.
    tasks = [dict(r) for r in c.execute(
        TASK_SELECT + " JOIN projects pj ON pj.id = t.project_id WHERE pj.pce_id = ? "
        "AND COALESCE(a.excluded, 0) = 0 ORDER BY t.sort_order, t.id", (pid,))]
    # ...and so are environment sub-tasks whose environment is excluded for that application.
    off = {(r["application_id"], r["environment_id"]) for r in c.execute(
        "SELECT application_id, environment_id FROM application_environments WHERE excluded = 1")}
    tasks = [t for t in tasks if (t["application_id"], t["environment_id"]) not in off]
    shown = {t["id"] for t in tasks}
    while True:
        orphans = {t["id"] for t in tasks if t["id"] in shown and t["parent_id"] is not None and t["parent_id"] not in shown}
        if not orphans:
            break
        shown -= orphans
    tasks = [t for t in tasks if t["id"] in shown]
    return jsonify(
        projects=[dict(r) for r in c.execute(
            "SELECT id, name, description FROM projects WHERE pce_id = ? ORDER BY id", (pid,))],
        tasks=tasks,
        dependencies=[dict(r) for r in c.execute(
            f"SELECT predecessor_id, successor_id FROM task_dependencies WHERE successor_id {in_pce}", (pid,))
            if r["predecessor_id"] in shown and r["successor_id"] in shown],
        milestones=[dict(r) for r in c.execute(
            "SELECT m.id, m.task_id, m.name, m.due_date, s.name AS status FROM milestones m "
            f"JOIN task_statuses s ON s.id = m.status_id WHERE m.task_id {in_pce} ORDER BY m.due_date, m.id", (pid,))
            if r["task_id"] in shown],
        environments=[dict(r) for r in c.execute(
            "SELECT id, name FROM environments WHERE pce_id = ? ORDER BY name COLLATE NOCASE", (pid,))],
        task_statuses=TASK_STATUSES,
    )


@bp.get("/api/tasks/<int:task_id>")
def task_detail(task_id):
    task = get_task(task_id)
    c = con()
    comments = [dict(r) for r in c.execute(
        "SELECT 't' || cm.id AS id, cm.body AS text, cm.created_at, COALESCE(pe.name, 'Former user') AS author, 'comment' AS kind "
        "FROM task_comments cm LEFT JOIN people pe ON pe.id = cm.author_id WHERE cm.task_id = ?", (task_id,))]
    # An application task (or environment sub-task) shares one comment thread with Application
    # Tracking: it is stored once, on the application, and read from there by both pages.
    if task["application_id"]:
        if task["environment_id"]:
            scope = ("cm.application_environment_id = (SELECT id FROM application_environments "
                     "WHERE application_id = ? AND environment_id = ?)")
            args = (task["application_id"], task["environment_id"])
        else:
            scope, args = "cm.application_id = ? AND cm.application_environment_id IS NULL", (task["application_id"],)
        comments += [dict(r) for r in c.execute(
            "SELECT 'a' || cm.id AS id, cm.body AS text, cm.created_at, "
            "COALESCE(pe.name, 'Former user') AS author, 'comment' AS kind "
            f"FROM comments cm LEFT JOIN people pe ON pe.id = cm.author_id WHERE {scope}", args)]
    comments.sort(key=lambda r: r["created_at"], reverse=True)
    return jsonify(
        comments=comments,
        events=[dict(r) for r in c.execute(
            "SELECT ev.id, ev.message AS text, ev.created_at, COALESCE(pe.name, 'Former user') AS author, 'edit' AS kind "
            "FROM task_events ev LEFT JOIN people pe ON pe.id = ev.author_id WHERE ev.task_id = ? ORDER BY ev.id DESC",
            (task_id,))],
        resources=[r["person_id"] for r in c.execute("SELECT person_id FROM task_resources WHERE task_id = ?", (task_id,))],
    )


# -------------------------------------------------------------------------- projects

@bp.post("/api/projects")
def project_create():
    data = core.body()
    name = clean_name(data.get("name"), "Project name")
    c = con()
    pid = active_pce_id(create=True)
    if pid is None:
        raise core.ApiError("No PCE is configured - add one in Settings")
    if c.execute("SELECT 1 FROM projects WHERE pce_id = ? AND name = ?", (pid, name)).fetchone():
        raise core.ApiError("A project with that name already exists")
    project_id = c.execute("INSERT INTO projects (pce_id, name, description, created_at) VALUES (?,?,?,?)",
                           (pid, name, core.clean_text(data.get("description"), 500), core.now())).lastrowid
    if data.get("template"):
        start = datetime.date.fromisoformat(core.clean_date(data.get("start_date"), "Start date")
                                            or datetime.date.today().isoformat())
        phase, items = TEMPLATE_PLANNING
        parent = insert_task(project_id, None, phase)
        previous = None
        for task_name, offset, days in items:
            begin = start + datetime.timedelta(days=offset)
            task_id = insert_task(project_id, parent, task_name, start_date=begin.isoformat(),
                                  end_date=(begin + datetime.timedelta(days=days - 1)).isoformat())
            if previous:
                c.execute("INSERT INTO task_dependencies (predecessor_id, successor_id) VALUES (?,?)", (previous, task_id))
            previous = task_id
        generate_app_tasks(project_id)
        reconcile(pid)
        phase, names = TEMPLATE_CLOSURE
        parent = insert_task(project_id, None, phase)
        previous = None
        for task_name in names:
            task_id = insert_task(project_id, parent, task_name)
            if previous:
                c.execute("INSERT INTO task_dependencies (predecessor_id, successor_id) VALUES (?,?)", (previous, task_id))
            previous = task_id
    c.commit()
    return jsonify(id=project_id)


@bp.patch("/api/projects/<int:project_id>")
def project_update(project_id):
    row = get_project(project_id)
    data = core.body()
    name = clean_name(data.get("name", row["name"]), "Project name")
    if con().execute("SELECT 1 FROM projects WHERE pce_id = ? AND name = ? AND id != ?",
                     (row["pce_id"], name, project_id)).fetchone():
        raise core.ApiError("A project with that name already exists")
    con().execute("UPDATE projects SET name = ?, description = ? WHERE id = ?",
                  (name, core.clean_text(data.get("description", row["description"]), 500), project_id))
    con().commit()
    return jsonify(ok=True)


@bp.post("/api/projects/<int:project_id>/copy")
def project_copy(project_id):
    """Save a project under a new name: its tasks, dependencies, milestones, resources and baseline
    are copied. Comments and the change log stay with the original."""
    src = get_project(project_id)
    data = core.body()
    name = clean_name(data.get("name"), "Project name")
    c = con()
    if c.execute("SELECT 1 FROM projects WHERE pce_id = ? AND name = ?", (src["pce_id"], name)).fetchone():
        raise core.ApiError("A project with that name already exists")
    ts = core.now()
    new_id = c.execute("INSERT INTO projects (pce_id, name, description, created_at) VALUES (?,?,?,?)",
                       (src["pce_id"], name, core.clean_text(data.get("description", src["description"]), 500), ts)).lastrowid
    cols = ("application_id", "environment_id", "name", "description", "sort_order", "priority_id", "status_id", "progress",
            "start_date", "end_date", "assigned_ps_id", "project_manager_id", "app_owner_id", "baseline_start", "baseline_end")
    pending = c.execute("SELECT * FROM tasks WHERE project_id = ? ORDER BY id", (project_id,)).fetchall()
    new_task = {}  # old task id -> copied task id
    while pending:
        ready = [t for t in pending if t["parent_id"] is None or t["parent_id"] in new_task]
        if not ready:
            break
        for t in ready:
            new_task[t["id"]] = c.execute(
                f"INSERT INTO tasks (project_id, parent_id, {', '.join(cols)}, created_at, updated_at) "
                f"VALUES ({', '.join('?' * (len(cols) + 4))})",
                (new_id, new_task.get(t["parent_id"]), *[t[k] for k in cols], ts, ts)).lastrowid
            log(new_task[t["id"]], f"Copied from project {src['name']}")
        pending = [t for t in pending if t["id"] not in new_task]
    marks = ",".join("?" * len(new_task)) or "NULL"
    old_ids = tuple(new_task)
    for d in c.execute(f"SELECT * FROM task_dependencies WHERE successor_id IN ({marks})", old_ids).fetchall():
        if d["predecessor_id"] in new_task:
            c.execute("INSERT INTO task_dependencies (predecessor_id, successor_id) VALUES (?,?)",
                      (new_task[d["predecessor_id"]], new_task[d["successor_id"]]))
    for m in c.execute(f"SELECT * FROM milestones WHERE task_id IN ({marks})", old_ids).fetchall():
        c.execute("INSERT INTO milestones (task_id, name, due_date, status_id) VALUES (?,?,?,?)",
                  (new_task[m["task_id"]], m["name"], m["due_date"], m["status_id"]))
    for r in c.execute(f"SELECT * FROM task_resources WHERE task_id IN ({marks})", old_ids).fetchall():
        c.execute("INSERT INTO task_resources (task_id, person_id) VALUES (?,?)", (new_task[r["task_id"]], r["person_id"]))
    c.commit()
    return jsonify(id=new_id, tasks=len(new_task))


@bp.delete("/api/projects/<int:project_id>")
def project_delete(project_id):
    get_project(project_id)
    con().execute("DELETE FROM projects WHERE id = ?", (project_id,))
    con().commit()
    return jsonify(ok=True)


def generate_app_tasks(project_id):
    """Add one task per tracked application that this project does not cover yet, plus the
    environment sub-tasks of every application task. Returns (applications added, environments added)."""
    c = con()
    project = get_project(project_id)
    phase = c.execute("SELECT id FROM tasks WHERE project_id = ? AND parent_id IS NULL AND name = ?",
                      (project_id, APP_PHASE)).fetchone()
    covered = {r["application_id"] for r in c.execute(
        "SELECT application_id FROM tasks WHERE project_id = ? AND application_id IS NOT NULL", (project_id,))}
    apps = [a for a in c.execute(core.APP_SELECT + " WHERE a.pce_id = ? AND a.hidden = 0 AND a.excluded = 0 ORDER BY a.rank, a.id",
                                 (project["pce_id"],)) if a["id"] not in covered]
    if apps:
        phase_id = phase["id"] if phase else insert_task(project_id, None, APP_PHASE)
        for a in apps:
            insert_task(project_id, phase_id, a["name"], application_id=a["id"], description=a["description"],
                        priority=a["priority"], status=APP_TO_TASK_STATUS[a["status"]], progress=a["progress"],
                        start_date=a["start_date"], end_date=a["end_date"],
                        assigned_ps_id=a["ps_id"], project_manager_id=a["pm_id"], app_owner_id=a["owner_id"])
    return len(apps), add_environment_subtasks(project_id)


def add_environment_subtasks(project_id):
    """Give every application task one sub-task per tracked environment it does not have yet.

    The sub-task takes its status and progress from that environment and its dates, priority and
    people from the application task, which then rolls its own figures up from its environments.
    """
    c = con()
    added = 0
    app_tasks = c.execute(
        TASK_SELECT + " WHERE t.project_id = ? AND t.application_id IS NOT NULL AND t.environment_id IS NULL "
        "AND a.hidden = 0 AND a.excluded = 0 ORDER BY t.sort_order, t.id", (project_id,)).fetchall()
    for task in app_tasks:
        have = {r["environment_id"] for r in c.execute(
            "SELECT environment_id FROM tasks WHERE parent_id = ? AND environment_id IS NOT NULL", (task["id"],))}
        for env in c.execute(core.ENV_SELECT + " WHERE ae.application_id = ? AND ae.excluded = 0 ORDER BY e.name COLLATE NOCASE",
                             (task["application_id"],)).fetchall():
            if env["environment_id"] in have:
                continue
            insert_task(project_id, task["id"], env["name"], application_id=task["application_id"],
                        environment_id=env["environment_id"], priority=env["priority"],
                        status=APP_TO_TASK_STATUS[env["status"]], progress=env["progress"],
                        start_date=env["start_date"] or task["start_date"], end_date=env["end_date"] or task["end_date"],
                        assigned_ps_id=task["ps_id"], project_manager_id=task["pm_id"], app_owner_id=task["owner_id"])
            added += 1
    return added


# ---- keeping application / environment tasks and Application Tracking in step ---------------
# Application Tracking is the record. A task that stands for an application (with no environment
# sub-tasks) mirrors that application's status, progress, priority, dates and people; an environment
# sub-task mirrors that environment's status, progress, priority and dates, and the application's
# people. Edits made on either page are written to the tracking record and copied to every such task.
MIRRORED = ("status", "progress", "priority", "start_date", "end_date", "ps_id", "pm_id", "owner_id")
LEAF = "id NOT IN (SELECT parent_id FROM tasks WHERE parent_id IS NOT NULL)"


def tracking_status(task_status, current):
    """A task has three states, tracking has four: 'In Progress' keeps a current Selective Enforcement."""
    if task_status == "Completed":
        return "Fully Enforced"
    if task_status == "Not Started":
        return "Not Started"
    return current if current in ("In Progress", "Selective Enforcement") else "In Progress"


def mirror_application(app_id):
    a = core.get_app(app_id)
    c = con()
    c.execute(
        f"UPDATE tasks SET priority_id = ?, status_id = ?, progress = ?, start_date = ?, end_date = ? "
        f"WHERE application_id = ? AND environment_id IS NULL AND {LEAF}",
        (core.lookup("priorities")[a["priority"]], core.lookup("task_statuses")[APP_TO_TASK_STATUS[a["status"]]],
         a["progress"], a["start_date"], a["end_date"], app_id))
    c.execute("UPDATE tasks SET assigned_ps_id = ?, project_manager_id = ?, app_owner_id = ? WHERE application_id = ?",
              (a["ps_id"], a["pm_id"], a["owner_id"], app_id))


def mirror_environment(pair_id):
    e = con().execute(core.ENV_SELECT + " WHERE ae.id = ?", (pair_id,)).fetchone()
    if e is None:
        return
    con().execute(
        "UPDATE tasks SET priority_id = ?, status_id = ?, progress = ?, start_date = ?, end_date = ? "
        "WHERE application_id = ? AND environment_id = ?",
        (core.lookup("priorities")[e["priority"]], core.lookup("task_statuses")[APP_TO_TASK_STATUS[e["status"]]],
         e["progress"], e["start_date"], e["end_date"], e["app_id"], e["environment_id"]))


def push_task_to_tracking(row, changes):
    """An edit made on a timeline task that stands for an application or environment is an edit to
    that application or environment: write it there, which in turn updates every task for it."""
    if not row["application_id"] or not any(k in changes for k in MIRRORED):
        return
    app = core.get_app(row["application_id"])
    new = {**dict(row), **changes}
    values = {k: changes[k] for k in changes if k in ("status", "progress", "priority")}
    if "start_date" in changes or "end_date" in changes:   # send both so the pair is always valid
        values.update(start_date=new["start_date"], end_date=new["end_date"])
    people = {k: changes[k] for k in changes if k in ("ps_id", "pm_id", "owner_id")}
    if row["environment_id"]:
        pair = con().execute(core.ENV_SELECT + " WHERE ae.application_id = ? AND ae.environment_id = ?",
                             (row["application_id"], row["environment_id"])).fetchone()
        if pair is not None and values:
            if "status" in values:
                values["status"] = tracking_status(values["status"], pair["status"])
            core.apply_env_changes(pair, values)
        if people:
            core.apply_app_changes(app, people)
    else:
        is_summary = con().execute("SELECT 1 FROM tasks WHERE parent_id = ?", (row["id"],)).fetchone()
        if not is_summary:
            if "status" in values:
                values["status"] = tracking_status(values["status"], app["status"])
            people.update(values)
        if people:
            core.apply_app_changes(app, people)


def reconcile(pce_id):
    """Bring every application / environment task into line with Application Tracking.

    Normally the two already match and this changes nothing. Where they differ (data written before
    they were kept in sync), the side that was edited more recently wins, so nobody's latest edit is
    lost. Blank dates or people in tracking are filled from a task that has them."""
    c = con()

    def differs(task, record):
        return (task["status"] != APP_TO_TASK_STATUS[record["status"]] or task["progress"] != record["progress"]
                or task["priority"] != record["priority"] or task["start_date"] != record["start_date"]
                or task["end_date"] != record["end_date"])

    def from_task(task, record):
        return {"status": tracking_status(task["status"], record["status"]), "progress": task["progress"],
                "priority": task["priority"], "start_date": task["start_date"], "end_date": task["end_date"]}

    for a in c.execute(core.APP_SELECT + " WHERE a.pce_id = ?", (pce_id,)).fetchall():
        every = c.execute(TASK_SELECT + " WHERE t.application_id = ? ORDER BY t.updated_at DESC, t.id", (a["id"],)).fetchall()
        if not every:
            continue
        tasks = [t for t in every if t["environment_id"] is None
                 and not c.execute("SELECT 1 FROM tasks WHERE parent_id = ?", (t["id"],)).fetchone()]
        fill = {}
        for key, column, pool in (("start_date", "start_date", tasks), ("end_date", "end_date", tasks),
                                  ("ps_id", "assigned_ps_id", every), ("pm_id", "project_manager_id", every),
                                  ("owner_id", "app_owner_id", every)):
            if a[key] is None:
                value = next((t[key] for t in pool if t[key] is not None), None)
                if value is not None:
                    fill[column] = value
        start, end = fill.get("start_date", a["start_date"]), fill.get("end_date", a["end_date"])
        if start and end and end < start:
            fill.pop("start_date", None)
            fill.pop("end_date", None)
        if fill:
            c.execute(f"UPDATE applications SET {', '.join(k + ' = ?' for k in fill)} WHERE id = ?", (*fill.values(), a["id"]))
            a = core.get_app(a["id"])
        newest = tasks[0] if tasks else None
        if newest is not None and differs(newest, a) and newest["updated_at"] > a["updated_at"]:
            core.apply_app_changes(a, from_task(newest, a))   # the timeline edit is the latest word
        else:
            mirror_application(a["id"])

    for e in c.execute(core.ENV_SELECT + " WHERE env_app.pce_id = ?", (pce_id,)).fetchall():
        tasks = c.execute(TASK_SELECT + " WHERE t.application_id = ? AND t.environment_id = ? ORDER BY t.updated_at DESC, t.id",
                          (e["app_id"], e["environment_id"])).fetchall()
        if not tasks:
            continue
        fill = {}
        for key in ("start_date", "end_date"):
            if e[key] is None:
                value = next((t[key] for t in tasks if t[key] is not None), None)
                if value is not None:
                    fill[key] = value
        start, end = fill.get("start_date", e["start_date"]), fill.get("end_date", e["end_date"])
        if start and end and end < start:
            fill = {}
        if fill:
            c.execute(f"UPDATE application_environments SET {', '.join(k + ' = ?' for k in fill)} WHERE id = ?", (*fill.values(), e["id"]))
            e = c.execute(core.ENV_SELECT + " WHERE ae.id = ?", (e["id"],)).fetchone()
        # when this environment was last edited in tracking: its audit entries start with its name
        edited = c.execute("SELECT MAX(created_at) m FROM events WHERE application_id = ? AND message LIKE ?",
                           (e["app_id"], e["name"] + ":%")).fetchone()["m"]
        newest = tasks[0]
        if differs(newest, e) and (edited is None or newest["updated_at"] > edited):
            core.apply_env_changes(e, from_task(newest, e))
        else:
            mirror_environment(e["id"])


def list_tracked_applications(pce_id):
    """Keep project plans in step with Application Tracking: every application that is not
    excluded gets a task (and environment sub-tasks) in each project that plans applications.

    Called whenever an application or environment is included again, restored, or newly imported
    by a sync. A project "plans applications" when it has the Application Enforcement phase or any
    application task; projects without either are left alone. Returns (applications, environments) added.
    """
    apps = envs = 0
    for r in con().execute(
            "SELECT p.id FROM projects p WHERE p.pce_id = ? AND EXISTS (SELECT 1 FROM tasks t WHERE t.project_id = p.id "
            "AND (t.application_id IS NOT NULL OR (t.parent_id IS NULL AND t.name = ?)))", (pce_id, APP_PHASE)).fetchall():
        a, e = generate_app_tasks(r["id"])
        apps, envs = apps + a, envs + e
    if apps or envs:
        reconcile(pce_id)
    return apps, envs


@bp.post("/api/projects/<int:project_id>/generate")
def project_generate(project_id):
    added, environments = generate_app_tasks(project_id)
    reconcile(get_project(project_id)["pce_id"])
    con().commit()
    return jsonify(ok=True, added=added, environments=environments)


@bp.post("/api/projects/<int:project_id>/baseline")
def baseline_set(project_id):
    get_project(project_id)
    con().execute("UPDATE tasks SET baseline_start = start_date, baseline_end = end_date WHERE project_id = ?", (project_id,))
    con().commit()
    return jsonify(ok=True)


@bp.delete("/api/projects/<int:project_id>/baseline")
def baseline_clear(project_id):
    get_project(project_id)
    con().execute("UPDATE tasks SET baseline_start = NULL, baseline_end = NULL WHERE project_id = ?", (project_id,))
    con().commit()
    return jsonify(ok=True)


# ----------------------------------------------------------------------------- tasks

def descendants(task_id):
    found, queue = set(), [task_id]
    while queue:
        for r in con().execute("SELECT id FROM tasks WHERE parent_id = ?", (queue.pop(),)):
            if r["id"] not in found:
                found.add(r["id"])
                queue.append(r["id"])
    return found


def clean_parent(value, project_id, task_id=None):
    if value in (None, "", 0):
        return None
    row = con().execute("SELECT id FROM tasks WHERE id = ? AND project_id = ?", (value, project_id)).fetchone()
    if row is None:
        raise core.ApiError("The parent must be a task in the same project")
    if task_id is not None and (row["id"] == task_id or row["id"] in descendants(task_id)):
        raise core.ApiError("A task cannot be moved underneath itself")
    return row["id"]


def task_changes(data, project, current=None):
    """Validate the writable task fields present in `data`."""
    out = {}
    if "name" in data or current is None:
        out["name"] = clean_name(data.get("name"), "Task name")
    if "description" in data:
        out["description"] = core.clean_text(data["description"], 500)
    if "parent_id" in data:
        out["parent_id"] = clean_parent(data["parent_id"], project["id"], current["id"] if current else None)
    if "application_id" in data:
        out["application_id"] = clean_ref(data["application_id"], "applications", "application", project["pce_id"])
    if "environment_id" in data:
        out["environment_id"] = clean_ref(data["environment_id"], "environments", "environment", project["pce_id"])
    if "priority" in data:
        out["priority"] = core.clean_choice(data["priority"], core.PRIORITIES, "Priority")
    if "status" in data:
        out["status"] = core.clean_choice(data["status"], TASK_STATUSES, "Status")
    if "progress" in data:
        out["progress"] = core.clean_progress(data["progress"])
    for field, label in (("start_date", "Start date"), ("end_date", "End date")):
        if field in data:
            out[field] = core.clean_date(data[field], label)
    for field in ("ps_id", "pm_id", "owner_id"):
        if field in data:
            out[field] = core.clean_person(data[field])
    start = out.get("start_date", current["start_date"] if current else None)
    end = out.get("end_date", current["end_date"] if current else None)
    if start and end and end < start:
        raise core.ApiError("End date cannot be before the start date")

    # Keep status and % complete telling the same story when only one of them is sent.
    old_status = current["status"] if current else "Not Started"
    old_progress = current["progress"] if current else 0
    status_changed = "status" in out and out["status"] != old_status
    progress_changed = "progress" in out and out["progress"] != old_progress
    if status_changed and not progress_changed:
        if out["status"] == "Completed":
            out["progress"] = 100
        elif out["status"] == "Not Started":
            out["progress"] = 0
        elif old_progress == 100:
            out["progress"] = 90
    elif progress_changed and not status_changed:
        pct = out["progress"]
        out["status"] = "Completed" if pct == 100 else "In Progress" if pct > 0 else "Not Started"
    return out


@bp.post("/api/tasks")
def task_create():
    data = core.body()
    project = get_project(data.get("project_id"))
    fields = task_changes(data, project)
    parent_id = fields.pop("parent_id", None)
    name = fields.pop("name")
    extra = {TASK_COLUMNS[k]: v for k, v in fields.items() if k not in ("priority", "status")}
    task_id = insert_task(project["id"], parent_id, name, priority=fields.get("priority", "Medium"),
                          status=fields.get("status", "Not Started"), **extra)
    con().commit()
    return jsonify(id=task_id)


LOCKED = ("This task is an application or environment from the PCE: its name cannot be changed and it cannot be "
          "deleted here. Exclude the application or the environment in Application Tracking to take it off the timeline.")


@bp.patch("/api/tasks/<int:task_id>")
def task_update(task_id):
    row = get_task(task_id)
    project = get_project(row["project_id"])
    changes = {k: v for k, v in task_changes(core.body(), project, row).items() if v != row[k]}
    if row["application_id"] and any(k in changes for k in ("name", "application_id", "environment_id")):
        raise core.ApiError(LOCKED)
    if changes:
        ids = {"status": core.lookup("task_statuses"), "priority": core.lookup("priorities")}
        values = [ids[k][v] if k in ids else v for k, v in changes.items()]
        sets = [f"{TASK_COLUMNS[k]} = ?" for k in changes]
        if "parent_id" in changes:
            sets.append("sort_order = ?")
            values.append(next_order(project["id"], changes["parent_id"]))
        con().execute(f"UPDATE tasks SET {', '.join(sets)}, updated_at = ? WHERE id = ?", (*values, core.now(), task_id))
        if "status" in changes:
            log(task_id, f"Status: {row['status']} → {changes['status']}")
        if "progress" in changes:
            log(task_id, f"% complete: {row['progress']}% → {changes['progress']}%")
        if "start_date" in changes or "end_date" in changes:
            new = {**dict(row), **changes}
            log(task_id, f"Dates: {row['start_date'] or '—'} to {row['end_date'] or '—'} → "
                         f"{new['start_date'] or '—'} to {new['end_date'] or '—'}")
        if "priority" in changes:
            log(task_id, f"Priority: {row['priority']} → {changes['priority']}")
        if any(k not in ("status", "progress", "start_date", "end_date", "priority") for k in changes):
            log(task_id, "Task details updated")
        push_task_to_tracking(row, changes)
        con().commit()
    return jsonify(ok=True)


@bp.delete("/api/tasks/<int:task_id>")
def task_delete(task_id):
    row = get_task(task_id)
    if row["application_id"]:
        raise core.ApiError(LOCKED)
    inside = descendants(task_id)
    if inside and con().execute(
            f"SELECT 1 FROM tasks WHERE application_id IS NOT NULL AND id IN ({','.join('?' * len(inside))})",
            tuple(inside)).fetchone():
        raise core.ApiError("This phase contains applications from the PCE, so it cannot be deleted. "
                            "Move them to another phase first, or exclude them in Application Tracking.")
    con().execute("DELETE FROM tasks WHERE id = ?", (task_id,))  # children cascade
    con().commit()
    return jsonify(ok=True)


@bp.post("/api/tasks/<int:task_id>/move")
def task_move(task_id):
    row = get_task(task_id)
    direction = core.body().get("direction")
    if direction not in ("up", "down"):
        raise core.ApiError("direction must be 'up' or 'down'")
    siblings = [r["id"] for r in con().execute(
        "SELECT id FROM tasks WHERE project_id = ? AND parent_id IS ? ORDER BY sort_order, id",
        (row["project_id"], row["parent_id"]))]
    at = siblings.index(task_id)
    to = at - 1 if direction == "up" else at + 1
    if 0 <= to < len(siblings):
        siblings[at], siblings[to] = siblings[to], siblings[at]
        for pos, sid in enumerate(siblings, start=1):
            con().execute("UPDATE tasks SET sort_order = ? WHERE id = ?", (pos, sid))
        con().commit()
    return jsonify(ok=True)


@bp.post("/api/tasks/<int:task_id>/reorder")
def task_reorder(task_id):
    """Drag-and-drop: place a task before/after another task, or inside a phase (or the project top level)."""
    row = get_task(task_id)
    data = core.body()
    position = data.get("position")
    if position not in ("before", "after", "inside"):
        raise core.ApiError("position must be 'before', 'after' or 'inside'")
    target_id = data.get("target_id")
    if target_id in (None, "", 0):
        if position != "inside":
            raise core.ApiError("target_id is required")
        parent_id, anchor = None, None  # top level of the project
    else:
        target = get_task(target_id)
        if target["project_id"] != row["project_id"]:
            raise core.ApiError("Tasks can only be reordered within their own project")
        if target["id"] == task_id:
            return jsonify(ok=True)
        parent_id = target["id"] if position == "inside" else target["parent_id"]
        anchor = None if position == "inside" else target["id"]
    parent_id = clean_parent(parent_id, row["project_id"], task_id)
    siblings = [r["id"] for r in con().execute(
        "SELECT id FROM tasks WHERE project_id = ? AND parent_id IS ? AND id != ? ORDER BY sort_order, id",
        (row["project_id"], parent_id, task_id))]
    at = len(siblings) if anchor is None else siblings.index(anchor) + (1 if position == "after" else 0)
    siblings.insert(at, task_id)
    con().execute("UPDATE tasks SET parent_id = ?, updated_at = ? WHERE id = ?", (parent_id, core.now(), task_id))
    for pos, sid in enumerate(siblings, start=1):
        con().execute("UPDATE tasks SET sort_order = ? WHERE id = ?", (pos, sid))
    if parent_id != row["parent_id"]:
        log(task_id, "Moved to another phase")
    con().commit()
    return jsonify(ok=True)


@bp.post("/api/tasks/<int:task_id>/dependencies")
def dependency_add(task_id):
    task = get_task(task_id)
    pred = get_task(core.body().get("predecessor_id") or 0)
    if pred["id"] == task_id:
        raise core.ApiError("A task cannot depend on itself")
    if pred["project_id"] != task["project_id"]:
        raise core.ApiError("Dependencies must stay within one project")
    # Reject cycles: the predecessor must not already come after this task.
    seen, queue = set(), [task_id]
    while queue:
        for r in con().execute("SELECT successor_id FROM task_dependencies WHERE predecessor_id = ?", (queue.pop(),)):
            if r["successor_id"] == pred["id"]:
                raise core.ApiError(f"That would create a loop: {pred['name']} already depends on {task['name']}")
            if r["successor_id"] not in seen:
                seen.add(r["successor_id"])
                queue.append(r["successor_id"])
    con().execute("INSERT OR IGNORE INTO task_dependencies (predecessor_id, successor_id) VALUES (?,?)",
                  (pred["id"], task_id))
    log(task_id, f"Dependency added: after {pred['name']}")
    con().commit()
    return jsonify(ok=True)


@bp.delete("/api/tasks/<int:task_id>/dependencies/<int:pred_id>")
def dependency_remove(task_id, pred_id):
    get_task(task_id)
    con().execute("DELETE FROM task_dependencies WHERE predecessor_id = ? AND successor_id = ?", (pred_id, task_id))
    log(task_id, "Dependency removed")
    con().commit()
    return jsonify(ok=True)


@bp.put("/api/tasks/<int:task_id>/resources")
def resources_set(task_id):
    get_task(task_id)
    ids = core.body().get("person_ids")
    if not isinstance(ids, list):
        raise core.ApiError("person_ids must be a list")
    people = [core.clean_person(i) for i in ids]
    con().execute("DELETE FROM task_resources WHERE task_id = ?", (task_id,))
    for person_id in dict.fromkeys(p for p in people if p):
        con().execute("INSERT INTO task_resources (task_id, person_id) VALUES (?,?)", (task_id, person_id))
    log(task_id, "Resources updated")
    con().commit()
    return jsonify(ok=True)


@bp.post("/api/tasks/<int:task_id>/comments")
def task_comment(task_id):
    task = get_task(task_id)
    text = core.clean_text(core.body().get("text"), 500)
    if not text:
        raise core.ApiError("Comment cannot be empty")
    if task["application_id"]:
        # Goes on the application's thread, so Application Tracking shows the same comment.
        pair = con().execute("SELECT id FROM application_environments WHERE application_id = ? AND environment_id = ?",
                             (task["application_id"], task["environment_id"])).fetchone() if task["environment_id"] else None
        core.add_comment(task["application_id"], text, pair["id"] if pair else None)
    else:
        con().execute("INSERT INTO task_comments (task_id, author_id, body, created_at) VALUES (?,?,?,?)",
                      (task_id, core.current_user_id(), text, core.now()))
    con().commit()
    return jsonify(ok=True)


# ------------------------------------------------------------------------ milestones

def milestone_fields(data, partial):
    out = {}
    if "name" in data or not partial:
        out["name"] = clean_name(data.get("name"), "Milestone name")
    if "due_date" in data or not partial:
        out["due_date"] = core.clean_date(data.get("due_date"), "Date")
        if not out["due_date"]:
            raise core.ApiError("A milestone needs a date")
    if "status" in data or not partial:
        status = core.clean_choice(data.get("status", "Not Started"), TASK_STATUSES, "Status")
        out["status_id"] = core.lookup("task_statuses")[status]
    return out


@bp.post("/api/tasks/<int:task_id>/milestones")
def milestone_create(task_id):
    get_task(task_id)
    fields = milestone_fields(core.body(), partial=False)
    con().execute("INSERT INTO milestones (task_id, name, due_date, status_id) VALUES (?,?,?,?)",
                  (task_id, fields["name"], fields["due_date"], fields["status_id"]))
    log(task_id, f"Milestone added: {fields['name']}")
    con().commit()
    return jsonify(ok=True)


@bp.patch("/api/milestones/<int:milestone_id>")
def milestone_update(milestone_id):
    if con().execute("SELECT 1 FROM milestones WHERE id = ?", (milestone_id,)).fetchone() is None:
        raise core.ApiError("Milestone not found", 404)
    fields = milestone_fields(core.body(), partial=True)
    if fields:
        con().execute(f"UPDATE milestones SET {', '.join(k + ' = ?' for k in fields)} WHERE id = ?",
                      (*fields.values(), milestone_id))
        con().commit()
    return jsonify(ok=True)


@bp.delete("/api/milestones/<int:milestone_id>")
def milestone_delete(milestone_id):
    con().execute("DELETE FROM milestones WHERE id = ?", (milestone_id,))
    con().commit()
    return jsonify(ok=True)
