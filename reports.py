"""Illumio Enforcement Summary report: the data behind the Reports page, as an Excel workbook.

Sheets: Dashboard (KPIs, charts, task table with a weekly Gantt), Application Tracker,
Project Timeline, People and Environments.
"""
import datetime
import io

import xlsxwriter
from flask import Blueprint, Response, request

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
    return {"apps": apps, "people": people, "projects": projects, "tl": tl, "pce": pce_name,
            "last_sync": last["completed_at"] if last else None}


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


@bp.get("/api/reports/enforcement-summary.xlsx")
def enforcement_summary():
    raw = request.args.get("projects", "")
    try:
        project_ids = [int(x) for x in raw.split(",") if x.strip()]
    except ValueError:
        raise core.ApiError("projects must be a comma-separated list of project ids")
    payload = build(collect(project_ids))
    filename = f"illumio-enforcement-summary-{datetime.date.today().isoformat()}.xlsx"
    return Response(payload, mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})
