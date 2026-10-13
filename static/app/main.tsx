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

function Root() {
  const [data, setData] = useState(null as Bootstrap | null);
  const [fatal, setFatal] = useState('');
  const [route, setRoute] = useState(routeFromHash());
  const [modal, setModal] = useState(null as ModalState | null);
  const [toasts, setToasts] = useState([] as { id: number; message: string; kind: 'ok' | 'err' }[]);
  const [syncing, setSyncing] = useState(false);
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
    setSyncing(true);
    try {
      const r = await api('POST', '/api/sync', {});
      await reload();
      toast(`Synced ${r.applications} applications, ${r.environments} environment pairs and ${r.workloads} workloads` +
        (r.added ? ` · ${r.added} new` : '') + (r.deleted ? ` · ${r.deleted} application(s) deleted in PCE` : '') +
        (r.deleted_environments ? ` · ${r.deleted_environments} environment(s) deleted in PCE` : ''));
    } catch (e: any) { toast(e.message, 'err'); }
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
