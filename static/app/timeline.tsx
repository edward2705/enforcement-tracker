// Project Timeline: task grid + Gantt chart, task details and milestones.

type TaskStatus = 'Not Started' | 'In Progress' | 'Completed';
interface Project { id: number; name: string; description: string; }
interface Task {
  id: number; project_id: number; parent_id: number | null; application_id: number | null; environment_id: number | null;
  name: string; description: string; sort_order: number; priority: Priority; status: TaskStatus; progress: number;
  start_date: string | null; end_date: string | null; ps_id: number | null; pm_id: number | null; owner_id: number | null;
  baseline_start: string | null; baseline_end: string | null; updated_at: string;
  application: string | null; environment: string | null;
}
interface Dependency { predecessor_id: number; successor_id: number; }
interface Milestone { id: number; task_id: number; name: string; due_date: string; status: TaskStatus; }
interface TimelineData {
  projects: Project[]; tasks: Task[]; dependencies: Dependency[]; milestones: Milestone[];
  environments: { id: number; name: string }[]; task_statuses: TaskStatus[];
}
interface TRow {
  key: string; kind: 'project' | 'summary' | 'task'; level: number; project: Project; task?: Task; name: string;
  start: string | null; end: string | null; duration: number | null; progress: number; status: TaskStatus; hasChildren: boolean;
}
interface TFilters { // projects: [] = all of them; the last four are only used by the summary report
  projects: number[]; app: string; env: string; status: string; q: string;
  priority?: string; ps?: string; pm?: string; owner?: string;
}
interface Rollup { start: string | null; end: string | null; weight: number; done: number; statuses: Set<TaskStatus>; }
type Act = (fn: () => Promise<any>, okMessage?: string) => Promise<any>;

const TASK_STATUSES: TaskStatus[] = ['Not Started', 'In Progress', 'Completed'];
const TASK_TONE: Record<TaskStatus, string> = { 'Not Started': 'ns', 'In Progress': 'ip', 'Completed': 'fe' };
const T_ROW = 32, T_HEAD = 44, DAY_MS = 86400000;
const ZOOMS: Record<string, number> = { Weeks: 12, Months: 4.5, Quarters: 2.2 };
// Grid columns: checkbox, #, Task Name, Status, %, Start, End, Dur., Priority, Assigned PS, Application, Environment.
// The Task Name column is flexible until the user drags its edge, then it keeps that width.
const NAME_MIN = 140, NAME_MAX = 720, OTHER_COLS_WIDTH = 830;
const gridCols = (nameWidth: number | null): string =>
  `30px 36px ${nameWidth ? `${nameWidth}px` : 'minmax(210px, 1.6fr)'} 116px 56px 68px 68px 54px 92px 108px 118px 84px`;

const isoOf = (d: Date): string =>
  `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
const dayDiff = (a: string, b: string): number => Math.round((parseDay(b).getTime() - parseDay(a).getTime()) / DAY_MS);
const addDays = (iso: string, n: number): string => { const d = parseDay(iso); d.setDate(d.getDate() + n); return isoOf(d); };
const spanDays = (start: string | null, end: string | null): number | null => (start && end ? dayDiff(start, end) + 1 : null);

// An environment sub-task is called by its environment in the grid (it sits under its application),
// but everywhere it stands alone it is shown as "Application:Environment" so it is unambiguous.
const taskLabel = (t: Task): string => (t.application && t.environment ? `${t.application}:${t.environment}` : t.name);

function rollupStatus(statuses: Set<TaskStatus>): TaskStatus {
  if (statuses.size === 1) return [...statuses][0];
  return statuses.size === 0 ? 'Not Started' : 'In Progress';
}

function mergeRollup(into: Rollup, r: Rollup) {
  if (r.start && (!into.start || r.start < into.start)) into.start = r.start;
  if (r.end && (!into.end || r.end > into.end)) into.end = r.end;
  into.weight += r.weight;
  into.done += r.done;
  r.statuses.forEach(s => into.statuses.add(s));
}

// Flatten projects > phases > tasks into display rows. Phases and projects roll their dates,
// % complete (weighted by duration) and status up from all of their leaf tasks.
function buildTimelineRows(tl: TimelineData, f: TFilters, collapsed: Set<string>): TRow[] {
  const kids = new Map<string, Task[]>();
  tl.tasks.forEach(t => {
    const k = `${t.project_id}:${t.parent_id ?? 0}`;
    kids.set(k, [...(kids.get(k) || []), t]);
  });
  const childrenOf = (projectId: number, parentId: number) => kids.get(`${projectId}:${parentId}`) || [];
  const rollups = new Map<number, Rollup>();
  const rollup = (t: Task): Rollup => {
    const cached = rollups.get(t.id);
    if (cached) return cached;
    const children = childrenOf(t.project_id, t.id);
    const r: Rollup = { start: null, end: null, weight: 0, done: 0, statuses: new Set() };
    if (children.length) children.forEach(c => mergeRollup(r, rollup(c)));
    else {
      const w = spanDays(t.start_date, t.end_date) || 1;
      mergeRollup(r, { start: t.start_date, end: t.end_date, weight: w, done: w * t.progress, statuses: new Set([t.status]) });
    }
    rollups.set(t.id, r);
    return r;
  };

  const q = f.q.trim().toLowerCase();
  const filtering = !!(f.app || f.env || f.status || q || f.priority || f.ps || f.pm || f.owner);
  const matches = (t: Task) =>
    (!f.app || String(t.application_id) === f.app) && (!f.env || String(t.environment_id) === f.env) &&
    (!f.status || t.status === f.status) && (!f.priority || t.priority === f.priority) &&
    (!f.ps || String(t.ps_id) === f.ps) && (!f.pm || String(t.pm_id) === f.pm) && (!f.owner || String(t.owner_id) === f.owner) &&
    (!q || `${t.name} ${t.application || ''} ${t.environment || ''} ${t.description}`.toLowerCase().includes(q));
  const fromRollup = (r: Rollup) => ({
    start: r.start, end: r.end, duration: spanDays(r.start, r.end),
    progress: r.weight ? Math.round(r.done / r.weight) : 0, status: rollupStatus(r.statuses),
  });

  const visit = (t: Task, level: number, project: Project, out: TRow[]): boolean => {
    const children = childrenOf(t.project_id, t.id);
    if (!children.length) {
      if (filtering && !matches(t)) return false;
      out.push({
        key: `t${t.id}`, kind: 'task', level, project, task: t, name: t.name, start: t.start_date, end: t.end_date,
        duration: spanDays(t.start_date, t.end_date), progress: t.progress, status: t.status, hasChildren: false,
      });
      return true;
    }
    const below: TRow[] = [];
    children.forEach(c => visit(c, level + 1, project, below));
    if (filtering && !below.length) return false;
    out.push({ key: `t${t.id}`, kind: 'summary', level, project, task: t, name: t.name, hasChildren: true, ...fromRollup(rollup(t)) });
    if (!collapsed.has(`t${t.id}`)) out.push(...below);
    return true;
  };

  const rows: TRow[] = [];
  tl.projects.filter(p => !f.projects.length || f.projects.includes(p.id)).forEach(project => {
    const top = childrenOf(project.id, 0);
    const below: TRow[] = [];
    top.forEach(t => visit(t, 1, project, below));
    if (filtering && !below.length) return;
    const total: Rollup = { start: null, end: null, weight: 0, done: 0, statuses: new Set() };
    top.forEach(t => mergeRollup(total, rollup(t)));
    rows.push({ key: `p${project.id}`, kind: 'project', level: 0, project, name: project.name, hasChildren: top.length > 0, ...fromRollup(total) });
    if (!collapsed.has(`p${project.id}`)) rows.push(...below);
  });
  return rows;
}

function TaskStatusPill({ status }: { status: TaskStatus }) {
  return <span className={`pill t-${TASK_TONE[status]}`}><span className="dot" />{status}</span>;
}

// Inline editor for a name cell: Enter or leaving the field saves, Escape cancels.
function InlineName({ initial, label, onCommit, onCancel }: {
  initial: string; label: string; onCommit: (name: string) => void; onCancel: () => void;
}) {
  const [value, setValue] = useState(initial);
  const done = useRef(false);
  const finish = (save: boolean) => {
    if (done.current) return;
    done.current = true;
    const name = value.trim();
    if (save && name && name !== initial) onCommit(name); else onCancel();
  };
  return (
    <input className="form-control form-control-sm tl-edit" autoFocus aria-label={label} maxLength={120} value={value}
      onFocus={(e: any) => e.target.select()} onChange={(e: any) => setValue(e.target.value)}
      onClick={(e: any) => e.stopPropagation()} onBlur={() => finish(true)}
      onKeyDown={(e: any) => {
        e.stopPropagation();
        if (e.key === 'Enter') finish(true);
        if (e.key === 'Escape') finish(false);
      }} />
  );
}

// ---------------------------------------------------------------------- Gantt
function Gantt({ rows, tl, ppd, showBaseline, showDeps, selected, flagged, onSelect, scroller, maxHeight, onScroll }: {
  rows: TRow[]; tl: TimelineData; ppd: number; showBaseline: boolean; showDeps: boolean;
  selected: string | null; flagged: Set<string>; onSelect: (key: string) => void;
  scroller: any; maxHeight: number; onScroll: (e: any) => void; // the page keeps the two panes scrolling together
}) {
  const today = todayISO();
  const visibleTaskIds = new Set(rows.filter(r => r.task).map(r => r.task!.id));
  const stones = tl.milestones.filter(m => visibleTaskIds.has(m.task_id));
  const dates = [today, ...rows.flatMap(r => [r.start, r.end]), ...stones.map(m => m.due_date)].filter(Boolean) as string[];
  const first = dates.reduce((a, b) => (a < b ? a : b));
  const last = dates.reduce((a, b) => (a > b ? a : b));
  const lead = parseDay(addDays(first, -10));
  lead.setDate(lead.getDate() - ((lead.getDay() + 6) % 7)); // back to Monday
  const from = isoOf(lead);
  const days = dayDiff(from, addDays(last, 56)); // room for the labels right of the last bars
  const x = (iso: string) => dayDiff(from, iso) * ppd;
  const W = days * ppd, H = Math.max(rows.length, 1) * T_ROW;

  const months: { label: string; left: number; width: number }[] = [];
  for (let d = parseDay(from); isoOf(d) < addDays(from, days);) {
    const next = new Date(d.getFullYear(), d.getMonth() + 1, 1);
    const left = Math.max(0, x(isoOf(d)));
    const width = Math.min(W, x(isoOf(next))) - left;
    months.push({ label: d.toLocaleDateString(undefined, { month: 'short', year: 'numeric' }), left, width });
    d = next;
  }
  const weeks: { label: number; left: number }[] = [];
  for (let i = 0; i < days; i += 7) weeks.push({ label: parseDay(addDays(from, i)).getDate(), left: i * ppd });
  const weekEvery = ppd * 7 >= 24 ? 1 : ppd * 7 >= 12 ? 2 : 4;

  useEffect(() => {
    const el = scroller.current;
    if (el) el.scrollLeft = Math.max(0, x(today) - el.clientWidth * 0.6);
  }, [ppd, from]);

  const index = new Map(rows.map((r, i) => [r.key, i] as [string, number]));
  const mid = (i: number) => i * T_ROW + T_ROW / 2;
  const diamond = (cx: number, cy: number, r: number) => `${cx},${cy - r} ${cx + r},${cy} ${cx},${cy + r} ${cx - r},${cy}`;

  return (
    <div className="tl-gantt" ref={scroller} style={{ maxHeight }} onScroll={onScroll}>
      <div style={{ width: W, position: 'relative' }}>
        <div className="tl-ghead" style={{ height: T_HEAD }}>
          {months.map(m => <div key={m.left} className="gm" style={{ left: m.left, width: m.width }}>{m.width > 54 ? m.label : ''}</div>)}
          {weeks.map((w, i) => i % weekEvery === 0 && <div key={w.left} className="gw num" style={{ left: w.left }}>{w.label}</div>)}
          <div className="today-badge" style={{ left: x(today) + ppd / 2 }}>Today</div>
        </div>
        <svg width={W} height={H} className="tl-svg" role="img" aria-label="Gantt chart of the tasks listed in the table">
          <defs>
            <marker id="dep-arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto">
              <path d="M0 0L8 4L0 8z" className="dep-head" />
            </marker>
            <marker id="dep-arrow-late" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto">
              <path d="M0 0L8 4L0 8z" className="dep-head late" />
            </marker>
          </defs>
          {rows.map((r, i) => (
            <rect key={r.key} x={0} y={i * T_ROW} width={W} height={T_ROW} onClick={() => onSelect(r.key)}
              className={`row-band${r.kind !== 'task' ? ' sum' : ''}${flagged.has(r.key) ? ' gone' : ''}${selected === r.key ? ' sel' : ''}`} />
          ))}
          {weeks.map(w => <line key={w.left} x1={w.left} x2={w.left} y1={0} y2={H} className="wk" />)}
          {months.map(m => <line key={m.left} x1={m.left} x2={m.left} y1={0} y2={H} className="mo" />)}
          {rows.map((r, i) => <line key={r.key} x1={0} x2={W} y1={(i + 1) * T_ROW - 0.5} y2={(i + 1) * T_ROW - 0.5} className="rl" />)}

          {showDeps && tl.dependencies.map(d => {
            const a = index.get(`t${d.predecessor_id}`), b = index.get(`t${d.successor_id}`);
            if (a == null || b == null) return null;
            const pred = rows[a], succ = rows[b];
            if (!pred.end || !succ.start) return null;
            const x1 = x(pred.end) + ppd, y1 = mid(a), xs = x(succ.start), y2 = mid(b);
            const late = succ.start <= pred.end;
            const path = xs >= x1 + 12
              ? `M${x1} ${y1}H${x1 + 6}V${y2}H${xs - 1}`
              : `M${x1} ${y1}H${x1 + 6}V${y1 + (y2 > y1 ? T_ROW / 2 : -T_ROW / 2)}H${xs - 10}V${y2}H${xs - 1}`;
            return <path key={`${d.predecessor_id}-${d.successor_id}`} d={path} className={`dep${late ? ' late' : ''}`}
              markerEnd={`url(#dep-arrow${late ? '-late' : ''})`}>
              <title>{`${succ.name} follows ${pred.name}${late ? ' — starts before its predecessor ends' : ''}`}</title>
            </path>;
          })}

          {rows.map((r, i) => {
            const c = mid(i);
            const base = showBaseline && r.task && r.task.baseline_start && r.task.baseline_end && r.kind === 'task'
              ? <rect x={x(r.task.baseline_start)} y={c + 10} height={3} rx={1.5} className="baseline"
                  width={Math.max(x(r.task.baseline_end) + ppd - x(r.task.baseline_start), 3)}>
                  <title>{`Baseline: ${fmtDate(r.task.baseline_start)} → ${fmtDate(r.task.baseline_end)}`}</title>
                </rect>
              : null;
            if (!r.start || !r.end) return <g key={r.key}>{base}</g>;
            const x1 = x(r.start), w = Math.max(x(r.end) + ppd - x1, 4);
            const tip = `${r.name}: ${fmtDate(r.start)} → ${fmtDate(r.end)} · ${r.duration}d · ${r.progress}% · ${r.status}`;
            if (r.kind !== 'task') {
              return (
                <g key={r.key} onClick={() => onSelect(r.key)} className="bar">
                  <title>{tip}</title>
                  <rect x={x1} y={c - 4} width={w} height={7} rx={2} className={`sum-bar${r.kind === 'project' ? ' proj' : ''}`} />
                  <rect x={x1} y={c - 4} width={w * r.progress / 100} height={7} rx={2} className="sum-done" />
                  <polygon points={diamond(x1 + w, c, 6)} className="sum-end" />
                  <text x={x1 + w + 12} y={c + 4} className="lab strong">{r.name}</text>
                </g>
              );
            }
            const tone = TASK_TONE[r.status];
            return (
              <g key={r.key} onClick={() => onSelect(r.key)} className="bar">
                <title>{tip}</title>
                <rect x={x1} y={c - 8} width={w} height={16} rx={4} style={{ fill: `var(--st-${tone}-bg)`, stroke: `var(--st-${tone})` }} />
                {r.progress > 0 && <rect x={x1} y={c - 8} width={w * r.progress / 100} height={16} rx={4}
                  style={{ fill: `var(--st-${tone})`, opacity: 0.6 }} />}
                {base}
                <text x={x1 + w + 8} y={c + 4} className={`lab${flagged.has(r.key) ? ' gone' : ''}`}>{r.name}{flagged.has(r.key) ? ' — deleted in PCE' : ''}</text>
              </g>
            );
          })}

          {stones.map(m => {
            const i = index.get(`t${m.task_id}`);
            if (i == null) return null;
            return (
              <polygon key={m.id} points={diamond(x(m.due_date) + ppd / 2, mid(i), 6.5)} className="stone"
                style={{ fill: `var(--st-${TASK_TONE[m.status]})` }}>
                <title>{`Milestone: ${m.name} · ${fmtDate(m.due_date)} · ${m.status}`}</title>
              </polygon>
            );
          })}
          <line x1={x(today) + ppd / 2} x2={x(today) + ppd / 2} y1={0} y2={H} className="today" />
        </svg>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- task details
function TaskForm({ row, tl, act, canDelete, onDeleted }: {
  row: TRow; tl: TimelineData; act: Act; canDelete: boolean; onDeleted: () => void;
}) {
  const { data } = useStore();
  const task = row.task!;
  const fromPce = !!task.application_id; // an application or environment task: name and PCE links are fixed
  const initial = {
    name: task.name, description: task.description, parent_id: task.parent_id, application_id: task.application_id,
    environment_id: task.environment_id, priority: task.priority, status: task.status, progress: task.progress,
    start_date: task.start_date || '', end_date: task.end_date || '', ps_id: task.ps_id, pm_id: task.pm_id, owner_id: task.owner_id,
  };
  const [form, setForm] = useState(initial);
  const set = (patch: any) => setForm((prev: any) => ({ ...prev, ...patch }));
  const dirty = JSON.stringify(form) !== JSON.stringify(initial);
  const badDates = !!form.start_date && !!form.end_date && form.end_date < form.start_date;
  const duration = spanDays(form.start_date || null, form.end_date || null);
  const app = data.apps.find(a => a.id === form.application_id);
  const envOptions = app
    ? app.environments.map(e => ({ id: (e as any).environment_id as number, name: e.name }))
    : tl.environments;
  // A task can move under any other task in its project except itself and its own subtree.
  const blocked = new Set([task.id]);
  for (let grew = true; grew;) {
    grew = false;
    tl.tasks.forEach(t => { if (t.parent_id != null && blocked.has(t.parent_id) && !blocked.has(t.id)) { blocked.add(t.id); grew = true; } });
  }
  const parents = tl.tasks.filter(t => t.project_id === task.project_id && !blocked.has(t.id) && !t.application_id);
  const summary = row.hasChildren;
  const save = () => {
    const payload: any = summary ? { name: form.name, description: form.description, parent_id: form.parent_id } : { ...form };
    // Send only one of status / % complete when only one changed, so the server keeps them in step.
    if (!summary && form.status === initial.status) delete payload.status;
    if (!summary && form.progress === initial.progress) delete payload.progress;
    return act(() => api('PATCH', `/api/tasks/${task.id}`, payload), 'Task saved');
  };
  const remove = async () => {
    const extra = summary ? ' and everything under it' : '';
    if (confirm(`Delete "${task.name}"${extra}?`) && await act(() => api('DELETE', `/api/tasks/${task.id}`), 'Task deleted')) onDeleted();
  };

  return (
    <div className="row g-3">
      <div className="col-lg-5">
        <label className="form-label">Task Name <span className="req">*</span></label>
        <input className="form-control" value={fromPce ? taskLabel(task) : form.name} maxLength={120} disabled={fromPce} onChange={(e: any) => set({ name: e.target.value })} />
        {fromPce && <div className="muted mt-1"><Icon name="bi-lock" /> {task.environment_id ? 'Environment' : 'Application'} from the PCE — the name cannot be changed.</div>}
        <label className="form-label mt-3">Description</label>
        <textarea className="form-control" rows={2} maxLength={500} value={form.description} onChange={(e: any) => set({ description: e.target.value })} />
        <div className="row g-3 mt-0">
          <div className="col-12">
            <label className="form-label">Parent phase</label>
            <select className="form-select" value={form.parent_id ?? ''} onChange={(e: any) => set({ parent_id: e.target.value ? Number(e.target.value) : null })}>
              <option value="">None (top level of {row.project.name})</option>
              {parents.map(t => <option key={t.id} value={t.id}>{t.name}</option>)}
            </select>
          </div>
          {!summary && <>
            <div className="col-6">
              <label className="form-label">Application (from PCE)</label>
              <select className="form-select" value={form.application_id ?? ''} disabled={fromPce}
                onChange={(e: any) => set({ application_id: e.target.value ? Number(e.target.value) : null, environment_id: null })}>
                <option value="">All / not specific</option>
                {activeApps(data.apps).map(a => <option key={a.id} value={a.id}>{a.name}</option>)}
              </select>
              {!fromPce && form.application_id != null &&
                <div className="muted mt-1">Once saved, this becomes an application task: its name and application are locked and it cannot be deleted here.</div>}
            </div>
            <div className="col-6">
              <label className="form-label">Environment (from PCE)</label>
              <select className="form-select" value={form.environment_id ?? ''} disabled={fromPce}
                onChange={(e: any) => set({ environment_id: e.target.value ? Number(e.target.value) : null })}>
                <option value="">All</option>
                {envOptions.map(e => <option key={e.id} value={e.id}>{e.name}</option>)}
              </select>
            </div>
          </>}
        </div>
      </div>

      {summary ? (
        <div className="col-lg-7">
          <div className="note mb-3" style={{ background: 'var(--accent-soft)' }}>
            <Icon name="bi-diagram-3" />
            <div>This is a phase. Its dates, duration, % complete and status are rolled up from the tasks underneath it.</div>
          </div>
          <div className="facts">
            <div><div className="k">Start</div><span className="num">{fmtDate(row.start)}</span></div>
            <div><div className="k">End</div><span className="num">{fmtDate(row.end)}</span></div>
            <div><div className="k">Duration</div><span className="num">{row.duration != null ? `${row.duration} days` : '—'}</span></div>
            <div><div className="k">Status</div><TaskStatusPill status={row.status} /></div>
            <div className="span2"><div className="k">% Complete</div><ProgressBar value={row.progress} wide /></div>
          </div>
        </div>
      ) : (
        <div className="col-lg-7">
          <div className="row g-3">
            <div className="col-md-4">
              <label className="form-label">Priority <span className="req">*</span></label>
              <select className="form-select" value={form.priority} onChange={(e: any) => set({ priority: e.target.value })}>
                {PRIORITIES.map(p => <option key={p}>{p}</option>)}
              </select>
            </div>
            <div className="col-md-4">
              <label className="form-label">Status</label>
              <select className="form-select" value={form.status} onChange={(e: any) => set({ status: e.target.value })}>
                {TASK_STATUSES.map(s => <option key={s}>{s}</option>)}
              </select>
            </div>
            <div className="col-md-4">
              <label className="form-label" htmlFor="task-pct">% Complete</label>
              <div className="input-group">
                <input id="task-pct" type="number" className="form-control num" min={0} max={100} value={form.progress}
                  onChange={(e: any) => set({ progress: Math.max(0, Math.min(100, Math.round(Number(e.target.value)) || 0)) })} />
                <span className="input-group-text">%</span>
              </div>
              <input type="range" className="pct-range" min={0} max={100} step={5} value={form.progress} aria-label="% complete slider"
                onChange={(e: any) => set({ progress: Number(e.target.value) })} />
            </div>
            <div className="col-md-4">
              <label className="form-label">Assigned PS</label>
              <PersonSelect role="ps" value={form.ps_id} onChange={id => set({ ps_id: id })} />
            </div>
            <div className="col-md-4">
              <label className="form-label">Project Manager</label>
              <PersonSelect role="pm" value={form.pm_id} onChange={id => set({ pm_id: id })} />
            </div>
            <div className="col-md-4">
              <label className="form-label">App Owner</label>
              <PersonSelect role="owner" value={form.owner_id} onChange={id => set({ owner_id: id })} />
            </div>
            <div className="col-md-4">
              <label className="form-label">Start Date</label>
              <input type="date" className="form-control" value={form.start_date} onChange={(e: any) => set({ start_date: e.target.value })} />
            </div>
            <div className="col-md-4">
              <label className="form-label">End Date</label>
              <input type="date" className={`form-control${badDates ? ' is-invalid' : ''}`} value={form.end_date} min={form.start_date || undefined}
                onChange={(e: any) => set({ end_date: e.target.value })} />
              {badDates && <div className="invalid-feedback">End date cannot be before the start date.</div>}
            </div>
            <div className="col-md-4">
              <label className="form-label" htmlFor="task-dur">Duration</label>
              <div className="input-group">
                <input id="task-dur" type="number" min={1} className="form-control num" disabled={!form.start_date} value={duration ?? ''}
                  onChange={(e: any) => { const n = Math.round(Number(e.target.value)); if (n >= 1) set({ end_date: addDays(form.start_date, n - 1) }); }} />
                <span className="input-group-text">days</span>
              </div>
            </div>
          </div>
        </div>
      )}
      <div className="col-12 d-flex gap-2">
        {canDelete
          ? <button className="btn btn-outline-danger btn-sm" onClick={remove}><Icon name="bi-trash3" /> Delete</button>
          : <span className="muted align-self-center"><Icon name="bi-lock" /> {fromPce
              ? `Cannot be deleted here. Exclude the ${task.environment_id ? 'environment' : 'application'} in Application Tracking to take it off the timeline.`
              : 'Cannot be deleted while it contains applications from the PCE.'}</span>}
        <span className="ms-auto" />
        <button className="btn btn-light" disabled={!dirty} onClick={() => setForm(initial)}>Reset</button>
        <button className="btn btn-primary" disabled={!dirty || badDates || !form.name.trim()} onClick={save}>Save Changes</button>
      </div>
    </div>
  );
}

function ProjectForm({ row, act, onDeleted }: { row: TRow; act: Act; onDeleted: () => void }) {
  const p = row.project;
  const [name, setName] = useState(p.name);
  const [description, setDescription] = useState(p.description);
  const dirty = name !== p.name || description !== p.description;
  return (
    <div className="row g-3">
      <div className="col-lg-5">
        <label className="form-label">Project Name <span className="req">*</span></label>
        <input className="form-control" value={name} maxLength={120} onChange={(e: any) => setName(e.target.value)} />
        <label className="form-label mt-3">Description</label>
        <textarea className="form-control" rows={2} maxLength={500} value={description} onChange={(e: any) => setDescription(e.target.value)} />
      </div>
      <div className="col-lg-7">
        <div className="facts">
          <div><div className="k">Start</div><span className="num">{fmtDate(row.start)}</span></div>
          <div><div className="k">End</div><span className="num">{fmtDate(row.end)}</span></div>
          <div><div className="k">Duration</div><span className="num">{row.duration != null ? `${row.duration} days` : '—'}</span></div>
          <div><div className="k">Status</div><TaskStatusPill status={row.status} /></div>
          <div className="span2"><div className="k">% Complete</div><ProgressBar value={row.progress} wide /></div>
        </div>
      </div>
      <div className="col-12 d-flex gap-2">
        <button className="btn btn-outline-danger btn-sm" onClick={async () => {
          if (confirm(`Delete the project "${p.name}" and all of its tasks?`) &&
            await act(() => api('DELETE', `/api/projects/${p.id}`), 'Project deleted')) onDeleted();
        }}><Icon name="bi-trash3" /> Delete project</button>
        <span className="ms-auto" />
        <button className="btn btn-primary" disabled={!dirty || !name.trim()}
          onClick={() => act(() => api('PATCH', `/api/projects/${p.id}`, { name, description }), 'Project saved')}>Save Changes</button>
      </div>
    </div>
  );
}

// Type-to-search picker for adding a predecessor. Arrow keys move, Enter adds, Escape closes.
function TaskSearch({ options, parents, onPick }: { options: Task[]; parents: Map<number, Task>; onPick: (t: Task) => void }) {
  const [q, setQ] = useState('');
  const [open, setOpen] = useState(false);
  const [at, setAt] = useState(0);
  const list = useRef(null as HTMLDivElement | null);
  const where = (t: Task) => (t.parent_id != null && parents.get(t.parent_id) ? taskLabel(parents.get(t.parent_id)!) : 'Top level');
  const words = q.trim().toLowerCase().split(/\s+/).filter(Boolean);
  const found = options.filter(t => {
    const hay = `${taskLabel(t)} ${t.name} ${where(t)} ${t.status}`.toLowerCase();
    return words.every(w => hay.includes(w));
  });
  const shown = found.slice(0, 50);
  const active = Math.min(at, Math.max(shown.length - 1, 0));
  useEffect(() => {
    const el = list.current && (list.current.children[active] as HTMLElement | undefined);
    if (el && el.scrollIntoView) el.scrollIntoView({ block: 'nearest' });
  }, [active, open]);
  const choose = (t: Task) => { onPick(t); setQ(''); setAt(0); setOpen(false); };
  return (
    <div className="combo mt-2">
      <Icon name="bi-search" />
      <input className="form-control" type="search" role="combobox" aria-expanded={open} aria-controls="pred-options" aria-autocomplete="list"
        aria-label="Search for a task to add as a predecessor" placeholder="Search tasks to add as a predecessor…" autoComplete="off" value={q}
        onChange={(e: any) => { setQ(e.target.value); setAt(0); setOpen(true); }}
        onFocus={() => setOpen(true)} onBlur={() => setOpen(false)}
        onKeyDown={(e: any) => {
          if (e.key === 'ArrowDown') { e.preventDefault(); setOpen(true); setAt(Math.min(active + 1, shown.length - 1)); }
          if (e.key === 'ArrowUp') { e.preventDefault(); setAt(Math.max(active - 1, 0)); }
          if (e.key === 'Enter' && open && shown[active]) { e.preventDefault(); choose(shown[active]); }
          if (e.key === 'Escape') setOpen(false);
        }} />
      {open && (
        <div className="combo-list" id="pred-options" role="listbox" ref={list}>
          {shown.map((t, i) => (
            // mousedown, not click: it fires before the input's blur closes the list
            <div key={t.id} role="option" aria-selected={i === active} className={`combo-opt${i === active ? ' on' : ''}`}
              onMouseEnter={() => setAt(i)} onMouseDown={(e: any) => { e.preventDefault(); choose(t); }}>
              <div><b>{taskLabel(t)}</b><span className="muted"> · {where(t)}</span></div>
              <div className="muted num">{fmtDate(t.start_date)} → {fmtDate(t.end_date)} · {t.status}</div>
            </div>
          ))}
          {shown.length === 0 && <div className="combo-empty muted">{options.length ? `No task matches “${q.trim()}”.` : 'There are no other tasks to add.'}</div>}
          {found.length > shown.length && <div className="combo-empty muted">Showing the first {shown.length} of {found.length} — keep typing to narrow it down.</div>}
        </div>
      )}
    </div>
  );
}

function DependenciesTab({ task, tl, act }: { task: Task; tl: TimelineData; act: Act }) {
  const byId = new Map(tl.tasks.map(t => [t.id, t] as [number, Task]));
  const preds = tl.dependencies.filter(d => d.successor_id === task.id).map(d => byId.get(d.predecessor_id)).filter(Boolean) as Task[];
  const succs = tl.dependencies.filter(d => d.predecessor_id === task.id).map(d => byId.get(d.successor_id)).filter(Boolean) as Task[];
  const taken = new Set([task.id, ...preds.map(t => t.id)]);
  const options = tl.tasks.filter(t => t.project_id === task.project_id && !taken.has(t.id));
  const line = (t: Task, late: boolean, onRemove?: () => void) => (
    <div key={t.id} className="pce-row" style={{ padding: '8px 0' }}>
      <div style={{ flex: 1 }}>
        <b>{taskLabel(t)}</b>
        {late && <span className="flag"><Icon name="bi-exclamation-circle-fill" />Overlaps</span>}
        <div className="muted num">{fmtDate(t.start_date)} → {fmtDate(t.end_date)}</div>
      </div>
      <TaskStatusPill status={t.status} />
      {onRemove && <button className="kebab" aria-label={`Remove dependency on ${taskLabel(t)}`} onClick={onRemove}><Icon name="bi-x-lg" /></button>}
    </div>
  );
  return (
    <div className="grid-2">
      <div>
        <div className="form-label">Predecessors — must finish before this task starts</div>
        {preds.length === 0 && <div className="muted mb-2">None.</div>}
        {preds.map(t => line(t, !!t.end_date && !!task.start_date && task.start_date <= t.end_date,
          () => act(() => api('DELETE', `/api/tasks/${task.id}/dependencies/${t.id}`))))}
        <TaskSearch options={options} parents={byId}
          onPick={t => act(() => api('POST', `/api/tasks/${task.id}/dependencies`, { predecessor_id: t.id }), `${taskLabel(t)} added as a predecessor`)} />
      </div>
      <div>
        <div className="form-label">Successors — waiting on this task</div>
        {succs.length === 0 && <div className="muted">None.</div>}
        {succs.map(t => line(t, !!task.end_date && !!t.start_date && t.start_date <= task.end_date))}
      </div>
    </div>
  );
}

function TaskPanel({ row, tl, act, version, canDelete, onDeleted }: {
  row: TRow; tl: TimelineData; act: Act; version: number; canDelete: boolean; onDeleted: () => void;
}) {
  const { data, toast } = useStore();
  const task = row.task!;
  const [tab, setTab] = useState('details');
  const [detail, setDetail] = useState({ comments: [] as Activity[], events: [] as Activity[], resources: [] as number[] });
  const [draft, setDraft] = useState('');
  const load = () => api('GET', `/api/tasks/${task.id}`).then(setDetail).catch((e: any) => toast(e.message, 'err'));
  useEffect(() => { load(); }, [task.id, version]);
  const depCount = tl.dependencies.filter(d => d.successor_id === task.id || d.predecessor_id === task.id).length;
  const toggleResource = (id: number, on: boolean) => {
    const ids = on ? [...detail.resources, id] : detail.resources.filter(x => x !== id);
    act(() => api('PUT', `/api/tasks/${task.id}/resources`, { person_ids: ids }));
  };
  const tabs: [string, string, number | null][] = [
    ['details', 'Task Details', null], ['deps', 'Dependencies', depCount], ['resources', 'Resources', detail.resources.length],
    ['comments', 'Comments', detail.comments.length], ['log', 'Change Log', null],
  ];
  return (
    <>
      <div className="tabs" role="tablist">
        {tabs.map(([id, label, count]) => (
          <button key={id} role="tab" aria-selected={tab === id} className={tab === id ? 'on' : ''} onClick={() => setTab(id)}>
            {label}{count ? <span className="cnt num">{count}</span> : null}
          </button>
        ))}
      </div>
      {tab === 'details' && <TaskForm key={`${task.id}:${task.updated_at}`} row={row} tl={tl} act={act} canDelete={canDelete} onDeleted={onDeleted} />}
      {tab === 'deps' && <DependenciesTab task={task} tl={tl} act={act} />}
      {tab === 'resources' && (
        data.people.length === 0
          ? <div className="muted">Add people under People Management to assign them here.</div>
          : <>
              <div className="form-label">People working on this task</div>
              <div className="res-grid">
                {data.people.map(p => (
                  <label key={p.id} className="res">
                    <input type="checkbox" className="form-check-input" checked={detail.resources.includes(p.id)}
                      onChange={(e: any) => toggleResource(p.id, e.target.checked)} />
                    <Avatar name={p.name} size={24} />
                    <span><b>{p.name}</b><span className="muted d-block">{p.title || [p.is_ps && 'PS', p.is_pm && 'PM', p.is_owner && 'Owner'].filter(Boolean).join(' · ') || '—'}</span></span>
                  </label>
                ))}
              </div>
            </>
      )}
      {tab === 'comments' && (
        <>
          <TextArea value={draft} onChange={setDraft} rows={2} placeholder="Write a comment…" />
          <div className="text-end mb-2">
            <button className="btn btn-primary btn-sm" disabled={!draft.trim()}
              onClick={async () => { if (await act(() => api('POST', `/api/tasks/${task.id}/comments`, { text: draft }))) setDraft(''); }}>
              Add Comment
            </button>
          </div>
          <Feed items={detail.comments} empty="No comments yet." />
        </>
      )}
      {tab === 'log' && <Feed items={detail.events} empty="No changes recorded yet." />}
    </>
  );
}

function MilestonesCard({ row, rows, tl, act }: { row: TRow | null; rows: TRow[]; tl: TimelineData; act: Act }) {
  const [adding, setAdding] = useState(false);
  const [name, setName] = useState('');
  const [due, setDue] = useState('');
  const task = row && row.task;
  const names = new Map(tl.tasks.map(t => [t.id, taskLabel(t)] as [number, string]));
  const shown = new Set(rows.filter(r => r.task).map(r => r.task!.id));
  const list = task ? tl.milestones.filter(m => m.task_id === task.id) : tl.milestones.filter(m => shown.has(m.task_id));
  useEffect(() => { setAdding(false); setName(''); setDue(''); }, [task && task.id]);
  const add = async () => {
    if (await act(() => api('POST', `/api/tasks/${task!.id}/milestones`, { name, due_date: due }), 'Milestone added')) {
      setAdding(false); setName(''); setDue('');
    }
  };
  return (
    <section className="card-x">
      <div className="hd">
        <h2>Milestones</h2>
        <span className="hint">{task ? taskLabel(task) : 'All visible tasks'}</span>
        <button className="btn btn-primary btn-sm ms-auto" disabled={!task} title={task ? '' : 'Select a task first'}
          onClick={() => { setAdding(true); setDue(task!.end_date || todayISO()); }}>
          <Icon name="bi-plus-lg" /> Add Milestone
        </button>
      </div>
      <div className="bd">
        {adding && (
          <div className="input-group mb-2">
            <input className="form-control" autoFocus placeholder="Milestone name" value={name} maxLength={120} onChange={(e: any) => setName(e.target.value)}
              onKeyDown={(e: any) => { if (e.key === 'Enter' && name.trim() && due) add(); }} />
            <input type="date" className="form-control" style={{ maxWidth: 150 }} aria-label="Milestone date" value={due} onChange={(e: any) => setDue(e.target.value)} />
            <button className="btn btn-primary" disabled={!name.trim() || !due} onClick={add}>Add</button>
            <button className="btn btn-light" aria-label="Cancel" onClick={() => setAdding(false)}><Icon name="bi-x-lg" /></button>
          </div>
        )}
        {list.length === 0 && !adding && <div className="muted">{task ? 'No milestones for this task yet.' : 'No milestones yet. Select a task to add one.'}</div>}
        {list.map(m => (
          <div key={m.id} className="ms-row">
            <svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true">
              <polygon points="7,0.5 13.5,7 7,13.5 0.5,7" style={{ fill: `var(--st-${TASK_TONE[m.status]})` }} />
            </svg>
            <div className="nm">
              <b>{m.name}</b>
              {!task && <span className="muted d-block">{names.get(m.task_id)}</span>}
            </div>
            <span className="num muted">{fmtDate(m.due_date)}</span>
            <PillSelect label={`Status of ${m.name}`} value={m.status} options={TASK_STATUSES} tone={TASK_TONE[m.status]}
              onChange={v => act(() => api('PATCH', `/api/milestones/${m.id}`, { status: v }))} />
            <button className="kebab" aria-label={`Delete milestone ${m.name}`}
              onClick={() => act(() => api('DELETE', `/api/milestones/${m.id}`))}><Icon name="bi-x-lg" /></button>
          </div>
        ))}
      </div>
    </section>
  );
}

function ProjectModal({ act, onClose }: { act: Act; onClose: (id?: number) => void }) {
  const { data } = useStore();
  const [form, setForm] = useState({ name: 'Illumio Implementation', description: '', template: true, start_date: todayISO() });
  const set = (patch: any) => setForm({ ...form, ...patch });
  const save = async () => {
    const res = await act(() => api('POST', '/api/projects', form), 'Project created');
    if (res) onClose(res.id);
  };
  return (
    <Modal title="New Project" icon="bi-kanban" onClose={() => onClose()}
      footer={<>
        <button className="btn btn-light" onClick={() => onClose()}>Cancel</button>
        <button className="btn btn-primary" disabled={!form.name.trim()} onClick={save}>Create Project</button>
      </>}>
      <label className="form-label">Project Name <span className="req">*</span></label>
      <input className="form-control mb-3" autoFocus value={form.name} maxLength={120} onChange={(e: any) => set({ name: e.target.value })} />
      <label className="form-label">Description</label>
      <input className="form-control mb-3" value={form.description} maxLength={500} onChange={(e: any) => set({ description: e.target.value })} />
      <Toggle id="proj-template" checked={form.template} onChange={v => set({ template: v })} label="Start from the Illumio implementation plan" />
      {form.template && (
        <>
          <div className="muted mt-1 mb-3">
            Creates <b>Discovery &amp; Planning</b>, <b>Application Enforcement</b> (one task per tracked application
            — {activeApps(data.apps).length} right now — with a sub-task for each of its environments, copying dates, owners
            and progress) and <b>Validation &amp; Closure</b>.
          </div>
          <label className="form-label">Project start date</label>
          <input type="date" className="form-control" value={form.start_date} onChange={(e: any) => set({ start_date: e.target.value })} />
        </>
      )}
    </Modal>
  );
}

// "Save project as": copies the project under a new name so several plans can exist side by side.
function SaveAsModal({ project, taken, act, onClose }: { project: Project; taken: string[]; act: Act; onClose: (id?: number) => void }) {
  const [name, setName] = useState(() => {
    let n = `${project.name} (copy)`;
    for (let i = 2; taken.includes(n); i++) n = `${project.name} (copy ${i})`;
    return n;
  });
  const [description, setDescription] = useState(project.description);
  const clash = taken.includes(name.trim());
  const save = async () => {
    const res = await act(() => api('POST', `/api/projects/${project.id}/copy`, { name, description }), `Saved as "${name.trim()}"`);
    if (res) onClose(res.id);
  };
  return (
    <Modal title="Save Project As" icon="bi-save" onClose={() => onClose()}
      footer={<>
        <button className="btn btn-light" onClick={() => onClose()}>Cancel</button>
        <button className="btn btn-primary" disabled={!name.trim() || clash} onClick={save}>Save Project</button>
      </>}>
      <p className="muted">Saves a copy of <b>{project.name}</b> as a separate project, with its tasks, dependencies, milestones,
        resources and baseline. The original stays as it is, and the two are edited independently from then on.</p>
      <label className="form-label">New project name <span className="req">*</span></label>
      <input className={`form-control${clash ? ' is-invalid' : ''}`} autoFocus value={name} maxLength={120}
        onFocus={(e: any) => e.target.select()} onChange={(e: any) => setName(e.target.value)}
        onKeyDown={(e: any) => { if (e.key === 'Enter' && name.trim() && !clash) save(); }} />
      {clash && <div className="invalid-feedback">A project with that name already exists.</div>}
      <label className="form-label mt-3">Description</label>
      <input className="form-control" value={description} maxLength={500} onChange={(e: any) => setDescription(e.target.value)} />
    </Modal>
  );
}

// Drag handle on the right edge of the Task Name header. Drag or use the arrow keys to resize;
// double-click returns the column to its automatic width.
function ColumnResizer({ width, onChange }: { width: number | null; onChange: (w: number | null) => void }) {
  const drag = useRef(null as null | { x: number; w: number });
  const [active, setActive] = useState(false);
  const clamp = (n: number) => Math.round(Math.max(NAME_MIN, Math.min(NAME_MAX, n)));
  const current = (el: any) => el.parentElement.getBoundingClientRect().width;
  return (
    <span className={`col-resize${active ? ' on' : ''}`} role="separator" tabIndex={0} aria-orientation="vertical"
      aria-label="Resize the Task Name column" aria-valuemin={NAME_MIN} aria-valuemax={NAME_MAX} aria-valuenow={width || undefined}
      title="Drag to resize the Task Name column · double-click for automatic width"
      onPointerDown={(e: any) => { e.preventDefault(); e.stopPropagation(); e.currentTarget.setPointerCapture(e.pointerId); drag.current = { x: e.clientX, w: current(e.currentTarget) }; setActive(true); }}
      onPointerMove={(e: any) => { if (drag.current) onChange(clamp(drag.current.w + e.clientX - drag.current.x)); }}
      onPointerUp={(e: any) => { e.currentTarget.releasePointerCapture(e.pointerId); drag.current = null; setActive(false); }}
      onPointerCancel={() => { drag.current = null; setActive(false); }}
      onDoubleClick={() => onChange(null)}
      onKeyDown={(e: any) => {
        if (e.key === 'ArrowLeft') { e.preventDefault(); onChange(clamp(current(e.currentTarget) - 16)); }
        if (e.key === 'ArrowRight') { e.preventDefault(); onChange(clamp(current(e.currentTarget) + 16)); }
      }} />
  );
}

// Horizontal bar under the table and chart: drag it up to give Task Details and Milestones more
// room, or down to see more rows. Arrow keys work too; double-click returns to the default height.
const PANE_H_DEFAULT = 460, PANE_H_MIN = 150, PANE_H_MAX = 3000;
function HeightSplitter({ height, onChange, pane }: { height: number | null; onChange: (h: number | null) => void; pane: any }) {
  const drag = useRef(null as null | { y: number; h: number });
  const [active, setActive] = useState(false);
  const clamp = (n: number) => Math.round(Math.max(PANE_H_MIN, Math.min(PANE_H_MAX, n)));
  const current = () => (pane.current ? pane.current.getBoundingClientRect().height : PANE_H_DEFAULT);
  return (
    <div className={`tl-hsplit${active ? ' on' : ''}`} role="separator" tabIndex={0} aria-orientation="horizontal"
      aria-label="Resize the timeline to give Task Details and Milestones more or less room"
      aria-valuemin={PANE_H_MIN} aria-valuemax={PANE_H_MAX} aria-valuenow={height || PANE_H_DEFAULT}
      title="Drag up or down to resize · double-click to reset"
      onPointerDown={(e: any) => { e.preventDefault(); e.currentTarget.setPointerCapture(e.pointerId); drag.current = { y: e.clientY, h: current() }; setActive(true); }}
      onPointerMove={(e: any) => { if (drag.current) onChange(clamp(drag.current.h + e.clientY - drag.current.y)); }}
      onPointerUp={(e: any) => { e.currentTarget.releasePointerCapture(e.pointerId); drag.current = null; setActive(false); }}
      onPointerCancel={() => { drag.current = null; setActive(false); }}
      onDoubleClick={() => onChange(null)}
      onKeyDown={(e: any) => {
        if (e.key === 'ArrowUp') { e.preventDefault(); onChange(clamp(current() - 32)); }
        if (e.key === 'ArrowDown') { e.preventDefault(); onChange(clamp(current() + 32)); }
      }}>
      <span />
    </div>
  );
}

// Draggable divider between the task grid and the Gantt chart. Drag, use the arrow keys, or
// double-click to reset; the chosen width is remembered in this browser.
const SPLIT_DEFAULT = 54, SPLIT_MIN = 20, SPLIT_MAX = 80;
function PaneSplitter({ value, onChange, container }: { value: number; onChange: (pct: number) => void; container: any }) {
  const [active, setActive] = useState(false);
  const dragging = useRef(false); // read by pointermove, which can fire before the next render
  const clamp = (n: number) => Math.round(Math.max(SPLIT_MIN, Math.min(SPLIT_MAX, n)) * 10) / 10;
  const fromPointer = (e: any) => {
    const box = container.current.getBoundingClientRect();
    onChange(clamp(((e.clientX - box.left) / box.width) * 100));
  };
  return (
    <div className={`tl-split${active ? ' on' : ''}`} role="separator" tabIndex={0} aria-orientation="vertical"
      aria-label="Resize the task table and the chart" aria-valuemin={SPLIT_MIN} aria-valuemax={SPLIT_MAX} aria-valuenow={Math.round(value)}
      title="Drag to resize · double-click to reset"
      onPointerDown={(e: any) => { e.preventDefault(); e.currentTarget.setPointerCapture(e.pointerId); dragging.current = true; setActive(true); }}
      onPointerMove={(e: any) => { if (dragging.current) fromPointer(e); }}
      onPointerUp={(e: any) => { e.currentTarget.releasePointerCapture(e.pointerId); dragging.current = false; setActive(false); }}
      onPointerCancel={() => { dragging.current = false; setActive(false); }}
      onDoubleClick={() => onChange(SPLIT_DEFAULT)}
      onKeyDown={(e: any) => {
        if (e.key === 'ArrowLeft') { e.preventDefault(); onChange(clamp(value - 2)); }
        if (e.key === 'ArrowRight') { e.preventDefault(); onChange(clamp(value + 2)); }
        if (e.key === 'Home') { e.preventDefault(); onChange(SPLIT_MIN); }
        if (e.key === 'End') { e.preventDefault(); onChange(SPLIT_MAX); }
      }}>
      <span />
    </div>
  );
}

// ----------------------------------------------------------------------- page
function TimelinePage() {
  const { data, toast, person, reload } = useStore();
  const [checked, setChecked] = useState(new Set<number>()); // ticked task ids (leaf tasks only)
  const [tl, setTl] = useState(null as TimelineData | null);
  const [filters, setFiltersState] = useState(() => {
    let projects: number[] = [];
    try { const saved = JSON.parse(localStorage.getItem('tracker-timeline-projects') || '[]'); if (Array.isArray(saved)) projects = saved.filter(n => typeof n === 'number'); }
    catch (e) {}
    return { projects, app: '', env: '', status: '', q: '' } as TFilters;
  });
  // Which projects are loaded is remembered in this browser.
  const setFilters = (f: TFilters) => {
    setFiltersState(f);
    try { localStorage.setItem('tracker-timeline-projects', JSON.stringify(f.projects)); } catch (e) {}
  };
  const [collapsed, setCollapsed] = useState(new Set<string>());
  const [selected, setSelected] = useState(null as string | null);
  const [version, setVersion] = useState(0);
  const [menu, setMenu] = useState(null as null | { kind: 'add' | 'baseline' | 'view' | 'project' | 'load'; anchor: DOMRect });
  const [newProject, setNewProject] = useState(false);
  const [saveAs, setSaveAs] = useState(null as Project | null);
  const [editing, setEditing] = useState(null as string | null);
  const [cellEdit, setCellEdit] = useState(null as null | { key: string; field: string }); // in-grid date / person editors
  const [drag, setDrag] = useState(null as string | null);
  const [over, setOver] = useState(null as null | { key: string; pos: 'before' | 'after' | 'inside' });
  const [view, setView] = useState({ zoom: 'Months', baseline: true, deps: true });
  const panes = useRef(null as HTMLDivElement | null);
  const [split, setSplitState] = useState(() => {
    try { const n = Number(localStorage.getItem('tracker-timeline-split')); return n >= SPLIT_MIN && n <= SPLIT_MAX ? n : SPLIT_DEFAULT; }
    catch (e) { return SPLIT_DEFAULT; }
  });
  const [nameWidth, setNameWidthState] = useState(() => {
    try { const n = Number(localStorage.getItem('tracker-timeline-name-width')); return n >= NAME_MIN && n <= NAME_MAX ? n : null; }
    catch (e) { return null; }
  });
  const setNameWidth = (w: number | null) => {
    setNameWidthState(w);
    try { w ? localStorage.setItem('tracker-timeline-name-width', String(w)) : localStorage.removeItem('tracker-timeline-name-width'); } catch (e) {}
  };
  const cols = gridCols(nameWidth);
  // Height of the table/chart area. Both panes scroll vertically together under their sticky headers.
  const gridPane = useRef(null as HTMLDivElement | null);
  const chartPane = useRef(null as HTMLDivElement | null);
  const [paneHeight, setPaneHeightState] = useState(() => {
    try { const n = Number(localStorage.getItem('tracker-timeline-height')); return n >= PANE_H_MIN && n <= PANE_H_MAX ? n : null; }
    catch (e) { return null; }
  });
  const setPaneHeight = (h: number | null) => {
    setPaneHeightState(h);
    try { h ? localStorage.setItem('tracker-timeline-height', String(h)) : localStorage.removeItem('tracker-timeline-height'); } catch (e) {}
  };
  const maxHeight = paneHeight || PANE_H_DEFAULT;
  const syncScroll = (e: any) => {
    const other = e.currentTarget === gridPane.current ? chartPane.current : gridPane.current;
    if (other && other.scrollTop !== e.currentTarget.scrollTop) other.scrollTop = e.currentTarget.scrollTop;
  };
  const setSplit = (pct: number) => {
    setSplitState(pct);
    try { localStorage.setItem('tracker-timeline-split', String(pct)); } catch (e) {}
  };

  const load = useCallback(async () => { setTl(await api('GET', '/api/timeline')); }, []);
  // Reload after a PCE sync too: it can add tasks and flag deleted applications or environments.
  useEffect(() => { load().catch((e: any) => toast(e.message, 'err')); }, [data.settings.last_sync]);
  const act: Act = async (fn, okMessage) => {
    try {
      const res = await fn();
      // Application and environment tasks write through to Application Tracking, so refresh both.
      await Promise.all([load(), reload()]);
      setVersion((v: number) => v + 1);
      if (okMessage) toast(okMessage);
      return res || true;
    } catch (e: any) { toast(e.message || 'Something went wrong', 'err'); return null; }
  };
  // Ignore remembered projects that no longer exist; none left means "all projects".
  const loaded = tl ? filters.projects.filter(id => tl.projects.some(p => p.id === id)) : [];
  const rows = useMemo(() => (tl ? buildTimelineRows(tl, { ...filters, projects: loaded }, collapsed) : []), [tl, filters, collapsed]);
  if (!tl) return <div className="boot" style={{ height: 240 }}><div className="spinner-border text-primary" role="status" /></div>;

  const set = (patch: Partial<TFilters>) => setFilters({ ...filters, ...patch });
  const selRow = rows.find(r => r.key === selected) || null;
  const toggle = (key: string) => setCollapsed((prev: Set<string>) => {
    const next = new Set(prev);
    next.has(key) ? next.delete(key) : next.add(key);
    return next;
  });
  const openMenu = (kind: 'add' | 'baseline' | 'view' | 'project' | 'load') => (e: any) => setMenu({ kind, anchor: e.currentTarget.getBoundingClientRect() });
  // Where new work goes: next to the selection, else the filtered or first project.
  const project = selRow ? selRow.project : tl.projects.find(p => !loaded.length || loaded.includes(p.id)) || tl.projects[0];
  // Show a project that was just created, saved or picked, without unloading the others.
  const alsoLoad = (id: number) => { if (loaded.length && !loaded.includes(id)) set({ projects: [...loaded, id] }); };
  const toggleProject = (id: number) => {
    const current = loaded.length ? loaded : tl.projects.map(p => p.id);
    const next = current.includes(id) ? current.filter(x => x !== id) : [...current, id];
    if (!next.length) return; // at least one project stays loaded
    set({ projects: next.length === tl.projects.length ? [] : tl.projects.map(p => p.id).filter(x => next.includes(x)) });
  };
  const loadedLabel = !loaded.length ? `All Projects (${tl.projects.length})`
    : loaded.length === 1 ? tl.projects.find(p => p.id === loaded[0])!.name : `${loaded.length} of ${tl.projects.length} projects`;
  // A new task goes inside the row it was added from when that row is a project, a phase or an
  // empty top-level task; otherwise it joins the same phase as that row. It opens ready to rename.
  const addTask = async (asPhase: boolean, from: TRow | null = selRow) => {
    setMenu(null);
    const target = from ? from.project : project;
    if (!target) return;
    let parent: number | null = null;
    if (!asPhase && from && from.task) {
      const container = from.hasChildren || (from.level === 1 && !from.task.application_id);
      parent = container ? from.task.id : from.task.parent_id;
    }
    const res = await act(() => api('POST', '/api/tasks', {
      project_id: target.id, parent_id: parent, name: asPhase ? 'New phase' : 'New task',
      ...(asPhase ? {} : { start_date: todayISO(), end_date: addDays(todayISO(), 6) }),
    }));
    if (res) {
      setCollapsed((prev: Set<string>) => { const next = new Set(prev); next.delete(`p${target.id}`); if (parent) next.delete(`t${parent}`); return next; });
      setSelected(`t${res.id}`);
      setEditing(`t${res.id}`);
    }
  };
  // Drag and drop: a task can be dropped before or after another task, or onto a project, a phase
  // or an empty top-level task to move inside it. It cannot leave its project or enter its own subtree.
  const dragRow = drag ? rows.find(r => r.key === drag) : undefined;
  const dragTree = new Set<number>();
  if (dragRow && dragRow.task) {
    dragTree.add(dragRow.task.id);
    for (let grew = true; grew;) {
      grew = false;
      tl.tasks.forEach(t => { if (t.parent_id != null && dragTree.has(t.parent_id) && !dragTree.has(t.id)) { dragTree.add(t.id); grew = true; } });
    }
  }
  const dropPosition = (r: TRow, e: any): 'before' | 'after' | 'inside' | null => {
    if (!dragRow || !dragRow.task || r.project.id !== dragRow.project.id) return null;
    if (r.task && dragTree.has(r.task.id)) return null;
    if (!r.task) return 'inside';
    const box = e.currentTarget.getBoundingClientRect();
    const at = (e.clientY - box.top) / box.height;
    const container = r.hasChildren || (r.level === 1 && !r.task.application_id);
    if (container) return at < 0.3 ? 'before' : at > 0.7 ? 'after' : 'inside';
    return at < 0.5 ? 'before' : 'after';
  };
  const endDrag = () => { setDrag(null); setOver(null); };
  const drop = (r: TRow, e: any) => {
    const pos = dropPosition(r, e);
    const task = dragRow && dragRow.task;
    endDrag();
    if (!pos || !task) return;
    e.preventDefault();
    if (pos === 'inside') setCollapsed((prev: Set<string>) => { const next = new Set(prev); next.delete(r.key); return next; });
    act(() => api('POST', `/api/tasks/${task.id}/reorder`, { target_id: r.task ? r.task.id : null, position: pos }));
  };
  // Application tasks come from the PCE: they keep their name and cannot be deleted here, and a
  // phase that holds any of them cannot be deleted either.
  const parentOf = new Map(tl.tasks.map(t => [t.id, t.parent_id] as [number, number | null]));
  const holdsApps = new Set<number>();
  tl.tasks.filter(t => t.application_id).forEach(t => {
    for (let p = t.parent_id; p != null && !holdsApps.has(p); p = parentOf.get(p) ?? null) holdsApps.add(p);
  });
  const deletedApps = new Set(data.apps.filter(a => !a.in_pce).map(a => a.id));
  const deletedEnvs = new Set(data.apps.flatMap(a => a.environments.filter(e => !e.in_pce).map(e => `${a.id}:${e.environment_id}`)));
  // What the PCE no longer has: 'app' (application label deleted) or 'env' (environment gone for that application).
  const goneKind = (r: TRow): '' | 'app' | 'env' => {
    const t = r.task;
    if (!t || t.application_id == null) return '';
    if (deletedApps.has(t.application_id)) return 'app';
    return t.environment_id != null && deletedEnvs.has(`${t.application_id}:${t.environment_id}`) ? 'env' : '';
  };
  const goneRows = new Set(rows.filter(r => goneKind(r)).map(r => r.key));
  const canRename = (r: TRow) => !(r.task && r.task.application_id);
  const canDelete = (r: TRow) => !r.task || (!r.task.application_id && !holdsApps.has(r.task.id));
  // Checkboxes: a project or phase box stands for every task listed under it.
  const leavesUnder = (i: number): number[] => {
    const r = rows[i];
    if (r.kind === 'task') return [r.task!.id];
    const ids: number[] = [];
    for (let j = i + 1; j < rows.length && rows[j].level > r.level; j++) if (rows[j].kind === 'task') ids.push(rows[j].task!.id);
    return ids;
  };
  const allLeaves = rows.filter(r => r.kind === 'task').map(r => r.task!.id);
  const tick = (ids: number[], on: boolean) => setChecked((prev: Set<number>) => {
    const next = new Set(prev);
    ids.forEach(id => (on ? next.add(id) : next.delete(id)));
    return next;
  });
  const picked = tl.tasks.filter(t => checked.has(t.id)); // ignores ids that have since disappeared
  // What the ticked rows mean: an application is excluded when its own row or every one of its
  // environment sub-tasks is ticked; otherwise only the ticked environments are excluded for it.
  const pickedApps: number[] = [];
  const pickedEnvs: number[] = []; // application_environments ids
  let pickedPce = 0;
  Array.from(new Set(picked.filter(t => t.application_id != null).map(t => t.application_id as number))).forEach(appId => {
    const mine = picked.filter(t => t.application_id === appId);
    pickedPce += mine.length;
    const envTasks = tl.tasks.filter(t => t.application_id === appId && t.environment_id != null);
    const tickedEnvs = mine.filter(t => t.environment_id != null);
    if (mine.some(t => t.environment_id == null) || (envTasks.length > 0 && tickedEnvs.length === envTasks.length)) { pickedApps.push(appId); return; }
    const app = data.apps.find(a => a.id === appId);
    tickedEnvs.forEach(t => {
      const pair = app && app.environments.find(e => (e as any).environment_id === t.environment_id);
      if (pair && !pickedEnvs.includes(pair.id)) pickedEnvs.push(pair.id);
    });
  });
  const plural = (n: number, word: string) => `${n} ${word}${n === 1 ? '' : 's'}`;
  const pickedLabel = [pickedApps.length && plural(pickedApps.length, 'application'), pickedEnvs.length && plural(pickedEnvs.length, 'environment')]
    .filter(Boolean).join(' and ');
  const excludePicked = async () => {
    const ok = await act(async () => {
      if (pickedApps.length) await api('POST', '/api/applications/bulk', { ids: pickedApps, excluded: true });
      for (const id of pickedEnvs) await api('PATCH', `/api/environments/${id}`, { excluded: true });
      await reload(); // Application Tracking and the dashboard totals read the same flags
    }, `${pickedLabel} excluded and taken off the timeline. Include them again from Application Tracking.`);
    if (ok) setChecked(new Set());
  };
  // Status, %, Start, End, Priority and Assigned PS are edited in the grid on task rows. Phases and projects
  // roll these up from their tasks, so their cells stay read-only.
  const patchTask = (id: number, payload: any) => { setCellEdit(null); act(() => api('PATCH', `/api/tasks/${id}`, payload)); };
  // Status and % can also be set on a project, phase or application row: the value is applied to
  // every task underneath it (including collapsed and filtered-out ones), and the row rolls up again.
  const tasksUnder = (r: TRow): number[] => {
    const kids = new Map<number, Task[]>();
    tl.tasks.filter(t => t.project_id === r.project.id).forEach(t => kids.set(t.parent_id ?? 0, [...(kids.get(t.parent_id ?? 0) || []), t]));
    const out: number[] = [];
    const walk = (parent: number) => (kids.get(parent) || []).forEach(t => (kids.has(t.id) ? walk(t.id) : out.push(t.id)));
    walk(r.task ? r.task.id : 0);
    return out;
  };
  const patchUnder = (r: TRow, payload: any, what: string) => {
    const ids = tasksUnder(r);
    setCellEdit(null);
    act(async () => { for (const id of ids) await api('PATCH', `/api/tasks/${id}`, payload); },
      `${what} applied to ${ids.length} task${ids.length === 1 ? '' : 's'} under ${r.name}`);
  };
  const editCell = (r: TRow, field: string, label: string, display: any, editor: (t: Task) => any, cls = '') => {
    const t = r.kind === 'task' ? r.task : undefined;
    if (!t) return <span className={cls}>{display}</span>;
    if (cellEdit && cellEdit.key === r.key && cellEdit.field === field)
      return <span className="editing" onClick={(e: any) => e.stopPropagation()}>{editor(t)}</span>;
    return (
      <span className={`${cls} can-edit`}>
        <button className="ed-val" title="Click to edit" aria-label={`Edit ${label} of ${r.name}`}
          onClick={(e: any) => { e.stopPropagation(); setSelected(r.key); setCellEdit({ key: r.key, field }); }}>{display}</button>
      </span>
    );
  };
  const dateCell = (r: TRow, field: 'start_date' | 'end_date', label: string, value: string | null) =>
    editCell(r, field, label, <span title={fmtDate(value)}>{fmtDateShort(value)}</span>, t => (
      <InlineInput type="date" width={136} allowEmpty initial={t[field] || ''} label={`${label} of ${r.name}`}
        onCancel={() => setCellEdit(null)}
        onCommit={v => {
          // Moving the start past the end keeps the task's length instead of being refused.
          const length = spanDays(t.start_date, t.end_date);
          if (field === 'start_date' && v && t.end_date && v > t.end_date && length)
            patchTask(t.id, { start_date: v, end_date: addDays(v, length - 1) });
          else patchTask(t.id, { [field]: v || null });
        }} />
    ), 'num');
  const rename = (r: TRow, name: string) => {
    setEditing(null);
    act(() => r.task
      ? api('PATCH', `/api/tasks/${r.task.id}`, { name })
      : api('PATCH', `/api/projects/${r.project.id}`, { name }));
  };
  const removeRow = async (r: TRow) => {
    const what = r.kind === 'project' ? `the project "${r.name}" and all of its tasks`
      : r.hasChildren ? `"${r.name}" and everything under it` : `"${r.name}"`;
    if (!confirm(`Delete ${what}?`)) return;
    const ok = await act(() => api('DELETE', r.task ? `/api/tasks/${r.task.id}` : `/api/projects/${r.project.id}`),
      r.kind === 'project' ? 'Project deleted' : 'Task deleted');
    if (ok && selected === r.key) setSelected(null);
  };
  const exportCsv = () => {
    const safe = (v: any) => {
      let s = v == null ? '' : String(v);
      if (/^[=+\-@]/.test(s)) s = "'" + s;
      return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
    };
    const names = new Map(tl.tasks.map(t => [t.id, taskLabel(t)] as [number, string]));
    const head = ['#', 'Level', 'Type', 'Task Name', 'Application', 'Environment', 'Priority', 'Assigned PS', 'Project Manager', 'App Owner',
      'Start Date', 'End Date', 'Duration (days)', '% Complete', 'Status', 'Predecessors'];
    const lines = rows.map((r, i) => {
      const t = r.task;
      const leaf = r.kind === 'task' ? t : undefined;
      return [i + 1, r.level, r.kind === 'project' ? 'Project' : r.kind === 'summary' ? 'Phase' : 'Task', r.name,
        leaf?.application || '', leaf?.environment || '', leaf?.priority || '',
        leaf ? person(leaf.ps_id)?.name || '' : '', leaf ? person(leaf.pm_id)?.name || '' : '', leaf ? person(leaf.owner_id)?.name || '' : '',
        r.start || '', r.end || '', r.duration ?? '', r.progress, r.status,
        t ? tl.dependencies.filter(d => d.successor_id === t.id).map(d => names.get(d.predecessor_id)).join('; ') : '',
      ].map(safe).join(',');
    });
    const url = URL.createObjectURL(new Blob([[head.join(','), ...lines].join('\n')], { type: 'text/csv' }));
    const a = document.createElement('a');
    a.href = url; a.download = `illumio-project-timeline-${todayISO()}.csv`; a.click();
    URL.revokeObjectURL(url);
  };
  const hasBaseline = tl.tasks.some(t => t.project_id === project?.id && t.baseline_start);
  const move = (direction: 'up' | 'down') => act(() => api('POST', `/api/tasks/${selRow!.task!.id}/move`, { direction }));

  if (tl.projects.length === 0) {
    return (
      <section className="card-x">
        <Empty icon="bi-kanban" title="No project plan yet">
          <p className="mb-3">Create a project to plan phases, tasks, dependencies and milestones on a Gantt chart.</p>
          <button className="btn btn-primary" onClick={() => setNewProject(true)}><Icon name="bi-plus-lg" /> New Project</button>
        </Empty>
        {newProject && <ProjectModal act={act} onClose={() => setNewProject(false)} />}
      </section>
    );
  }

  return (
    <>
      <section className="card-x">
        <div className="filters no-print">
          <div className="f">
            <label>Projects</label>
            <button className="form-select form-select-sm text-start text-truncate" aria-haspopup="menu" onClick={openMenu('load')}
              title="Choose which saved projects to load">{loadedLabel}</button>
          </div>
          <button className="btn btn-light btn-sm" aria-haspopup="menu" onClick={openMenu('project')}
            title="Save this project under a new name, or start another project">
            <Icon name="bi-save" /> Save Project
          </button>
          <div className="f">
            <label>Application</label>
            <select className="form-select form-select-sm" value={filters.app} onChange={(e: any) => set({ app: e.target.value })}>
              <option value="">All Applications</option>
              {activeApps(data.apps).map(a => <option key={a.id} value={a.id}>{a.name}</option>)}
            </select>
          </div>
          <div className="f">
            <label>Environment</label>
            <select className="form-select form-select-sm" value={filters.env} onChange={(e: any) => set({ env: e.target.value })}>
              <option value="">All Environments</option>
              {tl.environments.map(e => <option key={e.id} value={e.id}>{e.name}</option>)}
            </select>
          </div>
          <div className="f">
            <label>Status</label>
            <select className="form-select form-select-sm" value={filters.status} onChange={(e: any) => set({ status: e.target.value })}>
              <option value="">All</option>
              {TASK_STATUSES.map(s => <option key={s}>{s}</option>)}
            </select>
          </div>
          <div className="search">
            <Icon name="bi-search" />
            <input className="form-control form-control-sm" type="search" placeholder="Search tasks…" aria-label="Search tasks"
              value={filters.q} onChange={(e: any) => set({ q: e.target.value })} />
          </div>
          <div className="btn-group">
            <button className="btn btn-primary btn-sm" onClick={() => addTask(false)}><Icon name="bi-plus-lg" /> Add Task</button>
            <button className="btn btn-primary btn-sm px-2" aria-label="More ways to add" aria-haspopup="menu" onClick={openMenu('add')}>
              <Icon name="bi-chevron-down" />
            </button>
          </div>
          <button className="btn btn-light btn-sm" aria-haspopup="menu" onClick={openMenu('baseline')}><Icon name="bi-layout-three-columns" /> Baseline</button>
          <button className="btn btn-light btn-sm" onClick={exportCsv}><Icon name="bi-download" /> Export</button>
          <button className="btn btn-light btn-sm" disabled={split === SPLIT_DEFAULT && nameWidth == null && paneHeight == null}
            onClick={() => { setSplit(SPLIT_DEFAULT); setNameWidth(null); setPaneHeight(null); }}
            title={split === SPLIT_DEFAULT && nameWidth == null && paneHeight == null ? 'The panes and the Task Name column are at their original sizes'
              : 'Put the table, the chart, their height and the Task Name column back to their original sizes'}>
            <Icon name="bi-layout-split" /> Reset panes
          </button>
          <button className="btn btn-light btn-sm" aria-label="View options" aria-haspopup="menu" onClick={openMenu('view')}><Icon name="bi-gear" /></button>
        </div>

        {picked.length > 0 && (
          <div className="bulk no-print">
            <b>{picked.length} selected</b>
            <button className="btn btn-light btn-sm" disabled={!pickedLabel} onClick={excludePicked}
              title="Excludes them in Application Tracking, which also takes them off this timeline. Ticking every environment of an application excludes the application.">
              <Icon name="bi-slash-circle" /> Exclude {pickedLabel || 'from PCE tracking'}
            </button>
            {picked.length > pickedPce && (
              <span className="muted">
                {pickedLabel
                  ? `${picked.length - pickedPce} selected task(s) are not PCE applications or environments and will be left as they are.`
                  : 'Only tasks that are PCE applications or environments can be excluded.'}
              </span>
            )}
            <button className="btn btn-link btn-sm ms-auto" onClick={() => setChecked(new Set())}>Clear selection</button>
          </div>
        )}
        {rows.length === 0 ? <Empty icon="bi-funnel" title="No tasks match these filters" /> : (
          <div className="tl" ref={panes}>
            <div className="tl-grid" ref={gridPane} style={{ flexBasis: `${split}%`, maxHeight }} onScroll={syncScroll}>
              <div className="tl-inner" style={nameWidth ? { minWidth: OTHER_COLS_WIDTH + nameWidth } : undefined}>
                <div className="tl-row tl-head" style={{ gridTemplateColumns: cols, height: T_HEAD }}>
                  <span className="c ck">
                    <input type="checkbox" className="form-check-input" aria-label="Select all tasks"
                      checked={allLeaves.length > 0 && allLeaves.every(id => checked.has(id))}
                      onChange={(e: any) => tick(allLeaves, e.target.checked)} />
                  </span>
                  <span className="c">#</span><span className="resizable">Task Name<ColumnResizer width={nameWidth} onChange={setNameWidth} /></span><span>Status</span><span className="r" title="% complete">%</span>
                  <span>Start</span><span>End</span><span className="r">Dur.</span><span>Priority</span><span>Assigned PS</span>
                  <span>Application</span><span>Environment</span>
                </div>
                {rows.map((r, i) => {
                  const leaf = r.kind === 'task' ? r.task : undefined;
                  return (
                    <div key={r.key} role="button" tabIndex={0} aria-pressed={selected === r.key}
                      className={`tl-row${goneRows.has(r.key) ? ' gone' : ''}${selected === r.key ? ' sel' : ''}${r.kind !== 'task' ? ' sum' : ''}${drag === r.key ? ' dragging' : ''}${over && over.key === r.key ? ` drop-${over.pos}` : ''}`}
                      draggable={!!r.task && editing !== r.key && !(cellEdit && cellEdit.key === r.key)}
                      onDragStart={(e: any) => { e.dataTransfer.effectAllowed = 'move'; e.dataTransfer.setData('text/plain', r.name); setDrag(r.key); }}
                      onDragOver={(e: any) => {
                        const pos = dropPosition(r, e);
                        if (!pos) { if (over && over.key === r.key) setOver(null); return; }
                        e.preventDefault();
                        e.dataTransfer.dropEffect = 'move';
                        if (!over || over.key !== r.key || over.pos !== pos) setOver({ key: r.key, pos });
                      }}
                      onDragLeave={() => { if (over && over.key === r.key) setOver(null); }}
                      onDrop={(e: any) => drop(r, e)} onDragEnd={endDrag}
                      style={{ gridTemplateColumns: cols, height: T_ROW }}
                      onClick={() => setSelected(r.key)}
                      onKeyDown={(e: any) => {
                        if (e.target !== e.currentTarget) return;
                        if (e.key === 'Enter') setSelected(r.key);
                        if (e.key === 'F2' && canRename(r)) { e.preventDefault(); setEditing(r.key); }
                      }}>
                      <span className="c ck">
                        {(() => {
                          const ids = leavesUnder(i);
                          return <input type="checkbox" className="form-check-input" disabled={!ids.length}
                            aria-label={r.kind === 'task' ? `Select ${r.name}` : `Select every task under ${r.name}`}
                            checked={ids.length > 0 && ids.every(id => checked.has(id))}
                            onClick={(e: any) => e.stopPropagation()} onChange={(e: any) => tick(ids, e.target.checked)} />;
                        })()}
                      </span>
                      <span className="c num muted grip" title={r.task ? 'Drag to reorder' : undefined}>
                        <span className="n">{i + 1}</span>{r.task && <Icon name="bi-grip-vertical" />}
                      </span>
                      <span className="nm" style={{ paddingLeft: r.level * 16 }}>
                        {r.hasChildren
                          ? <button className="caret" aria-label={collapsed.has(r.key) ? `Expand ${r.name}` : `Collapse ${r.name}`}
                              onClick={(e: any) => { e.stopPropagation(); toggle(r.key); }}>
                              <Icon name={collapsed.has(r.key) ? 'bi-chevron-right' : 'bi-chevron-down'} />
                            </button>
                          : <span className="caret" />}
                        {editing === r.key
                          ? <InlineName initial={r.name} label={`Name of ${r.name}`} onCommit={name => rename(r, name)} onCancel={() => setEditing(null)} />
                          : <>
                              <span className={`txt${leaf?.application_id ? ' lnk' : ''}${canRename(r) ? '' : ' fixed'}`}
                                title={canRename(r) ? `${r.name} — double-click to rename` : `${r.name} — from the PCE; the name cannot be changed`}
                                onDoubleClick={(e: any) => { e.stopPropagation(); if (canRename(r)) setEditing(r.key); }}>{r.name}</span>
                              {goneKind(r) && <span className="flag" title={goneKind(r) === 'env'
                                  ? 'This environment no longer exists for this application in the PCE. Its task and tracking data are kept.'
                                  : 'This application label was deleted in the PCE. Its task and tracking data are kept.'}>
                                <Icon name="bi-exclamation-octagon-fill" />Deleted in PCE</span>}
                              <span className="acts no-print">
                                <button aria-label={`Add a task ${r.kind === 'task' && r.level > 1 ? 'next to' : 'under'} ${r.name}`} title="Add task"
                                  onClick={(e: any) => { e.stopPropagation(); addTask(false, r); }}><Icon name="bi-plus-lg" /></button>
                                {canRename(r) && <button aria-label={`Rename ${r.name}`} title="Rename (F2)"
                                  onClick={(e: any) => { e.stopPropagation(); setEditing(r.key); }}><Icon name="bi-pencil" /></button>}
                                {canDelete(r) && <button className="del" aria-label={`Delete ${r.name}`} title="Delete"
                                  onClick={(e: any) => { e.stopPropagation(); removeRow(r); }}><Icon name="bi-trash3" /></button>}
                                {!canRename(r) && <Icon name="bi-lock" className="lock" />}
                              </span>
                            </>}
                      </span>
                      <span className={leaf || r.hasChildren ? 'pick' : ''} onClick={leaf || r.hasChildren ? (e: any) => e.stopPropagation() : undefined}
                        title={leaf ? undefined : r.hasChildren ? 'Rolled up from the tasks underneath. Choosing a status applies it to all of them.' : undefined}>
                        {leaf
                          ? <PillSelect label={`Status of ${r.name}`} value={leaf.status} options={TASK_STATUSES}
                              tone={TASK_TONE[leaf.status]} onChange={v => patchTask(leaf.id, { status: v })} />
                          : r.hasChildren
                            ? <PillSelect label={`Status of every task under ${r.name}`} value={r.status} options={TASK_STATUSES}
                                tone={TASK_TONE[r.status]} onChange={v => patchUnder(r, { status: v }, `Status "${v}"`)} />
                            : <TaskStatusPill status={r.status} />}
                      </span>
                      {leaf
                        ? editCell(r, 'progress', '% complete', <b>{r.progress}%</b>, t => (
                            <InlineInput type="number" width={64} min={0} max={100} initial={String(t.progress)} label={`% complete of ${r.name}`}
                              onCancel={() => setCellEdit(null)}
                              onCommit={v => patchTask(t.id, { progress: Math.max(0, Math.min(100, Math.round(Number(v)) || 0)) })} />
                          ), 'r num')
                        : !r.hasChildren ? <span className="r num"><b>{r.progress}%</b></span>
                        : cellEdit && cellEdit.key === r.key && cellEdit.field === 'progress'
                          ? <span className="editing" onClick={(e: any) => e.stopPropagation()}>
                              <InlineInput type="number" width={64} min={0} max={100} initial={String(r.progress)} label={`% complete of every task under ${r.name}`}
                                onCancel={() => setCellEdit(null)}
                                onCommit={v => { const n = Math.max(0, Math.min(100, Math.round(Number(v)) || 0)); patchUnder(r, { progress: n }, `${n}%`); }} />
                            </span>
                          : <span className="r num can-edit">
                              <button className="ed-val" aria-label={`Edit % complete of every task under ${r.name}`}
                                title="Rolled up from the tasks underneath. Typing a value applies it to all of them."
                                onClick={(e: any) => { e.stopPropagation(); setSelected(r.key); setCellEdit({ key: r.key, field: 'progress' }); }}><b>{r.progress}%</b></button>
                            </span>}
                      {dateCell(r, 'start_date', 'start date', r.start)}
                      {dateCell(r, 'end_date', 'end date', r.end)}
                      <span className="r num">{r.duration != null ? `${r.duration}d` : '—'}</span>
                      <span className={leaf ? 'pick' : ''} onClick={leaf ? (e: any) => e.stopPropagation() : undefined}>
                        {leaf && <PillSelect label={`Priority of ${r.name}`} value={leaf.priority} options={PRIORITIES}
                          tone={PRIORITY_META[leaf.priority].tone} onChange={v => patchTask(leaf.id, { priority: v })} />}
                      </span>
                      {editCell(r, 'ps_id', 'Assigned PS', leaf ? person(leaf.ps_id)?.name || <span className="muted">Unassigned</span> : '', t => (
                        <InlinePerson role="ps" value={t.ps_id} label={`Assigned PS of ${r.name}`} onCancel={() => setCellEdit(null)}
                          onCommit={async id => { if (id !== t.ps_id) { await reload(); patchTask(t.id, { ps_id: id }); } else setCellEdit(null); }} />
                      ))}
                      <span title={leaf?.application || ''}>{leaf ? leaf.application || <span className="muted">All</span> : ''}</span>
                      <span>{leaf ? leaf.environment || <span className="muted">All</span> : ''}</span>
                    </div>
                  );
                })}
              </div>
            </div>
            <PaneSplitter value={split} onChange={setSplit} container={panes} />
            <Gantt rows={rows} tl={tl} ppd={ZOOMS[view.zoom]} showBaseline={view.baseline} showDeps={view.deps}
              selected={selected} flagged={goneRows} onSelect={setSelected}
              scroller={chartPane} maxHeight={maxHeight} onScroll={syncScroll} />
          </div>
        )}
        {rows.length > 0 && <HeightSplitter height={paneHeight} onChange={setPaneHeight} pane={gridPane} />}
      </section>

      <div className="tl-bottom">
        <section className="card-x">
          <div className="hd">
            <h2>{selRow ? (selRow.task ? taskLabel(selRow.task) : selRow.name) : 'Task Details'}</h2>
            {selRow && <span className="hint">{selRow.kind === 'project' ? 'Project' : selRow.hasChildren ? 'Phase' : 'Task'} in {selRow.project.name}</span>}
            {selRow && selRow.task && (
              <span className="ms-auto d-flex gap-1 no-print">
                <button className="btn btn-light btn-sm" onClick={() => move('up')} aria-label="Move up"><Icon name="bi-arrow-up" /></button>
                <button className="btn btn-light btn-sm" onClick={() => move('down')} aria-label="Move down"><Icon name="bi-arrow-down" /></button>
              </span>
            )}
          </div>
          <div className="bd">
            {!selRow && <div className="muted">Select a row in the table or a bar in the chart to see and edit its details.</div>}
            {selRow && selRow.kind === 'project' && <ProjectForm key={`${selRow.project.id}:${selRow.project.name}`} row={selRow} act={act} onDeleted={() => setSelected(null)} />}
            {selRow && selRow.task && <TaskPanel key={selRow.task.id} row={selRow} tl={tl} act={act} version={version} canDelete={canDelete(selRow)} onDeleted={() => setSelected(null)} />}
          </div>
        </section>
        <MilestonesCard row={selRow} rows={rows} tl={tl} act={act} />
      </div>

      {menu && menu.kind === 'add' && (
        <Menu anchor={menu.anchor} onClose={() => setMenu(null)}>
          <button role="menuitem" onClick={() => addTask(false)}><Icon name="bi-plus-lg" />Add Task</button>
          <button role="menuitem" onClick={() => addTask(true)}><Icon name="bi-diagram-3" />Add Phase</button>
          <button role="menuitem" onClick={async () => {
            setMenu(null);
            const res = await act(() => api('POST', `/api/projects/${project!.id}/generate`, {}));
            if (res) toast(res.added || res.environments
              ? `Added ${res.added} application task(s) and ${res.environments} environment sub-task(s) to ${project!.name}`
              : 'Every tracked application and environment already has a task');
          }}><Icon name="bi-box-seam" />Add applications &amp; environments</button>
          <hr />
          <button role="menuitem" onClick={() => { setMenu(null); setNewProject(true); }}><Icon name="bi-kanban" />New Project…</button>
        </Menu>
      )}
      {menu && menu.kind === 'project' && project && (
        <Menu anchor={menu.anchor} onClose={() => setMenu(null)} align="left">
          <div className="muted" style={{ padding: '6px 10px 8px', maxWidth: 280 }}>
            <b style={{ color: 'var(--ink)' }}>{project.name}</b><br />
            Changes are saved automatically as you make them.
          </div>
          <hr />
          <button role="menuitem" onClick={() => { setMenu(null); setSaveAs(project); }}><Icon name="bi-save" />Save as a new project…</button>
          <button role="menuitem" onClick={() => { setMenu(null); setNewProject(true); }}><Icon name="bi-kanban" />New project…</button>
          <button role="menuitem" onClick={() => { setMenu(null); setSelected(`p${project.id}`); }}>
            <Icon name="bi-pencil" />Rename or delete this project
          </button>
        </Menu>
      )}
      {menu && menu.kind === 'load' && (
        <Menu anchor={menu.anchor} onClose={() => setMenu(null)} align="left" keepOnScroll>
          <div style={{ minWidth: 260, maxWidth: 360, maxHeight: 360, overflowY: 'auto' }}>
            <div className="muted" style={{ padding: '4px 10px 6px' }}>Load saved projects</div>
            <label className="menu-check">
              <input type="checkbox" className="form-check-input" checked={!loaded.length} onChange={() => set({ projects: [] })} />
              <b>All projects</b><span className="muted ms-auto num">{tl.projects.length}</span>
            </label>
            <hr />
            {tl.projects.map(p => {
              const on = !loaded.length || loaded.includes(p.id);
              const count = tl.tasks.filter(t => t.project_id === p.id).length;
              return (
                <label key={p.id} className="menu-check" title={p.description || p.name}>
                  <input type="checkbox" className="form-check-input" checked={on} onChange={() => toggleProject(p.id)} />
                  <span className="text-truncate">{p.name}</span><span className="muted ms-auto num">{count} tasks</span>
                </label>
              );
            })}
            <hr />
            <button role="menuitem" onClick={() => { setMenu(null); setNewProject(true); }}><Icon name="bi-plus-lg" />New project…</button>
          </div>
        </Menu>
      )}
      {menu && menu.kind === 'baseline' && (
        <Menu anchor={menu.anchor} onClose={() => setMenu(null)}>
          <button role="menuitem" onClick={() => {
            setMenu(null);
            if (!hasBaseline || confirm(`Replace the saved baseline for ${project!.name} with the current dates?`))
              act(() => api('POST', `/api/projects/${project!.id}/baseline`, {}), `Baseline saved for ${project!.name}`);
          }}><Icon name="bi-bookmark-check" />Save current dates as baseline</button>
          <button role="menuitem" disabled={!hasBaseline} onClick={() => {
            setMenu(null);
            act(() => api('DELETE', `/api/projects/${project!.id}/baseline`), 'Baseline cleared');
          }}><Icon name="bi-bookmark-x" />Clear baseline</button>
        </Menu>
      )}
      {menu && menu.kind === 'view' && (
        <Menu anchor={menu.anchor} onClose={() => setMenu(null)}>
          <div style={{ padding: '6px 10px', width: 220 }}>
            <div className="form-label">Zoom</div>
            <div className="btn-group w-100 mb-3">
              {Object.keys(ZOOMS).map(z => (
                <button key={z} className={`btn btn-sm justify-content-center ${view.zoom === z ? 'btn-primary' : 'btn-light'}`}
                  onClick={() => setView({ ...view, zoom: z })}>{z}</button>
              ))}
            </div>
            <Toggle id="tl-deps" checked={view.deps} onChange={v => setView({ ...view, deps: v })} label="Show dependencies" />
            <Toggle id="tl-base" checked={view.baseline} onChange={v => setView({ ...view, baseline: v })} label="Show baseline" />
          </div>
        </Menu>
      )}
      {newProject && <ProjectModal act={act} onClose={id => { setNewProject(false); if (id) { setSelected(`p${id}`); alsoLoad(id); } }} />}
      {saveAs && <SaveAsModal project={saveAs} taken={tl.projects.map(p => p.name)} act={act}
        onClose={id => { setSaveAs(null); if (id) { setSelected(`p${id}`); alsoLoad(id); } }} />}
    </>
  );
}
