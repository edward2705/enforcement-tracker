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
