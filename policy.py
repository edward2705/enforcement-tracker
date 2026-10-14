"""Rulesets and blocked traffic per scope: read from the PCE on every sync, kept as a snapshot,
and summarised per application and environment for the reports."""
import json

import requests

import db
import pce as pcelib

TRAFFIC_DAYS = 30          # how far back blocked traffic is counted, unless changed under Settings
TRAFFIC_DAYS_RANGE = (1, 365)
TRAFFIC_MAX_RESULTS = 100000
DECISIONS = ("potentially_blocked", "blocked")
# Explorer's terms: a connection is one source, destination, port and protocol; its flows are the number
# of times it was seen (Explorer's "Flows" column, the PCE's num_connections).
TRAFFIC_COLS = ("potentially_blocked_flows", "potentially_blocked_connections", "blocked_flows", "blocked_connections")
# The same flows judged against the draft rules (Explorer's "Draft" view). Allowed: provisioning the draft would allow them.
DRAFT_COLS = tuple("draft_" + k for k in TRAFFIC_COLS) + ("draft_allowed_flows", "draft_allowed_connections")
# A scope's traffic in each direction: "in" where the scope is the destination, "out" where it is the
# source, "all" either way. A flow inside one scope is both inbound and outbound to it.
DIRECTIONS = ("all", "in", "out")
KEY_ORDER = {"app": 0, "env": 1, "loc": 2}
ALL = {"app": "All Applications", "env": "All Environments"}
# What a ruleset scope narrows to. "none": no labels at all, so the rules apply to every workload in the
# PCE (scopeless); "app": an application but no environment; "app_env": both; "other": no application
# (only environment or other labels).
SCOPE_TYPES = {"none": "Scopeless", "app": "Application only", "app_env": "Application + Environment", "other": "No application"}


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


def traffic_days(c):
    """The traffic period for the next sync, in days: the "traffic_days" setting, or the default."""
    try:
        days = int(db.get_setting(c, "traffic_days", TRAFFIC_DAYS))
    except ValueError:
        return TRAFFIC_DAYS
    return min(max(days, TRAFFIC_DAYS_RANGE[0]), TRAFFIC_DAYS_RANGE[1])


def read_scope(entries, labels, groups):
    """One ruleset scope -> (text as the PCE shows it, app label hrefs or None, env label hrefs or None,
    scope type - see SCOPE_TYPES). None means the scope does not narrow that dimension to particular labels."""
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
    present = {k for _o, k, _n in parts}   # label types the scope names, before "All ..." fills the gaps
    for key, text in ALL.items():
        if not any(k == key for _o, k, _n in parts):
            parts.append((KEY_ORDER[key], key, text))
    kind = ("none" if not entries else "other" if "app" not in present
            else "app_env" if "env" in present else "app")
    return " | ".join(name for _o, _k, name in sorted(parts)), named.get("app"), named.get("env"), kind


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
            text, apps, envs, kind = read_scope(entries, labels, groups)
            app_ids = sorted({app_id[h] for h in apps or () if h in app_id}) or [None]
            env_ids = sorted({env_id[h] for h in envs or () if h in env_id}) or [None]
            for aid in app_ids:
                for eid in env_ids if aid else [None]:
                    c.execute("INSERT INTO rule_set_scopes (rule_set_id, scope, application_id, environment_id, scope_type) "
                              "VALUES (?,?,?,?,?)", (rid, text, aid, eid, kind))


def draft_bucket(decision):
    """potentially_blocked, blocked or allowed for a draft_policy_decision; the PCE adds qualifiers
    such as "blocked_by_boundary" or "allowed_across_boundary"."""
    for bucket in ("potentially_blocked", "blocked", "allowed"):
        if (decision or "").startswith(bucket):
            return bucket
    return None


def sync_traffic(client, c, pid):
    """Count blocked and potentially blocked connections and their flows for the whole PCE, per
    application and per application and environment, inbound and outbound. Each is counted by the
    decision the VENs reported under the provisioned rules, and by the PCE's decision under the draft
    rules. Returns (truncated, draft_error)."""
    flows, draft_error = client.traffic_flows(traffic_days(c), DECISIONS, TRAFFIC_MAX_RESULTS, draft=True)
    app_id = {r["label_href"]: r["id"] for r in c.execute("SELECT id, label_href FROM applications WHERE pce_id = ?", (pid,))}
    env_id = {r["label_href"]: r["id"] for r in c.execute("SELECT id, label_href FROM environments WHERE pce_id = ?", (pid,))}

    def end(side):
        wl = side.get("workload") or {}
        lab = {l.get("key"): l.get("href") for l in wl.get("labels") or []}
        return wl.get("href") or side.get("ip") or "", app_id.get(lab.get("app")), env_id.get(lab.get("env"))

    # The PCE returns several records for one connection (from the source and the destination VEN,
    # and split by time range and boundary decision). Explorer shows one row per connection with the
    # flows of all its records added up, so the same is done here.
    unique = {}
    for f in flows:
        if f.get("policy_decision") not in DECISIONS:
            continue
        src, dst, svc = end(f.get("src") or {}), end(f.get("dst") or {}), f.get("service") or {}
        key = (src[0], dst[0], svc.get("port"), svc.get("proto"), f["policy_decision"])
        entry = unique.setdefault(key, {"src": src, "dst": dst, "flows": 0, "draft": None, "biggest": -1})
        count = f.get("num_connections") or 0
        entry["flows"] += count
        if count > entry["biggest"]:
            # The draft decision of the busiest record; one the PCE cannot judge against the draft
            # ("unknown") keeps the decision it was reported with.
            entry["biggest"] = count
            entry["draft"] = draft_bucket(f.get("draft_policy_decision")) or (f["policy_decision"] if "draft_policy_decision" in f else None)

    def scopes_of(side):
        """The application and the application-and-environment scope of one end of a flow."""
        _href, aid, eid = side
        if not aid:
            return set()
        return {(aid, None), (aid, eid)} if eid else {(aid, None)}

    totals = {}
    blank = dict.fromkeys(TRAFFIC_COLS + DRAFT_COLS, 0)
    for (_s, _d, _port, _proto, decision), entry in unique.items():
        src, dst, draft, seen = entry["src"], entry["dst"], entry["draft"], entry["flows"]
        inbound, outbound = scopes_of(dst), scopes_of(src)
        keys = ({(None, None, "all")} | {(*sc, "all") for sc in inbound | outbound}
                | {(*sc, "in") for sc in inbound} | {(*sc, "out") for sc in outbound})
        for key in keys:
            row = totals.setdefault(key, dict(blank))
            row[f"{decision}_connections"] += 1
            row[f"{decision}_flows"] += seen
            if draft:
                row[f"draft_{draft}_connections"] += 1
                row[f"draft_{draft}_flows"] += seen
    totals.setdefault((None, None, "all"), dict(blank))

    # Per port, for the Selective Enforcement port suggestions in the Word report.
    ports = {}
    for (src_id, dst_id, port, proto, decision), entry in unique.items():
        row = ports.setdefault((port, proto, decision), {"connections": 0, "flows": 0, "sources": set(), "destinations": set()})
        row["connections"] += 1
        row["flows"] += entry["flows"]
        row["sources"].add(src_id)
        row["destinations"].add(dst_id)
    c.execute("DELETE FROM traffic_ports WHERE pce_id = ?", (pid,))
    for (port, proto, decision), row in ports.items():
        if proto is None:
            continue
        c.execute("INSERT INTO traffic_ports (pce_id, port, proto, decision, connections, flows, sources, destinations) "
                  "VALUES (?,?,?,?,?,?,?,?)", (pid, port or None, proto, decision, row["connections"], row["flows"],
                                               len(row["sources"]), len(row["destinations"])))
    cols = TRAFFIC_COLS + DRAFT_COLS
    c.execute("DELETE FROM traffic_summaries WHERE pce_id = ?", (pid,))
    for (aid, eid, direction), row in totals.items():
        c.execute(f"INSERT INTO traffic_summaries (pce_id, application_id, environment_id, direction, {', '.join(cols)}) "
                  f"VALUES (?,?,?,?{',?' * len(cols)})", (pid, aid, eid, direction, *[row[k] for k in cols]))
    return len(flows) >= TRAFFIC_MAX_RESULTS, draft_error


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
    """Refresh the snapshots. Traffic is cleared first, every time: the stored counts are only ever the
    ones this sync read, so a traffic query that fails leaves no traffic rather than old numbers. Any
    other part the PCE refuses or cannot answer keeps its previous snapshot. Failures are reported as
    warnings so the rest of the sync still goes through. Each part asks the PCE first and commits on its
    own, so the database is not held locked while the traffic query runs."""
    info = json.loads(db.get_setting(c, f"policy_snapshot:{pid}", "{}"))
    days = traffic_days(c)   # read once: the period recorded with the snapshot is the one that was queried
    warnings = []
    c.execute("DELETE FROM traffic_summaries WHERE pce_id = ?", (pid,))
    c.execute("DELETE FROM traffic_ports WHERE pce_id = ?", (pid,))
    info["traffic"] = {"cleared_at": ts}
    db.set_setting(c, f"policy_snapshot:{pid}", json.dumps(info))
    c.commit()
    parts = (("rules", "rulesets", sync_rules), ("traffic", "blocked traffic", sync_traffic),
             ("labels", "labels", lambda *args: sync_labels(*args, workloads)), ("pce", "PCE details", sync_pce))
    for part, label, run in parts:
        try:
            result = run(client, c, pid)
            info[part] = {"synced_at": ts, "error": ""}
            if part == "traffic":
                truncated, draft_error = result
                info[part].update(days=days, truncated=bool(truncated), draft=not draft_error, draft_error=draft_error,
                                  ports=True)   # per-port counts were stored (older syncs did not)
                if draft_error:
                    warnings.append(f"Could not read draft policy decisions: {draft_error}. Counts by provisioned rules are up to date.")
            elif part == "pce":
                info[part].update(result)
        except (pcelib.PCEError, requests.RequestException, ValueError, KeyError) as err:
            c.rollback()
            message = str(err) if isinstance(err, pcelib.PCEError) else f"PCE request failed ({type(err).__name__})"
            info[part] = {**info.get(part, {}), "error": message}
            if part == "traffic":
                message += "; traffic was cleared and shows no counts until the next sync reads it"
            warnings.append(f"Could not read {label}: {message}")
        db.set_setting(c, f"policy_snapshot:{pid}", json.dumps(info))
        c.commit()
    return warnings


def summary(c, pid, apps):
    """What the reports show: one row per tracked application and environment (as on the Dashboard),
    then the ruleset scopes that name no tracked application, and PCE-wide totals."""
    by_scope, names, kinds, app_only = {}, {}, {}, {}
    for r in c.execute("SELECT rs.*, s.scope, s.application_id, s.environment_id, s.scope_type FROM rule_set_scopes s "
                       "JOIN rule_sets rs ON rs.id = s.rule_set_id WHERE rs.pce_id = ? ORDER BY rs.name COLLATE NOCASE", (pid,)):
        by_scope.setdefault((r["application_id"], r["environment_id"]), {})[r["id"]] = r
        names.setdefault(r["id"], set()).add(r["scope"])
        kinds[r["scope"]] = r["scope_type"]
        if r["application_id"] and r["scope_type"] == "app":
            app_only.setdefault(r["application_id"], {})[r["id"]] = r
    traffic = {(r["application_id"], r["environment_id"], r["direction"]): r for r in
               c.execute("SELECT * FROM traffic_summaries WHERE pce_id = ?", (pid,))}

    def rules_of(sets):
        sets = list(sets.values())
        return {"rulesets": len(sets), "ruleset_names": ", ".join(r["name"] for r in sets),
                "rules_draft": sum(r["rules_draft"] for r in sets), "rules_active": sum(r["rules_active"] for r in sets),
                "rules_pending": sum(r["rules_pending"] for r in sets)}

    def counts(key):
        row = traffic.get(key)
        return {k: row[k] if row else 0 for k in TRAFFIC_COLS + DRAFT_COLS}

    def traffic_of(scope):
        """The counts either way at the top level, and inbound and outbound under "in" and "out"."""
        return {**counts((*scope, "all")), "in": counts((*scope, "in")), "out": counts((*scope, "out"))}

    rows = []
    for a in apps:
        if a["excluded"]:
            continue
        envs = [e for e in a["environments"] if not e["excluded"]]
        if envs and a["id"] in app_only:
            # Rulesets scoped to the application alone (no environment): a row of their own, so the
            # report can show them apart. Their rules also count in each environment's row below.
            rows.append({"application": a["name"], "environment": "", "tracked": True, "scope_type": "app",
                         "scope": f"{a['name']} | {ALL['env']}", "status": a["status"], "pce_state": a["pce_state"],
                         **rules_of(app_only[a["id"]]), **traffic_of((a["id"], None))})
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
                         "scope_type": "app_env" if e or a["id"] not in app_only else "app",
                         "scope": f"{a['name']} | {e['name'] if e else ALL['env']}",
                         "status": (e or a)["status"], "pce_state": (e or a)["pce_state"],
                         **rules_of(sets), **traffic_of((a["id"], e["environment_id"] if e else None))})
    other = {}
    for rid, r in by_scope.get((None, None), {}).items():
        for text in names[rid]:
            other.setdefault(text, {})[rid] = r
    # Scopeless rulesets first: their rules apply to every workload in the PCE.
    for text in sorted(other, key=lambda t: (kinds.get(t) != "none", t.lower())):
        rows.append({"application": "", "environment": "", "tracked": False, "scope": text, "status": "", "pce_state": "",
                     "scope_type": kinds.get(text) or "",   # "": synced before scope types were recorded
                     **rules_of(other[text]), **dict.fromkeys(TRAFFIC_COLS + DRAFT_COLS), "in": None, "out": None})
    every = c.execute("SELECT COUNT(*) AS rulesets, COALESCE(SUM(rules_draft), 0) AS rules_draft, COALESCE(SUM(rules_active), 0) "
                      "AS rules_active, COALESCE(SUM(rules_pending), 0) AS rules_pending FROM rule_sets WHERE pce_id = ?", (pid,)).fetchone()
    flagged = {kind: sorted({r["name"] for sets in by_scope.values() for r in sets.values() if r["scope_type"] == kind}, key=str.lower)
               for kind in ("none", "app")}
    return {"rows": rows, "totals": {**dict(every), **counts((None, None, "all"))},
            "scopeless": flagged["none"], "app_only": flagged["app"],
            "info": json.loads(db.get_setting(c, f"policy_snapshot:{pid}", "{}"))}
