// Reports > Illumio Enforcement Summary: one read-only page of KPIs, charts and the project task table,
// with the same content available as an Excel workbook.

const REPORT_COLS = '40px minmax(230px, 1.6fr) 96px 92px 116px 124px 116px 62px 62px 54px 132px 124px';

function PhaseOverview({ phases }: { phases: { name: string; progress: number; counts: Record<TaskStatus, number> }[] }) {
  const tip = useTip();
  const order: TaskStatus[] = ['Completed', 'In Progress', 'Not Started'];
  if (!phases.length) return <div className="muted">No project phases yet.</div>;
  return (
    <div className="phase-list">
      {phases.map(p => {
        const total = order.reduce((n, s) => n + p.counts[s], 0);
        return (
          <div key={p.name} className="phase-row">
            <span className="nm" title={p.name}>{p.name}</span>
            <span className="track">
              {order.filter(s => p.counts[s] > 0).map(s => (
                <i key={s} style={{ flex: p.counts[s], background: `var(--st-${TASK_TONE[s]})` }}
                  {...tip.bind(<><b>{p.name}</b> · {p.counts[s]} of {total} tasks {s.toLowerCase()}</>)} />
              ))}
              {total === 0 && <i style={{ flex: 1, background: 'var(--line)' }} />}
            </span>
            <b className="num">{p.progress}%</b>
          </div>
        );
      })}
      <div className="phase-legend">
        {order.map(s => <span key={s}><span className="swatch" style={{ background: `var(--st-${TASK_TONE[s]})` }} /> {s}</span>)}
      </div>
      {tip.el}
    </div>
  );
}

// Dashboard card: every project's phases with their task mix, % complete and dates.
function ProjectOverviewCard() {
  const { data, toast } = useStore();
  const tip = useTip();
  const [tl, setTl] = useState(null as TimelineData | null);
  useEffect(() => { api('GET', '/api/timeline').then(setTl).catch((e: any) => toast(e.message, 'err')); }, [data.settings.last_sync, data.apps]);
  const noFold = useMemo(() => new Set<string>(), []);
  const rows = useMemo(() => (tl ? buildTimelineRows(tl, { projects: [], q: '', app: '', env: '', status: '' }, noFold) : []), [tl]);
  const order: TaskStatus[] = ['Completed', 'In Progress', 'Not Started'];
  const kids = new Map<number, Task[]>();
  (tl ? tl.tasks : []).forEach(t => { if (t.parent_id != null) kids.set(t.parent_id, [...(kids.get(t.parent_id) || []), t]); });
  const tally = (t: Task, counts: Record<TaskStatus, number>) => {
    const below = kids.get(t.id);
    if (below) below.forEach(c => tally(c, counts)); else counts[t.status] += 1;
  };
  const projects = rows.filter(r => r.kind === 'project');
  return (
    <section className="card-x">
      <div className="hd">
        <h2>Project Timeline Overview</h2>
        <span className="hint">Phases, their tasks by state, and % complete</span>
        <span className="phase-legend ms-auto" style={{ marginTop: 0 }}>
          {order.map(s => <span key={s}><span className="swatch" style={{ background: `var(--st-${TASK_TONE[s]})` }} /> {s}</span>)}
        </span>
        <a className="btn btn-light btn-sm no-print" href="#/timeline">Open Project Timeline <Icon name="bi-arrow-right" /></a>
      </div>
      <div className="bd">
        {!tl ? <div className="muted">Loading…</div>
          : projects.length === 0 ? <div className="muted">No project plan yet. Create one on the <a href="#/timeline">Project Timeline</a> page.</div>
          : projects.map(p => {
            const phases = rows.filter(r => r.level === 1 && r.task && r.project.id === p.project.id);
            return (
              <div key={p.key} className="overview">
                <div className="overview-head">
                  <b>{p.name}</b>
                  <TaskStatusPill status={p.status} />
                  <span className="muted num">{p.start ? `${fmtDate(p.start)} → ${fmtDate(p.end)}` : 'No dates yet'}{p.duration != null && ` · ${p.duration} days`}</span>
                  <span className="ms-auto" style={{ width: 190 }}><ProgressBar value={p.progress} wide /></span>
                </div>
                {phases.length === 0 && <div className="muted">This project has no phases yet.</div>}
                {phases.map(r => {
                  const counts: Record<TaskStatus, number> = { 'Completed': 0, 'In Progress': 0, 'Not Started': 0 };
                  tally(r.task!, counts);
                  const total = order.reduce((n, st) => n + counts[st], 0);
                  return (
                    <div key={r.key} className="phase-row wide">
                      <span className="nm" title={r.name}>{r.name}</span>
                      <span className="track">
                        {order.filter(st => counts[st] > 0).map(st => (
                          <i key={st} style={{ flex: counts[st], background: `var(--st-${TASK_TONE[st]})` }}
                            {...tip.bind(<><b>{r.name}</b> · {counts[st]} of {total} tasks {st.toLowerCase()}</>)} />
                        ))}
                        {total === 0 && <i style={{ flex: 1, background: 'var(--line)' }} />}
                      </span>
                      <b className="num">{r.progress}%</b>
                      <span className="muted num">{counts['Completed']} of {total} done</span>
                      <span className="muted num">{r.start ? `${fmtDateShort(r.start)} → ${fmtDateShort(r.end)}` : 'No dates'}</span>
                    </div>
                  );
                })}
              </div>
            );
          })}
        {tip.el}
      </div>
    </section>
  );
}

function EnforcementSummary() {
  const { data, toast, person } = useStore();
  const [tl, setTl] = useState(null as TimelineData | null);
  const [project, setProject] = useState('');
  const [f, setF] = useState({ app: '', env: '', status: '', priority: '', ps: '', pm: '', owner: '' });
  const [selected, setSelected] = useState(null as string | null);
  useEffect(() => { api('GET', '/api/timeline').then(setTl).catch((e: any) => toast(e.message, 'err')); }, [data.settings.last_sync]);
  const noFold = useMemo(() => new Set<string>(), []);
  const projects = project ? [Number(project)] : [];
  const rows = useMemo(() => (tl ? buildTimelineRows(tl, { projects, q: '', ...f }, noFold) : []), [tl, project, f]);
  const allRows = useMemo(() => (tl ? buildTimelineRows(tl, { projects, q: '', app: '', env: '', status: '' }, noFold) : []), [tl, project]);
  const set = (patch: any) => setF({ ...f, ...patch });

  const apps = activeApps(data.apps);
  const total = apps.length;
  const byStatus = (s: Status) => apps.filter(a => a.status === s).length;
  const envCount = apps.reduce((n, a) => n + trackedEnvs(a).length, 0);

  // Phase overview: how many leaf tasks under each top-level phase are in each state.
  const phases = useMemo(() => {
    if (!tl) return [];
    const kids = new Map<number, Task[]>();
    tl.tasks.forEach(t => { if (t.parent_id != null) kids.set(t.parent_id, [...(kids.get(t.parent_id) || []), t]); });
    const tally = (t: Task, counts: Record<TaskStatus, number>) => {
      const below = kids.get(t.id);
      if (below) below.forEach(c => tally(c, counts)); else counts[t.status] += 1;
    };
    return allRows.filter(r => r.level === 1 && r.task).map(r => {
      const counts: Record<TaskStatus, number> = { 'Completed': 0, 'In Progress': 0, 'Not Started': 0 };
      tally(r.task!, counts);
      return { name: allRows.filter(x => x.kind === 'project').length > 1 ? `${r.project.name} · ${r.name}` : r.name, progress: r.progress, counts };
    });
  }, [tl, allRows]);

  const people = (role: RoleCode) => data.people.filter(p => (p as any)['is_' + role]);
  const select = (label: string, value: string, onChange: (v: string) => void, options: { value: string | number; label: string }[]) => (
    <div className="f">
      <label>{label}</label>
      <select className="form-select form-select-sm" value={value} onChange={(e: any) => onChange(e.target.value)}>
        <option value="">All</option>
        {options.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
      </select>
    </div>
  );
  const excelUrl = '/api/reports/enforcement-summary.xlsx' + (project ? `?projects=${project}` : '');
  let leafNo = 0;

  return (
    <>
      <section className="report-banner">
        <Logo />
        <div className="brandname">illumio</div>
        <div className="ttl">
          <h2>Illumio Enforcement Tracker</h2>
          <div>Plan. Track. Enforce.</div>
        </div>
        <div className="meta">Last Updated: {fmtDateTime(data.settings.last_sync)}<br />PCE: {data.pce.name}</div>
        <div className="pick no-print">
          <label htmlFor="report-project">Project</label>
          <select id="report-project" className="form-select form-select-sm" value={project} onChange={(e: any) => setProject(e.target.value)}>
            <option value="">All Projects</option>
            {(tl ? tl.projects : []).map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
        </div>
        <a className="btn btn-light no-print" href={excelUrl.replace('.xlsx', '.docx')} title="Download this report as a Word document">
          <Icon name="bi-file-earmark-word" /> Download Word
        </a>
        <a className="btn btn-light no-print" href={excelUrl} title="Download this report as an Excel workbook">
          <Icon name="bi-file-earmark-excel" /> Download Excel
        </a>
        <button className="btn btn-outline-light no-print" onClick={() => window.print()}><Icon name="bi-printer" /> Print</button>
      </section>

      <div className="kpis six">
        <Kpi icon="bi-box-seam" tone="accent" label="Total Applications" value={total} />
        <Kpi icon="bi-layers" tone="accent" label="Environments" value={envCount} />
        <Kpi icon="bi-shield-fill-check" tone="st-fe" label="Fully Enforced" value={byStatus('Fully Enforced')} share={pct(byStatus('Fully Enforced'), total)} />
        <Kpi icon="bi-hourglass-split" tone="st-ip" label="In Progress" value={byStatus('In Progress')} share={pct(byStatus('In Progress'), total)} />
        <Kpi icon="bi-shield-check" tone="st-se" label="Selective Enforcement" value={byStatus('Selective Enforcement')} share={pct(byStatus('Selective Enforcement'), total)} />
        <Kpi icon="bi-circle" tone="st-ns" label="Not Started" value={byStatus('Not Started')} share={pct(byStatus('Not Started'), total)} />
      </div>

      <div className="charts">
        <section className="card-x">
          <div className="hd"><h2>Applications by Status</h2></div>
          <div className="bd">
            <Donut centerLabel="Applications"
              slices={[...STATUSES].reverse().map(s => ({ label: s, value: byStatus(s), color: STATUS_META[s].color }))} />
          </div>
        </section>
        <section className="card-x">
          <div className="hd"><h2>Applications by Priority</h2></div>
          <div className="bd">
            <ColumnChart bars={PRIORITIES.map(p => ({ label: p, value: apps.filter(a => a.priority === p).length, color: PRIORITY_META[p].color }))} />
          </div>
        </section>
        <section className="card-x">
          <div className="hd"><h2>Project Timeline Overview</h2><span className="hint ms-auto">Tasks per phase, and % complete</span></div>
          <div className="bd"><PhaseOverview phases={phases} /></div>
        </section>
      </div>

      <div className="section-title">
        <h2>Project Plan</h2>
        <span>{rows.filter(r => r.kind === 'task').length} task(s){Object.values(f).some(Boolean) ? ' match the filters' : ''} · edit in <a href="#/timeline">Project Timeline</a></span>
      </div>
      <section className="card-x">
        <div className="filters no-print">
          {select('Application', f.app, v => set({ app: v }), apps.map(a => ({ value: a.id, label: a.name })))}
          {select('Environment', f.env, v => set({ env: v }), (tl ? tl.environments : []).map(e => ({ value: e.id, label: e.name })))}
          {select('Status', f.status, v => set({ status: v }), TASK_STATUSES.map(s => ({ value: s, label: s })))}
          {select('Priority', f.priority, v => set({ priority: v }), PRIORITIES.map(p => ({ value: p, label: p })))}
          {select('Assigned PS', f.ps, v => set({ ps: v }), people('ps').map(p => ({ value: p.id, label: p.name })))}
          {select('Project Manager', f.pm, v => set({ pm: v }), people('pm').map(p => ({ value: p.id, label: p.name })))}
          {select('App Owner', f.owner, v => set({ owner: v }), people('owner').map(p => ({ value: p.id, label: p.name })))}
          {Object.values(f).some(Boolean) && <button className="btn btn-light btn-sm"
            onClick={() => setF({ app: '', env: '', status: '', priority: '', ps: '', pm: '', owner: '' })}>Clear Filters</button>}
        </div>
        {!tl ? <div className="boot" style={{ height: 160 }}><div className="spinner-border text-primary" role="status" /></div>
          : tl.projects.length === 0 ? <Empty icon="bi-kanban" title="No project plan yet">Create a project on the <a href="#/timeline">Project Timeline</a> page to see it here.</Empty>
          : rows.length === 0 ? <Empty icon="bi-funnel" title="No tasks match these filters" />
          : (
            <div className="tl report-tl">
              <div className="tl-grid" style={{ flex: '1 1 100%', maxHeight: 560 }}>
                <div className="tl-inner" style={{ minWidth: 1260 }}>
                  <div className="tl-row tl-head" style={{ gridTemplateColumns: REPORT_COLS, height: T_HEAD }}>
                    <span className="c">#</span><span>Task / Application</span><span>Environment</span><span>Priority</span><span>Assigned PS</span>
                    <span>Project Manager</span><span>App Owner</span><span>Start</span><span>End</span><span className="r">Days</span>
                    <span>% Complete</span><span>Status</span>
                  </div>
                  {rows.map(r => {
                    const leaf = r.kind === 'task' ? r.task : undefined;
                    if (leaf) leafNo += 1;
                    return (
                      <div key={r.key} className={`tl-row${r.kind !== 'task' ? ' sum' : ''}${selected === r.key ? ' sel' : ''}${r.level === 1 ? ' phase' : ''}${r.kind === 'project' ? ' proj' : ''}`}
                        style={{ gridTemplateColumns: REPORT_COLS, height: T_ROW }} onClick={() => setSelected(r.key)}>
                        <span className="c num muted">{leaf ? leafNo : ''}</span>
                        <span className="nm" style={{ paddingLeft: r.level * 14 + 8 }} title={r.name}><span className="txt">{r.name}</span></span>
                        <span>{leaf ? leaf.environment || <span className="muted">All</span> : ''}</span>
                        <span>{leaf && <PriorityPill priority={leaf.priority} />}</span>
                        <span>{leaf && (person(leaf.ps_id)?.name || <span className="muted">—</span>)}</span>
                        <span>{leaf && (person(leaf.pm_id)?.name || <span className="muted">—</span>)}</span>
                        <span>{leaf && (person(leaf.owner_id)?.name || <span className="muted">—</span>)}</span>
                        <span className="num" title={fmtDate(r.start)}>{fmtDateShort(r.start)}</span>
                        <span className="num" title={fmtDate(r.end)}>{fmtDateShort(r.end)}</span>
                        <span className="r num">{r.duration != null ? r.duration : '—'}</span>
                        <span><ProgressBar value={r.progress} /></span>
                        <span><TaskStatusPill status={r.status} /></span>
                      </div>
                    );
                  })}
                </div>
              </div>
            </div>
          )}
      </section>
    </>
  );
}

// Reports > Enforcement Summary Report: the Word document for the customer, and the recommendations
// the consultant adds to it. What is typed here is saved with the tracker and printed in the document.
interface RowColumn { key: string; label: string; width: string; kind?: 'text' | 'long' | 'date' | 'select'; options?: string[]; }
const RECOMMENDATION_COLS: RowColumn[] = [
  { key: 'area', label: 'Area', width: '11%' }, { key: 'observation', label: 'Observation', width: '24%', kind: 'long' },
  { key: 'recommendation', label: 'Recommendation', width: '26%', kind: 'long' },
  { key: 'priority', label: 'Priority', width: '9%', kind: 'select', options: ['High', 'Medium', 'Low'] },
  { key: 'owner', label: 'Owner', width: '10%' }, { key: 'target_date', label: 'Target Date', width: '10%', kind: 'date' },
  { key: 'status', label: 'Status', width: '10%', kind: 'select', options: ['Open', 'In Progress', 'Done'] },
];
const RISK_COLS: RowColumn[] = [
  { key: 'risk', label: 'Risk or Dependency', width: '32%', kind: 'long' }, { key: 'impact', label: 'Impact', width: '22%', kind: 'long' },
  { key: 'mitigation', label: 'Mitigation', width: '32%', kind: 'long' }, { key: 'owner', label: 'Owner', width: '14%' },
];
const NEXT_STEP_COLS: RowColumn[] = [
  { key: 'action', label: 'Action', width: '40%', kind: 'long' }, { key: 'owner', label: 'Owner', width: '15%' },
  { key: 'due_date', label: 'Due Date', width: '13%', kind: 'date' }, { key: 'notes', label: 'Notes', width: '32%', kind: 'long' },
];
const ENGAGEMENT_FIELDS: { key: string; label: string; placeholder?: string }[] = [
  { key: 'customer', label: 'Customer' }, { key: 'customer_contact', label: 'Customer contact' },
  { key: 'consultant', label: 'Consultant' }, { key: 'role_company', label: 'Role / Company' },
  { key: 'review_period', label: 'Review period', placeholder: 'e.g. Oct 1 - Oct 31, 2026' },
  { key: 'next_review_date', label: 'Next review date', placeholder: 'e.g. Nov 30, 2026' },
  { key: 'document_version', label: 'Document version', placeholder: 'e.g. 1.0' },
];
const REPORT_SECTIONS = ['Cover page and table of contents', 'Executive Summary', '1. PCE Information Summary', '2. VENs & Workloads',
  '3. Label Summary', '4. Policy and Blocked Traffic by Scope', '5. Application Enforcement Status', '6. Assessment Summary',
  '7. Recommendations'];

function RowsEditor({ title, hint, columns, rows, addLabel, onChange }: {
  title: string; hint: string; columns: RowColumn[]; rows: any[]; addLabel: string; onChange: (rows: any[]) => void;
}) {
  const edit = (i: number, key: string, value: string) => onChange(rows.map((r, n) => (n === i ? { ...r, [key]: value } : r)));
  const blank = () => Object.fromEntries(columns.map(c => [c.key, '']));
  return (
    <div className="cr-block">
      <div className="cr-head"><h3>{title}</h3><span className="hint">{hint}</span></div>
      <table className="cr-table">
        <thead><tr><th style={{ width: 34 }}>#</th>{columns.map(c => <th key={c.key} style={{ width: c.width }}>{c.label}</th>)}<th style={{ width: 38 }} /></tr></thead>
        <tbody>
          {rows.length === 0 && <tr><td colSpan={columns.length + 2} className="muted cr-none">Nothing entered yet. The Word document will show empty rows to fill in by hand.</td></tr>}
          {rows.map((row, i) => (
            <tr key={i}>
              <td className="num muted">{i + 1}</td>
              {columns.map(c => (
                <td key={c.key}>
                  {c.kind === 'select' ? (
                    <select className="form-select form-select-sm" aria-label={c.label} value={row[c.key] || ''} onChange={(e: any) => edit(i, c.key, e.target.value)}>
                      <option value="">—</option>
                      {c.options!.map(o => <option key={o} value={o}>{o}</option>)}
                    </select>
                  ) : c.kind === 'long' ? (
                    <textarea className="form-control form-control-sm" rows={2} maxLength={1000} aria-label={c.label} value={row[c.key] || ''}
                      onChange={(e: any) => edit(i, c.key, e.target.value)} />
                  ) : (
                    <input type={c.kind === 'date' ? 'date' : 'text'} className="form-control form-control-sm" maxLength={200} aria-label={c.label}
                      value={row[c.key] || ''} onChange={(e: any) => edit(i, c.key, e.target.value)} />
                  )}
                </td>
              ))}
              <td><button className="btn btn-light btn-sm" title="Remove this row" aria-label="Remove this row" onClick={() => onChange(rows.filter((_r, n) => n !== i))}><Icon name="bi-trash" /></button></td>
            </tr>
          ))}
        </tbody>
      </table>
      <button className="btn btn-light btn-sm" onClick={() => onChange([...rows, blank()])}><Icon name="bi-plus-lg" /> {addLabel}</button>
    </div>
  );
}

function SummaryReport() {
  const { data, toast } = useStore();
  const [form, setForm] = useState(null as any);
  const [state, setState] = useState('saved' as 'saved' | 'dirty' | 'saving' | 'error');
  const [tl, setTl] = useState(null as TimelineData | null);
  const [project, setProject] = useState('');
  const latest = useRef(null as any);
  const dirty = useRef(false);
  const timer = useRef(0 as any);

  useEffect(() => {
    api('GET', '/api/reports/consultant').then(r => { latest.current = r; setForm(r); }).catch((e: any) => toast(e.message, 'err'));
    api('GET', '/api/timeline').then(setTl).catch(() => {});
    // Leaving the tab with edits still waiting to be saved: save them on the way out.
    return () => { clearTimeout(timer.current); if (dirty.current) api('PUT', '/api/reports/consultant', latest.current).catch(() => {}); };
  }, []);

  const push = async () => {
    if (!dirty.current) return true;
    dirty.current = false;
    setState('saving');
    try {
      await api('PUT', '/api/reports/consultant', latest.current);
      if (!dirty.current) setState('saved');
      return true;
    } catch (e: any) {
      dirty.current = true;
      setState('error');
      toast(e.message, 'err');
      return false;
    }
  };
  // Edits are saved automatically a moment after typing stops.
  const change = (patch: any) => {
    latest.current = { ...latest.current, ...patch };
    setForm(latest.current);
    dirty.current = true;
    setState('dirty');
    clearTimeout(timer.current);
    timer.current = setTimeout(push, 1200);
  };
  const download = async () => {
    clearTimeout(timer.current);
    if (await push()) window.location.href = '/api/reports/enforcement-summary.docx' + (project ? `?projects=${project}` : '');
  };

  const status = { saved: 'All changes saved', dirty: 'Unsaved changes…', saving: 'Saving…', error: 'Not saved - check the connection' }[state];
  return (
    <>
      <section className="report-banner">
        <Logo />
        <div className="brandname">illumio</div>
        <div className="ttl">
          <h2>Illumio Enforcement Summary Report</h2>
          <div>The Word document for the customer, with the consultant's recommendations</div>
        </div>
        <div className="meta">Data as of: {fmtDateTime(data.settings.last_sync)}<br />PCE: {data.pce.name}</div>
        <div className="pick">
          <label htmlFor="word-project">Project</label>
          <select id="word-project" className="form-select form-select-sm" value={project} onChange={(e: any) => setProject(e.target.value)}>
            <option value="">All Projects</option>
            {(tl ? tl.projects : []).map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
        </div>
        <button className="btn btn-light" onClick={download} disabled={!form} title="Save the recommendations and download the report as a Word document">
          <Icon name="bi-file-earmark-word" /> Download Word
        </button>
      </section>

      <section className="card-x">
        <div className="hd"><h2>What the document contains</h2><span className="hint">Built from the last sync; sync from the PCE first for current numbers</span></div>
        <div className="bd"><ol className="cr-sections">{REPORT_SECTIONS.map(s => <li key={s}>{s}</li>)}</ol></div>
      </section>

      <section className="card-x">
        <div className="hd">
          <h2>Recommendations</h2>
          <span className="hint">Filled in by the consultant and printed in section 7 of the Word document. Anything left empty becomes a blank form to fill in by hand.</span>
          <span className={`cr-state ms-auto ${state}`} role="status">{status}</span>
        </div>
        {!form ? <div className="boot" style={{ height: 160 }}><div className="spinner-border text-primary" role="status" /></div> : (
          <div className="bd">
            <div className="cr-block">
              <div className="cr-head"><h3>Engagement Details</h3><span className="hint">Customer and Consultant also appear on the cover page</span></div>
              <div className="cr-fields">
                {ENGAGEMENT_FIELDS.map(f => (
                  <div key={f.key}>
                    <label htmlFor={`cr-${f.key}`}>{f.label}</label>
                    <input id={`cr-${f.key}`} className="form-control form-control-sm" maxLength={200} placeholder={f.placeholder || ''}
                      value={form[f.key] || ''} onChange={(e: any) => change({ [f.key]: e.target.value })} />
                  </div>
                ))}
              </div>
            </div>
            <div className="cr-block">
              <div className="cr-head"><h3>Executive Summary</h3><span className="hint">Overall state of the programme and the main message; shown in the Executive Summary at the front</span></div>
              <textarea className="form-control" rows={4} maxLength={6000} aria-label="Executive summary" value={form.executive_summary || ''}
                onChange={(e: any) => change({ executive_summary: e.target.value })} />
            </div>
            <RowsEditor title="Recommended Actions" hint="One row per recommendation" columns={RECOMMENDATION_COLS} rows={form.recommendations}
              addLabel="Add recommendation" onChange={rows => change({ recommendations: rows })} />
            <RowsEditor title="Risks and Dependencies" hint="What could delay enforcement, and how it is handled" columns={RISK_COLS} rows={form.risks}
              addLabel="Add risk" onChange={rows => change({ risks: rows })} />
            <RowsEditor title="Next Steps" hint="Agreed actions after this review" columns={NEXT_STEP_COLS} rows={form.next_steps}
              addLabel="Add next step" onChange={rows => change({ next_steps: rows })} />
            <div className="cr-block">
              <div className="cr-head"><h3>Additional Notes</h3></div>
              <textarea className="form-control" rows={3} maxLength={6000} aria-label="Additional notes" value={form.notes || ''}
                onChange={(e: any) => change({ notes: e.target.value })} />
            </div>
          </div>
        )}
      </section>
    </>
  );
}
