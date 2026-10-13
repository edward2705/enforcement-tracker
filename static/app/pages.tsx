// Pages: Dashboard, Application Tracking, People, Reports, Settings (Project Timeline is in timeline.tsx).

function Kpi({ icon, tone, label, value, share, sub, active, onClick }: {
  icon: string; tone: string; label: string; value: number; share?: number; sub?: string; active?: boolean; onClick?: () => void;
}) {
  const body = (
    <>
      <span className="chip" style={{ background: `var(--${tone}-bg, var(--accent-soft))`, color: `var(--${tone}, var(--accent))` }}><Icon name={icon} /></span>
      <span className="txt">
        <span className="lbl d-block">{label}</span>
        <span className="val num d-block">{value}</span>
        {sub && <span className="sub d-block">{sub}</span>}
      </span>
      {share != null && <span className="side num">{share}%</span>}
      {share != null && <span className="meter"><i style={{ width: `${share}%`, background: `var(--${tone})` }} /></span>}
    </>
  );
  return onClick
    ? <button className={`card-x kpi${active ? ' on' : ''}`} onClick={onClick} aria-pressed={!!active}>{body}</button>
    : <div className="card-x kpi">{body}</div>;
}

// Headline card: overall progress, where every application sits on the enforcement journey,
// and the few numbers worth a glance (VENs enforced, overdue work, the next due date).
function DashboardHero({ apps, active, onPick, onOverdue }: {
  apps: App[]; active: string; onPick: (status: string) => void; onOverdue: () => void;
}) {
  const { data } = useStore();
  const total = apps.length;
  const avg = total ? Math.round(apps.reduce((n, a) => n + a.progress, 0) / total) : 0;
  const vens = apps.reduce((n, a) => n + managedCount(a), 0);
  const vensFull = apps.reduce((n, a) => n + a.wl_full, 0);
  const overdue = apps.filter(isOverdue).length;
  const gone = apps.filter(a => !a.in_pce).length;
  const today = todayISO();
  const next = apps.filter(a => a.end_date && a.end_date >= today && a.status !== 'Fully Enforced')
    .sort((a, b) => (a.end_date! < b.end_date! ? -1 : 1))[0];
  const R = 52, C = 2 * Math.PI * R;
  const hour = new Date().getHours();
  const greeting = hour < 12 ? 'Good morning' : hour < 18 ? 'Good afternoon' : 'Good evening';
  return (
    <section className="hero">
      <div className="hero-ring" role="img" aria-label={`Average progress ${avg}%`}>
        <svg viewBox="0 0 120 120">
          <defs>
            <linearGradient id="hero-grad" x1="0" y1="0" x2="1" y2="1">
              <stop offset="0" stopColor="#ffb07a" /><stop offset="1" stopColor="#f26b21" />
            </linearGradient>
          </defs>
          <circle cx="60" cy="60" r={R} className="track" />
          <circle cx="60" cy="60" r={R} className="value" stroke="url(#hero-grad)" strokeDasharray={`${(avg / 100) * C} ${C}`} />
        </svg>
        <div className="center"><b className="num">{avg}%</b><span>average progress</span></div>
      </div>

      <div className="hero-main">
        <div className="hero-hello">{greeting}, {data.settings.user_name.split(' ')[0]}</div>
        <h2>{total === 0 ? 'No applications are being tracked yet'
          : `${apps.filter(a => a.status === 'Fully Enforced').length} of ${total} applications are fully enforced`}</h2>
        <div className="journey" role="group" aria-label="Applications by stage of the enforcement journey">
          {STATUSES.map(s => {
            const n = apps.filter(a => a.status === s).length;
            return (
              <button key={s} className={`stage${active === s ? ' on' : ''}`} style={{ flex: Math.max(n, 0.6) }} onClick={() => onPick(s)}
                aria-pressed={active === s} title={`${n} application${n === 1 ? '' : 's'} ${s.toLowerCase()} — click to filter the list`}>
                <i style={{ background: STATUS_META[s].color }} />
                <span className="n num">{n}</span>
                <span className="l">{s}</span>
              </button>
            );
          })}
        </div>
      </div>

      <div className="hero-stats">
        <div><Icon name="bi-hdd-network" /><span><b className="num">{vensFull} of {vens}</b>VENs in full enforcement</span></div>
        <button className={overdue ? 'warn' : ''} onClick={onOverdue} disabled={!overdue} title={overdue ? 'Show overdue applications' : ''}>
          <Icon name={overdue ? 'bi-exclamation-circle-fill' : 'bi-check-circle'} />
          <span><b className="num">{overdue || 'None'}</b>{overdue === 1 ? 'application overdue' : 'applications overdue'}</span>
        </button>
        <div><Icon name="bi-calendar-event" />
          <span><b>{next ? fmtDate(next.end_date) : 'Nothing scheduled'}</b>{next ? `next due: ${next.name}` : 'no upcoming end dates'}</span>
        </div>
        {gone > 0 && <div className="warn"><Icon name="bi-exclamation-octagon-fill" /><span><b className="num">{gone}</b>deleted in the PCE</span></div>}
      </div>
    </section>
  );
}

function DashboardPage() {
  const { data } = useStore();
  const [filters, setFilters] = useState({ ...EMPTY_FILTERS, hideExcluded: true }); // the dashboard opens on in-scope applications
  const apps = activeApps(data.apps); // excluded applications are left out of the KPIs and charts
  const excluded = data.apps.length - apps.length;
  const total = apps.length;
  const byStatus = (s: Status) => apps.filter(a => a.status === s).length;
  const envCount = apps.reduce((n, a) => n + trackedEnvs(a).length, 0);
  const inFlight = byStatus('In Progress') + byStatus('Selective Enforcement');
  const overdue = apps.filter(isOverdue).length;
  const avg = total ? Math.round(apps.reduce((n, a) => n + a.progress, 0) / total) : 0;
  const pickStatus = (s: string) => setFilters({ ...filters, status: filters.status === s ? '' : s });
  const pickPriority = (p: string) => setFilters({ ...filters, priority: filters.priority === p ? '' : p });

  return (
    <>
      <DashboardHero apps={apps} active={filters.status} onPick={pickStatus}
        onOverdue={() => setFilters({ ...filters, attention: filters.attention ? '' : 'overdue' })} />

      <div className="kpis">
        <Kpi icon="bi-box-seam" tone="accent" label="Total Applications" value={total} sub={excluded ? `${excluded} excluded not counted` : 'PCE app labels'} />
        <Kpi icon="bi-layers" tone="accent" label="Environments" value={envCount} sub="Tracked app + env pairs" />
        <Kpi icon="bi-shield-fill-check" tone="st-fe" label="Fully Enforced" value={byStatus('Fully Enforced')}
          sub="of all applications" share={pct(byStatus('Fully Enforced'), total)} active={filters.status === 'Fully Enforced'} onClick={() => pickStatus('Fully Enforced')} />
        <Kpi icon="bi-hourglass-split" tone="st-ip" label="In Progress" value={inFlight} share={pct(inFlight, total)}
          sub={`Incl. ${byStatus('Selective Enforcement')} selective`} />
        <Kpi icon="bi-circle" tone="st-ns" label="Not Started" value={byStatus('Not Started')}
          sub="of all applications" share={pct(byStatus('Not Started'), total)} active={filters.status === 'Not Started'} onClick={() => pickStatus('Not Started')} />
      </div>

      <div className="charts">
        <section className="card-x">
          <div className="hd"><h2>Applications by Status</h2><span className="hint ms-auto">Click to filter</span></div>
          <div className="bd">
            <Donut centerLabel="Applications" active={filters.status} onPick={pickStatus}
              slices={[...STATUSES].reverse().map(s => ({ label: s, value: byStatus(s), color: STATUS_META[s].color }))} />
          </div>
        </section>
        <section className="card-x">
          <div className="hd"><h2>Applications by Priority</h2><span className="hint ms-auto">Click to filter</span></div>
          <div className="bd">
            <ColumnChart onPick={pickPriority}
              bars={PRIORITIES.map(p => ({ label: p, value: apps.filter(a => a.priority === p).length, color: PRIORITY_META[p].color }))} />
          </div>
        </section>
        <section className="card-x">
          <div className="hd">
            <h2>Project Progress</h2>
            <span className="hint ms-auto num">Average {avg}%</span>
            {overdue > 0 && (
              <button className="flag border-0" onClick={() => setFilters({ ...filters, attention: filters.attention ? '' : 'overdue' })}>
                <Icon name="bi-exclamation-circle-fill" />{overdue} overdue
              </button>
            )}
          </div>
          <div className="bd">
            <BarList rows={PROGRESS_BUCKETS.map(b => ({
              label: b.label, color: b.color, value: apps.filter(a => a.progress >= b.min && a.progress <= b.max).length,
            }))} />
          </div>
        </section>
      </div>

      <ProjectOverviewCard />

      <div className="section-title">
        <h2>Applications</h2>
        <span>{filtersActive(filters) ? 'Filtered' : 'In priority order'} · edit in <a href="#/tracking">Application Tracking</a></span>
      </div>
      <TrackerPanel filters={filters} onFilters={setFilters} />
    </>
  );
}

function TrackingPage() {
  const [filters, setFilters] = useState({ ...EMPTY_FILTERS, showEnv: true }); // environments listed under each application
  const [view, setView] = useState('apps');
  return (
    <>
      <div className="tabs report-tabs no-print" role="tablist">
        <button role="tab" aria-selected={view === 'apps'} className={view === 'apps' ? 'on' : ''} onClick={() => setView('apps')}>Applications</button>
        <button role="tab" aria-selected={view === 'ven'} className={view === 'ven' ? 'on' : ''} onClick={() => setView('ven')}>VEN Dashboard</button>
      </div>
      {view === 'apps'
        ? <TrackerPanel filters={filters} onFilters={setFilters} defaultPageSize={25} editable />
        : <VenDashboard />}
    </>
  );
}

// -------------------------------------------------------------------- people
function PersonModal({ person, onClose }: { person: Person | null; onClose: () => void }) {
  const { run } = useStore();
  const [form, setForm] = useState(person
    ? { name: person.name, email: person.email, title: person.title, is_ps: !!person.is_ps, is_pm: !!person.is_pm, is_owner: !!person.is_owner }
    : { name: '', email: '', title: '', is_ps: false, is_pm: false, is_owner: false });
  const set = (patch: any) => setForm({ ...form, ...patch });
  const save = async () => {
    const ok = await run(() => person ? api('PATCH', `/api/people/${person.id}`, form) : api('POST', '/api/people', form),
      person ? 'Person updated' : 'Person added');
    if (ok) onClose();
  };
  return (
    <Modal title={person ? 'Edit Person' : 'Add Person'} icon="bi-person-plus" onClose={onClose}
      footer={<>
        <button className="btn btn-light" onClick={onClose}>Cancel</button>
        <button className="btn btn-primary" disabled={!form.name.trim()} onClick={save}>Save</button>
      </>}>
      <div className="row g-3">
        <div className="col-12">
          <label className="form-label">Name <span className="req">*</span></label>
          <input className="form-control" autoFocus value={form.name} maxLength={80} onChange={(e: any) => set({ name: e.target.value })} />
        </div>
        <div className="col-md-6">
          <label className="form-label">Email</label>
          <input type="email" className="form-control" value={form.email} maxLength={120} onChange={(e: any) => set({ email: e.target.value })} />
        </div>
        <div className="col-md-6">
          <label className="form-label">Title</label>
          <input className="form-control" value={form.title} maxLength={80} onChange={(e: any) => set({ title: e.target.value })} />
        </div>
        <div className="col-12">
          <div className="form-label">Can be assigned as</div>
          <div className="d-flex gap-4">
            {(['ps', 'pm', 'owner'] as RoleCode[]).map(r => (
              <div className="form-check" key={r}>
                <input className="form-check-input" type="checkbox" id={`role-${r}`} checked={(form as any)['is_' + r]}
                  onChange={(e: any) => set({ ['is_' + r]: e.target.checked })} />
                <label className="form-check-label" htmlFor={`role-${r}`}>{ROLE_LABEL[r]}</label>
              </div>
            ))}
          </div>
        </div>
      </div>
    </Modal>
  );
}

function PeoplePage() {
  const { data, run } = useStore();
  const [editing, setEditing] = useState(undefined as undefined | null | Person);
  const load = (p: Person) => data.apps.filter(a => a.ps_id === p.id || a.pm_id === p.id || a.owner_id === p.id).length;
  const remove = (p: Person) => {
    const n = load(p);
    if (confirm(`Delete ${p.name}?${n ? ` They will be unassigned from ${n} application(s).` : ''}`))
      run(() => api('DELETE', `/api/people/${p.id}`), `${p.name} deleted`);
  };
  return (
    <section className="card-x">
      <div className="hd" style={{ paddingBottom: 14 }}>
        <h2>People</h2><span className="hint">{data.people.length} total</span>
        <button className="btn btn-primary btn-sm ms-auto" onClick={() => setEditing(null)}><Icon name="bi-plus-lg" /> Add Person</button>
      </div>
      {data.people.length === 0
        ? <Empty icon="bi-people" title="No people yet">Add the PS engineers, project managers and application owners you assign work to.</Empty>
        : <div className="table-wrap">
            <table className="grid">
              <thead><tr><th>Name</th><th>Title</th><th>Email</th><th>Assigned PS</th><th>Project Manager</th><th>App Owner</th><th>Applications</th><th /></tr></thead>
              <tbody>
                {data.people.map(p => (
                  <tr key={p.id}>
                    <td><span className="person"><Avatar name={p.name} size={26} /><b>{p.name}</b></span></td>
                    <td>{p.title || <span className="muted">—</span>}</td>
                    <td>{p.email ? <a href={`mailto:${p.email}`}>{p.email}</a> : <span className="muted">—</span>}</td>
                    {(['ps', 'pm', 'owner'] as RoleCode[]).map(r => (
                      <td key={r}>
                        <input type="checkbox" className="form-check-input" aria-label={`${p.name} is ${ROLE_LABEL[r]}`}
                          checked={!!(p as any)['is_' + r]}
                          onChange={(e: any) => run(() => api('PATCH', `/api/people/${p.id}`, { ['is_' + r]: e.target.checked }))} />
                      </td>
                    ))}
                    <td className="num">{load(p)}</td>
                    <td className="text-end">
                      <button className="kebab" aria-label={`Edit ${p.name}`} onClick={() => setEditing(p)}><Icon name="bi-pencil" /></button>
                      <button className="kebab" aria-label={`Delete ${p.name}`} onClick={() => remove(p)}><Icon name="bi-trash3" /></button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>}
      {editing !== undefined && <PersonModal person={editing} onClose={() => setEditing(undefined)} />}
    </section>
  );
}

// ------------------------------------------------------------------- reports
function ReportsPage() {
  const { data, run, open, person } = useStore();
  const [group, setGroup] = useState('pm');
  const [view, setView] = useState('summary'); // 'summary' = the enforcement summary report, 'rollups' = tables and exceptions
  const apps = activeApps(data.apps);
  const keyOf: Record<string, (a: App) => string[]> = {
    pm: a => [person(a.pm_id)?.name || 'Unassigned'],
    ps: a => [person(a.ps_id)?.name || 'Unassigned'],
    owner: a => [person(a.owner_id)?.name || 'Unassigned'],
    priority: a => [a.priority],
    env: a => (trackedEnvs(a).length ? trackedEnvs(a).map(e => e.name) : ['No environment']),
  };
  const groups = new Map<string, App[]>();
  apps.forEach(a => keyOf[group](a).forEach(k => groups.set(k, [...(groups.get(k) || []), a])));
  const ordered = [...groups.entries()].sort((a, b) =>
    group === 'priority' ? PRIORITIES.indexOf(a[0] as Priority) - PRIORITIES.indexOf(b[0] as Priority) : b[1].length - a[1].length);
  const overdue = apps.filter(isOverdue);
  // Where the tracker and the PCE disagree about how far enforcement has gone.
  const drift = apps.filter(a => a.in_pce && a.pce_state !== 'No Managed Workloads' && data.pce_state_status[a.pce_state] !== a.status);

  const tabs = (
    <div className="tabs report-tabs no-print" role="tablist">
      <button role="tab" aria-selected={view === 'summary'} className={view === 'summary' ? 'on' : ''} onClick={() => setView('summary')}>Illumio Enforcement Summary</button>
      <button role="tab" aria-selected={view === 'rollups'} className={view === 'rollups' ? 'on' : ''} onClick={() => setView('rollups')}>
        Roll-ups &amp; Exceptions{overdue.length + drift.length > 0 && <span className="cnt num">{overdue.length + drift.length}</span>}
      </button>
      {view === 'rollups' && (
        <span className="ms-auto d-flex gap-2">
          <a className="btn btn-light btn-sm" href="/api/export.csv"><Icon name="bi-download" /> Export CSV</a>
          <button className="btn btn-light btn-sm" onClick={() => window.print()}><Icon name="bi-printer" /> Print</button>
        </span>
      )}
    </div>
  );
  if (view === 'summary') return <>{tabs}<EnforcementSummary /></>;
  return (
    <>
      {tabs}
      <section className="card-x">
        <div className="hd" style={{ paddingBottom: 14 }}>
          <h2>Summary</h2>
          <select className="form-select form-select-sm ms-auto no-print" style={{ width: 200 }} aria-label="Group by" value={group}
            onChange={(e: any) => setGroup(e.target.value)}>
            <option value="pm">By Project Manager</option>
            <option value="ps">By Assigned PS</option>
            <option value="owner">By App Owner</option>
            <option value="priority">By Priority</option>
            <option value="env">By Environment</option>
          </select>
        </div>
        <div className="table-wrap">
          <table className="grid">
            <thead>
              <tr><th>Group</th><th>Applications</th>{STATUSES.map(s => <th key={s}>{s}</th>)}<th>Average progress</th><th>Overdue</th></tr>
            </thead>
            <tbody>
              {ordered.map(([name, list]) => (
                <tr key={name}>
                  <td><b>{name}</b></td>
                  <td className="num">{list.length}</td>
                  {STATUSES.map(s => <td key={s} className="num">{list.filter(a => a.status === s).length || <span className="muted">0</span>}</td>)}
                  <td><ProgressBar value={Math.round(list.reduce((n, a) => n + a.progress, 0) / list.length)} /></td>
                  <td className="num">{list.filter(isOverdue).length || <span className="muted">0</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {apps.length === 0 && <Empty icon="bi-bar-chart" title="Nothing to report yet">Sync from the PCE first.</Empty>}
        </div>
      </section>

      <div className="grid-2">
        <section className="card-x">
          <div className="hd"><h2>Overdue</h2><span className={`count-badge${overdue.length ? ' bad' : ''}`}>{overdue.length}</span>
            <span className="hint">Past end date and not fully enforced</span></div>
          <div className="bd">
            {overdue.length === 0 ? <div className="all-clear"><Icon name="bi-check-circle-fill" />Nothing is overdue.</div> : overdue.map(a => (
              <div key={a.id} className="pce-row">
                <div style={{ flex: 1 }}>
                  <button className="app-link" onClick={() => open({ type: 'details', appId: a.id })}>{a.name}</button>
                  <div className="muted num">Due {fmtDate(a.end_date)} · {a.progress}%</div>
                </div>
                <StatusPill status={a.status} />
              </div>
            ))}
          </div>
        </section>
        <section className="card-x">
          <div className="hd"><h2>Tracker vs PCE</h2><span className={`count-badge${drift.length ? ' warn' : ''}`}>{drift.length}</span>
            <span className="hint">Status differs from what workloads show</span></div>
          <div className="bd">
            {drift.length === 0 ? <div className="all-clear"><Icon name="bi-check-circle-fill" />Tracker statuses match the PCE.</div> : drift.map(a => {
              const implied = data.pce_state_status[a.pce_state];
              return (
                <div key={a.id} className="pce-row">
                  <div style={{ flex: 1 }}>
                    <button className="app-link" onClick={() => open({ type: 'details', appId: a.id })}>{a.name}</button>
                    <div className="muted">Tracker: {a.status} · PCE: {a.pce_state}</div>
                  </div>
                  <button className="btn btn-light btn-sm no-print"
                    onClick={() => run(() => api('PATCH', `/api/applications/${a.id}`, { status: implied }), `${a.name} set to ${implied}`)}>
                    Use “{implied}”
                  </button>
                </div>
              );
            })}
          </div>
        </section>
      </div>
    </>
  );
}

// ------------------------------------------------------------------ settings
interface PceConf { name: string; pce_fqdn: string; port: number; org: number; cert_path: string; api_key_hint: string; has_secret: boolean; is_default: boolean; }

function PceModal({ pce, onClose }: { pce: PceConf | null; onClose: (saved: boolean) => void }) {
  const { run } = useStore();
  const [form, setForm] = useState({
    name: pce?.name || '', pce_fqdn: pce?.pce_fqdn || '', port: pce?.port || 8443, org: pce?.org || 1,
    cert_path: pce?.cert_path || '', api_key: '', api_secret: '',
  });
  const set = (patch: any) => setForm({ ...form, ...patch });
  const save = async () => {
    const { name, ...rest } = form;
    if (await run(() => api('PUT', `/api/pce/${encodeURIComponent(name.trim())}`, rest), 'PCE connection saved')) onClose(true);
  };
  const ready = form.name.trim() && form.pce_fqdn.trim() && (pce || (form.api_key && form.api_secret));
  return (
    <Modal title={pce ? `Edit PCE: ${pce.name}` : 'Add PCE'} icon="bi-hdd-network" onClose={() => onClose(false)}
      footer={<>
        <button className="btn btn-light" onClick={() => onClose(false)}>Cancel</button>
        <button className="btn btn-primary" disabled={!ready} onClick={save}>Save</button>
      </>}>
      <div className="row g-3">
        <div className="col-md-5">
          <label className="form-label">Name <span className="req">*</span></label>
          <input className="form-control" value={form.name} disabled={!!pce} placeholder="e.g. prod" onChange={(e: any) => set({ name: e.target.value })} />
        </div>
        <div className="col-md-7">
          <label className="form-label">PCE FQDN <span className="req">*</span></label>
          <input className="form-control" value={form.pce_fqdn} placeholder="pce.example.com" onChange={(e: any) => set({ pce_fqdn: e.target.value })} />
        </div>
        <div className="col-6">
          <label className="form-label">Port</label>
          <input type="number" className="form-control num" value={form.port} onChange={(e: any) => set({ port: e.target.value })} />
        </div>
        <div className="col-6">
          <label className="form-label">Org ID</label>
          <input type="number" className="form-control num" value={form.org} onChange={(e: any) => set({ org: e.target.value })} />
        </div>
        <div className="col-12">
          <label className="form-label">API Key {!pce && <span className="req">*</span>}</label>
          <input className="form-control" autoComplete="off" value={form.api_key}
            placeholder={pce ? `${pce.api_key_hint} (leave blank to keep)` : 'api_xxxxxxxxxxxx'} onChange={(e: any) => set({ api_key: e.target.value })} />
        </div>
        <div className="col-12">
          <label className="form-label">API Secret {!pce && <span className="req">*</span>}</label>
          <input type="password" className="form-control" autoComplete="new-password" value={form.api_secret}
            placeholder={pce?.has_secret ? 'Stored (leave blank to keep)' : ''} onChange={(e: any) => set({ api_secret: e.target.value })} />
        </div>
        <div className="col-12">
          <label className="form-label">CA certificate path</label>
          <input className="form-control" value={form.cert_path} placeholder="Leave blank to use the system trust store"
            onChange={(e: any) => set({ cert_path: e.target.value })} />
        </div>
      </div>
      <p className="muted mt-3 mb-0">Saved to <code>config.yaml</code> on this machine. Stored secrets are never sent back to the browser.</p>
    </Modal>
  );
}

function SettingsPage() {
  const { data, run, reload, toast } = useStore();
  const [name, setName] = useState(data.settings.user_name);
  const [pces, setPces] = useState([] as PceConf[]);
  const [editing, setEditing] = useState(undefined as undefined | null | PceConf);
  const [testing, setTesting] = useState('');
  const loadPces = () => api('GET', '/api/pce').then(r => setPces(r.pces)).catch((e: any) => toast(e.message, 'err'));
  useEffect(() => { loadPces(); }, []);
  const test = async (p: PceConf) => {
    setTesting(p.name);
    try { toast((await api('POST', `/api/pce/${p.name}/test`, {})).message, 'ok'); }
    catch (e: any) { toast(e.message, 'err'); }
    setTesting('');
  };
  return (
    <>
      <div className="grid-2">
        <section className="card-x">
          <div className="hd"><h2>Profile</h2></div>
          <div className="bd">
            <label className="form-label" htmlFor="display-name">Display name</label>
            <div className="input-group">
              <input id="display-name" className="form-control" value={name} maxLength={80} onChange={(e: any) => setName(e.target.value)} />
              <button className="btn btn-primary" disabled={!name.trim() || name.trim() === data.settings.user_name}
                onClick={() => run(() => api('PUT', '/api/settings', { user_name: name.trim() }), 'Display name saved')}>Save</button>
            </div>
            <div className="muted mt-2">Shown on the comments and changes you make.</div>
          </div>
        </section>
        <section className="card-x">
          <div className="hd"><h2>Sync behaviour</h2></div>
          <div className="bd">
            <ul className="mb-0 ps-3" style={{ display: 'grid', gap: 6 }}>
              <li><b>Your settings are never overwritten.</b> Status, progress, priority, dates, people, comments and
                excluded applications or environments stay exactly as you left them.</li>
              <li>New applications and environments are added, with a starting status taken from their workloads.</li>
              <li>Label names and workload counts are refreshed from the PCE.</li>
              <li>An application whose label was deleted in the PCE is kept and <b style={{ color: 'var(--danger)' }}>marked red</b>.</li>
              <li>The sync only reads from the PCE; it never writes to it.</li>
            </ul>
          </div>
        </section>
      </div>

      <section className="card-x">
        <div className="hd">
          <h2>PCE Configuration</h2><span className="hint">Stored in config.yaml</span>
          <button className="btn btn-light btn-sm ms-auto" onClick={() => setEditing(null)}><Icon name="bi-plus-lg" /> Add PCE</button>
        </div>
        <div className="bd">
          {pces.length === 0 && <span className="muted">No PCE configured yet.</span>}
          {pces.map(p => (
            <div key={p.name} className="pce-row">
              <div className="app-head mb-0" style={{ flex: 1 }}>
                <div className="ic"><Icon name="bi-hdd-network" /></div>
                <div>
                  <div className="nm" style={{ fontSize: 14 }}>
                    {p.name} {p.is_default && <span className="pill t-fe ms-1"><span className="dot" />Active</span>}
                  </div>
                  <div className="muted num">https://{p.pce_fqdn}:{p.port} · org {p.org} · key {p.api_key_hint}</div>
                </div>
              </div>
              <button className="btn btn-light btn-sm" disabled={testing === p.name} onClick={() => test(p)}>
                {testing === p.name ? 'Testing…' : 'Test connection'}
              </button>
              {!p.is_default && (
                <button className="btn btn-light btn-sm"
                  onClick={() => run(() => api('PUT', `/api/pce/${p.name}`, { make_default: true }).then(loadPces), `${p.name} is now the active PCE`)}>
                  Make active
                </button>
              )}
              <button className="kebab" aria-label={`Edit ${p.name}`} onClick={() => setEditing(p)}><Icon name="bi-pencil" /></button>
              {!p.is_default && (
                <button className="kebab" aria-label={`Delete ${p.name}`} onClick={() => {
                  if (confirm(`Remove the PCE connection "${p.name}" from config.yaml?`))
                    run(() => api('DELETE', `/api/pce/${p.name}`).then(loadPces), 'PCE connection removed');
                }}><Icon name="bi-trash3" /></button>
              )}
            </div>
          ))}
        </div>
      </section>

      <section className="card-x">
        <div className="hd"><h2>Removed applications</h2><span className="hint">Hidden from the tracker; history is kept</span></div>
        <div className="bd">
          {data.hidden.length === 0 && <span className="muted">No applications have been removed.</span>}
          {data.hidden.map(a => (
            <div key={a.id} className="pce-row">
              <div style={{ flex: 1 }}><b>{a.name}</b> <span className="muted">· {a.status}</span></div>
              <button className="btn btn-light btn-sm"
                onClick={() => run(() => api('POST', `/api/applications/${a.id}/restore`, {}), `${a.name} restored`)}>Restore</button>
            </div>
          ))}
        </div>
      </section>
      {editing !== undefined && <PceModal pce={editing} onClose={saved => { setEditing(undefined); if (saved) { loadPces(); reload(); } }} />}
    </>
  );
}
