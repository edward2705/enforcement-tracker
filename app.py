"""Illumio Enforcement Tracker - Flask API + static React/TypeScript front end."""
import csv
import datetime
import getpass
import io
import os
import socket
import sys
import threading

import requests
from flask import Flask, Response, g, jsonify, request, send_from_directory
from werkzeug.serving import make_server

import db
import pce as pcelib

STATUSES = [name for name, _pct in db.STATUSES]
PRIORITIES = db.PRIORITIES
ROLE_CODES = [code for code, _name in db.ROLES]
# What the PCE itself reports for a group of workloads -> the tracker status it implies.
PCE_STATE_STATUS = {
    "Fully Enforced": "Fully Enforced",
    "Partially Enforced": "Selective Enforcement",
    "Visibility Only": "In Progress",
    "Idle": "Not Started",
    "No Managed Workloads": "Not Started",
}
WL_COLS = ("wl_full", "wl_selective", "wl_visibility", "wl_idle", "wl_unmanaged")
MODE_COL = {"full": "wl_full", "selective": "wl_selective",
            "visibility_only": "wl_visibility", "idle": "wl_idle"}
# API field -> applications column
APP_COLUMNS = {
    "description": "description", "excluded": "excluded", "progress": "progress", "start_date": "start_date", "end_date": "end_date",
    "priority": "priority_id", "status": "status_id",
    "ps_id": "assigned_ps_id", "pm_id": "project_manager_id", "owner_id": "app_owner_id",
}
APP_SELECT = """
SELECT a.id, a.pce_id, a.label_href, a.name, a.description, a.rank, a.progress, a.start_date, a.end_date,
       p.name AS priority, s.name AS status,
       a.assigned_ps_id AS ps_id, a.project_manager_id AS pm_id, a.app_owner_id AS owner_id,
       a.wl_full, a.wl_selective, a.wl_visibility, a.wl_idle, a.wl_unmanaged,
       a.in_pce, a.hidden, a.excluded, a.created_at, a.updated_at,
       (SELECT c.body FROM comments c WHERE c.application_id = a.id AND c.application_environment_id IS NULL
        ORDER BY c.id DESC LIMIT 1) AS comments,
       (SELECT COUNT(*) FROM comments c WHERE c.application_id = a.id) AS comment_count
FROM applications a
JOIN priorities p ON p.id = a.priority_id
JOIN statuses s ON s.id = a.status_id
"""
ENV_SELECT = """
SELECT ae.id, ae.application_id AS app_id, ae.environment_id, e.name, e.label_href, ae.excluded, ae.in_pce, s.name AS status, ae.progress,
       COALESCE(ep.name, app_p.name) AS priority, ae.priority_id IS NULL AS priority_inherited, ae.start_date, ae.end_date,
       (SELECT c.body FROM comments c WHERE c.application_environment_id = ae.id ORDER BY c.id DESC LIMIT 1) AS comments,
       ae.wl_full, ae.wl_selective, ae.wl_visibility, ae.wl_idle, ae.wl_unmanaged
FROM application_environments ae
JOIN environments e ON e.id = ae.environment_id
JOIN statuses s ON s.id = ae.status_id
JOIN applications env_app ON env_app.id = ae.application_id
JOIN priorities app_p ON app_p.id = env_app.priority_id
LEFT JOIN priorities ep ON ep.id = ae.priority_id
"""
AUTHOR_SQL = "COALESCE(pe.name, CASE WHEN ev.kind = 'sync' THEN 'PCE Sync' ELSE 'Former user' END)"

app = Flask(__name__, static_folder="static", static_url_path="/static")
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0
db.init()


class ApiError(Exception):
    def __init__(self, message, code=400):
        super().__init__(message)
        self.message = message
        self.code = code


@app.errorhandler(ApiError)
def _api_error(err):
    return jsonify(error=err.message), err.code


@app.errorhandler(pcelib.PCEError)
def _pce_error(err):
    return jsonify(error=str(err)), 502


@app.errorhandler(requests.RequestException)
def _pce_unreachable(err):
    if isinstance(err, requests.exceptions.SSLError):
        msg = "TLS verification failed - check cert_path for this PCE"
    elif isinstance(err, requests.exceptions.Timeout):
        msg = "The PCE did not respond in time"
    elif isinstance(err, requests.exceptions.ConnectionError):
        msg = "Could not connect to the PCE - check the FQDN, port and network"
    else:
        msg = f"PCE request failed ({type(err).__name__})"
    return jsonify(error=msg), 502


@app.before_request
def _guard_writes():
    # A custom header cannot be sent cross-site without a CORS preflight, which this
    # server never approves, so other web pages cannot drive the local API.
    if request.path.startswith("/api/") and request.method != "GET":
        if request.headers.get("X-Requested-With") != "enforcement-tracker":
            raise ApiError("Missing X-Requested-With header", 403)


def con():
    if "db" not in g:
        g.db = db.connect()
    return g.db


@app.teardown_appcontext
def _close_db(_exc):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def lookup(table):
    """name -> id for a lookup table (table name is always a literal from this module)."""
    return {r["name"]: r["id"] for r in con().execute(f"SELECT id, name FROM {table}")}


def current_user():
    default = getpass.getuser().replace(".", " ").replace("_", " ").title()
    return db.get_setting(con(), "user_name", default) or default


def current_user_id():
    """The signed-in user is a row in people so comments and events can reference it."""
    name = current_user()
    row = con().execute("SELECT id FROM people WHERE name = ?", (name,)).fetchone()
    if row:
        return row["id"]
    return con().execute("INSERT INTO people (name) VALUES (?)", (name,)).lastrowid


def body():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ApiError("Expected a JSON object")
    return data


def active_pce_name():
    cfg = pcelib.load_config()
    name = cfg.get("default_pce")
    if not name or not isinstance(cfg.get(name), dict):
        names = pcelib.pce_names(cfg)
        name = names[0] if names else ""
    return cfg, name


def pce_id_for(name, create=False):
    row = con().execute("SELECT id FROM pces WHERE name = ?", (name,)).fetchone()
    if row:
        return row["id"]
    if not create:
        return None
    return con().execute("INSERT INTO pces (name, created_at) VALUES (?, ?)", (name, now())).lastrowid


def get_app(app_id):
    row = con().execute(APP_SELECT + " WHERE a.id = ?", (app_id,)).fetchone()
    if row is None:
        raise ApiError("Application not found", 404)
    return row


def log_event(app_id, kind, message, system=False):
    con().execute(
        "INSERT INTO events (application_id, author_id, kind, message, created_at) VALUES (?,?,?,?,?)",
        (app_id, None if system else current_user_id(), kind, message, now()),
    )


def add_comment(app_id, text, env_id=None):
    """Add to the application's comment thread; env_id ties the comment to one of its environments."""
    text = clean_text(text, 500)
    if not text:
        return
    ts = now()
    con().execute("INSERT INTO comments (application_id, application_environment_id, author_id, body, created_at) "
                  "VALUES (?,?,?,?,?)", (app_id, env_id, current_user_id(), text, ts))
    con().execute("UPDATE applications SET updated_at = ? WHERE id = ?", (ts, app_id))
    log_event(app_id, "comment", "Comment added")


def clean_text(value, limit):
    text = str(value or "").strip()
    if len(text) > limit:
        raise ApiError(f"Text is limited to {limit} characters")
    return text


def clean_date(value, field):
    if value in (None, ""):
        return None
    try:
        return datetime.date.fromisoformat(str(value)).isoformat()
    except ValueError:
        raise ApiError(f"{field} must be a YYYY-MM-DD date")


def clean_progress(value):
    try:
        pct = int(value)
    except (TypeError, ValueError):
        raise ApiError("Progress must be a whole number")
    if not 0 <= pct <= 100:
        raise ApiError("Progress must be between 0 and 100")
    return pct


def clean_person(value):
    if value in (None, "", 0):
        return None
    row = con().execute("SELECT id FROM people WHERE id = ?", (value,)).fetchone()
    if row is None:
        raise ApiError("Unknown person")
    return row["id"]


def clean_choice(value, choices, field):
    if value not in choices:
        raise ApiError(f"{field} must be one of: {', '.join(choices)}")
    return value


def derive_state(counts):
    """Enforcement as the PCE sees it, from managed-workload counts per enforcement mode."""
    managed = counts["wl_full"] + counts["wl_selective"] + counts["wl_visibility"] + counts["wl_idle"]
    if managed == 0:
        return "No Managed Workloads"
    if counts["wl_full"] == managed:
        return "Fully Enforced"
    if counts["wl_full"] or counts["wl_selective"]:
        return "Partially Enforced"
    if counts["wl_visibility"]:
        return "Visibility Only"
    return "Idle"


def with_state(row):
    out = dict(row)
    out["pce_state"] = derive_state(out)
    return out


def env_dates(app_id):
    """Earliest start and latest end among an application's tracked environments (None when no
    environment has that date). When present they define the application's own dates."""
    r = con().execute("SELECT MIN(start_date) AS s, MAX(end_date) AS e FROM application_environments "
                      "WHERE application_id = ? AND excluded = 0", (app_id,)).fetchone()
    return r["s"], r["e"]


def recalc_app_dates(app_id):
    """Set the application's start and end date from its environments' dates, if they have any."""
    start, end = env_dates(app_id)
    if start is None and end is None:
        return False
    row = con().execute("SELECT start_date, end_date FROM applications WHERE id = ?", (app_id,)).fetchone()
    new_start = start if start is not None else row["start_date"]
    new_end = end if end is not None else row["end_date"]
    if new_start and new_end and new_end < new_start:   # a leftover manual date that no longer fits
        if start is None:
            new_start = None
        else:
            new_end = None
    if (new_start, new_end) == (row["start_date"], row["end_date"]):
        return False
    con().execute("UPDATE applications SET start_date = ?, end_date = ?, updated_at = ? WHERE id = ?",
                  (new_start, new_end, now(), app_id))
    log_event(app_id, "edit", f"Dates calculated from environments: {new_start or '—'} to {new_end or '—'}", system=True)
    timeline.mirror_application(app_id)
    return True


def people_list():
    people = [dict(r, is_ps=0, is_pm=0, is_owner=0)
              for r in con().execute("SELECT id, name, email, title FROM people ORDER BY name")]
    by_id = {p["id"]: p for p in people}
    for r in con().execute("SELECT pr.person_id, r.code FROM person_roles pr JOIN roles r ON r.id = pr.role_id"):
        by_id[r["person_id"]]["is_" + r["code"]] = 1
    return people


# ----------------------------------------------------------------------------- pages

@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


# ------------------------------------------------------------------------- bootstrap

@app.get("/api/bootstrap")
def bootstrap():
    cfg, name = active_pce_name()
    c = con()
    pid = pce_id_for(name)
    apps = [with_state(r) for r in c.execute(APP_SELECT + " WHERE a.pce_id = ? ORDER BY a.rank, a.id", (pid,))]
    by_id = {a["id"]: a for a in apps}
    for a in apps:
        a["environments"] = []
    for e in c.execute(ENV_SELECT + " JOIN applications a ON a.id = ae.application_id "
                       "WHERE a.pce_id = ? ORDER BY e.name COLLATE NOCASE", (pid,)):
        by_id[e["app_id"]]["environments"].append(with_state(e))
    for a in apps:   # which of the application's dates are calculated from its environments
        tracked = [e for e in a["environments"] if not e["excluded"]]
        a["start_auto"] = any(e["start_date"] for e in tracked)
        a["end_auto"] = any(e["end_date"] for e in tracked)
    activity = [dict(r) for r in c.execute(
        f"SELECT ev.id, ev.application_id AS app_id, ev.kind, ev.message AS text, ev.created_at, "
        f"{AUTHOR_SQL} AS author, a.name AS app_name "
        "FROM events ev JOIN applications a ON a.id = ev.application_id "
        "LEFT JOIN people pe ON pe.id = ev.author_id "
        "WHERE a.pce_id = ? ORDER BY ev.id DESC LIMIT 25", (pid,)
    )]
    last = c.execute("SELECT completed_at FROM sync_runs WHERE pce_id = ? ORDER BY id DESC LIMIT 1", (pid,)).fetchone()
    conf = cfg.get(name) or {}
    return jsonify(
        apps=[a for a in apps if not a["hidden"]],
        hidden=[a for a in apps if a["hidden"]],
        people=people_list(),
        activity=activity,
        statuses=STATUSES,
        priorities=PRIORITIES,
        pce_state_status=PCE_STATE_STATUS,
        settings={
            "user_name": current_user(),
            "last_sync": last["completed_at"] if last else "",
        },
        pce={
            "name": name,
            "fqdn": conf.get("pce_fqdn", ""),
            "ui_base": f"https://{conf.get('pce_fqdn', '')}:{conf.get('port', 443)}" if conf else "",
        },
    )


# ------------------------------------------------------------------------------ sync

def empty_counts():
    return {col: 0 for col in WL_COLS}


@app.post("/api/sync")
def sync():
    cfg, name = active_pce_name()
    if not name:
        raise ApiError("No PCE is configured - add one in Settings")
    client = pcelib.PCE(name, cfg[name])
    app_labels = client.collection("/labels", {"key": "app"})
    env_labels = client.collection("/labels", {"key": "env"})
    workloads = client.collection("/workloads")

    app_counts, env_counts = {}, {}
    for wl in workloads:
        if wl.get("deleted"):
            continue
        labels = {l.get("key"): l.get("href") for l in wl.get("labels") or []}
        app_href = labels.get("app")
        if not app_href:
            continue
        col = MODE_COL.get(wl.get("enforcement_mode")) if wl.get("managed") else "wl_unmanaged"
        if col is None:
            continue
        app_counts.setdefault(app_href, empty_counts())[col] += 1
        env_href = labels.get("env")
        if env_href:
            env_counts.setdefault(app_href, {}).setdefault(env_href, empty_counts())[col] += 1

    c = con()
    ts = now()
    pid = pce_id_for(name, create=True)
    status_id = lookup("statuses")
    default_progress = dict(db.STATUSES)
    medium = lookup("priorities")["Medium"]
    # A sync never overwrites tracker settings. For applications and environments it already
    # knows, it only refreshes what the PCE owns: the label name, workload counts and whether
    # the label still exists. Status, progress, priority, dates, people and exclusions are kept.

    env_id = {}
    for label in env_labels:
        if label.get("deleted"):
            continue
        c.execute(
            "INSERT INTO environments (pce_id, label_href, name) VALUES (?,?,?) "
            "ON CONFLICT(pce_id, label_href) DO UPDATE SET name = excluded.name",
            (pid, label["href"], label["value"]),
        )
    for r in c.execute("SELECT id, label_href FROM environments WHERE pce_id = ?", (pid,)):
        env_id[r["label_href"]] = r["id"]

    env_name = {r["id"]: r["name"] for r in c.execute("SELECT id, name FROM environments WHERE pce_id = ?", (pid,))}
    existing = {r["label_href"]: r for r in c.execute("SELECT * FROM applications WHERE pce_id = ?", (pid,))}
    next_rank = c.execute("SELECT COALESCE(MAX(rank), 0) m FROM applications WHERE pce_id = ?", (pid,)).fetchone()["m"] + 1
    stats = {"applications": 0, "added": 0, "environments": 0, "workloads": len(workloads), "deleted": 0,
             "deleted_environments": 0}
    seen = set()

    for label in app_labels:
        if label.get("deleted"):
            continue
        href = label["href"]
        seen.add(href)
        stats["applications"] += 1
        counts = app_counts.get(href, empty_counts())
        state = derive_state(counts)
        implied = PCE_STATE_STATUS[state]
        row = existing.get(href)
        if row is None:
            app_id = c.execute(
                "INSERT INTO applications (pce_id, label_href, name, rank, priority_id, status_id, progress, "
                "wl_full, wl_selective, wl_visibility, wl_idle, wl_unmanaged, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (pid, href, label["value"], next_rank, medium, status_id[implied], default_progress[implied],
                 *[counts[k] for k in WL_COLS], ts, ts),
            ).lastrowid
            next_rank += 1
            stats["added"] += 1
            log_event(app_id, "sync", f"Imported from PCE ({state})", system=True)
        else:
            app_id = row["id"]
            c.execute(
                "UPDATE applications SET name = ?, in_pce = 1, wl_full = ?, wl_selective = ?, "
                "wl_visibility = ?, wl_idle = ?, wl_unmanaged = ? WHERE id = ?",
                (label["value"], *[counts[k] for k in WL_COLS], app_id),
            )
            old_state = derive_state(row)
            if old_state != state:
                log_event(app_id, "sync", f"PCE enforcement changed: {old_state} → {state}", system=True)
            if not row["in_pce"]:
                log_event(app_id, "sync", "Application label exists in the PCE again", system=True)

        known = {r["environment_id"]: r for r in c.execute(
            "SELECT * FROM application_environments WHERE application_id = ?", (app_id,))}
        current = {env_id[h]: v for h, v in env_counts.get(href, {}).items() if h in env_id}
        for eid, ecounts in current.items():
            estate = derive_state(ecounts)
            eimplied = PCE_STATE_STATUS[estate]
            erow = known.get(eid)
            stats["environments"] += 1
            if erow is None:
                c.execute(
                    "INSERT INTO application_environments (application_id, environment_id, status_id, progress, "
                    "wl_full, wl_selective, wl_visibility, wl_idle, wl_unmanaged) VALUES (?,?,?,?,?,?,?,?,?)",
                    (app_id, eid, status_id[eimplied], default_progress[eimplied], *[ecounts[k] for k in WL_COLS]),
                )
            else:
                c.execute(
                    "UPDATE application_environments SET in_pce = 1, wl_full = ?, wl_selective = ?, "
                    "wl_visibility = ?, wl_idle = ?, wl_unmanaged = ? WHERE id = ?",
                    (*[ecounts[k] for k in WL_COLS], erow["id"]),
                )
                if not erow["in_pce"]:
                    log_event(app_id, "sync", f"Environment {env_name[eid]} exists in the PCE again", system=True)
        # An environment that is gone from the PCE for this application (its label was deleted, or no
        # workload carries both labels any more) keeps its tracking data and is flagged as deleted.
        for eid, erow in known.items():
            if eid not in current:
                c.execute(
                    "UPDATE application_environments SET in_pce = 0, wl_full = 0, wl_selective = 0, wl_visibility = 0, "
                    "wl_idle = 0, wl_unmanaged = 0 WHERE id = ?", (erow["id"],),
                )
                if erow["in_pce"]:
                    log_event(app_id, "sync", f"Environment {env_name[eid]} was deleted in the PCE", system=True)
                    stats["deleted_environments"] += 1

    # Keep a snapshot of every workload for the VEN dashboard (status, version, heartbeat, OS...).
    app_ids = {r["label_href"]: r["id"] for r in c.execute("SELECT id, label_href FROM applications WHERE pce_id = ?", (pid,))}
    c.execute("DELETE FROM workloads WHERE pce_id = ?", (pid,))
    for wl in workloads:
        if wl.get("deleted"):
            continue
        labels = {l.get("key"): l for l in wl.get("labels") or []}
        agent = (wl.get("agent") or {}).get("status") or {}
        ven = wl.get("ven") or {}
        c.execute(
            "INSERT OR REPLACE INTO workloads (pce_id, href, hostname, application_id, environment_id, managed, online, "
            "enforcement_mode, ven_status, ven_version, policy_sync_state, last_heartbeat, os, location, role) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (pid, wl.get("href", ""), wl.get("hostname") or wl.get("name") or "",
             app_ids.get((labels.get("app") or {}).get("href")), env_id.get((labels.get("env") or {}).get("href")),
             1 if wl.get("managed") else 0, 1 if wl.get("online") else 0, wl.get("enforcement_mode") or "",
             ven.get("status") or agent.get("status") or "", ven.get("version") or agent.get("agent_version") or "",
             agent.get("security_policy_sync_state") or "", agent.get("last_heartbeat_on"), wl.get("os_id") or "",
             (labels.get("loc") or {}).get("value", ""), (labels.get("role") or {}).get("value", "")))

    for href, row in existing.items():
        if href not in seen and row["in_pce"]:
            c.execute("UPDATE applications SET in_pce = 0 WHERE id = ?", (row["id"],))
            log_event(row["id"], "sync", "Application label was deleted in the PCE", system=True)
            stats["deleted"] += 1

    c.execute(
        "INSERT INTO sync_runs (pce_id, completed_at, application_count, environment_count, workload_count) "
        "VALUES (?,?,?,?,?)", (pid, ts, stats["applications"], stats["environments"], stats["workloads"]),
    )
    renumber(pid)  # newly imported applications rank after the active ones, ahead of excluded ones
    stats["timeline_applications"], stats["timeline_environments"] = timeline.list_tracked_applications(pid)
    timeline.reconcile(pid)
    for _app in c.execute("SELECT id FROM applications WHERE pce_id = ?", (pid,)).fetchall():
        recalc_app_dates(_app["id"])
    c.commit()
    return jsonify(ok=True, last_sync=ts, **stats)


@app.get("/api/ven")
def ven_inventory():
    """Workloads as of the last sync, for the VEN dashboard. Workloads of removed or excluded
    applications are left out, like everywhere else those applications are not counted."""
    _cfg, name = active_pce_name()
    rows = con().execute(
        "SELECT w.id, w.hostname, a.id AS application_id, a.name AS application, e.name AS environment, w.managed, w.online, "
        "w.enforcement_mode, w.ven_status, w.ven_version, w.policy_sync_state, w.last_heartbeat, w.os, w.location, w.role "
        "FROM workloads w LEFT JOIN applications a ON a.id = w.application_id LEFT JOIN environments e ON e.id = w.environment_id "
        "WHERE w.pce_id = ? AND COALESCE(a.hidden, 0) = 0 AND COALESCE(a.excluded, 0) = 0 "
        "ORDER BY a.name IS NULL, a.name COLLATE NOCASE, e.name COLLATE NOCASE, w.hostname COLLATE NOCASE", (pce_id_for(name),))
    return jsonify(workloads=[dict(r) for r in rows])


# ---------------------------------------------------------------------- applications

@app.get("/api/applications/<int:app_id>")
def application_detail(app_id):
    get_app(app_id)
    c = con()
    return jsonify(
        comments=[dict(r) for r in c.execute(
            "SELECT cm.id, CASE WHEN en.name IS NULL THEN cm.body ELSE '[' || en.name || '] ' || cm.body END AS text, "
            "cm.created_at, COALESCE(pe.name, 'Former user') AS author "
            "FROM comments cm LEFT JOIN people pe ON pe.id = cm.author_id "
            "LEFT JOIN application_environments cae ON cae.id = cm.application_environment_id "
            "LEFT JOIN environments en ON en.id = cae.environment_id "
            "WHERE cm.application_id = ? ORDER BY cm.id DESC", (app_id,))],
        events=[dict(r) for r in c.execute(
            f"SELECT ev.id, ev.kind, ev.message AS text, ev.created_at, {AUTHOR_SQL} AS author "
            "FROM events ev LEFT JOIN people pe ON pe.id = ev.author_id "
            "WHERE ev.application_id = ? ORDER BY ev.id DESC", (app_id,))],
    )


def apply_app_changes(row, changes):
    """Persist validated API-level changes for one application and record the audit trail."""
    if changes.get("status") == "Fully Enforced" and row["status"] != "Fully Enforced" and "progress" not in changes:
        changes["progress"] = 100
    # Dates that are calculated from the environments cannot be set on the application.
    auto_start, auto_end = env_dates(row["id"])
    if auto_start is not None:
        changes.pop("start_date", None)
    if auto_end is not None:
        changes.pop("end_date", None)
    changes = {k: v for k, v in changes.items() if v != row[k]}
    if not changes:
        return
    ids = {"status": lookup("statuses"), "priority": lookup("priorities")}
    values = [ids[k][v] if k in ids else v for k, v in changes.items()]
    sets = ", ".join(f"{APP_COLUMNS[k]} = ?" for k in changes)
    con().execute(f"UPDATE applications SET {sets}, updated_at = ? WHERE id = ?", (*values, now(), row["id"]))
    if "status" in changes:
        log_event(row["id"], "status", f"Status changed: {row['status']} → {changes['status']}")
    if "progress" in changes:
        log_event(row["id"], "progress", f"Progress updated: {row['progress']}% → {changes['progress']}%")
    if "priority" in changes:
        log_event(row["id"], "priority", f"Priority changed: {row['priority']} → {changes['priority']}")
    if "excluded" in changes:
        log_event(row["id"], "edit", "Excluded from tracking" if changes["excluded"] else "Included in tracking again")
        # Excluding closes the gap in the ranking; including again adds the application at the end.
        renumber(row["pce_id"])
        if not changes["excluded"]:
            timeline.list_tracked_applications(row["pce_id"])  # an included application is listed on the timeline
    if any(k not in ("status", "progress", "priority", "excluded") for k in changes):
        log_event(row["id"], "edit", "Project details updated")
    timeline.mirror_application(row["id"])  # Project Timeline tasks for this application follow it


@app.patch("/api/applications/<int:app_id>")
def application_update(app_id):
    row = get_app(app_id)
    data = body()
    changes = {}
    if "description" in data:
        changes["description"] = clean_text(data["description"], 500)
    if "excluded" in data:
        changes["excluded"] = 1 if data["excluded"] else 0
    if "priority" in data:
        changes["priority"] = clean_choice(data["priority"], PRIORITIES, "Priority")
    if "status" in data:
        changes["status"] = clean_choice(data["status"], STATUSES, "Status")
    if "progress" in data:
        changes["progress"] = clean_progress(data["progress"])
    for field, label in (("start_date", "Start date"), ("end_date", "End date")):
        if field in data:
            changes[field] = clean_date(data[field], label)
    for field in ("ps_id", "pm_id", "owner_id"):
        if field in data:
            changes[field] = clean_person(data[field])
    auto_start, auto_end = env_dates(app_id)
    for field, auto, label in (("start_date", auto_start, "start"), ("end_date", auto_end, "end")):
        if auto is not None and field in changes:
            if changes[field] != row[field]:
                raise ApiError(f"The {label} date is calculated from this application's environments. Change the environment dates instead.")
            del changes[field]
    start = changes.get("start_date", row["start_date"])
    end = changes.get("end_date", row["end_date"])
    if start and end and end < start:
        raise ApiError("End date cannot be before the start date")
    apply_app_changes(row, changes)
    if data.get("comment"):
        add_comment(app_id, data["comment"])
    con().commit()
    return jsonify(ok=True)


@app.post("/api/applications/bulk")
def application_bulk():
    data = body()
    ids = data.get("ids")
    if not isinstance(ids, list) or not ids or not all(isinstance(i, int) for i in ids):
        raise ApiError("ids must be a non-empty list of application ids")
    changes = {}
    if "status" in data:
        changes["status"] = clean_choice(data["status"], STATUSES, "Status")
    if "priority" in data:
        changes["priority"] = clean_choice(data["priority"], PRIORITIES, "Priority")
    if "excluded" in data:
        changes["excluded"] = 1 if data["excluded"] else 0
    rows = con().execute(APP_SELECT + f" WHERE a.id IN ({','.join('?' * len(ids))})", ids).fetchall()
    for row in rows:
        apply_app_changes(row, dict(changes))
        if data.get("hide") and not row["hidden"]:
            hide_app(row)
    con().commit()
    return jsonify(ok=True, count=len(rows))


def renumber(pce_id, ordered_ids=None):
    """Close gaps in the ranking. Excluded applications are not ranked: they are numbered after
    every active application (the column is NOT NULL), and the UI shows no rank for them."""
    c = con()
    if ordered_ids is None:
        ordered_ids = [r["id"] for r in c.execute(
            "SELECT id FROM applications WHERE pce_id = ? AND hidden = 0 ORDER BY excluded, rank, id", (pce_id,))]
    for pos, app_id in enumerate(ordered_ids, start=1):
        c.execute("UPDATE applications SET rank = ? WHERE id = ?", (pos, app_id))


def hide_app(row):
    con().execute("UPDATE applications SET hidden = 1, updated_at = ? WHERE id = ?", (now(), row["id"]))
    log_event(row["id"], "edit", "Removed from the tracker")
    renumber(row["pce_id"])


@app.post("/api/applications/<int:app_id>/rank")
def application_rank(app_id):
    row = get_app(app_id)
    try:
        target = int(body().get("rank"))
    except (TypeError, ValueError):
        raise ApiError("rank must be a number")
    if row["excluded"]:
        raise ApiError("Excluded applications are not ranked. Include the application first.")
    listed = con().execute(
        "SELECT id, excluded FROM applications WHERE pce_id = ? AND hidden = 0 ORDER BY excluded, rank, id",
        (row["pce_id"],)).fetchall()
    ids = [r["id"] for r in listed if not r["excluded"]]
    if app_id not in ids:
        raise ApiError("Application is not in the tracker")
    ids.remove(app_id)
    ids.insert(max(0, min(len(ids), target - 1)), app_id)
    renumber(row["pce_id"], ids + [r["id"] for r in listed if r["excluded"]])
    con().commit()
    return jsonify(ok=True)


@app.delete("/api/applications/<int:app_id>")
def application_hide(app_id):
    hide_app(get_app(app_id))
    con().commit()
    return jsonify(ok=True)


@app.post("/api/applications/<int:app_id>/restore")
def application_restore(app_id):
    row = get_app(app_id)
    top = con().execute("SELECT COALESCE(MAX(rank), 0) m FROM applications WHERE pce_id = ? AND hidden = 0",
                        (row["pce_id"],)).fetchone()["m"]
    con().execute("UPDATE applications SET hidden = 0, rank = ?, updated_at = ? WHERE id = ?", (top + 1, now(), app_id))
    log_event(app_id, "edit", "Restored to the tracker")
    renumber(row["pce_id"])
    timeline.list_tracked_applications(row["pce_id"])
    con().commit()
    return jsonify(ok=True)


@app.post("/api/applications/<int:app_id>/comments")
def application_comment(app_id):
    get_app(app_id)
    text = clean_text(body().get("text"), 500)
    if not text:
        raise ApiError("Comment cannot be empty")
    add_comment(app_id, text)
    con().commit()
    return jsonify(ok=True)


def apply_env_changes(row, changes):
    """Persist validated changes for one application-environment pairing (a row from ENV_SELECT),
    record the audit trail and carry the result over to its Project Timeline tasks."""
    env_id = row["id"]
    if changes.get("status") == "Fully Enforced" and row["status"] != "Fully Enforced" and "progress" not in changes:
        changes["progress"] = 100
    # A priority is stored once it is chosen here, even when it equals the application's, so it stops following it.
    changes = {k: v for k, v in changes.items() if v != row[k] or (k == "priority" and row["priority_inherited"])}
    if not changes:
        return
    cols = {"excluded": "excluded", "status": "status_id", "progress": "progress", "priority": "priority_id",
            "start_date": "start_date", "end_date": "end_date"}
    ids = {"status": lookup("statuses"), "priority": lookup("priorities")}
    values = [ids[k][v] if k in ids else v for k, v in changes.items()]
    con().execute(f"UPDATE application_environments SET {', '.join(cols[k] + ' = ?' for k in changes)} WHERE id = ?",
                  (*values, env_id))
    if "excluded" in changes:
        verb = "excluded from" if changes["excluded"] else "included in"
        log_event(row["app_id"], "environment", f"Environment {row['name']} {verb} tracking")
        if not changes["excluded"]:
            timeline.list_tracked_applications(get_app(row["app_id"])["pce_id"])
    if "status" in changes:
        log_event(row["app_id"], "status", f"{row['name']}: status changed {row['status']} → {changes['status']}")
    if "progress" in changes:
        log_event(row["app_id"], "progress", f"{row['name']}: progress updated {row['progress']}% → {changes['progress']}%")
    if "priority" in changes:
        log_event(row["app_id"], "priority", f"{row['name']}: priority set to {changes['priority']}")
    if "start_date" in changes or "end_date" in changes:
        log_event(row["app_id"], "edit", f"{row['name']}: dates updated")
    timeline.mirror_environment(env_id)
    if any(k in changes for k in ("start_date", "end_date", "excluded")):
        recalc_app_dates(row["app_id"])   # the application's dates follow its environments


@app.patch("/api/environments/<int:env_id>")
def environment_update(env_id):
    """Update one application-environment pairing (the id of an application_environments row)."""
    c = con()
    row = c.execute(ENV_SELECT + " WHERE ae.id = ?", (env_id,)).fetchone()
    if row is None:
        raise ApiError("Environment not found", 404)
    data = body()
    changes = {}
    if "excluded" in data:
        changes["excluded"] = 1 if data["excluded"] else 0
    if "status" in data:
        changes["status"] = clean_choice(data["status"], STATUSES, "Status")
    if "progress" in data:
        changes["progress"] = clean_progress(data["progress"])
    if "priority" in data:
        changes["priority"] = clean_choice(data["priority"], PRIORITIES, "Priority")
    for field, label in (("start_date", "Start date"), ("end_date", "End date")):
        if field in data:
            changes[field] = clean_date(data[field], label)
    start = changes.get("start_date", row["start_date"])
    end = changes.get("end_date", row["end_date"])
    if start and end and end < start:
        raise ApiError("End date cannot be before the start date")
    apply_env_changes(row, changes)
    if data.get("comment"):
        add_comment(row["app_id"], data["comment"], env_id)
    c.commit()
    return jsonify(ok=True)


# ---------------------------------------------------------------------------- people

def person_fields(data, partial=False):
    out = {}
    if "name" in data or not partial:
        out["name"] = clean_text(data.get("name"), 80)
        if not out["name"]:
            raise ApiError("Name is required")
    for field, limit in (("email", 120), ("title", 80)):
        if field in data:
            out[field] = clean_text(data[field], limit)
    return out


def set_roles(person_id, data, replace):
    role_id = {r["code"]: r["id"] for r in con().execute("SELECT id, code FROM roles")}
    for code in ROLE_CODES:
        key = "is_" + code
        if key not in data:
            continue
        if data[key]:
            con().execute("INSERT OR IGNORE INTO person_roles (person_id, role_id) VALUES (?,?)",
                          (person_id, role_id[code]))
        elif replace:
            con().execute("DELETE FROM person_roles WHERE person_id = ? AND role_id = ?", (person_id, role_id[code]))


@app.post("/api/people")
def people_create():
    data = body()
    fields = person_fields(data)
    c = con()
    existing = c.execute("SELECT id FROM people WHERE name = ?", (fields["name"],)).fetchone()
    if existing:
        # Adding the same person from another role dropdown just grants the extra role.
        person_id = existing["id"]
        set_roles(person_id, data, replace=False)
    else:
        cols = ", ".join(fields)
        person_id = c.execute(f"INSERT INTO people ({cols}) VALUES ({', '.join('?' * len(fields))})",
                              tuple(fields.values())).lastrowid
        set_roles(person_id, data, replace=True)
    c.commit()
    return jsonify(id=person_id)


@app.patch("/api/people/<int:person_id>")
def people_update(person_id):
    data = body()
    fields = person_fields(data, partial=True)
    c = con()
    if c.execute("SELECT 1 FROM people WHERE id = ?", (person_id,)).fetchone() is None:
        raise ApiError("Person not found", 404)
    if "name" in fields and c.execute("SELECT 1 FROM people WHERE name = ? AND id != ?",
                                      (fields["name"], person_id)).fetchone():
        raise ApiError("Someone with that name already exists")
    if fields:
        c.execute(f"UPDATE people SET {', '.join(k + ' = ?' for k in fields)} WHERE id = ?", (*fields.values(), person_id))
    set_roles(person_id, data, replace=True)
    c.commit()
    return jsonify(ok=True)


@app.delete("/api/people/<int:person_id>")
def people_delete(person_id):
    con().execute("DELETE FROM people WHERE id = ?", (person_id,))
    con().commit()
    return jsonify(ok=True)


# -------------------------------------------------------------------------- settings

@app.put("/api/settings")
def settings_update():
    data = body()
    c = con()
    if "user_name" in data:
        name = clean_text(data["user_name"], 80)
        if not name:
            raise ApiError("Display name is required")
        db.set_setting(c, "user_name", name)
    c.commit()
    return jsonify(ok=True)


def mask(value):
    value = str(value or "")
    return value[:8] + "…" if len(value) > 8 else value


@app.get("/api/pce")
def pce_list():
    cfg = pcelib.load_config()
    default = cfg.get("default_pce")
    return jsonify(pces=[
        {
            "name": name,
            "pce_fqdn": cfg[name].get("pce_fqdn", ""),
            "port": cfg[name].get("port", 443),
            "org": cfg[name].get("org", 1),
            "cert_path": cfg[name].get("cert_path", "") or "",
            "api_key_hint": mask(cfg[name].get("api_key")),
            "has_secret": bool(cfg[name].get("api_secret")),
            "is_default": name == default,
        }
        for name in pcelib.pce_names(cfg)
    ])


@app.put("/api/pce/<name>")
def pce_save(name):
    if not pcelib.NAME_RE.match(name) or name == "default_pce":
        raise ApiError("PCE name may only contain letters, numbers, dashes and underscores")
    data = body()
    cfg = pcelib.load_config()
    conf = dict(cfg.get(name) or {})
    fqdn = clean_text(data.get("pce_fqdn", conf.get("pce_fqdn")), 253)
    if not fqdn or "/" in fqdn or ":" in fqdn or " " in fqdn:
        raise ApiError("PCE FQDN must be a bare hostname, e.g. pce.example.com")
    try:
        port, org = int(data.get("port", conf.get("port", 8443))), int(data.get("org", conf.get("org", 1)))
    except (TypeError, ValueError):
        raise ApiError("Port and org must be numbers")
    if not 1 <= port <= 65535 or org < 1:
        raise ApiError("Port or org is out of range")
    conf.update(pce_fqdn=fqdn, port=port, org=org)
    # Credentials are write-only: a blank field keeps the stored value.
    for field in ("api_key", "api_secret"):
        if data.get(field):
            conf[field] = str(data[field]).strip()
    if not conf.get("api_key") or not conf.get("api_secret"):
        raise ApiError("API key and secret are required")
    if "cert_path" in data:
        cert = clean_text(data["cert_path"], 500)
        if cert and not os.path.isfile(cert):
            raise ApiError(f"Certificate file not found: {cert}")
        if cert:
            conf["cert_path"] = cert
        else:
            conf.pop("cert_path", None)
    cfg[name] = conf
    if data.get("make_default") or not cfg.get("default_pce"):
        cfg["default_pce"] = name
    pcelib.save_config(cfg)
    return jsonify(ok=True)


@app.delete("/api/pce/<name>")
def pce_delete(name):
    cfg = pcelib.load_config()
    if not isinstance(cfg.get(name), dict):
        raise ApiError("PCE not found", 404)
    if cfg.get("default_pce") == name:
        raise ApiError("Make another PCE the default before removing this one")
    del cfg[name]
    pcelib.save_config(cfg)
    return jsonify(ok=True)


@app.post("/api/pce/<name>/test")
def pce_test(name):
    cfg = pcelib.load_config()
    if not isinstance(cfg.get(name), dict):
        raise ApiError("PCE not found", 404)
    client = pcelib.PCE(name, cfg[name])
    client.get("/labels", {"key": "app", "max_results": 1}, timeout=15)
    return jsonify(ok=True, message=f"Connected to {client.fqdn} (org {client.org})")


# ---------------------------------------------------------------------------- export

def csv_safe(value):
    text = "" if value is None else str(value)
    # Stop spreadsheet apps from treating user text as a formula.
    return "'" + text if text[:1] in ("=", "+", "-", "@") else text


@app.get("/api/export.csv")
def export_csv():
    _cfg, name = active_pce_name()
    c = con()
    pid = pce_id_for(name)
    people = {r["id"]: r["name"] for r in c.execute("SELECT id, name FROM people")}
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["Priority Rank", "Application", "Environments", "Excluded Environments", "Priority", "Status",
                     "Progress %", "Start Date", "End Date", "Assigned PS", "Project Manager", "App Owner",
                     "PCE Enforcement", "Managed Workloads", "Enforced Workloads", "Latest Comment", "Excluded"])
    for a in c.execute(APP_SELECT + " WHERE a.pce_id = ? AND a.hidden = 0 ORDER BY a.rank, a.id", (pid,)).fetchall():
        envs = c.execute(ENV_SELECT + " WHERE ae.application_id = ? ORDER BY e.name", (a["id"],)).fetchall()
        managed = a["wl_full"] + a["wl_selective"] + a["wl_visibility"] + a["wl_idle"]
        writer.writerow([csv_safe(v) for v in (
            "" if a["excluded"] else a["rank"], a["name"],
            "; ".join(e["name"] for e in envs if not e["excluded"]),
            "; ".join(e["name"] for e in envs if e["excluded"]),
            a["priority"], a["status"], a["progress"], a["start_date"], a["end_date"],
            people.get(a["ps_id"], ""), people.get(a["pm_id"], ""), people.get(a["owner_id"], ""),
            derive_state(a), managed, a["wl_full"], a["comments"], "Yes" if a["excluded"] else "",
        )])
    filename = f"illumio-enforcement-tracker-{datetime.date.today().isoformat()}.csv"
    return Response(out.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


import timeline  # noqa: E402  (routes for the Project Timeline page; needs the helpers above)

timeline.register(sys.modules[__name__])

import reports  # noqa: E402  (Excel report for the Reports page)

reports.register(sys.modules[__name__])

# Bring Project Timeline tasks and Application Tracking into step once at start-up, for data
# written before the two were kept in sync.
with app.test_request_context():
    for _row in con().execute("SELECT id FROM pces").fetchall():
        timeline.reconcile(_row["id"])
    for _row in con().execute("SELECT id FROM applications").fetchall():
        recalc_app_dates(_row["id"])
    con().commit()


def listen_hosts():
    """Addresses to serve on: localhost plus the address of this machine's hostname."""
    hosts = ["127.0.0.1"]
    try:
        addr = socket.gethostbyname(socket.gethostname())
    except OSError:
        addr = ""
    if not addr or addr.startswith("127."):
        # Debian-style /etc/hosts maps the hostname to 127.0.1.1; use the primary interface instead.
        # Connecting a UDP socket only picks a route, it sends nothing.
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
                probe.connect(("192.0.2.1", 9))
                addr = probe.getsockname()[0]
        except OSError:
            addr = ""
    if addr and not addr.startswith("127."):
        hosts.append(addr)
    return hosts


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5055))
    servers = [make_server(host, port, app, threaded=True) for host in listen_hosts()]
    if len(servers) == 1:
        print(" * Hostname address not found, serving on localhost only", flush=True)
    for server in servers:
        print(f" * Running on http://{server.host}:{port}", flush=True)
    for server in servers[1:]:
        threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        servers[0].serve_forever()
    except KeyboardInterrupt:
        pass
