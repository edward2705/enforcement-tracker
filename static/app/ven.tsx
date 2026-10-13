// Application Tracking > VEN Dashboard: managed workloads (VENs) by enforcement mode, overall,
// per application, and per application and environment. Counts come from the last PCE sync.

interface VenRow extends WorkloadCounts { key: string; app: string; env?: string; total: number; }
const VEN_MODES: { key: keyof WorkloadCounts; label: string; color: string }[] = [
  { key: 'wl_full', label: 'Full Enforcement', color: 'var(--st-fe)' },
  { key: 'wl_selective', label: 'Selective Enforcement', color: 'var(--st-se)' },
  { key: 'wl_visibility', label: 'Visibility Only', color: 'var(--st-ip)' },
  { key: 'wl_idle', label: 'Idle', color: 'var(--st-ns)' },
];

function venRow(key: string, app: string, w: WorkloadCounts, env?: string): VenRow {
  return { key, app, env, total: managedCount(w), wl_full: w.wl_full, wl_selective: w.wl_selective,
    wl_visibility: w.wl_visibility, wl_idle: w.wl_idle, wl_unmanaged: w.wl_unmanaged };
}

function VenTable({ title, hint, rows, withEnv, onOpen }: {
  title: string; hint: string; rows: VenRow[]; withEnv?: boolean; onOpen: (app: string) => void;
}) {
  const [sort, setSort] = useState({ key: 'total', dir: -1 as 1 | -1 });
  const sorted = [...rows].sort((a: any, b: any) => {
    const x = a[sort.key] ?? '', y = b[sort.key] ?? '';
    return (x < y ? -1 : x > y ? 1 : a.app.localeCompare(b.app)) * sort.dir;
  });
  const sum = (k: keyof VenRow) => rows.reduce((n, r) => n + (r[k] as number), 0);
  const Th = ({ k, children, num }: { k: string; children: any; num?: boolean }) => (
    <th className={`sortable${num ? ' text-end' : ''}`} aria-sort={sort.key === k ? (sort.dir === 1 ? 'ascending' : 'descending') : 'none'}
      onClick={() => setSort({ key: k, dir: sort.key === k ? (sort.dir === 1 ? -1 : 1) : (num ? -1 : 1) })}>
      {children}
      <Icon name={sort.key !== k ? 'bi-chevron-expand' : sort.dir === 1 ? 'bi-caret-up-fill' : 'bi-caret-down-fill'}
        className={`sort${sort.key === k ? ' on' : ''}`} />
    </th>
  );
  const num = (n: number) => (n ? <span className="num">{n}</span> : <span className="muted num">0</span>);
  return (
    <section className="card-x">
      <div className="hd" style={{ paddingBottom: 12 }}><h2>{title}</h2><span className="hint">{hint}</span></div>
      <div className="table-wrap">
        <table className="grid ven">
          <thead>
            <tr>
              <Th k="app">Application</Th>
              {withEnv && <Th k="env">Environment</Th>}
              <Th k="total" num>VENs</Th>
              {VEN_MODES.map(m => <Th key={m.key} k={m.key} num>{m.label}</Th>)}
              <Th k="wl_unmanaged" num>Unmanaged</Th>
              <th style={{ minWidth: 190 }}>Enforcement mix</th>
              <th className="text-end">Fully enforced</th>
            </tr>
          </thead>
          <tbody>
            {sorted.map(r => (
              <tr key={r.key}>
                <td><button className="app-link" onClick={() => onOpen(r.app)}>{r.app}</button></td>
                {withEnv && <td>{r.env ? <span className="env-chip">{r.env}</span> : <span className="muted">No environment label</span>}</td>}
                <td className="text-end"><b className="num">{r.total}</b></td>
                {VEN_MODES.map(m => <td key={m.key} className="text-end">{num(r[m.key] as number)}</td>)}
                <td className="text-end">{num(r.wl_unmanaged)}</td>
                <td>
                  {r.total > 0
                    ? <div className="wl-bar" title={VEN_MODES.filter(m => r[m.key]).map(m => `${r[m.key]} ${m.label}`).join(', ')}>
                        {VEN_MODES.filter(m => (r[m.key] as number) > 0).map(m => <i key={m.key} style={{ flex: r[m.key] as number, background: m.color }} />)}
                      </div>
                    : <span className="muted">No VENs</span>}
                </td>
                <td className="text-end num">{r.total ? `${pct(r.wl_full, r.total)}%` : <span className="muted">—</span>}</td>
              </tr>
            ))}
          </tbody>
          {rows.length > 0 && (
            <tfoot>
              <tr>
                <td><b>Total</b></td>
                {withEnv && <td />}
                <td className="text-end"><b className="num">{sum('total')}</b></td>
                {VEN_MODES.map(m => <td key={m.key} className="text-end"><b className="num">{sum(m.key)}</b></td>)}
                <td className="text-end"><b className="num">{sum('wl_unmanaged')}</b></td>
                <td />
                <td className="text-end"><b className="num">{sum('total') ? `${pct(sum('wl_full'), sum('total'))}%` : '—'}</b></td>
              </tr>
            </tfoot>
          )}
        </table>
        {rows.length === 0 && <Empty icon="bi-hdd-network" title="Nothing to show">No applications match.</Empty>}
      </div>
    </section>
  );
}

interface Workload {
  id: number; hostname: string; application_id: number | null; application: string | null; environment: string | null;
  managed: number; online: number; enforcement_mode: string; ven_status: string; ven_version: string;
  policy_sync_state: string; last_heartbeat: string | null; os: string; location: string; role: string;
}
const MODE_LABEL: Record<string, string> = { full: 'Full Enforcement', selective: 'Selective Enforcement', visibility_only: 'Visibility Only', idle: 'Idle' };
const MODE_TONE: Record<string, string> = { full: 'fe', selective: 'se', visibility_only: 'ip', idle: 'ns' };
const hoursSince = (iso: string | null): number => (iso ? (Date.now() - new Date(iso).getTime()) / 3600000 : Infinity);
const titleCase = (s: string): string => (s ? s.charAt(0).toUpperCase() + s.slice(1).replace(/_/g, ' ') : 'Unknown');

// Why a VEN deserves a look. An empty list means it is healthy and fully enforced.
function venConcerns(w: Workload): string[] {
  const out: string[] = [];
  if (w.ven_status && w.ven_status !== 'active') out.push(`VEN ${w.ven_status}`);
  if (!w.online) out.push('Offline');
  if (hoursSince(w.last_heartbeat) > 24) out.push(w.last_heartbeat ? 'No heartbeat in 24h' : 'No heartbeat');
  if (w.policy_sync_state && w.policy_sync_state !== 'applied') out.push(`Policy ${w.policy_sync_state}`);
  if (w.enforcement_mode !== 'full') out.push('Not fully enforced');
  return out;
}

function VenDetails({ q }: { q: string }) {
  const { data, toast } = useStore();
  const [list, setList] = useState(null as Workload[] | null);
  const [attention, setAttention] = useState(false);
  const [sort, setSort] = useState({ key: 'application', dir: 1 as 1 | -1 });
  useEffect(() => { api('GET', '/api/ven').then(r => setList(r.workloads)).catch((e: any) => toast(e.message, 'err')); }, [data.settings.last_sync]);
  if (!list) return null;
  const vens = list.filter(w => w.managed);
  if (!list.length) {
    return (
      <section className="card-x">
        <Empty icon="bi-cloud-download" title="VEN details load on the next sync">
          Press <b>Sync from PCE</b> to bring in each VEN's status, version, heartbeat, operating system and location.
        </Empty>
      </section>
    );
  }
  const tally = (key: (w: Workload) => string, order?: string[]) => {
    const m = new Map<string, number>();
    vens.forEach(w => m.set(key(w), (m.get(key(w)) || 0) + 1));
    const rows = [...m.entries()].map(([label, value]) => ({ label, value }));
    return order ? rows.sort((a, b) => order.indexOf(a.label) - order.indexOf(b.label)) : rows.sort((a, b) => b.value - a.value);
  };
  const color = (rows: { label: string; value: number }[], pick: (label: string) => string) => rows.map(r => ({ ...r, color: pick(r.label) }));
  const good = 'var(--st-fe)', warn = 'var(--st-ip)', bad = 'var(--danger)', plain = 'var(--seq-3)';
  const beat = (w: Workload) => { const h = hoursSince(w.last_heartbeat); return h <= 1 ? 'Within the last hour' : h <= 24 ? 'Within 24 hours' : h === Infinity ? 'Never' : 'More than 24 hours ago'; };
  const cards: [string, string, Slice[]][] = [
    ['VEN Status', 'Reported by each VEN', color(tally(w => titleCase(w.ven_status)), l => (l === 'Active' ? good : l === 'Suspended' ? warn : bad))],
    ['Connectivity', 'Online in the PCE', color(tally(w => (w.online ? 'Online' : 'Offline'), ['Online', 'Offline']), l => (l === 'Online' ? good : bad))],
    ['Policy Sync', 'Is the latest policy applied', color(tally(w => titleCase(w.policy_sync_state)), l => (l === 'Applied' ? good : warn))],
    ['Last Heartbeat', 'Time since the VEN checked in',
      color(tally(beat, ['Within the last hour', 'Within 24 hours', 'More than 24 hours ago', 'Never']), l => (l === 'Within the last hour' ? good : l === 'Within 24 hours' ? warn : bad))],
    ['By Environment', 'VENs per environment label', color(tally(w => w.environment || 'No environment label'), () => plain)],
    ['By Operating System', 'From the VEN', color(tally(w => w.os || 'Unknown'), () => plain)],
    ['By VEN Version', 'Useful when planning upgrades', color(tally(w => w.ven_version || 'Unknown'), () => plain)],
    ['By Location', 'VENs per location label', color(tally(w => w.location || 'No location label'), () => plain)],
  ];
  const needle = q.trim().toLowerCase();
  const flagged = vens.filter(w => venConcerns(w).length > 0);
  const health = vens.filter(w => venConcerns(w).some(c => c !== 'Not fully enforced'));
  const shown = (attention ? flagged : vens).filter(w => !needle ||
    `${w.hostname} ${w.application || ''} ${w.environment || ''} ${w.os} ${w.location} ${w.role} ${w.ven_version}`.toLowerCase().includes(needle));
  const sorted = [...shown].sort((a: any, b: any) => {
    const x = (a[sort.key] ?? '\uffff').toString().toLowerCase(), y = (b[sort.key] ?? '\uffff').toString().toLowerCase();
    return (x < y ? -1 : x > y ? 1 : a.hostname.localeCompare(b.hostname)) * sort.dir;
  });
  const Th = ({ k, children }: { k: string; children: any }) => (
    <th className="sortable" aria-sort={sort.key === k ? (sort.dir === 1 ? 'ascending' : 'descending') : 'none'}
      onClick={() => setSort({ key: k, dir: sort.key === k && sort.dir === 1 ? -1 : 1 })}>
      {children}
      <Icon name={sort.key !== k ? 'bi-chevron-expand' : sort.dir === 1 ? 'bi-caret-up-fill' : 'bi-caret-down-fill'} className={`sort${sort.key === k ? ' on' : ''}`} />
    </th>
  );
  const unlabelled = vens.filter(w => !w.application).length;
  return (
    <>
      <div className="ven-cards">
        {cards.map(([title, hint, rows]) => (
          <section key={title} className="card-x">
            <div className="hd"><h2>{title}</h2></div>
            <div className="bd"><div className="muted mb-2" style={{ marginTop: -8 }}>{hint}</div><BarList rows={rows} unit="VENs" /></div>
          </section>
        ))}
      </div>

      <section className="card-x">
        <div className="hd" style={{ paddingBottom: 12, flexWrap: 'wrap' }}>
          <h2>VEN Inventory</h2>
          <span className="hint">
            {shown.length} of {vens.length} VENs · {health.length} with a health concern · {vens.length - flagged.length} healthy and fully enforced
            {unlabelled > 0 && ` · ${unlabelled} without an application label`}
          </span>
          <span className="ms-auto no-print"><Toggle id="ven-attention" checked={attention} onChange={setAttention} label="Needs attention only" /></span>
        </div>
        <div className="table-wrap">
          <table className="grid ven">
            <thead>
              <tr>
                <Th k="hostname">Hostname</Th><Th k="application">Application</Th><Th k="environment">Environment</Th>
                <Th k="enforcement_mode">Enforcement</Th><Th k="ven_status">VEN Status</Th><Th k="ven_version">Version</Th>
                <Th k="os">Operating System</Th><Th k="location">Location</Th><Th k="role">Role</Th><Th k="last_heartbeat">Last Heartbeat</Th>
                <th>Needs attention</th>
              </tr>
            </thead>
            <tbody>
              {sorted.map(w => {
                const concerns = venConcerns(w);
                return (
                  <tr key={w.id}>
                    <td><b>{w.hostname || '—'}</b>{!w.online && <span className="flag">Offline</span>}</td>
                    <td>{w.application || <span className="muted">No application label</span>}</td>
                    <td>{w.environment ? <span className="env-chip">{w.environment}</span> : <span className="muted">—</span>}</td>
                    <td><span className={`pill t-${MODE_TONE[w.enforcement_mode] || 'ns'}`}><span className="dot" />{MODE_LABEL[w.enforcement_mode] || titleCase(w.enforcement_mode)}</span></td>
                    <td>{w.ven_status === 'active' ? 'Active' : <span className="flag ms-0"><Icon name="bi-exclamation-circle-fill" />{titleCase(w.ven_status)}</span>}</td>
                    <td className="num">{w.ven_version || '—'}</td>
                    <td>{w.os || '—'}</td>
                    <td>{w.location || <span className="muted">—</span>}</td>
                    <td>{w.role || <span className="muted">—</span>}</td>
                    <td className="num" title={w.last_heartbeat ? fmtDateTime(w.last_heartbeat) : ''}>{w.last_heartbeat ? relTime(w.last_heartbeat) : <span className="muted">Never</span>}</td>
                    <td>{concerns.length ? concerns.join(' · ') : <span className="muted">—</span>}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {sorted.length === 0 && <Empty icon="bi-check2-circle" title={attention ? 'No VEN needs attention' : 'No VEN matches'} />}
        </div>
      </section>
    </>
  );
}

function VenDashboard() {
  const { data, open } = useStore();
  const [q, setQ] = useState('');
  const [onlyWithVens, setOnlyWithVens] = useState(true);
  const apps = activeApps(data.apps).filter(a => a.name.toLowerCase().includes(q.trim().toLowerCase())
    || a.environments.some(e => e.name.toLowerCase().includes(q.trim().toLowerCase())));

  const byApp = apps.map(a => venRow(String(a.id), a.name, a)).filter(r => !onlyWithVens || r.total + r.wl_unmanaged > 0);
  const byAppEnv: VenRow[] = [];
  apps.forEach(a => {
    const envs = trackedEnvs(a);
    envs.forEach(e => byAppEnv.push(venRow(`${a.id}-${e.id}`, a.name, e, e.name)));
    // Workloads that carry the application label but no environment label (or an excluded environment)
    const rest: any = {};
    (['wl_full', 'wl_selective', 'wl_visibility', 'wl_idle', 'wl_unmanaged'] as (keyof WorkloadCounts)[]).forEach(k => {
      rest[k] = Math.max(0, a[k] - envs.reduce((n, e) => n + e[k], 0));
    });
    if (managedCount(rest) + rest.wl_unmanaged > 0 || envs.length === 0) byAppEnv.push(venRow(`${a.id}-none`, a.name, rest));
  });
  const shownAppEnv = byAppEnv.filter(r => !onlyWithVens || r.total + r.wl_unmanaged > 0);

  const totals = activeApps(data.apps).reduce((t: any, a) => {
    VEN_MODES.forEach(m => { t[m.key] = (t[m.key] || 0) + a[m.key]; });
    t.wl_unmanaged = (t.wl_unmanaged || 0) + a.wl_unmanaged;
    return t;
  }, {} as any);
  const total = VEN_MODES.reduce((n, m) => n + (totals[m.key] || 0), 0);
  const openApp = (name: string) => { const a = data.apps.find(x => x.name === name); if (a) open({ type: 'details', appId: a.id }); };
  const tones: Record<string, string> = { wl_full: 'st-fe', wl_selective: 'st-se', wl_visibility: 'st-ip', wl_idle: 'st-ns' };
  const icons: Record<string, string> = { wl_full: 'bi-shield-fill-check', wl_selective: 'bi-shield-check', wl_visibility: 'bi-eye', wl_idle: 'bi-pause-circle' };
  const top = [...byApp].sort((a, b) => b.total - a.total).slice(0, 8).filter(r => r.total > 0);
  const maxTop = Math.max(...top.map(r => r.total), 1);

  const exportCsv = () => {
    const safe = (v: any) => { let s = v == null ? '' : String(v); if (/^[=+\-@]/.test(s)) s = "'" + s; return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s; };
    const head = ['Application', 'Environment', 'VENs', ...VEN_MODES.map(m => m.label), 'Unmanaged'];
    const lines = shownAppEnv.map(r => [r.app, r.env || '', r.total, ...VEN_MODES.map(m => r[m.key]), r.wl_unmanaged].map(safe).join(','));
    const url = URL.createObjectURL(new Blob([[head.join(','), ...lines].join('\n')], { type: 'text/csv' }));
    const a = document.createElement('a');
    a.href = url; a.download = `illumio-ven-counts-${todayISO()}.csv`; a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <>
      <div className="kpis six">
        <Kpi icon="bi-hdd-network" tone="accent" label="Total VENs" value={total} sub="Managed workloads" />
        {VEN_MODES.map(m => (
          <Kpi key={m.key} icon={icons[m.key]} tone={tones[m.key]} label={m.label} value={totals[m.key] || 0} share={pct(totals[m.key] || 0, total)} />
        ))}
        <Kpi icon="bi-question-circle" tone="st-ns" label="Unmanaged" value={totals.wl_unmanaged || 0} sub="Workloads without a VEN" />
      </div>

      <div className="grid-2">
        <section className="card-x">
          <div className="hd"><h2>VEN Enforcement Count</h2><span className="hint ms-auto">As of the last sync · {fmtDateTime(data.settings.last_sync)}</span></div>
          <div className="bd">
            <Donut centerLabel="VENs" slices={VEN_MODES.map(m => ({ label: m.label, value: totals[m.key] || 0, color: m.color }))} />
          </div>
        </section>
        <section className="card-x">
          <div className="hd"><h2>Applications with the most VENs</h2><span className="hint ms-auto">Colored by enforcement mode</span></div>
          <div className="bd">
            {top.length === 0 ? <span className="muted">No managed workloads carry an application label yet.</span> : (
              <div className="hbars">
                {top.map(r => (
                  <div key={r.key} className="hbar" style={{ gridTemplateColumns: 'minmax(90px, 34%) 1fr 34px' }}>
                    <span className="text-truncate" title={r.app}>{r.app}</span>
                    <span className="track" style={{ background: 'transparent' }}>
                      <span className="wl-bar" style={{ height: 14, width: `${(r.total / maxTop) * 100}%` }}>
                        {VEN_MODES.filter(m => (r[m.key] as number) > 0).map(m => <i key={m.key} style={{ flex: r[m.key] as number, background: m.color }} />)}
                      </span>
                    </span>
                    <span className="n num"><b>{r.total}</b></span>
                  </div>
                ))}
              </div>
            )}
          </div>
        </section>
      </div>

      <section className="card-x">
        <div className="filters no-print">
          <div className="search">
            <Icon name="bi-search" />
            <input className="form-control form-control-sm" type="search" placeholder="Search applications or environments…" aria-label="Search applications or environments"
              value={q} onChange={(e: any) => setQ(e.target.value)} />
          </div>
          <Toggle id="ven-only" checked={onlyWithVens} onChange={setOnlyWithVens} label="Only applications with workloads" />
          <button className="btn btn-light btn-sm" onClick={exportCsv}><Icon name="bi-download" /> Export CSV</button>
        </div>
      </section>

      <VenTable title="VEN Count by Application" hint={`${byApp.length} application(s)`} rows={byApp} onOpen={openApp} />
      <VenTable title="VEN Count by Application and Environment" hint={`${shownAppEnv.length} row(s)`} rows={shownAppEnv} withEnv onOpen={openApp} />
      <VenDetails q={q} />
    </>
  );
}
