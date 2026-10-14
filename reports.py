"""Illumio Enforcement Summary report: the data behind the Reports page, as an Excel workbook
and as a Word document.

Sheets: Dashboard (KPIs, charts, task table with a weekly Gantt), Application Tracker,
Policy & Traffic (rules and blocked traffic per scope), People, Environments and Project Timeline.
"""
import datetime
import io

import docx
import matplotlib
import xlsxwriter
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from docx.enum.section import WD_ORIENT
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor
from flask import Blueprint, Response, request

import policy
import timeline

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


def collect(project_ids):
    """Everything the report shows, already filtered to the chosen projects."""
    c = core.con()
    _cfg, pce_name = core.active_pce_name()
    pid = core.pce_id_for(pce_name)
    people = {p["id"]: p for p in core.people_list()}
    apps = [core.with_state(r) for r in c.execute(
        core.APP_SELECT + " WHERE a.pce_id = ? AND a.hidden = 0 ORDER BY a.excluded, a.rank, a.id", (pid,))]
    for a in apps:
        a["environments"] = [core.with_state(e) for e in c.execute(
            core.ENV_SELECT + " WHERE ae.application_id = ? ORDER BY e.name COLLATE NOCASE", (a["id"],))]
    tl = timeline.timeline().get_json()
    projects = [p for p in tl["projects"] if not project_ids or p["id"] in project_ids]
    last = c.execute("SELECT completed_at FROM sync_runs WHERE pce_id = ? ORDER BY id DESC LIMIT 1", (pid,)).fetchone()
    # The PCE, VEN, workload and label sections of the Word report describe the whole PCE as of the last sync.
    workloads = [dict(r) for r in c.execute(
        "SELECT w.hostname, a.name AS application, e.name AS environment, w.managed, w.online, w.enforcement_mode, w.ven_status, "
        "w.ven_version, w.policy_sync_state, w.os, w.location, w.role FROM workloads w "
        "LEFT JOIN applications a ON a.id = w.application_id LEFT JOIN environments e ON e.id = w.environment_id "
        "WHERE w.pce_id = ?", (pid,))]
    labels = [dict(r) for r in c.execute(
        "SELECT key, value, workload_count, used_by FROM labels WHERE pce_id = ? ORDER BY key COLLATE NOCASE, value COLLATE NOCASE", (pid,))]
    return {"apps": apps, "people": people, "projects": projects, "tl": tl, "pce": pce_name,
            "last_sync": last["completed_at"] if last else None, "policy": policy.summary(c, pid, apps),
            "workloads": workloads, "labels": labels, "conf": _cfg.get(pce_name) or {}}


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


POLICY_COUNTS = ("rulesets", "rules_draft", "rules_active", "rules_pending") + policy.TRAFFIC_COLS
POLICY_HEADERS = ["Rulesets", "Rules in Draft", "Rules Provisioned", "Draft Changes Not Provisioned",
                  "Potentially Blocked Flows", "Potentially Blocked Connections", "Blocked Flows", "Blocked Connections"]


def policy_notes(pol):
    """What a reader needs to know to trust the Policy & Traffic numbers, one sentence per item."""
    rules, traffic = pol["info"].get("rules") or {}, pol["info"].get("traffic") or {}
    notes = []
    if not rules.get("synced_at") and not traffic.get("synced_at"):
        notes.append("No rules or traffic have been read yet. Press Sync from PCE in the tracker.")
    for part, label in ((rules, "Rules"), (traffic, "Blocked traffic")):
        if part.get("error"):
            notes.append(f"{label} could not be read at the last sync ({part['error']}); "
                         + (f"the numbers are from {part['synced_at']}." if part.get("synced_at") else "none are shown."))
    if traffic.get("synced_at"):
        notes.append(f"Traffic covers the {traffic.get('days', policy.TRAFFIC_DAYS)} days before the sync of {traffic['synced_at']}. "
                     "A flow is one source, destination, port and protocol; it counts for the scope of its source and of its destination.")
    if traffic.get("truncated"):
        notes.append(f"The PCE returned its maximum of {policy.TRAFFIC_MAX_RESULTS:,} flows, so the traffic counts are incomplete.")
    notes.append("Rules of a scope are the rules of the rulesets scoped to that application, for that environment or for all "
                 "environments. Rulesets that name no tracked application are listed by their own scope, without traffic. "
                 "Totals count each ruleset and each flow once, for the whole PCE.")
    return notes


def write_policy(book, pol):
    """Policy & Traffic sheet: per scope, the rules written (draft and provisioned) and the blocked traffic."""
    rows = []
    for r in pol["rows"]:
        rows.append({"values": [r["application"], r["environment"], r["scope"], r["status"], r["pce_state"]]
                     + ["" if r[k] is None else r[k] for k in POLICY_COUNTS] + [r["ruleset_names"]],
                     "_style": {} if r["tracked"] else {"font_color": MUTED, "italic": True}})
    rows.append({"values": ["PCE total", "", "All scopes", "", ""] + [pol["totals"][k] for k in POLICY_COUNTS] + [""],
                 "_style": {"bold": True, "bg_color": "#F1F3F7", "top": 1, "top_color": NAVY}})
    numbers = {i: {"num_format": "#,##0", "align": "right"} for i in range(5, 5 + len(POLICY_COUNTS))}
    headers = ["Application", "Environment", "Scope", "Tracker Status", "PCE Enforcement"] + POLICY_HEADERS + ["Ruleset Names"]
    ws = write_table(book, "Policy & Traffic", headers, [28, 16, 40, 22, 22, 10, 12, 13, 16, 14, 16, 12, 14, 60], rows, numbers,
                     tab="#7A4FD0")
    ws.set_row(0, 40)  # the count headers wrap onto two lines
    for i, value in enumerate(headers):
        ws.write(0, i, value, book.fmt(bold=True, bg_color=NAVY, font_color="#FFFFFF", border=1, border_color="#26354D", indent=1, text_wrap=True))
    for i, note in enumerate(policy_notes(pol)):
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
    table._tbl.tblPr.find(qn("w:tblLayout")).addprevious(lines)


def doc_table(doc, headers, rows, widths, right=(), bold_rows=(), tints=None, colors=None, badges=None):
    """A banded table with a navy header that repeats on every page. `rows` are lists of cell values,
    `tints` maps a row index to a fill colour and `colors` a column index to a text colour. `badges` is
    (column, {value: (fill, text colour)}): cells of that column are coloured by their value."""
    table, widths = fixed_table(doc, len(headers), widths)
    grid_lines(table)
    repeat = OxmlElement("w:tblHeader")
    repeat.set(qn("w:val"), "true")
    table.rows[0]._tr.get_or_add_trPr().append(repeat)
    for i, text in enumerate(headers):
        write_cell(table.rows[0].cells[i], text, widths[i], bold=True, color="#FFFFFF", right=i in right)
        style_cell(table.rows[0].cells[i], NAVY, bottom=(12, ORANGE))
    tints, colors = tints or {}, colors or {}
    for n, values in enumerate(rows):
        cells = table.add_row().cells
        fill = tints.get(n) or ("#F1F3F7" if n in bold_rows else ZEBRA if n % 2 else None)
        for i, value in enumerate(values):
            badge = badges[1].get(value) if badges and badges[0] == i else None
            write_cell(cells[i], value, widths[i], bold=n in bold_rows or bool(badge), color=badge[1] if badge else colors.get(i),
                       right=i in right)
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


def hbars(ax, title, items, colors=ORANGE, limit=7, share=True):
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
    top = max(n for _label, n in items) or 1
    slots = max(len(items), 4)   # few bars stay slim instead of filling the panel
    ax.set_xlim(0, top * 1.45)
    ax.set_ylim(slots, 0)
    for row, ((label, n), color) in enumerate(zip(items, colors)):
        ax.text(0, row + 0.3, label, va="center", fontsize=8, color=INK)
        ax.barh(row + 0.68, top, height=0.26, color="#EEF1F5")
        ax.barh(row + 0.68, n, height=0.26, color=color)
        ax.text(top * 1.04, row + 0.68, f"{n:,}" + (f"  ({(n / total if total else 0):.0%})" if share else ""), va="center", fontsize=8,
                fontweight="bold", color=NAVY)


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


RATINGS = {"Action": ("#FBDFDF", "#B02A2A"), "Attention": ("#FDF1CF", "#8A5A00"), "Good": ("#DDF3E4", "#146C36")}


def assess(data):
    """Rule-based findings from the report's own numbers: (area, finding, rating, recommendation).
    Good means nothing is outstanding; Attention is work still to do; Action is something broken or late."""
    pol, workloads, labels = data["policy"], data["workloads"], data["labels"]
    t = pol["totals"]
    vens = [w for w in workloads if w["managed"]]
    out = []
    add = lambda area, finding, rating, advice: out.append((area, finding, rating, advice))  # noqa: E731
    names = lambda items, limit=5: ", ".join(items[:limit]) + (f" and {len(items) - limit} more" if len(items) > limit else "")  # noqa: E731
    if not workloads:
        add("Data", "No workloads have been synced from the PCE.", "Action", "Press Sync from PCE in the tracker, then download the report again.")
        return out

    full = sum(1 for w in vens if w["enforcement_mode"] == "full")
    rest = ", ".join(f"{sum(1 for w in vens if w['enforcement_mode'] == m):,} {name.lower()}" for m, name in MODE_NAMES.items() if m != "full")
    if vens and full == len(vens):
        add("Enforcement coverage", f"All {len(vens):,} VENs are in full enforcement.", "Good", "Keep monitoring blocked traffic for new flows.")
    else:
        add("Enforcement coverage", f"{full:,} of {len(vens):,} VENs ({(full / len(vens) if vens else 0):.0%}) are in full enforcement; {rest}.",
            "Attention", f"Move the remaining {len(vens) - full:,} VENs to full enforcement as their applications finish policy validation.")

    offline = [w["hostname"] for w in vens if not w["online"]]
    if offline:
        add("VEN connectivity", f"{len(offline):,} of {len(vens):,} VENs are offline: {names(sorted(offline))}.", "Action",
            "Restore connectivity to the PCE or unpair VENs of decommissioned hosts; offline VENs do not receive policy changes.")
    else:
        add("VEN connectivity", f"All {len(vens):,} VENs are online.", "Good", "No action needed.")

    unmanaged = len(workloads) - len(vens)
    if unmanaged:
        add("Unmanaged workloads", f"{unmanaged:,} of {len(workloads):,} workloads have no VEN, so the PCE cannot enforce policy on them.", "Attention",
            "Install a VEN where the platform supports it, or confirm these are meant to stay unmanaged.")
    else:
        add("Unmanaged workloads", "Every workload is managed by a VEN.", "Good", "No action needed.")

    versions = sorted({w["ven_version"] for w in vens if w["ven_version"]}, key=version_key, reverse=True)
    if len(versions) > 1:
        behind = sum(1 for w in vens if w["ven_version"] != versions[0])
        add("VEN versions", f"{len(versions):,} VEN versions are in use; {behind:,} VENs run a version older than {versions[0]}.", "Attention",
            f"Plan an upgrade of the older VENs to {versions[0]} so every workload has the same features and fixes.")
    elif versions:
        add("VEN versions", f"All VENs run version {versions[0]}.", "Good", "No action needed.")

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
                "Review the unused labels in section 4 and delete those that are no longer needed.")
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

    traffic = pol["info"].get("traffic") or {}
    if traffic.get("synced_at"):
        days = traffic.get("days", policy.TRAFFIC_DAYS)
        for key, area, zero, advice in (
                ("potentially_blocked_flows", "Potentially blocked traffic", "No potentially blocked flows",
                 "These flows will be dropped once the scope is enforced. Add rules for the legitimate ones first."),
                ("blocked_flows", "Blocked traffic", "No blocked flows",
                 "Confirm these blocks are intended and add rules for any legitimate traffic that is being dropped.")):
            if t[key]:
                top = sorted((r for r in pol["rows"] if r["tracked"] and r[key]), key=lambda r: -r[key])
                add(area, f"{t[key]:,} {area.lower().replace(' traffic', '')} flows in the last {days} days; most on "
                          f"{names([r['scope'] + f' ({r[key]:,})' for r in top], 3)}.", "Attention", advice)
            else:
                add(area, f"{zero} in the last {days} days.", "Good", "No action needed.")

    drift = [r["scope"] for r in pol["rows"] if r["tracked"] and core.PCE_STATE_STATUS.get(r["pce_state"]) != r["status"]]
    if drift:
        add("Tracker vs PCE", f"{len(drift):,} scope(s) have a tracker status that differs from what the PCE shows: {names(drift)}.", "Attention",
            "Update the tracker status, or change the enforcement mode in the PCE, so the two agree.")
    else:
        add("Tracker vs PCE", "The tracker status of every scope agrees with the PCE.", "Good", "No action needed.")

    tasks, _preds = task_rows(data["tl"], data["projects"])
    leaves = [r for r in tasks if r["kind"] == "task"]
    if leaves:
        today = datetime.date.today().isoformat()
        late = [r["name"] for r in leaves if r["end"] and r["end"] < today and r["status"] != "Completed"]
        done = sum(1 for r in leaves if r["status"] == "Completed")
        if late:
            add("Project plan", f"{len(late):,} of {len(leaves):,} tasks are past their end date and not completed: {names(late)}.", "Action",
                "Replan the overdue tasks or close them out on the Project Timeline.")
        else:
            add("Project plan", f"No task is overdue; {done:,} of {len(leaves):,} tasks are completed.", "Good", "No action needed.")
    return out


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


def form_box(doc, height):
    """An empty bordered box for free text."""
    table, widths = fixed_table(doc, 1, [1])
    grid_lines(table, FORM_LINE)
    table.rows[0].height = Inches(height)
    write_cell(table.rows[0].cells[0], "", widths[0], size=10)
    doc.add_paragraph().paragraph_format.space_after = Pt(2)


def form_grid(doc, headers, widths, blank_rows, height=0.42, numbered=True):
    """A table with the usual navy header and empty rows to fill in."""
    rows = [[n + 1 if numbered else ""] + [""] * (len(headers) - 1) for n in range(blank_rows)]
    table = doc_table(doc, headers, rows, widths, tints={n: "#FFFFFF" for n in range(blank_rows)}, colors={0: MUTED})
    grid_lines(table, FORM_LINE)
    for row in table.rows[1:]:
        row.height = Inches(height)
    return table


def doc_theme(doc, data):
    """Landscape page, Illumio colours on the headings, and a footer with the page number on every
    page but the cover."""
    section = doc.sections[0]
    section.orientation = WD_ORIENT.LANDSCAPE
    section.page_width, section.page_height = section.page_height, section.page_width
    for side in ("left_margin", "right_margin", "top_margin", "bottom_margin"):
        setattr(section, side, Inches(0.6))
    doc.styles["Normal"].font.name = "Calibri"
    doc.styles["Normal"].font.size = Pt(10)
    doc.styles["Normal"].font.color.rgb = rgb(INK)
    for name, size, color, before in (("Heading 1", 16, NAVY, 16), ("Heading 2", 12, ORANGE, 10)):
        style = doc.styles[name]
        style.font.name = "Calibri"
        style.font.size = Pt(size)
        style.font.bold = True
        style.font.color.rgb = rgb(color)
        style.paragraph_format.space_before = Pt(before)
        style.paragraph_format.space_after = Pt(6)
        fonts = style.element.rPr.find(qn("w:rFonts"))
        for attr in ("asciiTheme", "hAnsiTheme"):   # the template's theme font would win over the name set above
            fonts.attrib.pop(qn(f"w:{attr}"), None)
    rule = OxmlElement("w:pBdr")   # orange rule under every section heading
    line = OxmlElement("w:bottom")
    for attr, value in (("val", "single"), ("sz", "12"), ("space", "3"), ("color", ORANGE.lstrip("#"))):
        line.set(qn(f"w:{attr}"), value)
    rule.append(line)
    doc.styles["Heading 1"].element.pPr.find(qn("w:spacing")).addprevious(rule)

    footer = section.footer.paragraphs[0]
    footer.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    footer.add_run(f"illumio  ·  Enforcement Summary  ·  PCE {data['pce']}  ·  Page ")
    for kind in ("begin", "PAGE", "end"):
        run = footer.add_run()
        part = OxmlElement("w:instrText" if kind == "PAGE" else "w:fldChar")
        if kind == "PAGE":
            part.text = kind
        else:
            part.set(qn("w:fldCharType"), kind)
        run._r.append(part)
    for run in footer.runs:
        run.font.size = Pt(8)
        run.font.color.rgb = rgb(MUTED)

    section.different_first_page_header_footer = True   # no footer on the cover page


def field(par, code, kind):
    """One piece of a Word field in its own run: "begin", "separate" and "end" markers, or the field code."""
    run = par.add_run()
    part = OxmlElement("w:fldChar" if kind else "w:instrText")
    if kind:
        part.set(qn("w:fldCharType"), kind)
    else:
        part.set(qn("xml:space"), "preserve")
        part.text = code
    run._r.append(part)
    return run


def doc_front(doc, details):
    """Cover page and table of contents, written last (the contents list the headings that exist) and
    then moved ahead of the first section."""
    body = doc.element.body
    first, mark = body[0], len(body) - 1   # new elements are added just before the closing section properties
    headings = [(p.style.name, p.text) for p in doc.paragraphs if p.style.name in ("Heading 1", "Heading 2")]

    # ---- cover
    cover, widths = fixed_table(doc, 1, [1])
    cover.rows[0].height = Inches(3.5)
    cell = cover.rows[0].cells[0]
    cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
    for n, (text, size, color, bold) in enumerate((("illumio", 46, ORANGE, True), ("Enforcement Summary Report", 30, "#FFFFFF", True),
                                                   ("Plan. Track. Enforce.", 13, "#C8D1E0", False))):
        par = write_cell(cell, text, widths[0], size=size, bold=bold, color=color) if n == 0 else cell.add_paragraph()
        if n:
            run = par.add_run(text)
            run.font.size, run.bold = Pt(size), bold
            run.font.color.rgb = rgb(color)
        par.paragraph_format.left_indent = Inches(0.45)
        par.paragraph_format.space_after = Pt(4)
    style_cell(cell, NAVY, bottom=(48, ORANGE))
    doc.add_paragraph().paragraph_format.space_after = Pt(18)
    form_fields(doc, details, height=0.4)
    note(doc, "Generated by the Illumio Enforcement Tracker from the PCE data of the last sync.")

    # ---- contents: a real Word field, so page numbers are filled in when the document is opened
    title = doc.add_paragraph()
    title.paragraph_format.page_break_before = True
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
    for par in doc.paragraphs:   # the first section starts on the page after the contents
        if par.style.name == "Heading 1":
            par.paragraph_format.page_break_before = True
            break


def workload_groups(workloads, key, blank, extra=None):
    """One row per value of `key`: workloads, managed, unmanaged, online and VENs per enforcement mode."""
    groups = {}
    for w in workloads:
        groups.setdefault(w[key] or "", []).append(w)
    rows = []
    for name, items in sorted(groups.items(), key=lambda kv: (kv[0] == "", -len(kv[1]), kv[0].lower())):
        managed = [w for w in items if w["managed"]]
        rows.append([name or blank] + ([extra(items)] if extra else []) + [len(items), len(managed), len(items) - len(managed),
                    sum(1 for w in items if w["online"])] + [sum(1 for w in managed if w["enforcement_mode"] == m) for m in MODE_NAMES])
    return rows


def build_docx(data):
    """The enforcement summary as an Illumio-themed Word document: PCE, VENs, workloads and labels as
    of the last sync, then policy and blocked traffic by scope, the applications and the project plan."""
    doc = docx.Document()
    doc_theme(doc, data)
    people, pol, conf = data["people"], data["policy"], data["conf"]
    info, t = pol["info"], pol["totals"]
    pce = info.get("pce") or {}
    types = pce.get("label_types") or {}
    workloads, labels = data["workloads"], data["labels"]
    vens = [w for w in workloads if w["managed"]]
    unused = [l for l in labels if not l["used_by"]]
    active = [a for a in data["apps"] if not a["excluded"]]
    projects = ", ".join(p["name"] for p in data["projects"]) or "All Projects"
    by_key = lambda key: sum(1 for l in labels if l["key"] == key)  # noqa: E731

    # ---- 1. PCE
    doc.add_heading("1. PCE Information Summary", level=1)
    doc_tiles(doc, [("Workloads", len(workloads)), ("VENs", len(vens)), ("Unmanaged", len(workloads) - len(vens)),
                    ("Applications", by_key("app") if labels else len(data["apps"])), ("Environments", by_key("env")),
                    ("Labels", len(labels)), ("Rulesets", t["rulesets"]), ("Rules Provisioned", t["rules_active"])])
    cluster = "  ·  ".join(x for x in ((pce.get("status") or "").capitalize(), pce.get("cluster_type") or "",
                                       f"{pce['nodes']} node(s)" if pce.get("nodes") else "") if x)
    traffic = info.get("traffic") or {}
    online = sum(1 for w in vens if w["online"])
    doc_chart(doc, [
        lambda ax: donut(ax, "Workloads", [("Managed by a VEN", len(vens), NAVY), ("Unmanaged", len(workloads) - len(vens), ORANGE)], "workloads"),
        lambda ax: donut(ax, "VENs", [("Online", online, "#17994F"), ("Offline", len(vens) - online, "#D03B3B")], "VENs"),
        lambda ax: donut(ax, "Labels", [("In use", len(labels) - len(unused), NAVY), ("Unused", len(unused), ORANGE)], "labels"),
        lambda ax: hbars(ax, f"Policy ({t['rulesets']:,} rulesets)",
                         [("Rules in draft", t["rules_draft"]), ("Rules provisioned", t["rules_active"]),
                          ("Draft not provisioned", t["rules_pending"])], [NAVY, "#17994F", ORANGE], share=False),
        lambda ax: hbars(ax, f"Blocked Traffic ({traffic.get('days', policy.TRAFFIC_DAYS)} days)",
                         [("Potentially blocked flows", t["potentially_blocked_flows"]), ("Blocked flows", t["blocked_flows"])],
                         ["#E39B00", "#D03B3B"]),
    ], height=2.7, widths=[1, 1, 1, 1.15, 1.15])
    synced = datetime.datetime.strptime(data["last_sync"], "%Y-%m-%dT%H:%M:%SZ") if data["last_sync"] else None
    facts = [
        ["PCE", data["pce"]],
        ["Address", f"{conf.get('pce_fqdn', '')}:{conf.get('port', 443)}" if conf.get("pce_fqdn") else ""],
        ["Organization", conf.get("org", 1)],
        ["PCE version", pce.get("version") or "Not read yet"],
        ["Cluster health", cluster or "Not available to this API key"],
        ["Last sync", synced.strftime("%b %-d, %Y %-I:%M %p UTC") if synced else "Never"],
        ["Project", projects],
        ["Report generated", datetime.datetime.now().strftime("%b %-d, %Y %-I:%M %p")],
    ]
    doc_table(doc, ["Item", "Value"], [[k, str(v)] for k, v in facts], [2.4, 7.4])
    note(doc, "Sections 1 to 4 describe the whole PCE as of the last sync, including applications excluded from tracking.")

    # ---- 2. VENs
    doc.add_heading("2. VEN Summary", level=1)
    if not vens:
        doc.add_paragraph("No VENs to show yet. Sync from the PCE first.")
    else:
        versions = sorted(tally(vens, "ven_version", "Unknown"), key=lambda kv: version_key(kv[0]), reverse=True)
        doc_tiles(doc, [("VENs", len(vens)), ("Online", online), ("Offline", len(vens) - online, WARN if len(vens) > online else ORANGE),
                        ("Full Enforcement", sum(1 for w in vens if w["enforcement_mode"] == "full")),
                        ("Visibility Only", sum(1 for w in vens if w["enforcement_mode"] == "visibility_only")),
                        ("VEN Versions", len(versions))])
        doc.add_heading("Enforcement Mode, VEN Versions and Operating Systems", level=2)
        doc_chart(doc, [
            lambda ax: donut(ax, "Enforcement Mode", [(name, sum(1 for w in vens if w["enforcement_mode"] == mode), MODE_COLORS[mode])
                                                      for mode, name in MODE_NAMES.items()], "VENs"),
            lambda ax: hbars(ax, "VEN Versions", versions, NAVY),
            lambda ax: hbars(ax, "Operating Systems", tally(vens, "os", "Unknown")),
        ], height=3.2, widths=[1, 1.25, 1.25])
        doc.add_heading("VEN Status", level=2)
        share_table(doc, "Status", "VENs", [(name.capitalize(), n) for name, n in tally(vens, "ven_status", "Unknown")])
        doc.add_heading("VENs by Application", level=2)
        groups = {}
        for w in vens:
            groups.setdefault(w["application"] or "", []).append(w)
        rows = []
        for name, items in sorted(groups.items(), key=lambda kv: (kv[0] == "", kv[0].lower())):
            rows.append([name or "(no application label)", len(items)]
                        + [sum(1 for w in items if w["enforcement_mode"] == m) for m in MODE_NAMES]
                        + [sum(1 for w in items if w["online"]), sum(1 for w in items if not w["online"]),
                           ", ".join(sorted({w["ven_version"] or "Unknown" for w in items}, key=version_key, reverse=True)),
                           ", ".join(sorted({w["environment"] for w in items if w["environment"]}))])
        doc_table(doc, ["Application", "VENs", "Full", "Selective", "Visibility Only", "Idle", "Online", "Offline",
                        "VEN Versions", "Environments"],
                  rows, [2.4, 0.55, 0.5, 0.7, 0.8, 0.45, 0.6, 0.6, 2.0, 1.4], right=range(1, 8))

    # ---- 3. Workloads
    doc.add_heading("3. Workload Summary", level=1)
    if not workloads:
        doc.add_paragraph("No workloads to show yet. Sync from the PCE first.")
    else:
        counts = ["Workloads", "Managed", "Unmanaged", "Online", "Full", "Selective", "Visibility Only", "Idle"]
        narrow = [0.85, 0.8, 0.9, 0.7, 0.55, 0.75, 0.8, 0.5]
        doc.add_heading("By Application", level=2)
        envs_of = lambda items: ", ".join(sorted({w["environment"] for w in items if w["environment"]}))  # noqa: E731
        doc_table(doc, ["Application", "Environments"] + counts, workload_groups(workloads, "application", "(no application label)", envs_of),
                  [2.4, 1.55] + narrow, right=range(2, 10))
        doc.add_heading("By Environment", level=2)
        doc_table(doc, ["Environment"] + counts, workload_groups(workloads, "environment", "(no environment label)"), [3.95] + narrow,
                  right=range(1, 9))
        note(doc, "Managed workloads have a VEN; the enforcement mode columns count those. Online is as the PCE reported it at the last sync.")

    # ---- 4. Labels
    doc.add_heading("4. Label Summary", level=1)
    if not labels:
        doc.add_paragraph("Labels have not been read yet. Press Sync from PCE in the tracker.")
    else:
        type_name = lambda key: types.get(key) or key  # noqa: E731
        doc_tiles(doc, [("Labels", len(labels)), ("In Use", len(labels) - len(unused)),
                        ("Unused", len(unused), WARN if unused else ORANGE),
                        ("On Workloads", sum(1 for l in labels if l["workload_count"])), ("Label Types", len({l["key"] for l in labels}))])
        doc.add_heading("Labels by Type", level=2)
        rows, tints = [], {}
        for key in sorted({l["key"] for l in labels}, key=lambda k: type_name(k).lower()):
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
            par = doc.add_paragraph()
            run = par.add_run(f"{len(unused):,} label(s) are not used anywhere in the PCE")
            run.bold = True
            run.font.color.rgb = rgb(WARN)
            par.add_run(": no workload carries them and no ruleset, label group, pairing profile or other object refers to them. "
                        "They can be reviewed for removal.")
            doc_table(doc, ["Label Type", "Key", "Unused Label"], [[type_name(l["key"]), l["key"], l["value"]] for l in unused],
                      [3.0, 1.4, 5.4], tints={n: TINT for n in range(len(unused))}, colors={2: WARN})
        else:
            doc.add_paragraph("Every label is in use.")
        doc.add_heading("All Labels", level=2)
        rows = [[type_name(l["key"]), l["value"], l["workload_count"],
                 ", ".join(USED_BY.get(u, u.replace("_", " ").capitalize()) for u in l["used_by"].split(",") if u),
                 "In use" if l["used_by"] else "Unused"] for l in labels]
        doc_table(doc, ["Label Type", "Label", "Workloads", "Used By", "Status"], rows, [1.9, 2.6, 0.9, 3.4, 1.0], right=(2,),
                  tints={n: TINT for n, l in enumerate(labels) if not l["used_by"]})
        note(doc, "Unused labels are highlighted. Usage is what the PCE reports for each label, plus the workloads seen at the last sync.")

    # ---- 5. Policy and traffic
    doc.add_heading("5. Policy and Blocked Traffic by Scope", level=1)
    doc.add_paragraph(
        f"The PCE holds {t['rulesets']:,} rulesets with {t['rules_draft']:,} rules in draft and {t['rules_active']:,} provisioned; "
        f"{t['rules_pending']:,} draft changes are not provisioned yet. It reported {t['potentially_blocked_flows']:,} potentially "
        f"blocked flows ({t['potentially_blocked_connections']:,} connections) and {t['blocked_flows']:,} blocked flows "
        f"({t['blocked_connections']:,} connections).")
    rows = [[r["scope"], r["status"], r["pce_state"]] + [r[k] for k in POLICY_COUNTS] for r in pol["rows"]]
    rows.append(["PCE total", "", ""] + [t[k] for k in POLICY_COUNTS])
    doc_table(doc, ["Scope", "Tracker Status", "PCE Enforcement", "Rulesets", "Rules in Draft", "Rules Provisioned", "Draft Not Provisioned",
                    "Potentially Blocked Flows", "Potentially Blocked Conn.", "Blocked Flows", "Blocked Conn."],
              rows, [2.4, 1.15, 1.15, 0.65, 0.6, 0.8, 0.8, 0.8, 0.85, 0.6, 0.7], right=range(3, 11), bold_rows={len(rows) - 1})
    for text in policy_notes(pol):
        note(doc, text)

    # ---- 6. Applications
    doc.add_heading("6. Application Enforcement Status", level=1)
    total = len(active)
    tiles = [("Applications", total), ("Environments", sum(1 for a in active for e in a["environments"] if not e["excluded"]))]
    for s_ in ("Fully Enforced", "Selective Enforcement", "In Progress", "Not Started"):
        n = sum(1 for a in active if a["status"] == s_)
        tiles.append((s_, f"{n:,}   {(n / total if total else 0):.0%}"))
    doc_tiles(doc, tiles)
    rows = []
    for a in active:
        envs = [e for e in a["environments"] if not e["excluded"]]
        for e in envs or [None]:
            src = e or a
            rows.append([a["name"], e["name"] if e else "—", src["priority"], src["status"], f"{src['progress']}%",
                         src["start_date"] or a["start_date"] or "", src["end_date"] or a["end_date"] or "",
                         person_name(people, a["ps_id"]), person_name(people, a["pm_id"]), person_name(people, a["owner_id"]),
                         src["pce_state"]])
    if rows:
        doc_table(doc, ["Application", "Environment", "Priority", "Status", "% Complete", "Start", "End", "Assigned PS",
                        "Project Manager", "App Owner", "PCE Enforcement"],
                  rows, [1.7, 0.9, 0.7, 1.2, 0.65, 0.75, 0.75, 1.0, 1.05, 1.0, 1.2], right=(4,))
    else:
        doc.add_paragraph("No applications to show yet. Sync from the PCE first.")

    # ---- 7. Project plan
    doc.add_heading("7. Project Plan", level=1)
    tasks, _preds = task_rows(data["tl"], data["projects"])
    rows, bold = [], set()
    for row in tasks:
        t_ = row["task"]
        leaf = row["kind"] == "task"
        if not leaf:
            bold.add(len(rows))
        rows.append([("    " * row["level"]) + row["name"], (t_["environment"] or "") if t_ and leaf else "",
                     t_["priority"] if leaf else "", person_name(people, t_["ps_id"]) if leaf else "",
                     row["start"] or "", row["end"] or "", row["duration"] if row["duration"] is not None else "",
                     f"{row['progress']}%", row["status"]])
    if rows:
        doc_table(doc, ["Task / Application", "Environment", "Priority", "Assigned PS", "Start", "End", "Days", "% Complete", "Status"],
                  rows, [3.6, 1.1, 0.8, 1.3, 0.85, 0.85, 0.55, 0.8, 1.05], right=(6, 7), bold_rows=bold)
    else:
        doc.add_paragraph("No project plan yet.")

    # ---- 8. Assessment
    doc.add_heading("8. Assessment Summary", level=1)
    findings = assess(data)
    count = lambda rating: sum(1 for f in findings if f[2] == rating)  # noqa: E731
    doc_tiles(doc, [("Findings", len(findings)), ("Action", count("Action"), "#B02A2A" if count("Action") else ORANGE),
                    ("Attention", count("Attention")), ("Good", count("Good"), "#146C36")])
    doc.add_paragraph(f"Of {len(findings):,} areas assessed, {count('Action'):,} need action, {count('Attention'):,} need attention and "
                      f"{count('Good'):,} are in good standing.")
    order = {"Action": 0, "Attention": 1, "Good": 2}
    doc_table(doc, ["Area", "Finding", "Rating", "Recommendation"], [list(f) for f in sorted(findings, key=lambda f: order[f[2]])],
              [1.6, 3.9, 0.8, 3.5], badges=(2, RATINGS))
    note(doc, "The ratings are applied by fixed rules in the tracker from the numbers in this report, not by a manual review. "
              "Action: something is broken or late. Attention: work still to do. Good: nothing outstanding.")

    # ---- 9. Consultant recommendations: an empty form, completed by hand or in Word after download
    doc.add_heading("9. Consultant Recommendations", level=1).paragraph_format.page_break_before = True
    note(doc, "To be completed by the consultant. Type into the empty cells; add rows with Tab from the last cell of a table.")
    doc.add_heading("Engagement Details", level=2)
    form_fields(doc, [["Customer", ("PCE", data["pce"]), ("Report date", datetime.date.today().strftime("%b %-d, %Y"))],
                      ["Consultant", "Role / Company", "Review period"],
                      ["Customer contact", ("Project", projects), "Next review date"]])
    doc.add_heading("Executive Summary", level=2)
    note(doc, "Overall state of the enforcement programme and the main message for stakeholders.")
    form_box(doc, 1.3)
    doc.add_heading("Recommendations", level=2)
    note(doc, "One row per recommendation. Area: for example VENs, Workloads, Labels, Policy, Traffic, Process. "
              "Priority: High, Medium or Low. Status: Open, In Progress or Done.")
    form_grid(doc, ["#", "Area", "Observation", "Recommendation", "Priority", "Owner", "Target Date", "Status"],
              [0.35, 1.1, 2.5, 2.75, 0.7, 1.0, 0.8, 0.8], blank_rows=8)
    doc.add_heading("Risks and Dependencies", level=2)
    form_grid(doc, ["#", "Risk or Dependency", "Impact", "Mitigation", "Owner"], [0.35, 3.0, 2.2, 3.05, 1.2], blank_rows=4)
    doc.add_heading("Next Steps", level=2)
    form_grid(doc, ["#", "Action", "Owner", "Due Date", "Notes"], [0.35, 4.0, 1.4, 1.0, 3.05], blank_rows=5)
    doc.add_heading("Additional Notes", level=2)
    form_box(doc, 1.1)
    doc.add_heading("Sign-off", level=2)
    form_grid(doc, ["Role", "Name", "Signature", "Date"], [2.2, 3.0, 3.0, 1.6], blank_rows=3, height=0.5, numbered=False)
    for row, role in zip(doc.tables[-1].rows[1:], ("Prepared by (Consultant)", "Reviewed by", "Accepted by (Customer)")):
        run = row.cells[0].paragraphs[0].add_run(role)
        run.font.size = Pt(8)
        run.bold = True
        row.cells[0].vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER

    doc_front(doc, [[("PCE", data["pce"]), ("Address", facts[1][1]), ("PCE version", pce.get("version") or "")],
                    [("Project", projects), ("Last sync", facts[5][1]), ("Report date", datetime.date.today().strftime("%b %-d, %Y"))],
                    ["Prepared for", "Prepared by", "Document version"]])

    stream = io.BytesIO()
    doc.save(stream)
    return stream.getvalue()


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


@bp.get("/api/reports/enforcement-summary.docx")
def enforcement_summary_docx():
    return download(build_docx(report_data()), "docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
