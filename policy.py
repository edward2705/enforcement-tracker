"""Rulesets and blocked traffic per scope: read from the PCE on every sync, kept as a snapshot,
and summarised per application and environment for the reports."""
import json

import requests

import db
import pce as pcelib

TRAFFIC_DAYS = 30          # how far back blocked traffic is counted
TRAFFIC_MAX_RESULTS = 100000
DECISIONS = ("potentially_blocked", "blocked")
TRAFFIC_COLS = ("potentially_blocked_flows", "potentially_blocked_connections", "blocked_flows", "blocked_connections")
KEY_ORDER = {"app": 0, "env": 1, "loc": 2}
ALL = {"app": "All Applications", "env": "All Environments"}


def object_id(href):
    """A policy object's href without its draft/active part, so both versions of it compare equal."""
    return (href or "").replace("/sec_policy/draft/", "/sec_policy/").replace("/sec_policy/active/", "/sec_policy/")


def group_labels(groups):
    """label group id -> (key, name, hrefs of every label in it, sub-groups included)."""
    by_id = {object_id(g["href"]): g for g in groups}

    def members(gid, seen):
        g = by_id.get(gid)
        if g is None or gid in seen:
            return set()
        seen.add(gid)
        out = {l["href"] for l in g.get("labels") or []}
        for sub in g.get("sub_groups") or []:
            out |= members(object_id(sub["href"]), seen)
        return out

    return {gid: (g.get("key"), g.get("name") or "", members(gid, set())) for gid, g in by_id.items()}


def read_scope(entries, labels, groups):
    """One ruleset scope -> (text as the PCE shows it, app label hrefs or None, env label hrefs or None).
    None means the scope does not narrow that dimension to particular labels."""
    named = {}
    parts = []
    for entry in entries:
        if entry.get("label"):
            key, name = labels.get(entry["label"]["href"], ("", "Deleted label"))
            hrefs = {entry["label"]["href"]}
        else:
            key, name, hrefs = groups.get(object_id((entry.get("label_group") or {}).get("href")), ("", "Deleted label group", set()))
            name = f"{name} (label group)"
        if entry.get("exclusion"):
            name = f"All except {name}"
        elif key in ("app", "env"):
            named[key] = named.get(key, set()) | hrefs
        parts.append((KEY_ORDER.get(key, 3), key, name))
    for key, text in ALL.items():
        if not any(k == key for _o, k, _n in parts):
            parts.append((KEY_ORDER[key], key, text))
    return " | ".join(name for _o, _k, name in sorted(parts)), named.get("app"), named.get("env")


def sync_rules(client, c, pid):
    labels = {l["href"]: (l.get("key"), l.get("value")) for l in client.collection("/labels")}
    groups = group_labels(client.collection("/sec_policy/draft/label_groups") + client.collection("/sec_policy/active/label_groups"))
    draft = {object_id(r["href"]): r for r in client.collection("/sec_policy/draft/rule_sets")}
    active = {object_id(r["href"]): r for r in client.collection("/sec_policy/active/rule_sets")}
    app_id = {r["label_href"]: r["id"] for r in c.execute("SELECT id, label_href FROM applications WHERE pce_id = ?", (pid,))}
    env_id = {r["label_href"]: r["id"] for r in c.execute("SELECT id, label_href FROM environments WHERE pce_id = ?", (pid,))}
    rules = lambda rs: (rs.get("rules") or []) + (rs.get("deny_rules") or [])  # noqa: E731

    c.execute("DELETE FROM rule_sets WHERE pce_id = ?", (pid,))
    for href in sorted(set(draft) | set(active)):
        d, a = draft.get(href), active.get(href)
        removed = d is None or d.get("update_type") == "delete"   # provisioning will remove the ruleset
        drafted = [] if removed else [r for r in rules(d) if r.get("update_type") != "delete"]
        pending = len(rules(a)) if removed and a else sum(1 for r in rules(d or {}) if r.get("update_type"))
        src = d or a
        rid = c.execute(
            "INSERT INTO rule_sets (pce_id, href, name, enabled, rules_draft, rules_active, rules_pending) VALUES (?,?,?,?,?,?,?)",
            (pid, href, src.get("name") or "", 1 if src.get("enabled", True) else 0, len(drafted), len(rules(a)) if a else 0, pending),
        ).lastrowid
        for entries in src.get("scopes") or [[]]:
            text, apps, envs = read_scope(entries, labels, groups)
            app_ids = sorted({app_id[h] for h in apps or () if h in app_id}) or [None]
            env_ids = sorted({env_id[h] for h in envs or () if h in env_id}) or [None]
            for aid in app_ids:
                for eid in env_ids if aid else [None]:
                    c.execute("INSERT INTO rule_set_scopes (rule_set_id, scope, application_id, environment_id) VALUES (?,?,?,?)",
                              (rid, text, aid, eid))


def sync_traffic(client, c, pid):
    """Count blocked and potentially blocked flows for the whole PCE, per application and per
    application and environment. A flow counts for the scope of its source and of its destination."""
    flows = client.traffic_flows(TRAFFIC_DAYS, DECISIONS, TRAFFIC_MAX_RESULTS)
    app_id = {r["label_href"]: r["id"] for r in c.execute("SELECT id, label_href FROM applications WHERE pce_id = ?", (pid,))}
    env_id = {r["label_href"]: r["id"] for r in c.execute("SELECT id, label_href FROM environments WHERE pce_id = ?", (pid,))}

    def end(side):
        wl = side.get("workload") or {}
        lab = {l.get("key"): l.get("href") for l in wl.get("labels") or []}
        return wl.get("href") or side.get("ip") or "", app_id.get(lab.get("app")), env_id.get(lab.get("env"))

    # Both ends of a flow can report it; keep one copy with the higher connection count.
    unique = {}
    for f in flows:
        if f.get("policy_decision") not in DECISIONS:
            continue
        src, dst, svc = end(f.get("src") or {}), end(f.get("dst") or {}), f.get("service") or {}
        key = (src[0], dst[0], svc.get("port"), svc.get("proto"), f["policy_decision"])
        seen = unique.get(key)
        if seen is None or (f.get("num_connections") or 0) > seen[2]:
            unique[key] = (src, dst, f.get("num_connections") or 0)

    totals = {}
    for (_s, _d, _port, _proto, decision), (src, dst, connections) in unique.items():
        scopes = {(None, None)}
        for _href, aid, eid in (src, dst):
            if aid:
                scopes.add((aid, None))
                if eid:
                    scopes.add((aid, eid))
        for scope in scopes:
            row = totals.setdefault(scope, dict.fromkeys(TRAFFIC_COLS, 0))
            row[f"{decision}_flows"] += 1
            row[f"{decision}_connections"] += connections
    totals.setdefault((None, None), dict.fromkeys(TRAFFIC_COLS, 0))
    c.execute("DELETE FROM traffic_summaries WHERE pce_id = ?", (pid,))
    for (aid, eid), row in totals.items():
        c.execute(f"INSERT INTO traffic_summaries (pce_id, application_id, environment_id, {', '.join(TRAFFIC_COLS)}) "
                  "VALUES (?,?,?,?,?,?,?)", (pid, aid, eid, *[row[k] for k in TRAFFIC_COLS]))
    return len(flows) >= TRAFFIC_MAX_RESULTS


def sync_labels(client, c, pid, workloads):
    """Every label with the number of workloads carrying it and what else in the PCE uses it."""
    on_workloads = {}
    for wl in workloads:
        if not wl.get("deleted"):
            for l in wl.get("labels") or []:
                on_workloads[l.get("href")] = on_workloads.get(l.get("href"), 0) + 1
    labels = client.collection("/labels", {"usage": "true"})
    c.execute("DELETE FROM labels WHERE pce_id = ?", (pid,))
    for l in labels:
        if l.get("deleted"):
            continue
        used = sorted(k for k, v in (l.get("usage") or {}).items() if v)
        if on_workloads.get(l["href"]) and "workload" not in used:
            used.append("workload")
        c.execute("INSERT OR REPLACE INTO labels (pce_id, href, key, value, workload_count, used_by) VALUES (?,?,?,?,?,?)",
                  (pid, l["href"], l.get("key") or "", l.get("value") or "", on_workloads.get(l["href"], 0), ",".join(used)))


def sync_pce(client, _c, _pid):
    """Version, cluster health and the names of the label types. Health needs more rights than the
    rest, so it is left out when the PCE refuses it."""
    version = client.root_get("/product_version").json()
    out = {"version": version.get("long_display") or version.get("version") or "",
           "label_types": {d.get("key"): d.get("display_name") or d.get("key") for d in client.get("/label_dimensions").json()}}
    try:
        health = client.root_get("/health").json()
        cluster = health[0] if isinstance(health, list) and health else {}
        out.update(status=cluster.get("status") or "", cluster_type=cluster.get("type") or "", nodes=len(cluster.get("nodes") or []))
    except (pcelib.PCEError, requests.RequestException, ValueError):
        pass
    return out


def sync(client, c, pid, ts, workloads):
    """Refresh both snapshots. A part the PCE refuses or cannot answer keeps its previous snapshot and
    is reported as a warning, so the rest of the sync still goes through. Each part asks the PCE first
    and commits on its own, so the database is not held locked while the traffic query runs."""
    info = json.loads(db.get_setting(c, f"policy_snapshot:{pid}", "{}"))
    warnings = []
    parts = (("rules", "rulesets", sync_rules), ("traffic", "blocked traffic", sync_traffic),
             ("labels", "labels", lambda *args: sync_labels(*args, workloads)), ("pce", "PCE details", sync_pce))
    for part, label, run in parts:
        try:
            result = run(client, c, pid)
            info[part] = {"synced_at": ts, "error": ""}
            if part == "traffic":
                info[part].update(days=TRAFFIC_DAYS, truncated=bool(result))
            elif part == "pce":
                info[part].update(result)
        except (pcelib.PCEError, requests.RequestException, ValueError, KeyError) as err:
            c.rollback()
            message = str(err) if isinstance(err, pcelib.PCEError) else f"PCE request failed ({type(err).__name__})"
            info[part] = {**info.get(part, {}), "error": message}
            warnings.append(f"Could not read {label}: {message}")
        db.set_setting(c, f"policy_snapshot:{pid}", json.dumps(info))
        c.commit()
    return warnings


def summary(c, pid, apps):
    """What the reports show: one row per tracked application and environment (as on the Dashboard),
    then the ruleset scopes that name no tracked application, and PCE-wide totals."""
    by_scope, names = {}, {}
    for r in c.execute("SELECT rs.*, s.scope, s.application_id, s.environment_id FROM rule_set_scopes s "
                       "JOIN rule_sets rs ON rs.id = s.rule_set_id WHERE rs.pce_id = ? ORDER BY rs.name COLLATE NOCASE", (pid,)):
        by_scope.setdefault((r["application_id"], r["environment_id"]), {})[r["id"]] = r
        names.setdefault(r["id"], set()).add(r["scope"])
    traffic = {(r["application_id"], r["environment_id"]): r for r in
               c.execute("SELECT * FROM traffic_summaries WHERE pce_id = ?", (pid,))}

    def rules_of(sets):
        sets = list(sets.values())
        return {"rulesets": len(sets), "ruleset_names": ", ".join(r["name"] for r in sets),
                "rules_draft": sum(r["rules_draft"] for r in sets), "rules_active": sum(r["rules_active"] for r in sets),
                "rules_pending": sum(r["rules_pending"] for r in sets)}

    def traffic_of(key):
        row = traffic.get(key)
        return {k: row[k] if row else 0 for k in TRAFFIC_COLS}

    rows = []
    for a in apps:
        if a["excluded"]:
            continue
        envs = [e for e in a["environments"] if not e["excluded"]]
        for e in envs or [None]:
            # Rulesets scoped to this application: for this environment, or for all of its environments.
            sets = dict(by_scope.get((a["id"], None), {}))
            if e:
                sets.update(by_scope.get((a["id"], e["environment_id"]), {}))
            else:
                for (aid, _eid), more in by_scope.items():
                    if aid == a["id"]:
                        sets.update(more)
            rows.append({"application": a["name"], "environment": e["name"] if e else "", "tracked": True,
                         "scope": f"{a['name']} | {e['name'] if e else ALL['env']}",
                         "status": (e or a)["status"], "pce_state": (e or a)["pce_state"],
                         **rules_of(sets), **traffic_of((a["id"], e["environment_id"] if e else None))})
    other = {}
    for rid, r in by_scope.get((None, None), {}).items():
        for text in names[rid]:
            other.setdefault(text, {})[rid] = r
    for text in sorted(other):
        rows.append({"application": "", "environment": "", "tracked": False, "scope": text, "status": "", "pce_state": "",
                     **rules_of(other[text]), **dict.fromkeys(TRAFFIC_COLS)})
    every = c.execute("SELECT COUNT(*) AS rulesets, COALESCE(SUM(rules_draft), 0) AS rules_draft, COALESCE(SUM(rules_active), 0) "
                      "AS rules_active, COALESCE(SUM(rules_pending), 0) AS rules_pending FROM rule_sets WHERE pce_id = ?", (pid,)).fetchone()
    return {"rows": rows, "totals": {**dict(every), **traffic_of((None, None))},
            "info": json.loads(db.get_setting(c, f"policy_snapshot:{pid}", "{}"))}
