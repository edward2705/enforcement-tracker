"""Illumio Enforcement Summary report: the data behind the Reports page, as an Excel workbook
and as a Word document.

Sheets: Dashboard (KPIs, charts, task table with a weekly Gantt), Application Tracker,
Policy & Traffic (rules and blocked traffic per scope), People, Environments and Project Timeline.
"""
import datetime
import io
import json
import os

import docx
import matplotlib
import xlsxwriter
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from docx.enum.section import WD_ORIENT
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor
from flask import Blueprint, Response, jsonify, request

import policy
import timeline
import vault

bp = Blueprint("reports", __name__)
core = None

NAVY, ORANGE, INK, MUTED, LINE = "#0C1728", "#F26B21", "#111827", "#6B7280", "#D9DEE7"
STATUS_COLOR = {"Fully Enforced": "#17994F", "Selective Enforcement": "#2A78D6", "In Progress": "#E39B00", "Not Started": "#D03B3B"}
PRIORITY_COLOR = {"Critical": "#D03B3B", "High": "#E8703A", "Medium": "#D99A00", "Low": "#2A78D6"}
PRIORITY_TINT = {"Critical": "#FBE6E6", "High": "#FDEBE1", "Medium": "#FBF1D3", "Low": "#E4EFFC"}
PHASE_BANDS = ["#DDF3E3", "#DCE9FB", "#FDF1D0", "#EDE4FA", "#FCE3D6"]


def register(app_module):
    global core
    core = app_module
    core.app.register_blueprint(bp)


def day(iso):
    return datetime.date.fromisoformat(iso) if iso else None


def tracked_apps(c, pid):
    apps = [core.with_state(r) for r in c.execute(
        core.APP_SELECT + " WHERE a.pce_id = ? AND a.hidden = 0 ORDER BY a.excluded, a.rank, a.id", (pid,))]
    for a in apps:
        a["environments"] = [core.with_state(e) for e in c.execute(
            core.ENV_SELECT + " WHERE ae.application_id = ? ORDER BY e.name COLLATE NOCASE", (a["id"],))]
    return apps


def collect(project_ids):
    """Everything the report shows, already filtered to the chosen projects."""
    c = core.con()
    _cfg, pce_name = core.active_pce_name()
    pid = core.pce_id_for(pce_name)
    people = {p["id"]: p for p in core.people_list()}
    apps = tracked_apps(c, pid)
    tl = timeline.timeline().get_json()
    projects = [p for p in tl["projects"] if not project_ids or p["id"] in project_ids]
    last = c.execute("SELECT completed_at FROM sync_runs WHERE pce_id = ? ORDER BY id DESC LIMIT 1", (pid,)).fetchone()
    # The PCE, VEN, workload and label sections of the Word report describe the whole PCE as of the last sync.
    workloads = [dict(r, hostname=vault.reveal(r["hostname"], "workloads.hostname")) for r in c.execute(
        "SELECT w.hostname, a.name AS application, e.name AS environment, w.managed, w.online, w.enforcement_mode, w.ven_status, "
        "w.ven_version, w.ven_type, w.os, w.os_detail, w.online, w.role, w.location, w.app_label, w.env_label, w.label_keys FROM workloads w "
        "LEFT JOIN applications a ON a.id = w.application_id LEFT JOIN environments e ON e.id = w.environment_id "
        "WHERE w.pce_id = ?", (pid,))]
    labels = [dict(r) for r in c.execute(
        "SELECT key, value, workload_count, used_by FROM labels WHERE pce_id = ? ORDER BY key COLLATE NOCASE, value COLLATE NOCASE", (pid,))]
    ports = {(r["port"], r["proto"]): dict(r) for r in c.execute(
        "SELECT port, proto, connections, flows, sources, destinations FROM traffic_ports "
        "WHERE pce_id = ? AND decision = 'potentially_blocked'", (pid,))}
    blocked = {(r["port"], r["proto"]): r["connections"] for r in c.execute(
        "SELECT port, proto, connections FROM traffic_ports WHERE pce_id = ? AND decision = 'blocked'", (pid,))}
    return {"apps": apps, "people": people, "projects": projects, "tl": tl, "pce": pce_name, "ports": ports, "ports_blocked": blocked,
            "last_sync": last["completed_at"] if last else None, "policy": policy.summary(c, pid, apps),
            "workloads": workloads, "labels": labels, "conf": _cfg.get(pce_name) or {}, "consultant": load_consultant(pid)}


def task_rows(tl, projects):
    """Flatten projects > phases > tasks, rolling dates, % complete and status up from the leaf tasks."""
    kids = {}
    for t in tl["tasks"]:
        kids.setdefault((t["project_id"], t["parent_id"] or 0), []).append(t)
    names = {t["id"]: t for t in tl["tasks"]}

    def span(start, end):
        return (day(end) - day(start)).days + 1 if start and end else None

    def roll(t):
        children = kids.get((t["project_id"], t["id"]), [])
        if not children:
            w = span(t["start_date"], t["end_date"]) or 1
            return {"start": t["start_date"], "end": t["end_date"], "w": w, "done": w * t["progress"],
                    "count": {t["status"]: 1}}
        out = {"start": None, "end": None, "w": 0, "done": 0, "count": {}}
        for child in children:
            merge(out, roll(child))
        return out

    def merge(into, r):
        if r["start"] and (not into["start"] or r["start"] < into["start"]):
            into["start"] = r["start"]
        if r["end"] and (not into["end"] or r["end"] > into["end"]):
            into["end"] = r["end"]
        into["w"] += r["w"]
        into["done"] += r["done"]
        for k, v in r["count"].items():
            into["count"][k] = into["count"].get(k, 0) + v

    def status_of(count):
        present = [s for s, n in count.items() if n]
        return present[0] if len(present) == 1 else "In Progress" if present else "Not Started"

    def label(t):
        return f"{t['application']}:{t['environment']}" if t["application"] and t["environment"] else t["name"]

    rows = []

    def visit(t, level, project, band):
        children = kids.get((t["project_id"], t["id"]), [])
        r = roll(t)
        rows.append({
            "kind": "summary" if children else "task", "level": level, "project": project["name"], "task": t,
            "name": t["name"] if children or level == 1 else label(t), "band": band,
            "start": r["start"], "end": r["end"], "duration": span(r["start"], r["end"]),
            "progress": round(r["done"] / r["w"]) if r["w"] else 0, "status": status_of(r["count"]), "count": r["count"],
        })
        for child in children:
            visit(child, level + 1, project, band)

    for project in projects:
        top = kids.get((project["id"], 0), [])
        total = {"start": None, "end": None, "w": 0, "done": 0, "count": {}}
        for t in top:
            merge(total, roll(t))
        rows.append({"kind": "project", "level": 0, "project": project["name"], "task": None, "name": project["name"], "band": None,
                     "start": total["start"], "end": total["end"], "duration": span(total["start"], total["end"]),
                     "progress": round(total["done"] / total["w"]) if total["w"] else 0,
                     "status": status_of(total["count"]), "count": total["count"]})
        for i, t in enumerate(top):
            visit(t, 1, project, PHASE_BANDS[i % len(PHASE_BANDS)])
    preds = {}
    for d in tl["dependencies"]:
        if d["predecessor_id"] in names:
            preds.setdefault(d["successor_id"], []).append(label(names[d["predecessor_id"]]))
    return rows, preds


class Book:
    """Thin wrapper that caches cell formats by their properties."""

    def __init__(self, stream):
        self.wb = xlsxwriter.Workbook(stream, {"in_memory": True})
        self._formats = {}

    def fmt(self, **props):
        base = {"font_name": "Calibri", "font_size": 10, "valign": "vcenter", "font_color": INK}
        base.update(props)
        key = tuple(sorted(base.items()))
        if key not in self._formats:
            self._formats[key] = self.wb.add_format(base)
        return self._formats[key]


def person_name(people, person_id):
    return people[person_id]["name"] if person_id in people else ""


# Dashboard table columns (0-based): one row per application and environment.
D_NUM, D_APP, D_ENV, D_PRI, D_PS, D_PM, D_OWNER, D_START, D_END, D_DUR, D_PCT, D_BAR, D_STATUS, D_NOTE = range(14)
TRACKER = "'Application Tracker'"
T_STATUS, T_PRIORITY, T_TYPE = "$D:$D", "$E:$E", "$Q:$Q"   # columns on the Application Tracker sheet
APP_STATUSES = ["Fully Enforced", "Selective Enforcement", "In Progress", "Not Started"]
PRIORITIES = ["Critical", "High", "Medium", "Low"]
STATUS_TINT = {"Fully Enforced": "#DDF3E4", "Selective Enforcement": "#DDE9FC", "In Progress": "#FDF1CF", "Not Started": "#FBDFDF"}
STATUS_TEXT = {"Fully Enforced": "#146C36", "Selective Enforcement": "#1B4DAA", "In Progress": "#8A5A00", "Not Started": "#B02A2A"}
BAR_BLOCKS = 14  # width of the in-cell progress bar, in block characters


def write_dashboard(book, data, rows):
    """Dashboard sheet: banner, six tiles, three charts and one row per application and environment.

    It is formula-driven. Tiles and the status/priority charts count the Application Tracker sheet;
    the Completion by Environment chart, the durations and the progress bars follow this sheet's own
    table; colours are conditional formatting. Cached results are stored for viewers that do not
    recalculate."""
    wb = book.wb
    ws = wb.add_worksheet("Dashboard")
    ws.hide_gridlines(2)
    ws.set_tab_color(ORANGE)
    ws.set_zoom(95)
    widths = [5, 28, 16, 11, 18, 18, 18, 12, 12, 10, 11, 17, 21, 46]
    for i, w in enumerate(widths):
        ws.set_column(i, i, w)
    LAST = len(widths) - 1
    active = [a for a in data["apps"] if not a["excluded"]]
    total = len(active)
    count = lambda s: sum(1 for a in active if a["status"] == s)  # noqa: E731

    # ---- banner
    ws.set_row(0, 26, book.fmt(bg_color=NAVY))
    ws.set_row(1, 22, book.fmt(bg_color=NAVY))
    ws.set_row(2, 20, book.fmt(bg_color=NAVY))
    ws.merge_range(0, 0, 2, 1, "illumio", book.fmt(bg_color=NAVY, font_color=ORANGE, bold=True, font_size=26, align="center"))
    ws.merge_range(0, 2, 1, 8, "Illumio Enforcement Tracker", book.fmt(bg_color=NAVY, font_color="#FFFFFF", bold=True, font_size=22))
    ws.merge_range(2, 2, 2, 8, "Plan. Track. Enforce.", book.fmt(bg_color=NAVY, font_color="#C8D1E0", font_size=11, valign="top"))
    projects = ", ".join(p["name"] for p in data["projects"]) or "All Projects"
    updated = datetime.datetime.now().strftime("%b %-d, %Y %-I:%M %p")
    ws.merge_range(0, 9, 0, LAST - 1, f"Last Updated: {updated}", book.fmt(bg_color=NAVY, font_color="#C8D1E0", align="right"))
    ws.merge_range(1, 9, 1, LAST - 1, f"PCE: {data['pce']}", book.fmt(bg_color=NAVY, font_color="#C8D1E0", align="right"))
    ws.write(0, LAST, "Project", book.fmt(bg_color=NAVY, font_color="#C8D1E0", font_size=9, valign="bottom"))
    ws.write(1, LAST, projects, book.fmt(bg_color="#FFFFFF", font_color=INK, bold=True, border=1, border_color="#FFFFFF", indent=1))

    # ---- tiles: live counts of the Application Tracker sheet (applications that are not excluded)
    def apps_where(status=None, row_type="Application"):
        extra = f',{TRACKER}!{T_STATUS},"{status}"' if status else ""
        return f'COUNTIFS({TRACKER}!{T_TYPE},"{row_type}"{extra})'

    ws.set_row(3, 9)
    ws.set_row(4, 20)
    ws.set_row(5, 36)
    ws.set_row(6, 9)
    env_total = sum(1 for a in active for e in a["environments"] if not e["excluded"])
    tiles = [("◆  Total Applications", f"={apps_where()}", str(total), "#2A78D6", "#EAF2FE", INK),
             ("≡  Environments", f"={apps_where(row_type='Environment')}", str(env_total), "#17994F", "#E6F6EC", INK)]
    marks = {"Fully Enforced": "✔", "In Progress": "◔", "Selective Enforcement": "◉", "Not Started": "!"}
    for s_ in ("Fully Enforced", "In Progress", "Selective Enforcement", "Not Started"):
        n = count(s_)
        tiles.append((f"{marks[s_]}  {s_}", f'={apps_where(s_)}&"        "&TEXT(IFERROR({apps_where(s_)}/{apps_where()},0),"0%")',
                      f"{n}        {(n / total if total else 0):.0%}", STATUS_COLOR[s_], STATUS_TINT[s_], STATUS_TEXT[s_]))
    spans = [(0, 1), (2, 3), (4, 5), (6, 8), (9, 11), (12, 13)]
    for (c0, c1), (label, formula, cached, accent, tint, ink) in zip(spans, tiles):
        edge = {"left": 1, "right": 1, "left_color": "#FFFFFF", "right_color": "#FFFFFF"}
        ws.merge_range(4, c0, 4, c1, label, book.fmt(bold=True, font_color=accent, bg_color=tint, indent=1, top=2, top_color=accent, **edge))
        value = book.fmt(bold=True, font_size=22, font_color=ink, bg_color=tint, indent=1, align="left", bottom=1, bottom_color=tint, **edge)
        ws.merge_range(5, c0, 5, c1, "", value)
        ws.write_formula(5, c0, formula, value, cached)

    # ---- table: one row per application and tracked environment
    HEAD = 21
    FIRST = HEAD + 1
    items = []
    for a in active:
        envs = [e for e in a["environments"] if not e["excluded"]]
        if not envs:
            items.append((a, None))
        items.extend((a, e) for e in envs)
    headers = ["#", "Application", "Environment", "Priority", "Assigned PS", "Project Manager", "App Owner", "Start Date",
               "End Date", "Duration\n(days)", "% Complete", "Progress", "Status", "Comments"]
    ws.set_row(HEAD - 1, 8)
    ws.set_row(HEAD, 32)
    ws.write_row(HEAD, 0, headers, book.fmt(bold=True, bg_color=NAVY, font_color="#FFFFFF", text_wrap=True, border=1,
                                           border_color="#26354D", indent=1))
    ref = lambda r_, c_, row_abs=False: xlsxwriter.utility.xl_rowcol_to_cell(r_, c_, row_abs=row_abs, col_abs=True)  # noqa: E731
    for i, (a, e) in enumerate(items):
        r = FIRST + i
        ws.set_row(r, 19)
        src = e or a
        zebra = "#FFFFFF" if i % 2 == 0 else "#F6F8FB"
        cell = lambda **extra: book.fmt(**{"bg_color": zebra, "bottom": 1, "bottom_color": "#E3E7EE", "indent": 1, **extra})  # noqa: E731
        start = src["start_date"] or a["start_date"]
        end = src["end_date"] or a["end_date"]
        ws.write_number(r, D_NUM, i + 1, cell(font_color=MUTED, align="center", indent=0))
        ws.write(r, D_APP, a["name"], cell(font_color="#1B4DAA", bold=True))
        ws.write(r, D_ENV, e["name"] if e else "—", cell())
        ws.write(r, D_PRI, src["priority"], cell(bold=True, align="center", indent=0))
        for k, key in enumerate(("ps_id", "pm_id", "owner_id")):
            ws.write(r, D_PS + k, person_name(data["people"], a[key]), cell())
        for c_, value in ((D_START, start), (D_END, end)):
            if value:
                ws.write_datetime(r, c_, day(value), cell(num_format="m/d/yyyy", align="right"))
            else:
                ws.write_blank(r, c_, None, cell(num_format="m/d/yyyy", align="right"))
        S, E, P = ref(r, D_START), ref(r, D_END), ref(r, D_PCT)
        duration = (day(end) - day(start)).days + 1 if start and end else ""
        ws.write_formula(r, D_DUR, f'=IF(AND({S}<>"",{E}<>""),{E}-{S}+1,"")', cell(align="center", indent=0), duration)
        ws.write_number(r, D_PCT, src["progress"] / 100, cell(num_format="0%", bold=True, align="center", indent=0))
        ws.write_formula(r, D_BAR, f'=REPT("█",ROUND({P}*{BAR_BLOCKS},0))&REPT("░",{BAR_BLOCKS}-ROUND({P}*{BAR_BLOCKS},0))',
                         cell(font_size=9, font_color="#9AA5B6", indent=0, align="center"),
                         "█" * round(src["progress"] / 100 * BAR_BLOCKS) + "░" * (BAR_BLOCKS - round(src["progress"] / 100 * BAR_BLOCKS)))
        ws.write(r, D_STATUS, src["status"], cell(bold=True, align="center", indent=0))
        ws.write(r, D_NOTE, (src["comments"] if e and src["comments"] else a["comments"]) or "", cell())
    last = FIRST + len(items) - 1
    if items:
        ws.autofilter(HEAD, 0, last, LAST)
        ws.data_validation(FIRST, D_STATUS, last, D_STATUS, {"validate": "list", "source": APP_STATUSES})
        ws.data_validation(FIRST, D_PRI, last, D_PRI, {"validate": "list", "source": PRIORITIES})
        ws.data_validation(FIRST, D_PCT, last, D_PCT, {"validate": "decimal", "criteria": "between", "minimum": 0, "maximum": 1,
                                                      "error_message": "Enter a percentage between 0% and 100%."})
        for s_ in APP_STATUSES:  # colours follow the values, so they change when a cell is edited
            match = f'={ref(FIRST, D_STATUS)}="{s_}"'
            ws.conditional_format(FIRST, D_STATUS, last, D_STATUS, {"type": "formula", "criteria": match,
                                  "format": wb.add_format({"bg_color": STATUS_TINT[s_], "font_color": STATUS_TEXT[s_]})})
            ws.conditional_format(FIRST, D_BAR, last, D_BAR, {"type": "formula", "criteria": match,
                                  "format": wb.add_format({"font_color": STATUS_COLOR[s_]})})
            ws.conditional_format(FIRST, D_PCT, last, D_PCT, {"type": "formula", "criteria": match,
                                  "format": wb.add_format({"font_color": STATUS_TEXT[s_]})})
        for p_ in PRIORITIES:
            ws.conditional_format(FIRST, D_PRI, last, D_PRI, {"type": "cell", "criteria": "==", "value": f'"{p_}"',
                                  "format": wb.add_format({"bg_color": PRIORITY_TINT[p_], "font_color": PRIORITY_COLOR[p_]})})
        ws.merge_range(last + 2, 0, last + 2, LAST,
                       "Live workbook  ·  Tiles and the Status and Priority charts count the Application Tracker sheet. "
                       "Completion by Environment, durations, progress bars and colours follow this table. "
                       "Use the arrows in the header row to filter.", book.fmt(font_color=MUTED, italic=True, font_size=9))
    else:
        ws.merge_range(FIRST, 0, FIRST, LAST, "No applications to show yet. Sync from the PCE first.", book.fmt(font_color=MUTED, italic=True))

    # ---- chart data on a hidden sheet, as formulas over the visible sheets
    ds = wb.add_worksheet("Data")
    ds.hide()
    for i, s_ in enumerate(APP_STATUSES):
        ds.write(i, 0, s_)
        ds.write_formula(i, 1, f"={apps_where(s_)}", None, count(s_))
    for i, p_ in enumerate(PRIORITIES):
        ds.write(i, 3, p_)
        ds.write_formula(i, 4, f'=COUNTIFS({TRACKER}!{T_TYPE},"Application",{TRACKER}!{T_PRIORITY},"{p_}")',
                         None, sum(1 for a in active if a["priority"] == p_))
    # VENs by enforcement mode: sums of the per-application counts on the Application Tracker sheet
    ven_modes = [("Full Enforcement", "$N:$N", "wl_full", "#17994F"), ("Selective Enforcement", "$R:$R", "wl_selective", "#2A78D6"),
                 ("Visibility Only", "$S:$S", "wl_visibility", "#E39B00"), ("Idle", "$T:$T", "wl_idle", "#8A94A6")]
    for i, (label, column, key, _color) in enumerate(ven_modes):
        ds.write(i, 12, label)
        ds.write_formula(i, 13, f'=SUMIFS({TRACKER}!{column},{TRACKER}!{T_TYPE},"Application")', None, sum(a[key] for a in active))
    env_names = sorted({e["name"] for _a, e in items if e})
    ds.write_row(0, 6, ["Environment"] + APP_STATUSES)
    if items:
        env_col = f"Dashboard!{ref(FIRST, D_ENV, True)}:{ref(last, D_ENV, True)}"
        st_col = f"Dashboard!{ref(FIRST, D_STATUS, True)}:{ref(last, D_STATUS, True)}"
        pct_col = f"Dashboard!{ref(FIRST, D_PCT, True)}:{ref(last, D_PCT, True)}"
        for i, name in enumerate(env_names, start=1):
            mine = [e for _a, e in items if e and e["name"] == name]
            quoted = name.replace('"', '""')
            average = sum(e["progress"] for e in mine) / len(mine)
            ds.write_formula(i, 6, f'="{quoted}   "&TEXT(IFERROR(AVERAGEIFS({pct_col},{env_col},"{quoted}"),0),"0%")', None,
                             f"{name}   {average:.0f}%")
            for k, s_ in enumerate(APP_STATUSES):
                ds.write_formula(i, 7 + k, f'=COUNTIFS({env_col},"{quoted}",{st_col},"{s_}")', None,
                                 sum(1 for e in mine if e["status"] == s_))

    # ---- charts
    font = {"name": "Calibri", "size": 10, "color": "#374151"}
    title = lambda text: {"name": text, "name_font": {"name": "Calibri", "size": 13, "bold": True, "color": INK},  # noqa: E731
                          "overlay": False, "layout": {"x": 0.03, "y": 0.04}}
    frame = {"border": {"color": "#E3E7EE"}, "fill": {"color": "#FFFFFF"}}
    donut = wb.add_chart({"type": "doughnut"})
    donut.add_series({"name": "Applications by Status", "categories": ["Data", 0, 0, 3, 0], "values": ["Data", 0, 1, 3, 1],
                      "points": [{"fill": {"color": STATUS_COLOR[s_]}, "border": {"color": "#FFFFFF", "width": 1.5}} for s_ in APP_STATUSES],
                      "data_labels": {"value": True, "font": {"name": "Calibri", "color": "#FFFFFF", "bold": True, "size": 10}}})
    donut.set_title(title("Applications by Status"))
    donut.set_hole_size(60)
    donut.set_legend({"position": "right", "font": font})
    donut.set_size({"width": 437, "height": 262})
    donut.set_chartarea(frame)
    ws.insert_chart(7, 0, donut, {"x_offset": 0, "y_offset": 0})

    pie = wb.add_chart({"type": "pie"})
    pie.add_series({"name": "VEN Enforcement Type", "categories": ["Data", 0, 12, 3, 12], "values": ["Data", 0, 13, 3, 13],
                    "points": [{"fill": {"color": color}, "border": {"color": "#FFFFFF", "width": 1.5}} for *_x, color in ven_modes],
                    "data_labels": {"value": True, "percentage": True, "separator": "\n", "position": "best_fit",
                                    "font": {"name": "Calibri", "bold": True, "size": 10}}})
    pie.set_title(title("VEN Enforcement Type"))
    pie.set_legend({"position": "right", "font": font})
    pie.set_size({"width": 437, "height": 262})
    pie.set_chartarea(frame)
    ws.insert_chart(7, 4, pie, {"x_offset": 3, "y_offset": 0})

    bars = wb.add_chart({"type": "column"})
    bars.add_series({"name": "Applications", "categories": ["Data", 0, 3, 3, 3], "values": ["Data", 0, 4, 3, 4],
                     "points": [{"fill": {"color": PRIORITY_COLOR[p_]}} for p_ in PRIORITIES],
                     "data_labels": {"value": True, "font": {"name": "Calibri", "bold": True, "size": 10}}, "gap": 70})
    bars.set_title(title("Applications by Priority"))
    bars.set_legend({"none": True})
    bars.set_y_axis({"major_gridlines": {"visible": True, "line": {"color": "#ECEFF4"}}, "num_font": font, "line": {"none": True},
                     "major_tick_mark": "none"})
    bars.set_x_axis({"num_font": font, "line": {"color": "#D9DEE7"}, "major_tick_mark": "none"})
    bars.set_size({"width": 437, "height": 262})
    bars.set_chartarea(frame)
    ws.insert_chart(7, 7, bars, {"x_offset": 53, "y_offset": 0})

    if env_names:
        envs = wb.add_chart({"type": "bar", "subtype": "percent_stacked"})
        for k, s_ in enumerate(APP_STATUSES):
            envs.add_series({"name": ["Data", 0, 7 + k], "categories": ["Data", 1, 6, len(env_names), 6],
                             "values": ["Data", 1, 7 + k, len(env_names), 7 + k], "fill": {"color": STATUS_COLOR[s_]},
                             "border": {"color": "#FFFFFF", "width": 1}, "gap": 60})
        envs.set_title(title("Completion by Environment"))
        envs.set_legend({"position": "bottom", "font": font})
        envs.set_y_axis({"reverse": True, "num_font": {**font, "bold": True}, "line": {"none": True}, "major_tick_mark": "none"})
        envs.set_x_axis({"visible": False, "major_gridlines": {"visible": False}})
        envs.set_size({"width": 442, "height": 262})
        envs.set_chartarea(frame)
        ws.insert_chart(7, 12, envs, {"x_offset": 37, "y_offset": 0})

    ws.freeze_panes(FIRST, 2)
    ws.set_landscape()
    ws.set_paper(9)
    ws.fit_to_pages(1, 0)
    ws.repeat_rows(HEAD)
    return ws


def write_table(book, name, headers, widths, rows, formats=None, tab=None):
    """A data sheet: navy header, banded rows, autofilter, frozen header. `formats` maps column index -> format props."""
    ws = book.wb.add_worksheet(name)
    ws.hide_gridlines(2)
    if tab:
        ws.set_tab_color(tab)
    for i, w in enumerate(widths):
        ws.set_column(i, i, w)
    ws.set_row(0, 26)
    ws.write_row(0, 0, headers, book.fmt(bold=True, bg_color=NAVY, font_color="#FFFFFF", border=1, border_color="#26354D", indent=1))
    formats = formats or {}
    for r, row in enumerate(rows, start=1):
        ws.set_row(r, 18)
        style = row.pop("_style", {}) if isinstance(row, dict) else {}
        values = row["values"] if isinstance(row, dict) else row
        band = {} if "bg_color" in style or r % 2 else {"bg_color": "#F6F8FB"}
        for cidx, value in enumerate(values):
            props = {"bottom": 1, "bottom_color": "#E3E7EE", "indent": 1, **band, **formats.get(cidx, {}), **style}
            if isinstance(value, datetime.date):
                ws.write_datetime(r, cidx, value, book.fmt(num_format="m/d/yyyy", **props))
            elif value is None:
                ws.write_blank(r, cidx, None, book.fmt(**props))
            else:
                ws.write(r, cidx, value, book.fmt(**props))
    if rows:
        ws.autofilter(0, 0, len(rows), len(headers) - 1)
    ws.freeze_panes(1, 0)
    return ws


RULE_COUNTS = ("rulesets", "rules_draft", "rules_active", "rules_pending")
POLICY_COUNTS = RULE_COUNTS + policy.TRAFFIC_COLS
POLICY_HEADERS = ["Rulesets", "Rules in Draft", "Rules Provisioned", "Draft Changes Not Provisioned",
                  "Potentially Blocked Flows", "Potentially Blocked Connections", "Blocked Flows", "Blocked Connections"]
BASES = ("provisioned", "draft")   # which rules the flows are judged by
DIRECTION_NAMES = {"all": "All Directions", "in": "Inbound", "out": "Outbound"}


def policy_counts(basis):
    """The count columns and their headers for flows judged by the provisioned or the draft rules."""
    if basis == "draft":
        return (RULE_COUNTS + policy.DRAFT_COLS,
                POLICY_HEADERS[:4] + [h + " (Draft Rules)" for h in POLICY_HEADERS[4:]]
                + ["Allowed by Draft Rules (Flows)", "Allowed by Draft Rules (Connections)"])
    return POLICY_COUNTS, POLICY_HEADERS


def direction_note(direction):
    """Direction of traffic, seen from each scope - printed under every Policy & Traffic sheet."""
    return ({"all": "Direction: All Directions - each scope counts a connection when the scope is its source or its destination, once.",
             "in": "Direction: Inbound - traffic coming into the scope: the destination workload is in the scope, the source can be anywhere.",
             "out": "Direction: Outbound - traffic leaving the scope: the source workload is in the scope, the destination can be anywhere."}[direction]
            + " A connection between two workloads of the same scope is both inbound and outbound to it, so Inbound + Outbound can be more "
              "than All Directions. The PCE total counts every connection once, whatever its direction.")


def basis_notes(pol, basis):
    traffic = pol["info"].get("traffic") or {}
    if basis != "draft":
        return ["Flows are counted by the policy decision the VENs reported, that is under the provisioned rules."]
    notes = ["Flows are counted by the PCE's decision under the draft rules, as Explorer shows with Draft view: the "
             "flows reported as blocked or potentially blocked, judged again as if the draft were provisioned. "
             "\"Allowed by Draft Rules\" are the connections that provisioning the draft would allow. A connection the PCE "
             "cannot judge against the draft keeps the decision it was reported with. Connections reported as "
             "allowed are not re-checked, so a draft change that would block allowed traffic is not counted."]
    if traffic.get("synced_at") and not traffic.get("draft"):
        notes.append("Draft policy decisions were not available at the last sync"
                     + (f" ({traffic['draft_error']})" if traffic.get("draft_error") else "")
                     + "; the draft counts show 0. Sync from PCE again.")
    return notes


# Scopes that reach further than one application and environment are highlighted wherever they are listed.
SCOPE_BADGES = {"Scopeless": ("#B02A2A", "#FFFFFF"), "Application only": ("#FDF1CF", "#8A5A00")}
SCOPE_TINT = {"none": "#FBDFDF", "app": "#FEF7E4"}


def scope_callout(doc, pol):
    """Word section 4: name the scopeless and application-only rulesets above the table."""
    lines = []
    if pol.get("scopeless"):
        lines.append(f"Scopeless: {len(pol['scopeless'])} ruleset(s) have no scope, so their rules apply to every workload in the "
                     f"PCE: {', '.join(pol['scopeless'])}. Check that each rule is meant to be global.")
    if pol.get("app_only"):
        lines.append(f"Application only: {len(pol['app_only'])} ruleset(s) are scoped to an application without an environment, so "
                     f"their rules apply to every environment of it (for example production and development alike): "
                     f"{', '.join(pol['app_only'])}.")
    if lines:
        callout(doc, "Rulesets with a wide scope", lines, accent="#B02A2A" if pol.get("scopeless") else WARN, fill="#FDF6F6")


def policy_notes(pol, with_traffic=True):
    """What a reader needs to know to trust the Policy & Traffic numbers, one sentence per item.
    with_traffic=False: for a page that shows rules only (Word section 4), the traffic notes are left out."""
    rules, traffic = pol["info"].get("rules") or {}, pol["info"].get("traffic") or {}
    notes = []
    if not rules.get("synced_at") and not (with_traffic and traffic.get("synced_at")):
        notes.append("No rules or traffic have been read yet. Press Sync from PCE in the tracker." if with_traffic
                     else "No rules have been read yet. Press Sync from PCE in the tracker.")
    for part, label in ((rules, "Rules"),) + (((traffic, "Blocked traffic"),) if with_traffic else ()):
        if part.get("error"):
            notes.append(f"{label} could not be read at the last sync ({part['error']}); "
                         + (f"the numbers are from {part['synced_at']}." if part.get("synced_at") else "none are shown."))
    if with_traffic and traffic.get("synced_at"):
        notes.append(f"Traffic covers the {traffic.get('days', policy.TRAFFIC_DAYS)} days before the sync of {traffic['synced_at']}. "
                     "As in Explorer, a connection is one source, destination, port and protocol"
                     + ", and its flows are the number of times it was seen (all the PCE's records for it added up)"
                     + ". It counts for the scope of its source and of its destination.")
    if with_traffic and traffic.get("truncated"):
        notes.append(f"The PCE returned its maximum of {policy.TRAFFIC_MAX_RESULTS:,} traffic records, so the traffic counts are incomplete.")
    if pol.get("scopeless"):
        notes.append(f"Scopeless rulesets apply to every workload in the PCE: {', '.join(pol['scopeless'])}.")
    if pol.get("app_only"):
        notes.append(f"Application-only rulesets apply to every environment of their application: {', '.join(pol['app_only'])}. "
                     "They have a row of their own, and their rules also count in each environment's row.")
    notes.append("Rules of a scope are the rules of the rulesets scoped to that application, for that environment or for all "
                 "environments. Rulesets that name no tracked application are listed by their own scope"
                 + (", without traffic. Totals count each ruleset and each connection once, for the whole PCE." if with_traffic
                    else ". Totals count each ruleset once, for the whole PCE."))
    return notes


def write_policy(book, pol, basis="provisioned", direction="all"):
    """Policy & Traffic sheet: per scope, the rules written (draft and provisioned) and the blocked traffic,
    with flows judged by the provisioned or the draft rules, in either or one direction."""
    counts, count_headers = policy_counts(basis)
    if direction != "all":
        count_headers = count_headers[:4] + [f"{h} ({DIRECTION_NAMES[direction]})" for h in count_headers[4:]]
    rows = []
    for r in pol["rows"]:
        values = {**r, **(r[direction] or {})} if direction != "all" else r
        kind = r.get("scope_type")
        style = {} if r["tracked"] else {"font_color": MUTED, "italic": True}
        if kind in SCOPE_TINT:   # scopeless and application-only scopes stand out
            style = {**style, "bg_color": SCOPE_TINT[kind], "font_color": SCOPE_BADGES[policy.SCOPE_TYPES[kind]][0] if kind == "none" else INK}
        rows.append({"values": [r["application"], r["environment"], r["scope"], policy.SCOPE_TYPES.get(kind, ""), r["status"], r["pce_state"]]
                     + ["" if values.get(k) is None else values[k] for k in counts] + [r["ruleset_names"]],
                     "_style": style})
    rows.append({"values": ["PCE total", "", "All scopes", "", "", ""] + [pol["totals"][k] for k in counts] + [""],
                 "_style": {"bold": True, "bg_color": "#F1F3F7", "top": 1, "top_color": NAVY}})
    numbers = {i: {"num_format": "#,##0", "align": "right"} for i in range(6, 6 + len(counts))}
    headers = ["Application", "Environment", "Scope", "Scope Type", "Tracker Status", "PCE Enforcement"] + count_headers + ["Ruleset Names"]
    widths = [28, 16, 40, 22, 22, 22, 10, 12, 13, 16] + [16] * (len(counts) - 4) + [60]
    ws = write_table(book, "Policy & Traffic" if direction == "all" else f"Policy & Traffic {DIRECTION_NAMES[direction]}",
                     headers, widths, rows, numbers, tab="#7A4FD0")
    ws.set_row(0, 40)  # the count headers wrap onto two lines
    for i, value in enumerate(headers):
        ws.write(0, i, value, book.fmt(bold=True, bg_color=NAVY, font_color="#FFFFFF", border=1, border_color="#26354D", indent=1, text_wrap=True))
    for i, note in enumerate(basis_notes(pol, basis) + [direction_note(direction)] + policy_notes(pol)):
        ws.write(len(rows) + 2 + i, 0, note, book.fmt(font_color=MUTED, italic=True, font_size=9))
    return ws


def build(data):
    stream = io.BytesIO()
    book = Book(stream)
    rows, preds = task_rows(data["tl"], data["projects"])
    people = data["people"]
    write_dashboard(book, data, rows)

    # Only applications that are not excluded are reported, and for each of them only the
    # environments that are not excluded.
    tracker = []
    for a in data["apps"]:
        if a["excluded"]:
            continue
        managed = a["wl_full"] + a["wl_selective"] + a["wl_visibility"] + a["wl_idle"]
        style = {} if a["in_pce"] else {"font_color": "#B02A2A"}
        tracker.append({"values": [a["rank"], a["name"], "", a["status"], a["priority"], a["progress"] / 100,
                                   day(a["start_date"]), day(a["end_date"]), person_name(people, a["ps_id"]), person_name(people, a["pm_id"]),
                                   person_name(people, a["owner_id"]), a["pce_state"], managed, a["wl_full"],
                                   "" if a["in_pce"] else "Yes", a["comments"] or "", "Application",
                                   a["wl_selective"], a["wl_visibility"], a["wl_idle"]],
                        "_style": {"bold": True, **style}})
        for e in a["environments"]:
            if e["excluded"]:
                continue
            emanaged = e["wl_full"] + e["wl_selective"] + e["wl_visibility"] + e["wl_idle"]
            tracker.append({"values": ["", f"    ↳ {a['name']}", e["name"], e["status"], e["priority"], e["progress"] / 100,
                                       day(e["start_date"]), day(e["end_date"]), "", "", "", e["pce_state"], emanaged, e["wl_full"],
                                       "" if e["in_pce"] else "Yes", e["comments"] or "", "Environment",
                                       e["wl_selective"], e["wl_visibility"], e["wl_idle"]],
                            "_style": {} if e["in_pce"] else {"font_color": "#B02A2A"}})
    ts = write_table(book, "Application Tracker",
                     ["Rank", "Application", "Environment", "Status", "Priority", "Progress", "Start Date", "End Date", "Assigned PS",
                      "Project Manager", "App Owner", "PCE Enforcement", "VENs", "VENs Full Enforcement",
                      "Deleted in PCE", "Latest Comment", "Row Type", "VENs Selective", "VENs Visibility Only", "VENs Idle"],
                     [7, 32, 16, 22, 11, 10, 12, 12, 18, 18, 18, 22, 9, 13, 12, 50, 13, 12, 13, 10], tracker,
                     {5: {"num_format": "0%", "align": "center"}, 0: {"align": "center"}}, tab="#2A78D6")
    # The Dashboard tiles and charts count this sheet, so give it dropdowns and colours that follow
    # the values. The extra rows let applications be added by hand.
    end = len(tracker) + 200
    ts.data_validation(1, 3, end, 3, {"validate": "list", "source": APP_STATUSES})
    ts.data_validation(1, 4, end, 4, {"validate": "list", "source": PRIORITIES})
    ts.data_validation(1, 16, end, 16, {"validate": "list", "source": ["Application", "Environment"]})
    for s_, tint in (("Fully Enforced", "#DFF4E7"), ("Selective Enforcement", "#E4EFFC"), ("In Progress", "#FDF3D9"), ("Not Started", "#FBE6E6")):
        ts.conditional_format(1, 3, end, 3, {"type": "cell", "criteria": "==", "value": f'"{s_}"',
                                             "format": book.wb.add_format({"bg_color": tint})})
    for p_ in PRIORITIES:
        ts.conditional_format(1, 4, end, 4, {"type": "cell", "criteria": "==", "value": f'"{p_}"',
                                             "format": book.wb.add_format({"bg_color": PRIORITY_TINT[p_], "font_color": PRIORITY_COLOR[p_]})})

    write_policy(book, data["policy"])

    active = [a for a in data["apps"] if not a["excluded"]]
    write_table(book, "People", ["Name", "Title", "Email", "Assigned PS", "Project Manager", "App Owner", "Applications"],
                [26, 24, 30, 12, 15, 11, 13],
                [[p["name"], p["title"], p["email"], "Yes" if p["is_ps"] else "", "Yes" if p["is_pm"] else "", "Yes" if p["is_owner"] else "",
                  sum(1 for a in active if p["id"] in (a["ps_id"], a["pm_id"], a["owner_id"]))] for p in people.values()],
                {6: {"align": "center"}}, tab="#17994F")

    envs = {}
    for a in active:
        for e in a["environments"]:
            if not e["excluded"]:
                envs.setdefault(e["name"], []).append(e)
    statuses = ["Not Started", "In Progress", "Selective Enforcement", "Fully Enforced"]
    write_table(book, "Environments", ["Environment", "Applications"] + statuses + ["Average Progress", "Deleted in PCE"],
                [22, 13, 13, 13, 22, 15, 17, 15],
                [[name, len(items)] + [sum(1 for e in items if e["status"] == s) for s in statuses]
                 + [sum(e["progress"] for e in items) / len(items) / 100, sum(1 for e in items if not e["in_pce"]) or ""]
                 for name, items in sorted(envs.items())],
                {6: {"num_format": "0%", "align": "center"}}, tab="#E39B00")
    plan = []
    for i, row in enumerate(rows, start=1):
        t = row["task"]
        leaf = row["kind"] == "task"
        plan.append({"values": [
            i, row["project"], row["level"], ("    " * row["level"]) + row["name"],
            "Project" if row["kind"] == "project" else "Phase" if row["kind"] == "summary" else "Task",
            (t["application"] or "") if t else "", (t["environment"] or "") if t else "", t["priority"] if leaf else "",
            person_name(people, t["ps_id"]) if leaf else "", person_name(people, t["pm_id"]) if leaf else "",
            person_name(people, t["owner_id"]) if leaf else "", day(row["start"]), day(row["end"]),
            row["duration"] if row["duration"] is not None else "", row["progress"] / 100, row["status"],
            "; ".join(preds.get(t["id"], [])) if t else ""],
            "_style": {} if leaf else {"bold": True, "bg_color": "#F1F3F7"}})
    write_table(book, "Project Timeline",
                ["#", "Project", "Level", "Task Name", "Type", "Application", "Environment", "Priority", "Assigned PS",
                 "Project Manager", "App Owner", "Start Date", "End Date", "Duration (days)", "% Complete", "Status", "Predecessors"],
                [5, 26, 6, 44, 9, 24, 14, 10, 18, 18, 18, 12, 12, 10, 11, 14, 40], plan,
                {14: {"num_format": "0%", "align": "center"}, 0: {"align": "center"}, 2: {"align": "center"}}, tab="#8A94A6")

    book.wb.close()
    return stream.getvalue()


# ---------------------------------------------------------------------------- Word report
# Illumio theme: navy bands, orange accents. TINT marks what needs attention (unused labels).
TINT, WARN, ZEBRA, GRID = "#FDEBE1", "#B4470F", "#F6F8FB", "#D9DEE7"
MODE_NAMES = {"full": "Full Enforcement", "selective": "Selective Enforcement", "visibility_only": "Visibility Only", "idle": "Idle"}
USED_BY = {"workload": "Workloads", "ruleset": "Rulesets", "label_group": "Label groups", "pairing_profile": "Pairing profiles",
           "virtual_service": "Virtual services", "virtual_server": "Virtual servers", "container_workload": "Container workloads",
           "container_workload_profile": "Container workload profiles", "enforcement_boundary": "Deny rules", "permission": "Access scopes"}
BAR_WIDTH = 24  # longest in-cell bar of a share table, in block characters


def rgb(color):
    return RGBColor.from_string(color.lstrip("#"))


def style_cell(cell, fill=None, **borders):
    """Shade a cell and draw borders given as side=(eighths of a point, colour)."""
    props = cell._tc.get_or_add_tcPr()
    if borders:
        box = OxmlElement("w:tcBorders")
        for side in ("top", "left", "bottom", "right"):   # the order Word requires
            if side in borders:
                size, color = borders[side]
                line = OxmlElement(f"w:{side}")
                for attr, value in (("val", "single"), ("sz", str(size)), ("space", "0"), ("color", color.lstrip("#"))):
                    line.set(qn(f"w:{attr}"), value)
                box.append(line)
        props.append(box)
    if fill:
        shd = OxmlElement("w:shd")
        for attr, value in (("val", "clear"), ("color", "auto"), ("fill", fill.lstrip("#"))):
            shd.set(qn(f"w:{attr}"), value)
        props.append(shd)


def page_width(doc):
    section = doc.sections[0]
    return section.page_width - section.left_margin - section.right_margin


def fixed_table(doc, columns, widths):
    """An empty one-row table whose columns share the full width between the margins in the given
    proportions. Fixed layout: Word keeps the widths and wraps long text inside its cell."""
    usable = page_width(doc)
    widths = [int(usable * w / sum(widths)) for w in widths]
    table = doc.add_table(rows=1, cols=columns)
    table.autofit = False
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    total = table._tbl.tblPr.find(qn("w:tblW"))   # an explicit table width, for readers that ignore the column grid
    total.set(qn("w:type"), "dxa")
    total.set(qn("w:w"), str(sum(widths) // 635))
    for column, width in zip(table.columns, widths):
        column.width = width
    return table, widths


def write_cell(cell, value, width, size=8, bold=False, color=None, right=False):
    cell.width = width
    par = cell.paragraphs[0]
    par.paragraph_format.space_after = Pt(0)
    if right:
        par.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    run = par.add_run(f"{value:,}" if isinstance(value, int) else "" if value is None else str(value))
    run.font.size = Pt(size)
    run.bold = bold
    if color:
        run.font.color.rgb = rgb(color)
    return par


def grid_lines(table, color=GRID):
    """Thin lines around and between every cell of a table."""
    table.style = "Table Grid"
    for old in table._tbl.tblPr.findall(qn("w:tblBorders")):   # restyling a table replaces its lines
        table._tbl.tblPr.remove(old)
    lines = OxmlElement("w:tblBorders")
    for side in ("top", "left", "bottom", "right", "insideH", "insideV"):
        line = OxmlElement(f"w:{side}")
        for attr, value in (("val", "single"), ("sz", "4"), ("space", "0"), ("color", color.lstrip("#"))):
            line.set(qn(f"w:{attr}"), value)
        lines.append(line)
    layout = table._tbl.tblPr.find(qn("w:tblLayout"))
    layout.addprevious(lines)
    for old in table._tbl.tblPr.findall(qn("w:tblCellMar")):
        table._tbl.tblPr.remove(old)
    padding = OxmlElement("w:tblCellMar")   # breathing room around the text of every cell
    for side, twips in (("top", 55), ("left", 100), ("bottom", 55), ("right", 100)):
        edge = OxmlElement(f"w:{side}")
        edge.set(qn("w:w"), str(twips))
        edge.set(qn("w:type"), "dxa")
        padding.append(edge)
    layout.addnext(padding)


def doc_table(doc, headers, rows, widths, right=(), bold_rows=(), tints=None, colors=None, badges=None, size=None):
    """A banded table with a navy header that repeats on every page. `rows` are lists of cell values,
    `tints` maps a row index to a fill colour and `colors` a column index to a text colour. `badges`
    maps a column index to {value: (fill, text colour)}: cells of that column are coloured by their
    value. Wide tables get slightly smaller text so they stay on the page."""
    size = size or (9 if len(headers) <= 8 else 8)
    badges = badges or {}
    table, widths = fixed_table(doc, len(headers), widths)
    grid_lines(table)
    repeat = OxmlElement("w:tblHeader")
    repeat.set(qn("w:val"), "true")
    table.rows[0]._tr.get_or_add_trPr().append(repeat)
    for i, text in enumerate(headers):
        write_cell(table.rows[0].cells[i], text, widths[i], size=size, bold=True, color="#FFFFFF", right=i in right)
        style_cell(table.rows[0].cells[i], NAVY, bottom=(12, ORANGE))
        table.rows[0].cells[i].vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
    tints, colors = tints or {}, colors or {}
    for n, values in enumerate(rows):
        cells = table.add_row().cells
        fill = tints.get(n) or ("#F1F3F7" if n in bold_rows else ZEBRA if n % 2 else None)
        for i, value in enumerate(values):
            badge = badges.get(i, {}).get(value)
            write_cell(cells[i], value, widths[i], size=size, bold=n in bold_rows or bool(badge),
                       color=badge[1] if badge else colors.get(i), right=i in right)
            cells[i].vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            if badge or fill:
                style_cell(cells[i], badge[0] if badge else fill)
    doc.add_paragraph().paragraph_format.space_after = Pt(2)
    return table


def doc_tiles(doc, tiles):
    """A row of KPI tiles: (label, value) or (label, value, accent colour)."""
    table, widths = fixed_table(doc, len(tiles), [1] * len(tiles))
    for cell, width, tile in zip(table.rows[0].cells, widths, tiles):
        label, value, accent = (*tile, ORANGE)[:3]
        write_cell(cell, label.upper(), width, size=7, bold=True, color=MUTED)
        par = cell.add_paragraph()
        par.paragraph_format.space_after = Pt(2)
        run = par.add_run(f"{value:,}" if isinstance(value, int) else str(value))
        run.font.size = Pt(20)
        run.bold = True
        run.font.color.rgb = rgb(NAVY if accent == ORANGE else accent)
        style_cell(cell, TINT if accent == WARN else ZEBRA, top=(24, accent), left=(36, "#FFFFFF"), right=(36, "#FFFFFF"))
    doc.add_paragraph().paragraph_format.space_after = Pt(2)


def share_table(doc, what, unit, counts):
    """`counts` as (label, n) rows with each one's share of the total and a bar."""
    total = sum(n for _label, n in counts)
    top = max((n for _label, n in counts), default=0)
    rows = [[label, n, f"{(n / total if total else 0):.0%}", "█" * max(round(n / top * BAR_WIDTH), 1) if n else ""] for label, n in counts]
    doc_table(doc, [what, unit, "Share", ""], rows, [3.2, 0.9, 0.8, 4.9], right=(1, 2), colors={3: ORANGE})


def tally(items, key, blank):
    """(value, count) pairs, most common first; items without a value are counted under `blank`, last."""
    counts = {}
    for item in items:
        counts[item[key] or ""] = counts.get(item[key] or "", 0) + 1
    return [(name or blank, n) for name, n in sorted(counts.items(), key=lambda kv: (kv[0] == "", -kv[1], kv[0].lower()))]


def version_key(version):
    return [int(part) if part.isdigit() else -1 for part in version.replace("-", ".").split(".")]


def note(doc, text):
    par = doc.add_paragraph(text)
    par.paragraph_format.space_after = Pt(2)
    par.runs[0].font.size = Pt(8)
    par.runs[0].italic = True
    par.runs[0].font.color.rgb = rgb(MUTED)


MODE_COLORS = {"full": "#17994F", "selective": "#2A78D6", "visibility_only": "#E39B00", "idle": "#8A94A6"}
CHART_FONT = {"family": "sans-serif", "sans-serif": ["Calibri", "Arial", "Helvetica", "DejaVu Sans"]}


def donut(ax, title, parts, unit):
    """A doughnut of (label, n, colour) parts with the total in the middle and the counts in the legend."""
    total = sum(n for _label, n, _color in parts)
    ax.set_title(title, fontsize=10.5, fontweight="bold", color=NAVY, loc="left", pad=10)
    ax.set_aspect("equal")
    ax.set_anchor("N")   # keep the ring under its title, level with the bar charts beside it
    shown = [p for p in parts if p[1]]
    if not shown:
        ax.pie([1], colors=["#E3E7EE"], wedgeprops={"width": 0.36})
    else:
        ax.pie([n for _l, n, _c in shown], colors=[c for _l, _n, c in shown], startangle=90, counterclock=False,
               wedgeprops={"width": 0.36, "edgecolor": "white", "linewidth": 2})
    ax.text(0, 0.06, f"{total:,}", ha="center", va="center", fontsize=16, fontweight="bold", color=NAVY)
    ax.text(0, -0.24, unit, ha="center", va="center", fontsize=7.5, color=MUTED)
    handles = [ax.bar(0, 0, color=color) for _label, _n, color in parts]
    ax.legend(handles, [f"{label}   {n:,}  ({(n / total if total else 0):.0%})" for label, n, _color in parts],
              loc="upper center", bbox_to_anchor=(0.5, 0.04), frameon=False, fontsize=8, handlelength=0.9, handleheight=0.9, labelcolor=INK)


def hbars(ax, title, items, colors=ORANGE, limit=7, share=True, suffix="", scale=None):
    """Horizontal bars for (label, n) items in the order given. Each bar has its label above it and its
    count (and share of the total, unless the items are not parts of one whole) at its end, so long
    labels never run into the chart beside it."""
    ax.set_title(title, fontsize=10.5, fontweight="bold", color=NAVY, loc="left", pad=10)
    total = sum(n for _label, n in items)
    if len(items) > limit:   # keep the chart readable: the rest is one bar
        items = items[:limit - 1] + [(f"Other ({len(items) - limit + 1})", sum(n for _label, n in items[limit - 1:]))]
    colors = colors if isinstance(colors, list) else [colors] * len(items)
    ax.axis("off")
    if not items:
        ax.text(0.5, 0.5, "No data", ha="center", va="center", fontsize=9, color=MUTED, transform=ax.transAxes)
        return
    top = scale or max(n for _label, n in items) or 1   # `scale` fixes the full width, e.g. 100 for percentages
    slots = max(len(items), 4)   # few bars stay slim instead of filling the panel
    ax.set_xlim(0, top * 1.45)
    ax.set_ylim(slots, 0)
    for row, ((label, n), color) in enumerate(zip(items, colors)):
        ax.text(0, row + 0.3, label, va="center", fontsize=8, color=INK)
        ax.barh(row + 0.68, top, height=0.26, color="#EEF1F5")
        ax.barh(row + 0.68, n, height=0.26, color=color)
        ax.text(top * 1.04, row + 0.68, f"{n:,}{suffix}" + (f"  ({(n / total if total else 0):.0%})" if share else ""), va="center", fontsize=8,
                fontweight="bold", color=NAVY)


def stacked(ax, title, rows, series):
    """Horizontal stacked bars: `rows` are (label, [count per series]) and `series` are (name, colour)."""
    ax.set_title(title, fontsize=10.5, fontweight="bold", color=NAVY, loc="left", pad=10)
    ax.axis("off")
    if not rows:
        ax.text(0.5, 0.5, "No data", ha="center", va="center", fontsize=9, color=MUTED, transform=ax.transAxes)
        return
    top = max(sum(values) for _label, values in rows) or 1
    ax.set_xlim(0, top * 1.3)
    ax.set_ylim(max(len(rows), 4), 0)
    for row, (label, values) in enumerate(rows):
        ax.text(0, row + 0.3, label, va="center", fontsize=8, color=INK)
        ax.barh(row + 0.68, top, height=0.26, color="#EEF1F5")
        left = 0
        for n, (_name, color) in zip(values, series):
            ax.barh(row + 0.68, n, left=left, height=0.26, color=color)
            left += n
        ax.text(top * 1.04, row + 0.68, f"{sum(values):,}", va="center", fontsize=8, fontweight="bold", color=NAVY)
    handles = [ax.bar(0, 0, color=color) for _name, color in series]
    ax.legend(handles, [name for name, _color in series], loc="upper center", bbox_to_anchor=(0.45, 0.02), ncol=len(series), frameon=False,
              fontsize=8, handlelength=0.9, handleheight=0.9, columnspacing=1.2, labelcolor=INK)


def doc_chart(doc, panels, height=2.75, widths=None):
    """One picture across the page holding one chart per panel; each panel is a function drawing on an axis."""
    fig = Figure(figsize=(page_width(doc) / 914400, height), dpi=200)
    FigureCanvasAgg(fig)
    grid = fig.add_gridspec(1, len(panels), width_ratios=widths or [1] * len(panels), left=0.02, right=0.99, top=0.86, bottom=0.2, wspace=0.22)
    with matplotlib.rc_context({"font.family": CHART_FONT["family"], "font.sans-serif": CHART_FONT["sans-serif"]}):
        for i, draw in enumerate(panels):
            draw(fig.add_subplot(grid[0, i]))
        stream = io.BytesIO()
        fig.savefig(stream, format="png", facecolor="white")
    stream.seek(0)
    doc.add_picture(stream, width=page_width(doc))
    doc.paragraphs[-1].alignment = WD_ALIGN_PARAGRAPH.CENTER
    doc.paragraphs[-1].paragraph_format.space_after = Pt(6)


RATINGS = {"Critical": ("#B02A2A", "#FFFFFF"), "Action": ("#FBDFDF", "#B02A2A"), "Attention": ("#FDF1CF", "#8A5A00"), "Good": ("#DDF3E4", "#146C36")}


def windows_client(w):
    """A Windows desktop OS (Windows 10/11), as opposed to Windows Server. The PCE's os_id says
    "win-...-client"; older VENs only give the product name in os_detail."""
    os_id, detail = (w["os"] or "").lower(), (w["os_detail"] or "").lower()
    if not os_id.startswith("win") and "windows" not in detail:
        return False
    return "client" in os_id or ("windows" in detail and "server" not in detail and "server" not in os_id)


def unsupported_vens(workloads):
    """Windows clients running the server VEN: not a supported deployment (they need the endpoint VEN)."""
    return sorted((w for w in workloads if w["managed"] and (w["ven_type"] or "").lower() == "server" and windows_client(w)),
                  key=lambda w: (w["hostname"] or "").lower())


def os_name(w):
    """The product name the VEN reports, e.g. "Windows 10 Enterprise LTSC 2024", else the os_id."""
    detail = w["os_detail"] or ""
    return detail[detail.rfind("(") + 1:-1] if detail.endswith(")") and "(" in detail else (w["os"] or "")


def assess(data):
    """Rule-based findings from the report's own numbers: (area, finding, rating, recommendation).
    Good means nothing is outstanding; Attention is work still to do; Action is something broken or late;
    Critical is an unsupported deployment that must be fixed first."""
    pol, workloads, labels = data["policy"], data["workloads"], data["labels"]
    t = pol["totals"]
    vens = [w for w in workloads if w["managed"]]
    out = []
    add = lambda area, finding, rating, advice: out.append((area, finding, rating, advice))  # noqa: E731
    names = lambda items, limit=5: ", ".join(items[:limit]) + (f" and {len(items) - limit} more" if len(items) > limit else "")  # noqa: E731
    if not workloads:
        add("Data", "No workload data has been read from the PCE.", "Action", "Sync from the PCE, then produce the report again.")
        return out

    full = sum(1 for w in vens if w["enforcement_mode"] == "full")
    rest = ", ".join(f"{sum(1 for w in vens if w['enforcement_mode'] == m):,} {name.lower()}" for m, name in MODE_NAMES.items() if m != "full")
    if vens and full == len(vens):
        add("Enforcement coverage", f"All {len(vens):,} VENs are in full enforcement.", "Good", "Keep new workloads of these applications in full enforcement as they are added.")
    else:
        add("Enforcement coverage", f"{full:,} of {len(vens):,} VENs ({(full / len(vens) if vens else 0):.0%}) are in full enforcement; {rest}.",
            "Attention", f"Move the remaining {len(vens) - full:,} VENs to full enforcement as their applications finish policy validation.")

    offline = [w["hostname"] for w in vens if not w["online"]]
    if offline:
        add("VEN connectivity", f"{len(offline):,} of {len(vens):,} VENs are offline: {names(sorted(offline))}.", "Action",
            "Restore connectivity to the PCE or unpair VENs of decommissioned hosts; offline VENs do not receive policy changes.")
    else:
        add("VEN connectivity", f"All {len(vens):,} VENs are online.", "Good", "No action needed.")

    wrong = unsupported_vens(workloads)
    if wrong:
        add("VEN type", f"{len(wrong):,} Windows client(s) have the server VEN installed, which is not supported: "
            f"{names([w['hostname'] for w in wrong])}.", "Critical",
            "Unpair the server VEN and install the endpoint VEN on these Windows clients.")
    else:
        add("VEN type", "No Windows client has the server VEN installed.", "Good", "No action needed.")

    unmanaged = len(workloads) - len(vens)
    if unmanaged:
        add("Unmanaged workloads", f"{unmanaged:,} of {len(workloads):,} workloads have no VEN, so the PCE cannot enforce policy on them.", "Attention",
            "Install a VEN where the platform supports it, or confirm these are meant to stay unmanaged.")
    else:
        add("Unmanaged workloads", "Every workload is managed by a VEN.", "Good", "No action needed.")


    unlabelled = sum(1 for w in workloads if not w["application"] or not w["environment"])
    if unlabelled:
        add("Workload labelling", f"{unlabelled:,} of {len(workloads):,} workloads lack an application or environment label.", "Attention",
            "Label these workloads; scoped rulesets and this tracker only see workloads that carry both labels.")
    else:
        add("Workload labelling", "Every workload has an application and an environment label.", "Good", "No action needed.")

    if labels:
        unused = [l["value"] for l in labels if not l["used_by"]]
        if unused:
            add("Unused labels", f"{len(unused):,} of {len(labels):,} labels are not used anywhere: {names(unused)}.", "Attention",
                "Review the unused labels listed in the Label Summary and delete those that are no longer needed.")
        else:
            add("Unused labels", f"All {len(labels):,} labels are in use.", "Good", "No action needed.")

    if (pol["info"].get("rules") or {}).get("synced_at"):
        if t["rules_pending"]:
            add("Policy provisioning", f"{t['rules_pending']:,} draft rule changes are not provisioned, so they are not enforced yet.", "Attention",
                "Review the draft changes in the PCE and provision them, or revert the ones that are not wanted.")
        else:
            add("Policy provisioning", f"All {t['rules_draft']:,} draft rules are provisioned.", "Good", "No action needed.")
        bare = [r["scope"] for r in pol["rows"] if r["tracked"] and not r["rulesets"] and r["pce_state"] != "No Managed Workloads"]
        if bare:
            add("Policy coverage", f"{len(bare):,} scope(s) with VENs have no ruleset of their own: {names(bare)}.", "Attention",
                "Write scoped rulesets for these applications before moving them to enforcement, or confirm global rulesets cover them.")
        else:
            add("Policy coverage", "Every tracked scope with VENs has a ruleset of its own.", "Good", "No action needed.")

    tasks, _preds = task_rows(data["tl"], data["projects"])
    leaves = [r for r in tasks if r["kind"] == "task"]
    if leaves:
        today = datetime.date.today().isoformat()
        late = [r["name"] for r in leaves if r["end"] and r["end"] < today and r["status"] != "Completed"]
        done = sum(1 for r in leaves if r["status"] == "Completed")
        if late:
            add("Project plan", f"{len(late):,} of {len(leaves):,} tasks are past their end date and not completed: {names(late)}.", "Action",
                "Replan the overdue tasks or close them out in the project plan.")
        else:
            add("Project plan", f"No task is overdue; {done:,} of {len(leaves):,} tasks are completed.", "Good", "No action needed.")
    return out


LOGO_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "logo.png")


def logo_png(color=ORANGE):
    """The logo for the cover page. static/logo.png is used when it exists (drop the official artwork
    there); otherwise the tracker's own mark, the one in the app's sidebar, is drawn in `color`."""
    if os.path.exists(LOGO_FILE):
        return LOGO_FILE
    fig = Figure(figsize=(2, 2), dpi=220)
    FigureCanvasAgg(fig)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(2, 30)
    ax.set_ylim(30, 2)
    ax.axis("off")
    for x0, x1, x2 in ((4, 12, 7.5), (28, 20, 24.5)):   # the two brackets
        ax.fill([x0, x1, x1, x2, x2, x1, x1, x0], [4, 4, 7.5, 7.5, 24.5, 24.5, 28, 28], color=color, linewidth=0)
    for xs in ((11, 21), (21, 11)):                      # the cross between them
        ax.plot(xs, (11, 21), color=color, linewidth=13, solid_capstyle="round")
    stream = io.BytesIO()
    fig.savefig(stream, format="png", transparent=True)
    stream.seek(0)
    return stream


BLACK = "#000000"
# Cover page colours: a white page with the logo, wordmark and accents in Illumio orange and the type in navy.
COVER = {"page": "#FFFFFF", "accent": ORANGE, "brand": ORANGE, "title": NAVY, "text": NAVY, "label": MUTED, "empty": "#B6BEC9", "line": LINE}


def cover_background(width, height):
    """The cover page backdrop as a PNG the size of the page: the page colour, with an accent bar down
    the left edge and an accent line along the bottom. Both sit in the page margins, clear of the text."""
    fig = Figure(figsize=(width, height), dpi=110)
    FigureCanvasAgg(fig)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, width)
    ax.set_ylim(0, height)
    ax.axis("off")
    ax.fill([0, width, width, 0], [0, 0, height, height], color=COVER["page"], linewidth=0)
    ax.fill([0, 0.2, 0.2, 0], [0, 0, height, height], color=COVER["accent"], linewidth=0)
    ax.fill([0.9, width - 0.6, width - 0.6, 0.9], [0.36, 0.36, 0.39, 0.39], color=COVER["accent"], linewidth=0)
    stream = io.BytesIO()
    fig.savefig(stream, format="png", facecolor=COVER["page"])
    stream.seek(0)
    return stream


def behind_page(run):
    """Turn the inline picture in `run` into one fixed to the top-left corner of the page, behind the text."""
    inline = run._r.find(".//" + qn("wp:inline"))
    anchor = OxmlElement("wp:anchor")
    for attr, value in (("distT", "0"), ("distB", "0"), ("distL", "0"), ("distR", "0"), ("simplePos", "0"), ("relativeHeight", "0"),
                        ("behindDoc", "1"), ("locked", "1"), ("layoutInCell", "1"), ("allowOverlap", "1")):
        anchor.set(attr, value)
    origin = OxmlElement("wp:simplePos")
    origin.set("x", "0")
    origin.set("y", "0")
    anchor.append(origin)
    for tag in ("wp:positionH", "wp:positionV"):
        position = OxmlElement(tag)
        position.set("relativeFrom", "page")
        offset = OxmlElement("wp:posOffset")
        offset.text = "0"
        position.append(offset)
        anchor.append(position)
    kept = {child.tag: child for child in inline}
    for tag in ("wp:extent", "wp:effectExtent"):
        if qn(tag) in kept:
            anchor.append(kept[qn(tag)])
    anchor.append(OxmlElement("wp:wrapNone"))
    for tag in ("wp:docPr", "wp:cNvGraphicFramePr", "a:graphic"):
        anchor.append(kept[qn(tag)])
    inline.getparent().replace(inline, anchor)


def fmt_date(iso):
    return day(iso).strftime("%b %-d, %Y") if iso else "—"


def lead(doc, text):
    """The sentence under a section heading that says what the section shows."""
    par = doc.add_paragraph(text)
    par.paragraph_format.space_after = Pt(10)
    par.runs[0].font.size = Pt(10.5)
    par.runs[0].font.color.rgb = rgb("#374151")


def callout(doc, title, lines, accent=ORANGE, fill=ZEBRA, bullets=True):
    """A tinted box with a coloured bar on its left: a title, then one bullet (or plain paragraph) per line."""
    table, widths = fixed_table(doc, 1, [1])
    cell = table.rows[0].cells[0]
    par = write_cell(cell, title, widths[0], size=11, bold=True, color=NAVY)
    par.paragraph_format.space_before = Pt(6)
    par.paragraph_format.space_after = Pt(4)
    par.paragraph_format.left_indent = Inches(0.15)
    for n, line in enumerate(lines):
        par = cell.add_paragraph()
        par.paragraph_format.left_indent = Inches(0.15)
        par.paragraph_format.space_after = Pt(6 if n == len(lines) - 1 else 3)
        head, _sep, rest = line.partition(": ")
        if not bullets:
            par.add_run(line).font.size = Pt(10)
        elif rest:
            run = par.add_run(f"•  {head}: ")
            run.bold = True
            run.font.size = Pt(10)
            par.add_run(rest).font.size = Pt(10)
        else:
            par.add_run(f"•  {line}").font.size = Pt(10)
    style_cell(cell, fill, left=(36, accent))
    doc.add_paragraph().paragraph_format.space_after = Pt(2)


def progress_bar(percent, blocks=12):
    done = round(percent / 100 * blocks)
    return "█" * done + "░" * (blocks - done)


FORM_LINE = "#9AA5B6"   # form tables get darker lines than data tables, so empty cells still read as boxes


def form_fields(doc, rows, height=0.34):
    """Label and empty-cell pairs to fill in; `rows` are lists of labels, two or three to a row.
    A (label, value) pair instead of a label fills that cell in from the report's own data."""
    rows = [[item if isinstance(item, tuple) else (item, "") for item in row] for row in rows]
    per_row = max(len(r) for r in rows)
    table, widths = fixed_table(doc, per_row * 2, [1.3, 2.0] * per_row)
    grid_lines(table, FORM_LINE)
    for n, labels in enumerate(rows):
        row = table.rows[0] if n == 0 else table.add_row()
        row.height = Inches(height)
        for i in range(per_row):
            label, value = labels[i] if i < len(labels) else ("", "")
            write_cell(row.cells[i * 2], label, widths[i * 2], bold=True, color=NAVY)
            style_cell(row.cells[i * 2], ZEBRA)
            write_cell(row.cells[i * 2 + 1], value, widths[i * 2 + 1], size=10)
        for cell in row.cells:
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
    doc.add_paragraph().paragraph_format.space_after = Pt(2)


def form_box(doc, height, text=""):
    """A bordered box for free text: empty to write in, or holding what the consultant entered."""
    table, widths = fixed_table(doc, 1, [1])
    grid_lines(table, FORM_LINE)
    table.rows[0].height = Inches(height)
    cell = table.rows[0].cells[0]
    for n, line in enumerate(text.split("\n") if text else [""]):
        par = write_cell(cell, line, widths[0], size=10) if n == 0 else cell.add_paragraph()
        if n:
            par.add_run(line).font.size = Pt(10)
        par.paragraph_format.space_after = Pt(4)
    doc.add_paragraph().paragraph_format.space_after = Pt(2)


def form_grid(doc, headers, widths, blank_rows, height=0.42, numbered=True, filled=None, badges=None):
    """A table with the usual navy header and rows to fill in. `filled` are rows the consultant already
    entered (without the number column); when there are any, no empty rows are added."""
    if filled:
        rows = [([n + 1] if numbered else []) + list(values) for n, values in enumerate(filled)]
    else:
        rows = [[n + 1 if numbered else ""] + [""] * (len(headers) - 1) for n in range(blank_rows)]
    table = doc_table(doc, headers, rows, widths, tints={n: "#FFFFFF" for n in range(len(rows))}, colors={0: MUTED}, badges=badges,
                      size=9)
    grid_lines(table, FORM_LINE)
    for row in table.rows[1:]:
        row.height = Inches(height)
    return table


def doc_theme(doc, data, preview=False):
    """Landscape page, Illumio colours on the headings, and a footer with the page number on every
    page but the cover. The in-app preview leaves the page number out: browsers cannot work it out."""
    section = doc.sections[0]
    section.orientation = WD_ORIENT.LANDSCAPE
    section.page_width, section.page_height = section.page_height, section.page_width
    for side in ("left_margin", "right_margin", "top_margin", "bottom_margin"):
        setattr(section, side, Inches(0.6))
    doc.styles["Normal"].font.name = "Calibri"
    doc.styles["Normal"].font.size = Pt(10)
    doc.styles["Normal"].font.color.rgb = rgb(INK)
    for name, size, color, before in (("Heading 1", 20, NAVY, 0), ("Heading 2", 13, ORANGE, 14), ("Heading 3", 11, NAVY, 10)):
        style = doc.styles[name]
        style.font.name = "Calibri"
        style.font.size = Pt(size)
        style.font.bold = True
        style.font.color.rgb = rgb(color)
        style.paragraph_format.space_before = Pt(before)
        style.paragraph_format.space_after = Pt(8 if name == "Heading 1" else 6)
        fonts = style.element.rPr.find(qn("w:rFonts"))
        for attr in ("asciiTheme", "hAnsiTheme"):   # the template's theme font would win over the name set above
            fonts.attrib.pop(qn(f"w:{attr}"), None)
        # The template's linked character style ("Heading 1 Char") keeps its own blue; Word ignores it for
        # these paragraphs, but other viewers (the in-app preview) apply it, so give it the same colour.
        if f"{name} Char" in [st.name for st in doc.styles]:
            doc.styles[f"{name} Char"].font.color.rgb = rgb(color)
    rule = OxmlElement("w:pBdr")   # orange rule under every section heading
    line = OxmlElement("w:bottom")
    for attr, value in (("val", "single"), ("sz", "12"), ("space", "3"), ("color", ORANGE.lstrip("#"))):
        line.set(qn(f"w:{attr}"), value)
    rule.append(line)
    doc.styles["Heading 1"].element.pPr.find(qn("w:spacing")).addprevious(rule)

    doc.styles["Heading 1"].paragraph_format.page_break_before = True   # each section opens a page

    header = section.header.paragraphs[0]
    for text, size, color, bold in (("illumio", 12, ORANGE, True), ("    Enforcement Summary Report", 9, MUTED, False)):
        run = header.add_run(text)
        run.font.size, run.bold = Pt(size), bold
        run.font.color.rgb = rgb(color)
    edge = OxmlElement("w:pBdr")
    under = OxmlElement("w:bottom")
    for attr, value in (("val", "single"), ("sz", "6"), ("space", "6"), ("color", GRID.lstrip("#"))):
        under.set(qn(f"w:{attr}"), value)
    edge.append(under)
    header._p.get_or_add_pPr().append(edge)

    footer = section.footer.paragraphs[0]
    footer.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    customer = data["consultant"]["customer"]
    footer.add_run((f"Prepared for {customer}  ·  " if customer else "") + f"PCE {data['pce']}" + ("" if preview else "  ·  Page "))
    if not preview:
        field(footer, "PAGE", None, wrap=True)
        footer.add_run(" of ")
        field(footer, "NUMPAGES", None, wrap=True)
    for run in footer.runs:
        run.font.size = Pt(8)
        run.font.color.rgb = rgb(MUTED)
    section.different_first_page_header_footer = True   # neither on the cover page


def field(par, code, kind, wrap=False):
    """One piece of a Word field in its own run: "begin", "separate" and "end" markers, or the field code.
    `wrap` writes a whole simple field (begin, code, end) such as PAGE."""
    if wrap:
        field(par, None, "begin")
        field(par, f" {code} ", None)
        return field(par, None, "end")
    run = par.add_run()
    part = OxmlElement("w:fldChar" if kind else "w:instrText")
    if kind:
        part.set(qn("w:fldCharType"), kind)
    else:
        part.set(qn("xml:space"), "preserve")
        part.text = code
    run._r.append(part)
    return run


def doc_front(doc, details, preview=False):
    """Cover page and table of contents, written last (the contents list the headings that exist) and
    then moved ahead of the first section. For the in-app preview the full-page picture behind the
    cover is left out (browsers draw it over the text; the cover cells carry the same colours) and the
    contents start with a hard page break, which the browser renderer follows."""
    body = doc.element.body
    first, mark = body[0], len(body) - 1   # new elements are added just before the closing section properties
    headings = [(p.style.name, p.text) for p in doc.paragraphs if p.style.name in ("Heading 1", "Heading 2")]

    # ---- cover, in the COVER colours. The page colour comes twice: a full-page picture behind the
    # text (edge to edge, with the accent bars) and the fill of the cells (so the text stays readable
    # in viewers that do not draw that picture).
    # The picture hangs off a paragraph of the cover itself, not the page header: Word shows header
    # content dimmed on screen, which would fade the accent bars and any page colour.
    section = doc.sections[0]
    if not preview:
        holder = doc.add_paragraph()
        holder.paragraph_format.space_after = Pt(0)
        backdrop = holder.add_run()
        backdrop.font.size = Pt(1)
        backdrop.add_picture(cover_background(section.page_width / 914400, section.page_height / 914400),
                             width=section.page_width, height=section.page_height)
        behind_page(backdrop)

    cover, widths = fixed_table(doc, 1, [1])
    cover.rows[0].height = Inches(4.0)
    cell = cover.rows[0].cells[0]
    cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.BOTTOM
    for n, (text, size, color, bold) in enumerate((("  illumio", 46, COVER["brand"], True), ("Enforcement Summary Report", 32, COVER["title"], True),
                                                   ("Plan. Track. Enforce.", 14, COVER["label"], False))):
        par = write_cell(cell, "", widths[0]) if n == 0 else cell.add_paragraph()
        if n == 0:
            par.add_run().add_picture(logo_png(COVER["brand"]), height=Inches(0.62))
        run = par.add_run(text)
        run.font.size, run.bold = Pt(size), bold
        run.font.color.rgb = rgb(color)
        par.paragraph_format.left_indent = Inches(0.3)
        par.paragraph_format.space_after = Pt(6 if n < 2 else 22)
    style_cell(cell, COVER["page"], bottom=(36, COVER["accent"]))
    gap = doc.add_paragraph()
    gap.paragraph_format.space_after = Pt(14)

    per_row = max(len(row) for row in details)
    facts, widths = fixed_table(doc, per_row, [1] * per_row)
    for n, items in enumerate(details):
        row = facts.rows[0] if n == 0 else facts.add_row()
        for i, cell in enumerate(row.cells):
            label, value = (items[i] if isinstance(items[i], tuple) else (items[i], "")) if i < len(items) else ("", "")
            par = write_cell(cell, label.upper(), widths[i], size=7.5, bold=True, color=COVER["accent"])
            par.paragraph_format.left_indent = Inches(0.3)
            par.paragraph_format.space_before = Pt(8)
            par.paragraph_format.space_after = Pt(2)
            par = cell.add_paragraph()
            par.paragraph_format.left_indent = Inches(0.3)
            par.paragraph_format.space_after = Pt(8)
            run = par.add_run(str(value) if value else "—")
            run.font.size, run.bold = Pt(12), bool(value)
            run.font.color.rgb = rgb(COVER["text"] if value else COVER["empty"])
            style_cell(cell, COVER["page"], bottom=(4, COVER["line"]))
    tail = doc.add_paragraph()
    tail.paragraph_format.space_before = Pt(14)
    tail.paragraph_format.left_indent = Inches(0.3)
    run = tail.add_run("Generated by the Illumio Enforcement Tracker from the PCE data of the last sync.")
    run.font.size, run.italic = Pt(8), True
    run.font.color.rgb = rgb(COVER["label"])

    # ---- contents: a real Word field, so page numbers are filled in when the document is opened
    if preview:
        doc.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
    title = doc.add_paragraph()
    title.paragraph_format.page_break_before = not preview
    title.paragraph_format.space_after = Pt(10)
    run = title.add_run("Table of Contents")
    run.font.size, run.bold = Pt(16), True
    run.font.color.rgb = rgb(NAVY)
    rule = OxmlElement("w:pBdr")
    line = OxmlElement("w:bottom")
    for attr, value in (("val", "single"), ("sz", "12"), ("space", "3"), ("color", ORANGE.lstrip("#"))):
        line.set(qn(f"w:{attr}"), value)
    rule.append(line)
    title._p.pPr.find(qn("w:spacing")).addprevious(rule)
    # Until Word refreshes the field, the list below is what shows (without page numbers).
    par = None
    for n, (style, text) in enumerate(headings):
        par = doc.add_paragraph()
        par.paragraph_format.space_after = Pt(3 if style == "Heading 2" else 5)
        if n == 0:
            field(par, None, "begin")
            field(par, ' TOC \\o "1-2" \\h \\z \\u ', None)
            field(par, None, "separate")
        run = par.add_run(text)
        if style == "Heading 1":
            par.paragraph_format.space_before = Pt(6)
            run.bold = True
            run.font.color.rgb = rgb(NAVY)
        else:
            par.paragraph_format.left_indent = Inches(0.3)
    if par is not None:
        field(par, None, "end")
    refresh = OxmlElement("w:updateFields")   # ask Word to rebuild the contents, with page numbers, on opening
    refresh.set(qn("w:val"), "true")
    settings = doc.settings.element
    after = next((el for tag in ("hdrShapeDefaults", "footnotePr", "endnotePr", "compat", "docVars", "rsids")
                  for el in settings.findall(qn(f"w:{tag}"))), None)
    if after is not None:
        after.addprevious(refresh)
    else:
        settings.append(refresh)

    for element in list(body)[mark:-1]:
        first.addprevious(element)


LABEL_GAPS = (("No labels", "Workloads with No Labels", "", "No rule can select these workloads by label."),
              ("Role label only", "Workloads with Only a Role Label", "role",
               "Without an application and environment label these workloads fall outside every application scope."),
              ("Application label only", "Workloads with Only an Application Label", "app",
               "Without environment and role labels, rules cannot tell these workloads apart within the application."))
LABEL_GAP_LIMIT = 1000   # rows listed per category; the count is always complete


def unsupported_section(doc, wrong):
    """Section 2: Windows clients running the server VEN, flagged Critical and listed with their labels."""
    doc.add_heading("Unsupported VEN Deployments", level=2)
    if not wrong:
        callout(doc, "No unsupported VEN deployments", ["No Windows client has the server VEN installed."], accent="#17994F")
        return
    callout(doc, f"Critical: {len(wrong):,} Windows client(s) with the server VEN", [
        "The server VEN is supported on Windows Server. Windows desktop operating systems (Windows 10 and 11) need the endpoint VEN.",
        "Unpair the server VEN and pair these workloads with the endpoint VEN before moving them further towards enforcement.",
    ], accent="#B02A2A", fill="#FBDFDF", bullets=False)
    doc_table(doc, ["Hostname", "Operating System", "VEN Type", "VEN Version", "Application", "Environment", "Location", "Role", "Severity"],
              [[w["hostname"] or "(no hostname)", os_name(w), (w["ven_type"] or "").capitalize(), w["ven_version"] or "",
                w["app_label"] or "—", w["env_label"] or "—", w["location"] or "—", w["role"] or "—", "Critical"] for w in wrong],
              [1.4, 1.9, 0.7, 0.9, 1.2, 1.0, 0.9, 0.9, 0.8], badges={8: RATINGS})


def workload_labelling(doc, workloads):
    """Section 2: the workloads whose labels leave them out of policy - none at all, or only a role or
    only an application label - each listed by hostname."""
    doc.add_heading("Workload Labelling", level=2)
    if any(w["label_keys"] is None for w in workloads):
        doc.add_paragraph("Label assignments are read at the next Sync from PCE; sync again to list these workloads.")
        return
    groups = [(name, title, [w for w in workloads if w["label_keys"] == keys], why) for name, title, keys, why in LABEL_GAPS]
    doc_table(doc, ["Category", "Workloads", "Share", "Why it matters"],
              [[name, len(items), f"{(len(items) / len(workloads) if workloads else 0):.1%}", why] for name, _title, items, why in groups],
              [2.4, 1.0, 1.0, 5.4], right=(1, 2), colors={1: NAVY})
    for name, title, items, _why in groups:
        doc.add_heading(f"{title} ({len(items):,})", level=3)
        if not items:
            doc.add_paragraph("None.")
            continue
        label = {"Role label only": ("Role", "role"), "Application label only": ("Application", "app_label")}.get(name)
        items = sorted(items, key=lambda w: (w["hostname"] or "").lower())
        rows = [[w["hostname"] or "(no hostname)"] + ([w[label[1]]] if label else [])
                + ["VEN" if w["managed"] else "Unmanaged",
                   (w["enforcement_mode"] or "").replace("_", " ").capitalize() if w["managed"] else "",
                   ("Online" if w["online"] else "Offline") if w["managed"] else "", w["os"] or ""]
                for w in items[:LABEL_GAP_LIMIT]]
        doc_table(doc, ["Hostname"] + ([label[0]] if label else []) + ["Managed", "Enforcement", "Status", "OS"], rows,
                  [3.0] + ([1.8] if label else []) + [1.0, 1.3, 0.9, 1.8 if label else 3.6])
        if len(items) > LABEL_GAP_LIMIT:
            note(doc, f"The first {LABEL_GAP_LIMIT:,} of {len(items):,} workloads are listed, by hostname.")


# Ports usually closed first with Selective Enforcement (an enforcement boundary on these ports while the
# rest of the traffic stays in visibility): remote access and lateral movement, the paths ransomware takes.
# Always listed in the report.
SELECTIVE_PORTS = (
    ("RDP", ((3389, 6), (3389, 17)), "Remote desktop; the most common way attackers move between Windows hosts."),
    ("SMB", ((445, 6),), "File sharing; ransomware uses it to spread and to encrypt shares."),
    ("MS-RPC", ((135, 6),), "Remote administration (PsExec, WMI, remote services); widely used for lateral movement."),
    ("WinRM", ((5985, 6), (5986, 6)), "Remote PowerShell; should only come from admin hosts."),
    ("NetBIOS", ((137, 17), (138, 17), (139, 6)), "Legacy name and session services; rarely needed between servers."),
    ("SSH", ((22, 6),), "Remote shell on Linux and Unix; limit to jump hosts and automation."),
    ("VNC", ((5900, 6),), "Remote desktop, often without strong authentication."),
    ("Telnet", ((23, 6),), "Unencrypted remote shell; should not be in use."),
    ("FTP", ((20, 6), (21, 6)), "Unencrypted file transfer and credentials."),
)
# Other risky ports, listed only when the last sync found blocked or potentially blocked traffic on them:
# data stores, management planes and legacy services that should only be reached by known clients.
RISKY_PORTS = (
    ("VNC (more displays)", ((5901, 6), (5902, 6), (5903, 6)), "Further VNC remote desktop sessions."),
    ("rsh / rexec / rlogin", ((512, 6), (513, 6), (514, 6)), "Legacy remote shells with no encryption and weak authentication."),
    ("TFTP", ((69, 17),), "Unauthenticated file transfer; used to pull tools and configurations."),
    ("SNMP", ((161, 17), (162, 17)), "Device management; v1 and v2c send community strings in clear text."),
    ("LLMNR", ((5355, 17),), "Name resolution fallback; spoofed replies capture credentials (like NetBIOS)."),
    ("LDAP", ((389, 6), (389, 17), (636, 6)), "Directory queries; attackers enumerate users, groups and admins with it."),
    ("Global Catalog", ((3268, 6), (3269, 6)), "Forest-wide directory queries; same exposure as LDAP."),
    ("SMTP", ((25, 6),), "Mail relay; only mail servers should send or accept it."),
    ("rpcbind / NFS", ((111, 6), (111, 17), (2049, 6)), "Unix RPC and file shares; often exported more widely than intended."),
    ("X11", ((6000, 6),), "Remote display; lets a remote host capture the screen and keystrokes."),
    ("MS SQL Server", ((1433, 6), (1434, 17)), "Database; should only be reached from its application tier."),
    ("Oracle", ((1521, 6),), "Database listener; should only be reached from its application tier."),
    ("MySQL", ((3306, 6),), "Database; should only be reached from its application tier."),
    ("PostgreSQL", ((5432, 6),), "Database; should only be reached from its application tier."),
    ("Redis", ((6379, 6),), "In-memory store, often without authentication; a common foothold."),
    ("Memcached", ((11211, 6), (11211, 17)), "Cache with no authentication; exposes cached data."),
    ("MongoDB", ((27017, 6),), "Database, often deployed without authentication."),
    ("Elasticsearch", ((9200, 6), (9300, 6)), "Search cluster, often without authentication; exposes indexed data."),
    ("Docker API", ((2375, 6), (2376, 6)), "Container engine API; access gives root on the host."),
    ("Kubernetes API", ((6443, 6),), "Cluster control plane; only admins and cluster nodes need it."),
    ("Kubelet", ((10250, 6),), "Node agent API; can run commands in any container on the node."),
    ("etcd", ((2379, 6), (2380, 6)), "Kubernetes state store, including secrets."),
)
# What each service in SELECTIVE_PORTS and RISKY_PORTS does, in a few words, for the report.
PORT_PURPOSE = {
    "RDP": "Remote Desktop: log on to a Windows host's desktop over the network.",
    "SMB": "Windows file and printer sharing; also used by Group Policy and remote admin tools.",
    "MS-RPC": "Windows RPC endpoint mapper: starts remote services such as WMI, DCOM and service control.",
    "WinRM": "Windows Remote Management: remote PowerShell and scripted administration.",
    "NetBIOS": "Legacy Windows name lookup, browsing and file-sharing sessions.",
    "SSH": "Encrypted remote shell, file copy (SCP/SFTP) and tunnels, mostly on Linux and Unix.",
    "VNC": "Remote control of a host's screen, keyboard and mouse.",
    "Telnet": "Plain-text remote command line.",
    "FTP": "File transfer with user name and password in plain text.",
    "VNC (more displays)": "Extra VNC remote-control sessions (displays :1 to :3).",
    "rsh / rexec / rlogin": "Legacy Unix remote shell, command execution and login.",
    "TFTP": "Simple file transfer with no login, used for network boot and device configuration.",
    "SNMP": "Monitoring and management of network devices and servers.",
    "LLMNR": "Windows name lookup on the local network when DNS has no answer.",
    "LDAP": "Directory queries and logons against Active Directory or other directories.",
    "Global Catalog": "Active Directory searches across every domain in the forest.",
    "SMTP": "Sending and relaying email between mail servers.",
    "rpcbind / NFS": "Unix RPC service lookup and NFS network file shares.",
    "X11": "Unix remote graphical display.",
    "MS SQL Server": "Microsoft SQL Server database connections (1434: instance lookup).",
    "Oracle": "Oracle database listener: client connections to the database.",
    "MySQL": "MySQL and MariaDB database connections.",
    "PostgreSQL": "PostgreSQL database connections.",
    "Redis": "Redis in-memory data store and cache.",
    "Memcached": "Memcached in-memory cache.",
    "MongoDB": "MongoDB document database connections.",
    "Elasticsearch": "Elasticsearch search API (9200) and cluster traffic (9300).",
    "Docker API": "Remote control of the Docker container engine.",
    "Kubernetes API": "Kubernetes control plane: kubectl and cluster components.",
    "Kubelet": "Kubernetes node agent API: runs and inspects the containers on a node.",
    "etcd": "Kubernetes key-value store: client (2379) and peer (2380) traffic.",
}
PROTO_NAMES = {6: "TCP", 17: "UDP"}


def selective_ports(doc, data):
    """Section 6: the risky ports to put under Selective Enforcement. The core remote-access ports are
    always listed; other risky ports are listed when the last sync found traffic on them. Every port is
    reviewed before it goes into the boundary; the next step depends on whether a boundary on it would
    block anything (potentially blocked connections)."""
    doc.add_heading("Suggested Selective Enforcement Ports", level=2)
    traffic = data["policy"]["info"].get("traffic") or {}
    synced = bool(traffic.get("synced_at") and traffic.get("ports"))
    found = lambda ports: any(p in data["ports"] or p in data["ports_blocked"] for p in ports)
    listed = [(entry, False) for entry in SELECTIVE_PORTS] + [(entry, True) for entry in RISKY_PORTS if synced and found(entry[1])]
    rows, clear, needs_rules = [], [], []
    badges = {"Review first": RATINGS["Attention"], "Sync first": ("#EEF0F4", "#6B7280")}
    for (service, ports, why), _extra in listed:
        label = ", ".join(f"{PROTO_NAMES[proto]} {port}" for port, proto in ports)
        connections = sum(data["ports"][p]["connections"] for p in ports if p in data["ports"])
        blocked = sum(data["ports_blocked"].get(p, 0) for p in ports)
        if not synced:
            rows.append([label, service, PORT_PURPOSE.get(service, ""), why, "—", "Sync first", "Sync from PCE to see what this port carries."])
            continue
        seen_any = "Yes" if connections or blocked else "No"
        if not connections:
            clear.append(service)
            rows.append([label, service, PORT_PURPOSE.get(service, ""), why, seen_any, "Review first",
                         "Confirm with the application owners that nothing needs this port, then add it to the enforcement "
                         "boundary: no connection on it would be blocked."])
        else:
            needs_rules.append(service)
            rows.append([label, service, PORT_PURPOSE.get(service, ""), why, seen_any, "Review first",
                         f"Write allow rules for the connections that are needed (see Explorer, {label}), then add the port "
                         "to the boundary."])
    note(doc, "Selective Enforcement blocks only the ports in an enforcement boundary and leaves the rest of the traffic in "
              "visibility, so these risky ports can be closed before full enforcement. Traffic that an allow rule permits "
              "keeps flowing. The first rows are the remote-access and lateral-movement ports usually closed first; the "
              "others are risky ports (data stores, management planes, legacy services) found in this "
              "PCE's traffic. Seen means traffic was found on the port"
              + (f" in the last {traffic.get('days', policy.TRAFFIC_DAYS)} days" if synced else "")
              + ". Review every port before adding it to the boundary.")
    if synced:
        extra = len(listed) - len(SELECTIVE_PORTS)
        callout(doc, f"{len(listed)} risky services identified ({len(SELECTIVE_PORTS)} core, {extra} more found in traffic)",
                [f"No connection would be blocked, confirm and add: {', '.join(clear) or 'none'}",
                 f"Connections would be blocked, write allow rules first: {', '.join(needs_rules) or 'none'}"])
    doc_table(doc, ["Port", "Service", "What it does", "Why close it", "Seen", "Suggestion", "Next Step"],
              rows, [1.1, 0.95, 2.3, 2.4, 0.55, 0.9, 2.6],
              badges={4: {"Yes": RATINGS["Attention"], "No": ("#EEF0F4", "#6B7280")}, 5: badges})


ATTENTION_PORTS = (
    ("Windows Delivery Optimization (WDO)", ((7680, 6),),
     "Windows 10 and 11 service that shares Windows Update and Microsoft Store downloads peer to peer between "
     "PCs on the network. It is on by default.",
     "It creates a lot of east-west traffic between workloads that servers rarely need, so it shows up as "
     "potentially blocked noise in Explorer and pulls effort into rules nobody wants. Any host can also serve "
     "update content to its peers, which widens what each workload exposes.",
     "Do not write allow rules for it between servers. Set Delivery Optimization to HTTP only (Download Mode 0 or 99) "
     "with Group Policy or Intune, or let enforcement block it. If peer caching is wanted, allow it only between "
     "clients in the same site."),
    ("Multicast DNS (mDNS)", ((5353, 17),),
     "Zero-configuration name resolution and service discovery (Bonjour, Avahi, and Windows 10 and later by "
     "default), sent as multicast to 224.0.0.251 and ff02::fb.",
     "Every host announces itself to the whole segment, which is noise in the traffic data and tells an attacker "
     "host names and services. Like LLMNR and NetBIOS, its replies can be spoofed to capture credentials. Data "
     "centre workloads use DNS, so it is rarely needed.",
     "Turn it off on servers (Windows: EnableMDNS = 0 under HKLM\\SYSTEM\\CurrentControlSet\\Services\\Dnscache\\Parameters; "
     "Linux: stop and disable avahi-daemon). Do not write allow rules for it; blocking it is safe where DNS is used."),
)


def attention_ports(doc, data):
    """Section 6: services that are on by default but rarely needed, and how to deal with them."""
    doc.add_heading("Services and Ports That Need Attention", level=2)
    note(doc, "These services are often running without anyone needing them. They add noise to the traffic data and to "
              "rule writing, and they widen the attack surface. Deal with them at the source rather than with allow rules.")
    rows = [[", ".join(f"{PROTO_NAMES[proto]} {port}" for port, proto in ports), service, what, why, action]
            for service, ports, what, why, action in ATTENTION_PORTS]
    doc_table(doc, ["Port", "Service", "What it is", "Why it needs attention", "Recommendation"],
              rows, [0.85, 1.1, 2.4, 3.0, 2.85])


def build_docx(data, preview=False):
    """The enforcement summary as an Illumio-themed Word document for the customer: cover, contents,
    executive summary, the PCE, VENs and workloads, labels, policy and blocked traffic by scope, the
    applications, an assessment, and the consultant's recommendations (as entered on the Reports page,
    or as an empty form)."""
    doc = docx.Document()
    doc_theme(doc, data, preview)
    pol, conf, consultant = data["policy"], data["conf"], data["consultant"]
    info, t = pol["info"], pol["totals"]
    pce = info.get("pce") or {}
    types = pce.get("label_types") or {}
    workloads, labels = data["workloads"], data["labels"]
    vens = [w for w in workloads if w["managed"]]
    unused = [l for l in labels if not l["used_by"]]
    active = [a for a in data["apps"] if not a["excluded"]]
    scopes = [(a, e) for a in active for e in ([e for e in a["environments"] if not e["excluded"]] or [None])]
    projects = ", ".join(p["name"] for p in data["projects"]) or "All Projects"
    synced = datetime.datetime.strptime(data["last_sync"], "%Y-%m-%dT%H:%M:%SZ") if data["last_sync"] else None
    synced_text = synced.strftime("%b %-d, %Y %-I:%M %p UTC") if synced else "Never"
    share = lambda n, total: f"{(n / total if total else 0):.1%}"  # noqa: E731
    by_key = lambda key: sum(1 for l in labels if l["key"] == key)  # noqa: E731
    in_mode = lambda mode: sum(1 for w in vens if w["enforcement_mode"] == mode)  # noqa: E731
    with_status = lambda s_: sum(1 for a in active if a["status"] == s_)  # noqa: E731
    suspended = sum(1 for w in vens if w["ven_status"] == "suspended")
    online = sum(1 for w in vens if w["online"] and w["ven_status"] != "suspended")
    offline = len(vens) - online - suspended
    findings = assess(data)
    mode_parts = [(name, in_mode(mode), MODE_COLORS[mode]) for mode, name in MODE_NAMES.items()]
    status_parts = [(s_, with_status(s_), STATUS_COLOR[s_]) for s_ in APP_STATUSES]
    env_progress = {}
    for _a, e in scopes:
        if e:
            env_progress.setdefault(e["name"], []).append(e["progress"])
    completion = [(name, round(sum(v) / len(v))) for name, v in sorted(env_progress.items())]

    # ---- Executive summary
    doc.add_heading("Executive Summary", level=1)
    lead(doc, f"This report summarises the Illumio enforcement programme on PCE {data['pce']} as of {synced_text}. "
              f"{in_mode('full'):,} of {len(vens):,} VENs ({share(in_mode('full'), len(vens))}) are in full enforcement, and "
              f"{with_status('Fully Enforced'):,} of {len(active):,} tracked applications are fully enforced.")
    doc_tiles(doc, [("Applications", len(active)), ("Fully Enforced", with_status("Fully Enforced"), "#146C36"), ("VENs", len(vens)),
                    ("VENs in Full Enforcement", share(in_mode("full"), len(vens))),
                    ("Potentially Blocked Flows", t["potentially_blocked_flows"]), ("Blocked Flows", t["blocked_flows"])])
    doc_chart(doc, [lambda ax: donut(ax, "Applications by Status", status_parts, "applications"),
                    lambda ax: donut(ax, "VENs by Enforcement Mode", mode_parts, "VENs"),
                    lambda ax: hbars(ax, "Completion by Environment", completion, share=False, suffix="%", scale=100)],
              height=3.1, widths=[1, 1, 1.2])
    key = [f for f in findings if f[2] != "Good"]
    key.sort(key=lambda f: {"Critical": 0, "Action": 1}.get(f[2], 2))
    if key:
        callout(doc, "Key Findings", [f"{area}: {finding}" for area, finding, _rating, _advice in key[:6]])
    else:
        callout(doc, "Key Findings", ["No outstanding findings: every area assessed is in good standing."], accent="#17994F")

    # ---- 1. PCE
    doc.add_heading("1. PCE Information Summary", level=1)
    lead(doc, "The Policy Compute Engine this report was produced from, and what it manages.")
    doc_tiles(doc, [("Workloads", len(workloads)), ("VENs", len(vens)), ("Unmanaged", len(workloads) - len(vens)),
                    ("Applications", by_key("app") if labels else len(data["apps"])), ("Environments", by_key("env")),
                    ("Labels", len(labels)), ("Rulesets", t["rulesets"]), ("Rules Provisioned", t["rules_active"])])
    doc_chart(doc, [
        lambda ax: donut(ax, "Workloads", [("Managed by a VEN", len(vens), NAVY), ("Unmanaged", len(workloads) - len(vens), ORANGE)], "workloads"),
        lambda ax: donut(ax, "VENs", [("Online", online, "#17994F"), ("Offline", offline, "#D03B3B"), ("Suspended", suspended, "#E39B00")], "VENs"),
        lambda ax: donut(ax, "Labels", [("In use", len(labels) - len(unused), NAVY), ("Unused", len(unused), ORANGE)], "labels"),
        lambda ax: hbars(ax, f"Policy ({t['rulesets']:,} rulesets)",
                         [("Rules in draft", t["rules_draft"]), ("Rules provisioned", t["rules_active"]),
                          ("Draft not provisioned", t["rules_pending"])], [NAVY, "#17994F", ORANGE], share=False),
    ], height=2.7, widths=[1, 1, 1, 1.3])
    cluster = "  ·  ".join(x for x in ((pce.get("status") or "").capitalize(), pce.get("cluster_type") or "",
                                       f"{pce['nodes']} node(s)" if pce.get("nodes") else "") if x)
    address = f"{conf.get('pce_fqdn', '')}:{conf.get('port', 443)}" if conf.get("pce_fqdn") else ""
    doc_table(doc, ["Item", "Value"], [["PCE", data["pce"]], ["Address", address], ["Organization", str(conf.get("org", 1))],
                                       ["PCE version", pce.get("version") or "Not available"], ["Cluster health", cluster or "Not available"],
                                       ["Data as of", synced_text], ["Project", projects]], [2.4, 7.4])
    note(doc, "Sections 1 to 3 describe the whole PCE, including applications that are outside the scope of the enforcement project.")

    # ---- 2. VENs and workloads
    doc.add_heading("2. VENs & Workloads", level=1)
    lead(doc, "Deployment, version currency and connectivity of the VEN fleet.")
    if not workloads:
        doc.add_paragraph("No workload data is available yet.")
    else:
        by_type = [(name.capitalize(), n) for name, n in tally(vens, "ven_type", "unknown")]
        wrong = unsupported_vens(workloads)
        versions = sorted(tally(vens, "ven_version", "Unknown"), key=lambda kv: version_key(kv[0]), reverse=True)
        doc_table(doc, ["Measure", "Value", "Detail"], [
            ["Deployed VENs", len(vens), ", ".join(f"{n:,} {name.lower()}" for name, n in by_type)],
            ["Online", online, share(online, len(vens))],
            ["Offline", offline, share(offline, len(vens)) if offline else ""],
            ["Suspended", suspended, share(suspended, len(vens)) if suspended else ""],
            ["Unmanaged workloads", len(workloads) - len(vens), f"{share(len(workloads) - len(vens), len(workloads))} of {len(workloads):,} workloads"],
            ["VEN versions in use", len(versions), f"Newest: {versions[0][0]}" if versions else ""],
            ["Windows clients with a server VEN", len(wrong), "Critical - not supported; see Unsupported VEN Deployments" if wrong else "None"],
        ], [3.2, 1.3, 5.3], right=(1,), colors={1: NAVY}, tints={6: "#FBDFDF"} if wrong else None)
        doc_table(doc, ["Measure", "Value", "Detail"],
                  [[name.replace(" Enforcement", "").capitalize() + " enforcement", n, share(n, len(vens))] for name, n, _color in mode_parts],
                  [3.2, 1.3, 5.3], right=(1,), colors={1: NAVY})
        type_rows = [(name, [sum(1 for w in vens if (w["ven_type"] or "unknown").capitalize() == name and state(w)) for state in (
            lambda w: w["online"] and w["ven_status"] != "suspended",
            lambda w: not w["online"] and w["ven_status"] != "suspended",
            lambda w: w["ven_status"] == "suspended")]) for name, _n in by_type]
        type_colors = [NAVY, ORANGE, "#2A78D6", "#8A94A6", "#17994F"]
        doc_chart(doc, [
            lambda ax: donut(ax, "VENs by Type", [(name, n, type_colors[i % len(type_colors)]) for i, (name, n) in enumerate(by_type)], "VENs"),
            lambda ax: stacked(ax, "VEN Status by Type", type_rows, [("Online", "#17994F"), ("Offline", "#D03B3B"), ("Suspended", "#E39B00")]),
            lambda ax: donut(ax, "Enforcement Mode", mode_parts, "VENs"),
        ], height=3.2, widths=[1, 1.25, 1])
        doc_chart(doc, [lambda ax: hbars(ax, "VEN Versions", versions, NAVY),
                        lambda ax: hbars(ax, "Operating Systems", tally(vens, "os", "Unknown"))], height=2.9)

        def group_rows(key, blank, by_app):
            groups = {}
            for w in workloads:
                groups.setdefault(w[key] or "", []).append(w)
            rows = []
            for name, items in sorted(groups.items(), key=lambda kv: (kv[0] == "", -len(kv[1]), kv[0].lower())):
                mine = [w for w in items if w["managed"]]
                up = sum(1 for w in mine if w["online"] and w["ven_status"] != "suspended")
                rows.append([name or blank]
                            + ([", ".join(sorted({w["environment"] for w in items if w["environment"]})
                                          + (["(no environment label)"] if any(not w["environment"] for w in items) else []))]
                               if by_app else [])
                            + [len(items), len(mine), len(items) - len(mine), up, len(mine) - up]
                            + [sum(1 for w in mine if w["enforcement_mode"] == m) for m in MODE_NAMES]
                            + ([", ".join(sorted({w["ven_version"] for w in mine if w["ven_version"]}, key=version_key, reverse=True))] if by_app else []))
            return rows

        counts = ["Workloads", "VENs", "Unmanaged", "Online", "Offline", "Full", "Selective", "Visibility Only", "Idle"]
        narrow = [0.8, 0.55, 0.85, 0.6, 0.6, 0.45, 0.7, 0.75, 0.45]
        doc.add_heading("By Application", level=2)
        doc_table(doc, ["Application", "Environments"] + counts + ["VEN Versions"], group_rows("application", "(no application label)", True),
                  [1.9, 1.05] + narrow + [1.4], right=range(2, 11))
        doc.add_heading("By Environment", level=2)
        doc_table(doc, ["Environment"] + counts, group_rows("environment", "(no environment label)", False), [4.05] + narrow, right=range(1, 10))
        note(doc, "Online, Offline and the enforcement mode columns count VENs. Unmanaged workloads have no VEN and cannot be enforced by the PCE.")
        unsupported_section(doc, wrong)
        workload_labelling(doc, workloads)

    # ---- 3. Labels
    doc.add_heading("3. Label Summary", level=1)
    lead(doc, "The labels defined in the PCE, how they are used, and which ones are no longer used by anything.")
    if not labels:
        doc.add_paragraph("No label data is available yet.")
    else:
        type_name = lambda key: types.get(key) or key  # noqa: E731
        keys = sorted({l["key"] for l in labels}, key=lambda k: type_name(k).lower())
        doc_tiles(doc, [("Labels", len(labels)), ("In Use", len(labels) - len(unused)),
                        ("Unused", len(unused), WARN if unused else ORANGE),
                        ("On Workloads", sum(1 for l in labels if l["workload_count"])), ("Label Types", len(keys))])
        per_type = sorted(((type_name(k), by_key(k)) for k in keys), key=lambda kv: -kv[1])
        doc_chart(doc, [lambda ax: hbars(ax, "Labels by Type", per_type, NAVY),
                        lambda ax: donut(ax, "Label Usage", [("In use", len(labels) - len(unused), NAVY), ("Unused", len(unused), ORANGE)], "labels"),
                        lambda ax: hbars(ax, "Unused Labels by Type",
                                         [(type_name(k), sum(1 for l in unused if l["key"] == k)) for k in keys
                                          if any(l["key"] == k for l in unused)])], height=3.2, widths=[1.2, 1, 1.2])
        doc.add_heading("Labels by Type", level=2)
        rows, tints = [], {}
        for key in keys:
            mine = [l for l in labels if l["key"] == key]
            idle = sum(1 for l in mine if not l["used_by"])
            if idle:
                tints[len(rows)] = TINT
            rows.append([type_name(key), key, len(mine), sum(1 for l in mine if l["workload_count"]),
                         sum(1 for l in mine if l["used_by"] and not l["workload_count"]), idle])
        doc_table(doc, ["Label Type", "Key", "Labels", "On Workloads", "Used Elsewhere Only", "Unused"], rows,
                  [3.0, 1.4, 1.2, 1.4, 1.6, 1.2], right=range(2, 6), tints=tints, colors={5: WARN})
        doc.add_heading("Unused Labels", level=2)
        if unused:
            callout(doc, f"{len(unused):,} label(s) are not used anywhere in the PCE",
                    ["No workload carries them and no ruleset, label group, pairing profile or other object refers to them. "
                     "They can be reviewed for removal."], accent=WARN, fill=TINT, bullets=False)
            doc_table(doc, ["Label Type", "Key", "Unused Label"], [[type_name(l["key"]), l["key"], l["value"]] for l in unused],
                      [3.0, 1.4, 5.4], tints={n: TINT for n in range(len(unused))}, colors={2: WARN})
        else:
            callout(doc, "Every label is in use", ["No clean-up is needed."], accent="#17994F", bullets=False)
        doc.add_heading("All Labels", level=2)
        rows = [[type_name(l["key"]), l["value"], l["workload_count"],
                 ", ".join(USED_BY.get(u, u.replace("_", " ").capitalize()) for u in l["used_by"].split(",") if u),
                 "In use" if l["used_by"] else "Unused"] for l in labels]
        doc_table(doc, ["Label Type", "Label", "Workloads", "Used By", "Status"], rows, [1.9, 2.6, 0.9, 3.4, 1.0], right=(2,),
                  tints={n: TINT for n, l in enumerate(labels) if not l["used_by"]}, badges={4: {"Unused": (TINT, WARN)}})
        note(doc, "Unused labels are highlighted. Usage is as reported by the PCE for each label.")

    # ---- 4. Policy and traffic
    doc.add_heading("4. Policy and Blocked Traffic by Scope", level=1)
    lead(doc, "The rulesets and rules written for each application and environment, in draft and provisioned, and the "
              "draft changes that are not provisioned yet.")
    doc_tiles(doc, [("Rulesets", t["rulesets"]), ("Rules in Draft", t["rules_draft"]), ("Rules Provisioned", t["rules_active"]),
                    ("Draft Not Provisioned", t["rules_pending"], WARN if t["rules_pending"] else ORANGE)])
    tracked = [r for r in pol["rows"] if r["tracked"]]
    top = lambda key: [(r["scope"], r[key]) for r in sorted(tracked, key=lambda r: -(r[key] or 0)) if r[key]]  # noqa: E731
    doc_chart(doc, [lambda ax: hbars(ax, "Provisioned Rules by Scope", top("rules_active"), NAVY, limit=6),
                    lambda ax: hbars(ax, "Rules in Draft by Scope", top("rules_draft"), ORANGE, limit=6)], height=3.3)
    scope_callout(doc, pol)
    # Rules only here; the traffic counts are on the Reports tab and in the Excel workbook.
    counts = RULE_COUNTS
    rows = [[r["scope"], policy.SCOPE_TYPES.get(r.get("scope_type"), ""), r["pce_state"]] + [r[k] for k in counts]
            for r in pol["rows"]]
    rows.append(["PCE total", "", ""] + [t[k] for k in counts])
    doc_table(doc, ["Scope", "Scope Type", "PCE Enforcement", "Rulesets", "Rules in Draft", "Rules Provisioned",
                    "Draft Not Provisioned"],
              rows, [3.4, 1.4, 1.6, 0.85, 0.85, 1.0, 1.0], right=range(3, 7), bold_rows={len(rows) - 1},
              badges={1: SCOPE_BADGES},
              tints={n: SCOPE_TINT[r.get("scope_type")] for n, r in enumerate(pol["rows"]) if r.get("scope_type") in SCOPE_TINT})
    for text in policy_notes(pol, with_traffic=False):
        note(doc, text)

    # ---- 5. Assessment
    doc.add_heading("5. Assessment Summary", level=1)
    lead(doc, "An automated health check of the deployment: one finding per area, with a rating and a suggested next step.")
    count = lambda rating: sum(1 for f in findings if f[2] == rating)  # noqa: E731
    doc_tiles(doc, [("Areas Assessed", len(findings)), ("Critical", count("Critical"), "#B02A2A" if count("Critical") else ORANGE),
                    ("Action", count("Action"), "#B02A2A" if count("Action") else ORANGE),
                    ("Attention", count("Attention")), ("Good", count("Good"), "#146C36")])
    order = {"Critical": 0, "Action": 1, "Attention": 2, "Good": 3}
    doc_table(doc, ["Area", "Finding", "Rating", "Recommendation"], [list(f) for f in sorted(findings, key=lambda f: order[f[2]])],
              [1.6, 3.9, 0.8, 3.5], badges={2: RATINGS})
    note(doc, "Ratings are generated automatically from the data in this report. Critical: an unsupported deployment to fix first. "
              "Action: something is not working or is overdue. "
              "Attention: work still to do. Good: nothing outstanding.")

    # ---- 6. Recommendations: what the consultant entered on the Reports page, or an empty form
    doc.add_heading("6. Recommendations", level=1)
    entered = any(consultant[k] for k in CONSULTANT_TEXTS) or any(consultant[k] for k in CONSULTANT_LISTS)
    if entered:
        lead(doc, "Observations and recommendations from the consultant's review of this deployment.")
    else:
        note(doc, "To be completed by the consultant. Type into the empty cells; add rows with Tab from the last cell of a table.")
    doc.add_heading("Recommended Actions", level=2)
    recs = consultant["recommendations"]
    if not recs:
        note(doc, "One row per recommendation. Area: for example VENs, Workloads, Labels, Policy, Traffic, Process. "
                  "Priority: High, Medium or Low. Status: Open, In Progress or Done.")
    form_grid(doc, ["#", "Area", "Observation", "Recommendation", "Priority", "Owner", "Target Date", "Status"],
              [0.35, 1.1, 2.5, 2.75, 0.7, 1.0, 0.8, 0.8], blank_rows=8,
              filled=[[r["area"], r["observation"], r["recommendation"], r["priority"], r["owner"],
                       fmt_date(r["target_date"]) if r["target_date"] else "", r["status"]] for r in recs],
              badges={4: {"High": RATINGS["Action"], "Medium": RATINGS["Attention"], "Low": ("#E4EFFC", "#2A78D6")}})
    selective_ports(doc, data)
    attention_ports(doc, data)
    doc.add_heading("Risks and Dependencies", level=2)
    form_grid(doc, ["#", "Risk or Dependency", "Impact", "Mitigation", "Owner"], [0.35, 3.0, 2.2, 3.05, 1.2], blank_rows=4,
              filled=[[r["risk"], r["impact"], r["mitigation"], r["owner"]] for r in consultant["risks"]])
    doc.add_heading("Next Steps", level=2)
    form_grid(doc, ["#", "Action", "Owner", "Due Date", "Notes"], [0.35, 4.0, 1.4, 1.0, 3.05], blank_rows=5,
              filled=[[r["action"], r["owner"], fmt_date(r["due_date"]) if r["due_date"] else "", r["notes"]] for r in consultant["next_steps"]])
    doc.add_heading("Additional Notes", level=2)
    form_box(doc, 1.1, consultant["notes"])

    doc_front(doc, [[("PCE", data["pce"]), ("Address", address), ("PCE version", pce.get("version") or "")],
                    [("Project", projects), ("Data as of", synced_text), ("Report date", datetime.date.today().strftime("%b %-d, %Y"))],
                    [("Prepared for", consultant["customer"]), ("Prepared by", consultant["consultant"]),
                     ("Document version", consultant["document_version"])]], preview)

    stream = io.BytesIO()
    doc.save(stream)
    return stream.getvalue()


# What the consultant fills in on Reports > Enforcement Summary Report. Kept as one JSON document per
# PCE: it is free-form report text that is only ever read and written whole.
CONSULTANT_FIELDS = ("customer", "consultant", "role_company", "customer_contact", "review_period", "next_review_date", "document_version")
CONSULTANT_TEXTS = ("notes",)
CONSULTANT_LISTS = {"recommendations": ("area", "observation", "recommendation", "priority", "owner", "target_date", "status"),
                    "risks": ("risk", "impact", "mitigation", "owner"),
                    "next_steps": ("action", "owner", "due_date", "notes")}
CONSULTANT_CHOICES = {"priority": ("", "High", "Medium", "Low"), "status": ("", "Open", "In Progress", "Done")}


def clean_consultant(raw):
    """A complete, bounded copy of a consultant report: unknown keys dropped, empty rows removed."""
    raw = raw if isinstance(raw, dict) else {}
    text = lambda value, limit: str(value if value is not None else "").strip()[:limit]  # noqa: E731
    out = {key: text(raw.get(key), 200) for key in CONSULTANT_FIELDS}
    out.update({key: text(raw.get(key), 6000) for key in CONSULTANT_TEXTS})
    for name, columns in CONSULTANT_LISTS.items():
        rows = []
        for item in (raw.get(name) if isinstance(raw.get(name), list) else [])[:60]:
            item = item if isinstance(item, dict) else {}
            row = {c: text(item.get(c), 1000) for c in columns}
            for c, allowed in CONSULTANT_CHOICES.items():
                if c in row and row[c] not in allowed:
                    raise core.ApiError(f"{c} must be one of: {', '.join(a for a in allowed if a)}")
            if any(row.values()):
                rows.append(row)
        out[name] = rows
    return out


def load_consultant(pid):
    row = core.con().execute("SELECT content, updated_at FROM consultant_reports WHERE pce_id = ?", (pid,)).fetchone() if pid else None
    try:
        saved = json.loads(row["content"]) if row else {}
    except ValueError:
        saved = {}
    return {**clean_consultant(saved), "updated_at": row["updated_at"] if row else ""}


@bp.get("/api/reports/consultant")
def consultant_get():
    _cfg, name = core.active_pce_name()
    return jsonify(load_consultant(core.pce_id_for(name)))


@bp.put("/api/reports/consultant")
def consultant_put():
    _cfg, name = core.active_pce_name()
    if not name:
        raise core.ApiError("No PCE is configured - add one in Settings")
    pid = core.pce_id_for(name, create=True)
    content = clean_consultant(request.get_json(silent=True))
    core.con().execute(
        "INSERT INTO consultant_reports (pce_id, content, updated_at) VALUES (?,?,?) "
        "ON CONFLICT(pce_id) DO UPDATE SET content = excluded.content, updated_at = excluded.updated_at",
        (pid, json.dumps(content), core.now()))
    core.con().commit()
    return jsonify(load_consultant(pid))


def saved_summary(row):
    try:
        content = clean_consultant(json.loads(row["content"]))
    except ValueError:
        content = clean_consultant({})
    return {"id": row["id"], "name": row["name"], "saved_at": row["saved_at"], "saved_by": row["saved_by"],
            "customer": content["customer"], "recommendations": len(content["recommendations"]),
            "risks": len(content["risks"]), "next_steps": len(content["next_steps"])}


def saved_row(pid, save_id):
    row = core.con().execute("SELECT * FROM consultant_report_saves WHERE id = ? AND pce_id = ?", (save_id, pid)).fetchone()
    if row is None:
        raise core.ApiError("Saved recommendations not found", 404)
    return row


def active_pid():
    _cfg, name = core.active_pce_name()
    if not name:
        raise core.ApiError("No PCE is configured - add one in Settings")
    return core.pce_id_for(name, create=True)


@bp.get("/api/reports/consultant/saved")
def consultant_saved_list():
    """Named copies of the recommendations for this PCE, newest first."""
    _cfg, name = core.active_pce_name()
    pid = core.pce_id_for(name) if name else None
    rows = core.con().execute("SELECT * FROM consultant_report_saves WHERE pce_id = ? ORDER BY saved_at DESC, id DESC",
                              (pid,)).fetchall() if pid else []
    return jsonify(saved=[saved_summary(r) for r in rows])


@bp.post("/api/reports/consultant/saved")
def consultant_save():
    """Save the given recommendations (the form as it is now) under a name; the same name is overwritten."""
    pid = active_pid()
    body = request.get_json(silent=True) or {}
    label = str(body.get("name") or "").strip()[:100]
    if not label:
        raise core.ApiError("Give the saved recommendations a name")
    content = clean_consultant(body.get("content"))
    c = core.con()
    c.execute("INSERT INTO consultant_report_saves (pce_id, name, content, saved_at, saved_by) VALUES (?,?,?,?,?) "
              "ON CONFLICT(pce_id, name) DO UPDATE SET content = excluded.content, saved_at = excluded.saved_at, "
              "saved_by = excluded.saved_by", (pid, label, json.dumps(content), core.now(), core.current_user()))
    c.commit()
    row = c.execute("SELECT * FROM consultant_report_saves WHERE pce_id = ? AND name = ?", (pid, label)).fetchone()
    return jsonify(saved_summary(row))


@bp.post("/api/reports/consultant/saved/<int:save_id>/load")
def consultant_load(save_id):
    """Retrieve saved recommendations: they replace the ones being edited (and printed in the report)."""
    pid = active_pid()
    row = saved_row(pid, save_id)
    content = clean_consultant(json.loads(row["content"]))
    core.con().execute(
        "INSERT INTO consultant_reports (pce_id, content, updated_at) VALUES (?,?,?) "
        "ON CONFLICT(pce_id) DO UPDATE SET content = excluded.content, updated_at = excluded.updated_at",
        (pid, json.dumps(content), core.now()))
    core.con().commit()
    return jsonify(load_consultant(pid))


@bp.delete("/api/reports/consultant/saved/<int:save_id>")
def consultant_saved_delete(save_id):
    pid = active_pid()
    saved_row(pid, save_id)
    core.con().execute("DELETE FROM consultant_report_saves WHERE id = ?", (save_id,))
    core.con().commit()
    return jsonify(ok=True)


def report_data():
    raw = request.args.get("projects", "")
    try:
        project_ids = [int(x) for x in raw.split(",") if x.strip()]
    except ValueError:
        raise core.ApiError("projects must be a comma-separated list of project ids")
    return collect(project_ids)


def download(payload, extension, mimetype):
    filename = f"illumio-enforcement-summary-{datetime.date.today().isoformat()}.{extension}"
    return Response(payload, mimetype=mimetype, headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@bp.get("/api/reports/enforcement-summary.xlsx")
def enforcement_summary():
    return download(build(report_data()), "xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


def policy_data():
    c = core.con()
    _cfg, name = core.active_pce_name()
    pid = core.pce_id_for(name)
    return policy.summary(c, pid, tracked_apps(c, pid))


def basis_arg():
    basis = request.args.get("basis", "provisioned")
    if basis not in BASES:
        raise core.ApiError("basis must be provisioned or draft")
    return basis


@bp.get("/api/reports/policy")
def policy_report():
    """Reports > Policy and Blocked Traffic by Scope: every count, both by provisioned and by draft rules."""
    pol = policy_data()
    return jsonify({**pol, "notes": {b: basis_notes(pol, b) + policy_notes(pol) for b in BASES},
                    "direction_notes": {d: direction_note(d) for d in DIRECTION_NAMES}})


@bp.get("/api/reports/policy-traffic.xlsx")
def policy_report_xlsx():
    basis = basis_arg()
    direction = request.args.get("direction", "all")
    if direction not in DIRECTION_NAMES:
        raise core.ApiError("direction must be all, in or out")
    stream = io.BytesIO()
    book = Book(stream)
    pol = policy_data()
    # All directions: one sheet each way as well, so the workbook has both categories side by side.
    for d in (("all", "in", "out") if direction == "all" else (direction,)):
        write_policy(book, pol, basis, d)
    book.wb.close()
    suffix = "" if direction == "all" else f"-{DIRECTION_NAMES[direction].lower()}"
    filename = f"illumio-policy-traffic-{basis}{suffix}-{datetime.date.today().isoformat()}.xlsx"
    return Response(stream.getvalue(), mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@bp.get("/api/reports/enforcement-summary.docx")
def enforcement_summary_docx():
    # preview=1: the same document for Reports > View Report, adjusted for drawing in a browser.
    return download(build_docx(report_data(), preview=request.args.get("preview") == "1"), "docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
