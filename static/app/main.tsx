// App shell: sidebar, top bar, routing, data loading and toasts.

const PAGES: { id: string; label: string; icon: string; sub: string; view: () => any }[] = [
  { id: 'dashboard', label: 'Dashboard', icon: 'bi-house-door-fill', sub: 'Plan. Track. Enforce.', view: DashboardPage },
  { id: 'tracking', label: 'Application Tracking', icon: 'bi-grid-fill', sub: 'Every application and environment from the PCE', view: TrackingPage },
  { id: 'timeline', label: 'Project Timeline', icon: 'bi-calendar3', sub: 'Phases, tasks, dependencies and milestones', view: TimelinePage },
  { id: 'people', label: 'People Management', icon: 'bi-people-fill', sub: 'PS engineers, project managers and app owners', view: PeoplePage },
  { id: 'reports', label: 'Reports', icon: 'bi-bar-chart-fill', sub: 'Enforcement summary, roll-ups and exceptions', view: ReportsPage },
  { id: 'settings', label: 'Settings', icon: 'bi-gear-fill', sub: 'Profile, sync and PCE configuration', view: SettingsPage },
];
const routeFromHash = (): string => {
  const id = location.hash.replace(/^#\/?/, '');
  return PAGES.some(p => p.id === id) ? id : 'dashboard';
};

function Logo() {
  return (
    <svg width="30" height="30" viewBox="0 0 32 32" aria-hidden="true">
      <path d="M4 4h8v3.5H7.5v17H12V28H4zM28 4h-8v3.5h4.5v17H20V28h8z" fill="#f26b21" />
      <path d="M11 11l10 10M21 11L11 21" stroke="#f26b21" strokeWidth="3" strokeLinecap="round" />
    </svg>
  );
}

// Live progress of a PCE sync: one row per kind of Illumio object, polled from /api/sync/progress.
interface SyncStep { key: string; label: string; status: 'pending' | 'running' | 'done' | 'warning' | 'error' | 'skipped';
  count: number | null; detail: string; seconds: number | null; started: number | null }
interface SyncState { running: boolean; pce?: string; started_at?: string; finished_at?: string | null; error?: string; steps: SyncStep[] }

const STEP_ICON: Record<string, string> = {
  pending: 'bi-circle', running: 'bi-arrow-repeat', done: 'bi-check-circle-fill', warning: 'bi-exclamation-triangle-fill',
  error: 'bi-x-circle-fill', skipped: 'bi-dash-circle',
};

function SyncPanel({ state, onClose }: { state: SyncState; onClose: () => void }) {
  const [, tick] = useState(0);
  useEffect(() => {
    if (!state.running) return;
    const t = setInterval(() => tick((n: number) => n + 1), 1000);
    return () => clearInterval(t);
  }, [state.running]);
  const steps = state.steps || [];
  const finished = steps.filter(s => s.status !== 'pending' && s.status !== 'running').length;
  const running = steps.some(s => s.status === 'running');
  const pct = steps.length ? Math.round(((finished + (running ? 0.5 : 0)) / steps.length) * 100) : 0;
  const failed = !!state.error || steps.some(s => s.status === 'error');
  const warned = steps.some(s => s.status === 'warning');
  const elapsed = state.started_at
    ? Math.max(0, Math.round(((state.finished_at ? new Date(state.finished_at).getTime() : Date.now()) - new Date(state.started_at).getTime()) / 1000))
    : 0;
  const title = state.running ? `Syncing from ${state.pce || 'PCE'}…`
    : state.error ? 'Sync failed' : failed ? 'Sync finished with errors' : warned ? 'Sync finished with warnings' : 'Sync complete';
  const tone = state.running ? '' : failed ? ' err' : warned ? ' warn' : ' ok';
  return (
    <section className={`sync-panel${tone}`} role="status" aria-live="polite" aria-label="Sync progress">
      <div className="sp-head">
        <div>
          <div className="sp-title">{title}</div>
          <div className="sp-sub">{finished} of {steps.length} steps · {elapsed}s</div>
        </div>
        <button className="icon-btn" onClick={onClose} aria-label={state.running ? 'Hide sync progress' : 'Close sync progress'}
          title={state.running ? 'Hide (the sync keeps running)' : 'Close'}><Icon name="bi-x-lg" /></button>
      </div>
      <div className="sp-bar" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={pct} aria-label="Sync progress">
        <div className={`sp-fill${state.running ? ' live' : ''}`} style={{ width: `${state.running ? pct : 100}%` }} />
      </div>
      {state.error && <div className="sp-error">{state.error}</div>}
      <ol className="sp-steps">
        {steps.map(s => (
          <li key={s.key} className={`sp-step ${s.status}`}>
            <Icon name={STEP_ICON[s.status]} className={s.status === 'running' ? 'spin' : ''} />
            <div className="sp-main">
              <div className="sp-label">
                <span>{s.label}</span>
                {s.count != null && <span className="sp-count">{s.count.toLocaleString()}</span>}
              </div>
              {s.detail && <div className="sp-detail">{s.detail}</div>}
            </div>
            <span className="sp-time">
              {s.status === 'running' && s.started ? `${Math.max(0, Math.round(Date.now() / 1000 - s.started))}s`
                : s.seconds != null ? `${s.seconds}s` : ''}
            </span>
          </li>
        ))}
      </ol>
    </section>
  );
}

function Root() {
  const [data, setData] = useState(null as Bootstrap | null);
  const [fatal, setFatal] = useState('');
  const [route, setRoute] = useState(routeFromHash());
  const [modal, setModal] = useState(null as ModalState | null);
  const [toasts, setToasts] = useState([] as { id: number; message: string; kind: 'ok' | 'err' }[]);
  const [syncing, setSyncing] = useState(false);
  const [progress, setProgress] = useState(null as SyncState | null);
  const [showProgress, setShowProgress] = useState(false);
  const [bell, setBell] = useState(null as DOMRect | null);
  const [seen, setSeen] = useState(Number(localStorage.getItem('tracker-seen') || 0));
  const [theme, setTheme] = useState(document.documentElement.getAttribute('data-bs-theme') || 'light');

  const toast = useCallback((message: string, kind: 'ok' | 'err' = 'ok') => {
    const id = Date.now() + Math.random();
    setToasts((list: any[]) => [...list, { id, message, kind }]);
    setTimeout(() => setToasts((list: any[]) => list.filter(t => t.id !== id)), kind === 'err' ? 7000 : 3500);
  }, []);
  const reload = useCallback(async () => { setData(await api('GET', '/api/bootstrap')); }, []);
  const run = useCallback(async (fn: () => Promise<any>, okMessage?: string) => {
    try {
      await fn();
      await reload();
      if (okMessage) toast(okMessage);
      return true;
    } catch (e: any) {
      toast(e.message || 'Something went wrong', 'err');
      return false;
    }
  }, [reload, toast]);

  useEffect(() => {
    reload().catch((e: any) => setFatal(e.message));
    const onHash = () => setRoute(routeFromHash());
    window.addEventListener('hashchange', onHash);
    return () => window.removeEventListener('hashchange', onHash);
  }, []);
  // Poll the sync progress while a sync runs (also one started before this page was loaded).
  useEffect(() => {
    let live = true;
    api('GET', '/api/sync/progress').then((p: SyncState) => {
      if (live && p.running) { setProgress(p); setShowProgress(true); setSyncing(true); }
    }).catch(() => {});
    return () => { live = false; };
  }, []);
  useEffect(() => {
    if (!syncing) return;
    let live = true;
    const poll = async () => {
      try {
        const p: SyncState = await api('GET', '/api/sync/progress');
        if (!live) return;
        setProgress(p);
        if (!p.running && p.finished_at) { setSyncing(false); reload().catch(() => {}); }
      } catch (e) {}
    };
    poll();
    const t = setInterval(poll, 800);
    return () => { live = false; clearInterval(t); };
  }, [syncing]);
  useEffect(() => {
    document.documentElement.setAttribute('data-bs-theme', theme);
    try { localStorage.setItem('tracker-theme', theme); } catch (e) {}
  }, [theme]);

  const people = useMemo(() => new Map((data?.people || []).map(p => [p.id, p] as [number, Person])), [data]);
  const store: Store | null = useMemo(() => data && {
    data, reload, run, toast, open: setModal, person: (id: number | null) => (id == null ? undefined : people.get(id)),
  }, [data, people, reload, run, toast]);

  if (fatal) return <pre className="boot-error">Could not load tracker data: {fatal}</pre>;
  if (!data || !store) return <div className="boot"><div className="spinner-border text-primary" role="status" /></div>;

  const sync = async () => {
    setProgress(null);
    setShowProgress(true);
    setSyncing(true);
    try {
      const r = await api('POST', '/api/sync', {});
      setProgress(await api('GET', '/api/sync/progress'));
      await reload();
      toast(`Synced ${r.applications} applications, ${r.environments} environment pairs and ${r.workloads} workloads` +
        (r.added ? ` · ${r.added} new` : '') + (r.deleted ? ` · ${r.deleted} application(s) deleted in PCE` : '') +
        (r.deleted_environments ? ` · ${r.deleted_environments} environment(s) deleted in PCE` : ''));
      (r.warnings || []).forEach((w: string) => toast(w, 'err'));
    } catch (e: any) {
      toast(e.message, 'err');
      api('GET', '/api/sync/progress').then(setProgress).catch(() => {});
    }
    setSyncing(false);
  };
  const page = PAGES.find(p => p.id === route)!;
  const View = page.view;
  const newest = data.activity[0]?.id || 0;
  const openBell = (e: any) => {
    setBell(e.currentTarget.getBoundingClientRect());
    setSeen(newest);
    try { localStorage.setItem('tracker-seen', String(newest)); } catch (err) {}
  };

  return (
    <StoreCtx.Provider value={store}>
      <div className="shell">
        <aside className="sidebar no-print">
          <div className="brand">
            <Logo />
            <div className="brand-text">
              <div className="brand-word">illumio</div>
              <div className="brand-sub">Enforcement Tracker</div>
            </div>
          </div>
          <nav aria-label="Main">
            {PAGES.map(p => (
              <a key={p.id} href={`#/${p.id}`} className={`nav-item${route === p.id ? ' active' : ''}`}
                aria-current={route === p.id ? 'page' : undefined} title={p.label}>
                <Icon name={p.icon} /><span>{p.label}</span>
              </a>
            ))}
          </nav>
          <div className="side-foot">
            <div className="k">Connected PCE</div>
            <div className="v"><span className="conn-dot" style={{ background: data.pce.name ? undefined : 'var(--st-ns)' }} />{data.pce.name || 'Not configured'}</div>
            <div style={{ color: '#7f8ba0' }}>{data.pce.fqdn}</div>
          </div>
        </aside>

        <div className="main">
          <header className="topbar">
            <div>
              <h1>{route === 'dashboard' ? 'Illumio Enforcement Tracker' : page.label}</h1>
              <div className="sub">{page.sub}</div>
            </div>
            <div className="spacer" />
            <span className="sync-meta">Last sync: {fmtDateTime(data.settings.last_sync)}</span>
            {progress && progress.steps.length > 0 && !showProgress && (
              <button className="btn btn-outline-secondary btn-sm" onClick={() => setShowProgress(true)}>
                <Icon name="bi-list-check" /> {syncing ? 'Show progress' : 'Last sync details'}
              </button>
            )}
            <button className="btn btn-primary" onClick={sync} disabled={syncing || !data.pce.name}>
              <Icon name="bi-arrow-repeat" className={syncing ? 'spin' : ''} /> {syncing ? 'Syncing…' : 'Sync from PCE'}
            </button>
            <button className="icon-btn" onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}
              aria-label={`Switch to ${theme === 'dark' ? 'light' : 'dark'} theme`} title="Toggle theme">
              <Icon name={theme === 'dark' ? 'bi-sun' : 'bi-moon-stars'} />
            </button>
            <button className="icon-btn" onClick={openBell} aria-label="Recent activity" aria-haspopup="menu">
              <Icon name="bi-bell" />
              {newest > seen && <span className="badge-dot" />}
            </button>
            <div className="user-chip"><Avatar name={data.settings.user_name} size={32} /><span>{data.settings.user_name}</span></div>
          </header>

          <main className="content">
            {data.apps.length === 0 && !data.settings.last_sync && route !== 'settings' ? (
              <section className="card-x">
                <Empty icon="bi-cloud-download" title="Connect your PCE to get started">
                  <p className="mb-3">
                    {data.pce.name
                      ? <>The tracker imports application and environment labels and workload enforcement modes from <b>{data.pce.fqdn}</b>. It never writes to the PCE.</>
                      : <>No PCE is configured yet. Add one in <a href="#/settings">Settings</a>.</>}
                  </p>
                  {data.pce.name && <button className="btn btn-primary" onClick={sync} disabled={syncing}>
                    <Icon name="bi-arrow-repeat" className={syncing ? 'spin' : ''} /> {syncing ? 'Syncing…' : 'Sync from PCE'}
                  </button>}
                </Empty>
              </section>
            ) : <View />}
          </main>
        </div>
      </div>

      {bell && (
        <Menu anchor={bell} onClose={() => setBell(null)}>
          <div style={{ width: 340, maxHeight: 420, overflowY: 'auto', padding: '6px 8px' }}>
            <b>Recent activity</b>
            <Feed items={data.activity} empty="Nothing has happened yet." />
          </div>
        </Menu>
      )}
      <ModalHost modal={modal} onClose={() => setModal(null)} />
      {showProgress && progress && progress.steps.length > 0 && <SyncPanel state={progress} onClose={() => setShowProgress(false)} />}
      <div className="toasts" role="status" aria-live="polite">
        {toasts.map(t => (
          <div key={t.id} className={`toast-x ${t.kind}`}>
            <Icon name={t.kind === 'err' ? 'bi-exclamation-octagon-fill' : 'bi-check-circle-fill'} />{t.message}
          </div>
        ))}
      </div>
    </StoreCtx.Provider>
  );
}

ReactDOM.createRoot(document.getElementById('root')).render(<Root />);
