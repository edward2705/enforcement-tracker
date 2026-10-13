// Filter bar + application grid, shared by the Dashboard and Application Tracking pages.

interface Filters {
  q: string; env: string; status: string; priority: string; ps: string; pm: string; owner: string;
  attention: '' | 'overdue'; showEnv: boolean; hideExcluded: boolean;
}
const EMPTY_FILTERS: Filters = { q: '', env: '', status: '', priority: '', ps: '', pm: '', owner: '', attention: '', showEnv: false, hideExcluded: false };
// Clearing filters keeps the two view switches (Show Environment, Hide excluded) as they are.
const clearedFilters = (f: Filters): Filters => ({ ...EMPTY_FILTERS, showEnv: f.showEnv, hideExcluded: f.hideExcluded });
interface Row { key: string; app: App; env?: Env; } // env is set on the environment rows nested under an application
type SortKey = 'rank' | 'name' | 'env' | 'priority' | 'progress' | 'start' | 'end' | 'ps' | 'pm' | 'owner' | 'status';

const filtersActive = (f: Filters): boolean =>
  !!(f.q || f.env || f.status || f.priority || f.ps || f.pm || f.owner || f.attention);

function buildRows(apps: App[], f: Filters, person: (id: number | null) => Person | undefined): Row[] {
  const q = f.q.trim().toLowerCase();
  const matchPerson = (filter: string, id: number | null) =>
    !filter || (filter === 'none' ? id == null : String(id) === filter);
  const rows: Row[] = [];
  for (const app of apps) {
    if (f.hideExcluded && app.excluded) continue;
    if (f.priority && app.priority !== f.priority) continue;
    if (!matchPerson(f.ps, app.ps_id) || !matchPerson(f.pm, app.pm_id) || !matchPerson(f.owner, app.owner_id)) continue;
    if (f.attention === 'overdue' && !isOverdue(app)) continue;
    const envs = trackedEnvs(app).filter(e => !f.env || e.name === f.env);
    if (f.env && !envs.length) continue;
    if (q) {
      const hay = [app.name, app.description, app.comments || '', ...app.environments.map(e => e.name),
        person(app.ps_id)?.name, person(app.pm_id)?.name, person(app.owner_id)?.name].join(' ').toLowerCase();
      if (!hay.includes(q)) continue;
    }
    // With environments shown, an application also matches a status filter through one of its environments.
    if (f.status && app.status !== f.status && !(f.showEnv && envs.some(e => e.status === f.status))) continue;
    rows.push({ key: String(app.id), app });
  }
  return rows;
}

// The environment rows listed under an application when "Show Environment" is on. Excluded
// environments are listed too (greyed out) so they can be included again, unless hidden.
function environmentRows(app: App, f: Filters): Row[] {
  return app.environments
    .filter(e => (!f.hideExcluded || !e.excluded) && (!f.env || e.name === f.env)
      && (!f.status || app.status === f.status || e.status === f.status))
    .map(env => ({ key: `${app.id}-${env.id}`, app, env }));
}

function sortRows(rows: Row[], key: SortKey, dir: 1 | -1, person: (id: number | null) => Person | undefined): Row[] {
  const text = (v: string | null | undefined) => (v || '￿').toLowerCase();
  const val = (r: Row): string | number => {
    switch (key) {
      case 'name': return text(r.app.name);
      case 'env': return text(r.env ? r.env.name : trackedEnvs(r.app).map(e => e.name).join(','));
      case 'priority': return PRIORITIES.indexOf(r.app.priority);
      case 'progress': return (r.env || r.app).progress;
      case 'start': return r.app.start_date || '￿';
      case 'end': return r.app.end_date || '￿';
      case 'ps': return text(person(r.app.ps_id)?.name);
      case 'pm': return text(person(r.app.pm_id)?.name);
      case 'owner': return text(person(r.app.owner_id)?.name);
      case 'status': return STATUSES.indexOf((r.env || r.app).status);
      default: return r.app.rank;
    }
  };
  return [...rows].sort((a, b) => {
    const x = val(a), y = val(b);
    return (x < y ? -1 : x > y ? 1 : a.app.rank - b.app.rank) * dir;
  });
}

function PersonFilter({ label, role, value, onChange }: { label: string; role: RoleCode; value: string; onChange: (v: string) => void }) {
  const { data } = useStore();
  return (
    <div className="f">
      <label>{label}</label>
      <select className="form-select form-select-sm" value={value} onChange={(e: any) => onChange(e.target.value)}>
        <option value="">All</option>
        {data.people.filter(p => (p as any)['is_' + role]).map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
        <option value="none">Unassigned</option>
      </select>
    </div>
  );
}

function FilterBar({ filters, onChange, extra }: { filters: Filters; onChange: (f: Filters) => void; extra?: any }) {
  const { data } = useStore();
  const set = (patch: Partial<Filters>) => onChange({ ...filters, ...patch });
  const excludedCount = data.apps.filter(a => a.excluded).length;
  const envNames = useMemo(
    () => Array.from(new Set(data.apps.flatMap(a => trackedEnvs(a).map(e => e.name)))).sort(), [data.apps]);
  return (
    <div className="filters no-print">
      <div className="search">
        <Icon name="bi-search" />
        <input className="form-control form-control-sm" type="search" placeholder="Search applications…" aria-label="Search applications"
          value={filters.q} onChange={(e: any) => set({ q: e.target.value })} />
      </div>
      <div className="f">
        <label>Environment</label>
        <select className="form-select form-select-sm" value={filters.env} onChange={(e: any) => set({ env: e.target.value })}>
          <option value="">All</option>
          {envNames.map(n => <option key={n}>{n}</option>)}
        </select>
      </div>
      <div className="f">
        <label>Status</label>
        <select className="form-select form-select-sm" value={filters.status} onChange={(e: any) => set({ status: e.target.value })}>
          <option value="">All</option>
          {STATUSES.map(s => <option key={s}>{s}</option>)}
        </select>
      </div>
      <div className="f">
        <label>Priority</label>
        <select className="form-select form-select-sm" value={filters.priority} onChange={(e: any) => set({ priority: e.target.value })}>
          <option value="">All</option>
          {PRIORITIES.map(p => <option key={p}>{p}</option>)}
        </select>
      </div>
      <PersonFilter label="Assigned PS" role="ps" value={filters.ps} onChange={v => set({ ps: v })} />
      <PersonFilter label="Project Manager" role="pm" value={filters.pm} onChange={v => set({ pm: v })} />
      <PersonFilter label="App Owner" role="owner" value={filters.owner} onChange={v => set({ owner: v })} />
      <Toggle id="show-env" checked={filters.showEnv} onChange={v => set({ showEnv: v })} label="Show Environment" />
      <button className={`btn btn-sm ${filters.hideExcluded ? 'btn-primary' : 'btn-light'}`} disabled={!excludedCount && !filters.hideExcluded}
        aria-pressed={filters.hideExcluded} onClick={() => set({ hideExcluded: !filters.hideExcluded })}
        title={excludedCount ? '' : 'No applications are excluded'}>
        <Icon name={filters.hideExcluded ? 'bi-eye' : 'bi-eye-slash'} /> {filters.hideExcluded ? 'Show' : 'Hide'} excluded ({excludedCount})
      </button>
      {extra}
      {filtersActive(filters) && <button className="btn btn-light btn-sm"
        onClick={() => onChange(clearedFilters(filters))}>Clear Filters</button>}
    </div>
  );
}

function RowMenu({ row, anchor, onClose }: { row: Row; anchor: DOMRect; onClose: () => void }) {
  const { data, open, run } = useStore();
  const { app, env } = row;
  const go = (m: ModalState) => { onClose(); open(m); };
  return (
    <Menu anchor={anchor} onClose={onClose}>
      <button role="menuitem" onClick={() => go({ type: 'details', appId: app.id })}><Icon name="bi-eye" />View Details</button>
      <button role="menuitem" onClick={() => go({ type: 'edit', appId: app.id })}><Icon name="bi-pencil" />Edit Application</button>
      <button role="menuitem" onClick={() => go({ type: 'progress', appId: app.id, envId: env?.id })}><Icon name="bi-speedometer2" />Update Progress</button>
      <button role="menuitem" onClick={() => go({ type: 'status', appId: app.id, envId: env?.id })}><Icon name="bi-arrow-repeat" />Change Status</button>
      <button role="menuitem" disabled={!app.environments.length} onClick={() => go({ type: 'exclude', appId: app.id })}>
        <Icon name="bi-eye-slash" />Exclude Environment
      </button>
      <button role="menuitem" onClick={() => {
        onClose();
        run(() => api('PATCH', `/api/applications/${app.id}`, { excluded: !app.excluded }),
          app.excluded ? `${app.name} is tracked again` : `${app.name} excluded`);
      }}><Icon name={app.excluded ? 'bi-check-circle' : 'bi-slash-circle'} />{app.excluded ? 'Include Application' : 'Exclude Application'}</button>
      <button role="menuitem" onClick={() => go({ type: 'comment', appId: app.id })}><Icon name="bi-chat-left-text" />Add Comment</button>
      <a role="menuitem" href={pceLabelUrl(data, app)} target="_blank" rel="noopener noreferrer" onClick={onClose}>
        <Icon name="bi-box-arrow-up-right" />View in PCE
      </a>
      <hr />
      <button role="menuitem" className="danger" onClick={() => go({ type: 'remove', appId: app.id })}><Icon name="bi-trash3" />Remove from tracker</button>
    </Menu>
  );
}

function TrackerPanel({ filters, onFilters, defaultPageSize = 10, editable = false }: {
  filters: Filters; onFilters: (f: Filters) => void; defaultPageSize?: number; editable?: boolean;
}) {
  const { data, run, open, person } = useStore();
  const [sort, setSort] = useState({ key: 'rank' as SortKey, dir: 1 as 1 | -1 });
  const [edit, setEdit] = useState(null as null | { key: string; field: string });
  const gridId = editable ? 'tracking-grid' : 'dashboard-grid';
  const [drag, setDrag] = useState(null as number | null); // id of the application being dragged
  const [over, setOver] = useState(null as null | { key: string; pos: 'before' | 'after' });
  const [page, setPage] = useState(0);
  const [pageSize, setPageSize] = useState(defaultPageSize);
  const [selected, setSelected] = useState(new Set<number>());
  const [menu, setMenu] = useState(null as null | { row: Row; anchor: DOMRect });

  // Every column can be dragged wider or narrower from the grip at the right of its header.
  // Widths are kept per column id; a column with no entry sizes itself automatically.
  const [colW, setColWState] = useState(() => {
    const out: Record<string, number> = {};
    try {
      const v = JSON.parse(localStorage.getItem('tracker-column-widths') || '{}');
      Object.keys(v || {}).forEach(k => { if (Number(v[k]) > 0) out[k] = Number(v[k]); });
    } catch (e) {}
    return out;
  });
  const saveColW = (next: Record<string, number>) => {
    try { localStorage.setItem('tracker-column-widths', JSON.stringify(next)); } catch (e) {}
    return next;
  };
  const setColW = (col: string, w: number | null) => setColWState((prev: Record<string, number>) => {
    const next = { ...prev };
    if (w) next[col] = w; else delete next[col];
    return saveColW(next);
  });
  const grip = (id: string, label: string) => <ColResizer label={label} min={48} max={900} onChange={w => setColW(id, w)} />;
  // People columns can be switched off; the choice is remembered in this browser.
  const [colsMenu, setColsMenu] = useState(null as DOMRect | null);
  const [hiddenCols, setHiddenColsState] = useState(() => {
    try { const v = JSON.parse(localStorage.getItem('tracker-hidden-columns') || '[]'); return (Array.isArray(v) ? v : []).filter(c => c === 'ps' || c === 'pm' || c === 'owner') as RoleCode[]; }
    catch (e) { return [] as RoleCode[]; }
  });
  const showCol = (role: RoleCode) => !hiddenCols.includes(role);
  const setColShown = (role: RoleCode, on: boolean) => {
    const next = on ? hiddenCols.filter((c: RoleCode) => c !== role) : [...hiddenCols.filter((c: RoleCode) => c !== role), role];
    setHiddenColsState(next);
    try { localStorage.setItem('tracker-hidden-columns', JSON.stringify(next)); } catch (e) {}
  };
  // Hiding people columns hands their space to Comments (unless its width was set by dragging):
  // about 100px per hidden column, plus whatever else is left over in the row.
  const commentsGrow = hiddenCols.length > 0 && !colW.comments;
  const commentsMin = 195 + 100 * hiddenCols.length;
  // Application Tracking lists environments as rows under each application, so it has no
  // Environment column; the Dashboard grid keeps it as a summary.
  const envColumn = !editable;
  const columnOrder = ['_select', 'rank', 'name', editable && 'exclude', envColumn && 'env', 'status', 'priority', 'progress', 'start', 'end',
    showCol('ps') && 'ps', showCol('pm') && 'pm', showCol('owner') && 'owner', 'comments', '_actions'].filter(Boolean) as string[];
  const widthCss = columnOrder.map((id, i) => {
    const w = colW[id];
    if (!w) return '';
    const at = `#${gridId} > thead > tr > th:nth-child(${i + 1}), #${gridId} > tbody > tr > td:nth-child(${i + 1})`;
    return `${at} { width: ${w}px; min-width: ${w}px; max-width: ${w}px; }`
      + (id === 'comments' ? ` #${gridId} > tbody > tr > td:nth-child(${i + 1}) .cell-comment { max-width: none; }` : '');
  }).join('\n');
  const [folded, setFolded] = useState(new Set<number>()); // applications whose environments are collapsed
  const rows = useMemo(() => {
    const apps = sortRows(buildRows(data.apps, filters, person), sort.key, sort.dir, person);
    // Sort applications first, then slot each one's environments in underneath it.
    return filters.showEnv ? apps.flatMap(r => (folded.has(r.app.id) ? [r] : [r, ...environmentRows(r.app, filters)])) : apps;
  }, [data.apps, data.people, filters, sort, folded]);
  const appCount = rows.filter(r => !r.env).length;
  useEffect(() => { setPage(0); }, [filters, pageSize]);
  useEffect(() => {
    // Drop selections that are no longer in the tracker (removed or filtered data reloaded).
    const live = new Set(data.apps.map(a => a.id));
    setSelected((prev: Set<number>) => new Set([...prev].filter(id => live.has(id))));
  }, [data.apps]);

  const pages = Math.max(1, Math.ceil(rows.length / pageSize));
  const current = Math.min(page, pages - 1);
  const visible = rows.slice(current * pageSize, (current + 1) * pageSize);
  const pageIds = Array.from(new Set(visible.map(r => r.app.id)));
  const allChecked = pageIds.length > 0 && pageIds.every(id => selected.has(id));
  const toggle = (ids: number[], on: boolean) => setSelected((prev: Set<number>) => {
    const next = new Set(prev);
    ids.forEach(id => (on ? next.add(id) : next.delete(id)));
    return next;
  });
  const patchApp = (id: number, payload: any) => run(() => api('PATCH', `/api/applications/${id}`, payload));
  const patchRow = (r: Row, payload: any) =>
    r.env ? run(() => api('PATCH', `/api/environments/${r.env!.id}`, payload)) : patchApp(r.app.id, payload);
  const bulk = async (payload: any, message: string) => {
    const ok = await run(() => api('POST', '/api/applications/bulk', { ids: [...selected], ...payload }), message);
    if (ok) setSelected(new Set());
  };

  // Click-to-edit cells (when `editable`): `display` becomes a button that swaps in `editor`.
  const stop = () => setEdit(null);
  const save = (fn: () => Promise<any>) => { stop(); run(fn); };
  const cell = (r: Row, field: string, label: string, display: any, editor: () => any, cls = '') => {
    const active = editable && edit && edit.key === r.key && edit.field === field;
    return (
      <td className={`${cls}${editable ? ' ed' : ''}`}>
        {active ? editor()
          : editable
            ? <button className="ed-val" title="Click to edit" aria-label={`Edit ${label} for ${r.app.name}`}
                onClick={() => setEdit({ key: r.key, field })}>{display}</button>
            : display}
      </td>
    );
  };
  const personCell = (r: Row, field: 'ps_id' | 'pm_id' | 'owner_id', role: RoleCode) =>
    cell(r, field, ROLE_LABEL[role], <PersonCell id={r.app[field]} plain />, () => (
      <InlinePerson role={role} value={r.app[field]} label={`${ROLE_LABEL[role]} for ${r.app.name}`} onCancel={stop}
        onCommit={id => (id === r.app[field] ? stop() : save(() => api('PATCH', `/api/applications/${r.app.id}`, { [field]: id })))} />
    ));
  // Dates belong to whichever row they are on: the application, or one of its environments.
  const dateCell = (r: Row, field: 'start_date' | 'end_date', label: string, display: any) => {
    // An application's date is calculated (earliest start / latest end) once its environments have dates.
    if (!r.env && (field === 'start_date' ? r.app.start_auto : r.app.end_auto)) {
      return (
        <td className="num auto-date" title={`Calculated from the environments' ${label}s (${field === 'start_date' ? 'earliest' : 'latest'}). Edit the dates on the environment rows.`}>
          {display}<Icon name="bi-calculator" />
        </td>
      );
    }
    return cell(r, field, label, display, () => (
      <InlineInput type="date" width={136} allowEmpty initial={(r.env || r.app)[field] || ''}
        label={`${label} for ${r.env ? `${r.env.name} of ` : ''}${r.app.name}`} onCancel={stop}
        onCommit={v => { stop(); patchRow(r, { [field]: v || null }); }} />
    ), 'num');
  };

  // Drag a row to change its priority rank. Only offered while the grid is in rank order, so the
  // row lands where it is dropped.
  const canDrag = editable && sort.key === 'rank' && sort.dir === 1;
  const rankedCount = activeApps(data.apps).length; // excluded applications are not ranked
  const dragApp = drag == null ? undefined : data.apps.find(a => a.id === drag);
  const dropPos = (r: Row, e: any): 'before' | 'after' | null => {
    if (!dragApp || r.env || r.app.id === dragApp.id || r.app.excluded) return null; // excluded rows have no rank to drop onto
    const box = e.currentTarget.getBoundingClientRect();
    return e.clientY - box.top < box.height / 2 ? 'before' : 'after';
  };
  const endDrag = () => { setDrag(null); setOver(null); };
  const dropOn = (r: Row, e: any) => {
    const pos = dropPos(r, e);
    const moved = dragApp;
    endDrag();
    if (!pos || !moved) return;
    e.preventDefault();
    const t = r.app.rank;
    // The dragged row leaves the list first, so rows below it move up by one.
    const rank = pos === 'before' ? (moved.rank > t ? t : t - 1) : (moved.rank > t ? t + 1 : t);
    if (rank !== moved.rank) run(() => api('POST', `/api/applications/${moved.id}/rank`, { rank }), `${moved.name} moved to rank ${rank}`);
  };

  // One Exclude button for the selection: it flips each selected application's exclude setting.
  const pickedApps = data.apps.filter(a => selected.has(a.id));
  const allPickedExcluded = pickedApps.length > 0 && pickedApps.every(a => a.excluded);
  const toggleExcluded = async () => {
    const turnOn = pickedApps.filter(a => !a.excluded).map(a => a.id);
    const turnOff = pickedApps.filter(a => a.excluded).map(a => a.id);
    const ok = await run(async () => {
      if (turnOn.length) await api('POST', '/api/applications/bulk', { ids: turnOn, excluded: true });
      if (turnOff.length) await api('POST', '/api/applications/bulk', { ids: turnOff, excluded: false });
    }, [turnOn.length && `${turnOn.length} excluded`, turnOff.length && `${turnOff.length} tracked again`].filter(Boolean).join(', '));
    if (ok) setSelected(new Set());
  };

  const Th = ({ k, label, children }: { k: SortKey; label: string; children?: any }) => (
    <th className="sortable" aria-sort={sort.key === k ? (sort.dir === 1 ? 'ascending' : 'descending') : 'none'}
      onClick={() => setSort({ key: k, dir: sort.key === k && sort.dir === 1 ? -1 : 1 })}>
      {label}{children}
      <Icon name={sort.key !== k ? 'bi-chevron-expand' : sort.dir === 1 ? 'bi-caret-up-fill' : 'bi-caret-down-fill'}
        className={`sort${sort.key === k ? ' on' : ''}`} />
      {grip(k, label)}
    </th>
  );

  return (
    <section className="card-x">
      <FilterBar filters={filters} onChange={onFilters} extra={
        <button className="btn btn-light btn-sm" aria-haspopup="menu" title="Show or hide the people columns"
          onClick={(e: any) => setColsMenu(e.currentTarget.getBoundingClientRect())}>
          <Icon name="bi-layout-three-columns" /> Columns{hiddenCols.length ? ` (${hiddenCols.length} hidden)` : ''}
        </button>} />
      {selected.size > 0 && (
        <div className="bulk no-print">
          <b>{selected.size} selected</b>
          <select className="form-select form-select-sm" value="" aria-label="Set status for selected"
            onChange={(e: any) => bulk({ status: e.target.value }, `Status updated for ${selected.size} application(s)`)}>
            <option value="" disabled>Set status…</option>
            {STATUSES.map(s => <option key={s}>{s}</option>)}
          </select>
          <select className="form-select form-select-sm" value="" aria-label="Set priority for selected"
            onChange={(e: any) => bulk({ priority: e.target.value }, `Priority updated for ${selected.size} application(s)`)}>
            <option value="" disabled>Set priority…</option>
            {PRIORITIES.map(p => <option key={p}>{p}</option>)}
          </select>
          <button className={`btn btn-sm ${allPickedExcluded ? 'btn-primary' : 'btn-light'}`} aria-pressed={allPickedExcluded} onClick={toggleExcluded}
            title="Flips the exclude setting of every selected application: tracked ones are excluded, excluded ones are tracked again">
            <Icon name="bi-slash-circle" /> Exclude</button>
          <button className="btn btn-light btn-sm" onClick={() => {
            if (confirm(`Remove ${selected.size} application(s) from the tracker? They can be restored from Settings.`))
              bulk({ hide: true }, 'Removed from the tracker');
          }}><Icon name="bi-trash3" /> Remove</button>
          <button className="btn btn-link btn-sm ms-auto" onClick={() => setSelected(new Set())}>Clear selection</button>
        </div>
      )}
      <div className="table-wrap">
        {widthCss && <style>{widthCss}</style>}
        <table className="grid sized" id={gridId}>
          <thead>
            <tr>
              <th style={{ width: 34 }}>
                <input type="checkbox" className="form-check-input" aria-label="Select all on this page"
                  checked={allChecked} onChange={(e: any) => toggle(pageIds, e.target.checked)} />
              </th>
              <Th k="rank" label="Rank">{editable && !canDrag && <span className="visually-hidden"> (sort by rank to drag rows)</span>}</Th>
              <Th k="name" label="Application" />
              {editable && <th title="Excluded applications stay listed but are greyed out and left out of the Project Timeline and totals">Exclude{grip('exclude', 'Exclude')}</th>}
              {envColumn && <Th k="env" label="Environment" />}
              <Th k="status" label="Status" />
              <Th k="priority" label="Priority" />
              <Th k="progress" label="Progress" />
              <Th k="start" label="Start Date" />
              <Th k="end" label="End Date" />
              {showCol('ps') && <Th k="ps" label="Assigned PS" />}
              {showCol('pm') && <Th k="pm" label="Project Manager" />}
              {showCol('owner') && <Th k="owner" label="App Owner" />}
              <th style={!colW.comments && commentsGrow ? { width: '100%', minWidth: commentsMin } : undefined}>Comments{grip('comments', 'Comments')}</th>
              <th className="sticky-end" style={{ width: 44 }}><span className="visually-hidden">Actions</span></th>
            </tr>
          </thead>
          <tbody>
            {visible.map(r => {
              const { app, env } = r;
              const scope = env || app;
              const envs = trackedEnvs(app);
              if (env) {
                // An environment listed under its application: it has its own status, progress and
                // exclude switch; everything else belongs to the application row above it.
                const off = !!env.excluded || !!app.excluded;
                return (
                  <tr key={r.key} className={`child${off ? ' excl' : ''}${env.in_pce ? '' : ' gone'}`}>
                    <td /><td />
                    <td>
                      <span className="child-name cell-clip">
                        <Icon name="bi-arrow-return-right" />
                        <span className="env-chip" title={`In the PCE: ${env.pce_state} · ${managedCount(env)} managed workload(s)`}>{env.name}</span>
                        {!env.in_pce && <span className="flag ms-0" title="This environment no longer exists for this application in the PCE. Its tracking data is kept."><Icon name="bi-exclamation-octagon-fill" />Deleted in PCE</span>}
                        {!!env.excluded && <span className="flag off ms-0">Excluded</span>}
                      </span>
                    </td>
                    {editable && (
                      <td className="keep">
                        <div className="form-check form-switch m-0">
                          <input className="form-check-input" type="checkbox" role="switch" checked={!!env.excluded}
                            aria-label={`Exclude ${env.name} for ${app.name}`} title={env.excluded ? 'Excluded - click to track again' : 'Exclude this environment for this application'}
                            onChange={(e: any) => run(() => api('PATCH', `/api/environments/${env.id}`, { excluded: e.target.checked }))} />
                        </div>
                      </td>
                    )}
                    {envColumn && <td><span className="muted" title={`${managedCount(env)} managed workload(s) · ${env.pce_state}`}>{env.pce_state}</span></td>}
                    <td>
                      <PillSelect label={`Status of ${env.name} for ${app.name}`} value={env.status} options={STATUSES}
                        tone={STATUS_META[env.status].tone} onChange={v => patchRow(r, { status: v })} />
                    </td>
                    <td>
                      <span title={env.priority_inherited ? 'Follows the application until you choose one here' : undefined}>
                        <PillSelect label={`Priority of ${env.name} for ${app.name}`} value={env.priority} options={PRIORITIES}
                          tone={PRIORITY_META[env.priority].tone} onChange={v => patchRow(r, { priority: v })} />
                      </span>
                    </td>
                    {editable
                      ? cell(r, 'progress', `progress of ${env.name}`, <ProgressBar value={env.progress} />, () => (
                          <InlineInput type="number" width={70} min={0} max={100} initial={String(env.progress)}
                            label={`Progress percent of ${env.name} for ${app.name}`} onCancel={stop}
                            onCommit={v => { stop(); patchRow(r, { progress: Math.max(0, Math.min(100, Math.round(Number(v)) || 0)) }); }} />
                        ))
                      : <td><ProgressBar value={env.progress} onClick={() => open({ type: 'progress', appId: app.id, envId: env.id })} /></td>}
                    {dateCell(r, 'start_date', 'start date', <span title={fmtDate(env.start_date)}>{fmtDateShort(env.start_date)}</span>)}
                    {dateCell(r, 'end_date', 'end date', <span title={fmtDate(env.end_date)}>{fmtDateShort(env.end_date)}</span>)}
                    {showCol('ps') && <td />}{showCol('pm') && <td />}{showCol('owner') && <td />}
                    {cell(r, 'comment', `comments on ${env.name}`,
                      <div className="cell-comment" title={env.comments || ''}>{env.comments || <span className="muted">—</span>}</div>,
                      () => (
                        <InlineInput type="text" width={190} initial="" placeholder="Add a comment…" label={`New comment on ${env.name} for ${app.name}`} onCancel={stop}
                          onCommit={v => { stop(); patchRow(r, { comment: v }); }} />
                      ), commentsGrow ? 'grow' : '')}
                    <td className="sticky-end">
                      <button className="kebab" aria-label={`Actions for ${env.name} of ${app.name}`} aria-haspopup="menu"
                        onClick={(e: any) => setMenu({ row: r, anchor: e.currentTarget.getBoundingClientRect() })}>
                        <Icon name="bi-three-dots" />
                      </button>
                    </td>
                  </tr>
                );
              }
              const kids = filters.showEnv ? environmentRows(app, filters).length : 0;
              return (
                <tr key={r.key}
                  className={`${selected.has(app.id) ? 'sel' : ''}${app.excluded ? ' excl' : ''}${app.in_pce && (!env || env.in_pce) ? '' : ' gone'}${drag === app.id ? ' dragging' : ''}${over && over.key === r.key ? ` drop-${over.pos}` : ''}`}
                  draggable={canDrag && !app.excluded && !(edit && edit.key === r.key)}
                  onDragStart={(e: any) => { e.dataTransfer.effectAllowed = 'move'; e.dataTransfer.setData('text/plain', app.name); setDrag(app.id); }}
                  onDragOver={(e: any) => {
                    const pos = dropPos(r, e);
                    if (!pos) { if (over && over.key === r.key) setOver(null); return; }
                    e.preventDefault();
                    e.dataTransfer.dropEffect = 'move';
                    if (!over || over.key !== r.key || over.pos !== pos) setOver({ key: r.key, pos });
                  }}
                  onDragLeave={(e: any) => { if (over && over.key === r.key && !e.currentTarget.contains(e.relatedTarget)) setOver(null); }}
                  onDrop={(e: any) => dropOn(r, e)} onDragEnd={endDrag}>
                  <td>
                    <input type="checkbox" className="form-check-input" aria-label={`Select ${app.name}`}
                      checked={selected.has(app.id)} onChange={(e: any) => toggle([app.id], e.target.checked)} />
                  </td>
                  <td>
                    {app.excluded ? <span className="rank"><b className="num" title="Excluded applications are not ranked">—</b></span> : (
                    <span className="rank">
                      {editable && <Icon name="bi-grip-vertical" className={`grip${canDrag ? '' : ' off'}`} />}
                      {editable && edit && edit.key === r.key && edit.field === 'rank'
                        ? <InlineInput type="number" width={58} min={1} max={rankedCount} initial={String(app.rank)}
                            label={`Priority rank for ${app.name}`} onCancel={stop}
                            onCommit={v => save(() => api('POST', `/api/applications/${app.id}/rank`, { rank: Number(v) }))} />
                        : editable
                          ? <button className="ed-val num" title="Click to edit" aria-label={`Edit priority rank for ${app.name}`}
                              onClick={() => setEdit({ key: r.key, field: 'rank' })}><b>{app.rank}</b></button>
                          : <b className="num">{app.rank}</b>}
                      <span className="mv">
                        <button aria-label="Move up" disabled={app.rank <= 1}
                          onClick={() => run(() => api('POST', `/api/applications/${app.id}/rank`, { rank: app.rank - 1 }))}>
                          <Icon name="bi-caret-up-fill" />
                        </button>
                        <button aria-label="Move down" disabled={app.rank >= rankedCount}
                          onClick={() => run(() => api('POST', `/api/applications/${app.id}/rank`, { rank: app.rank + 1 }))}>
                          <Icon name="bi-caret-down-fill" />
                        </button>
                      </span>
                    </span>)}
                  </td>
                  <td>
                    <div className="cell-clip">
                    {filters.showEnv && (kids > 0
                      ? <button className="fold" aria-expanded={!folded.has(app.id)}
                          aria-label={`${folded.has(app.id) ? 'Show' : 'Hide'} the ${kids} environment(s) of ${app.name}`}
                          onClick={() => setFolded((prev: Set<number>) => { const next = new Set(prev); next.has(app.id) ? next.delete(app.id) : next.add(app.id); return next; })}>
                          <Icon name={folded.has(app.id) ? 'bi-chevron-right' : 'bi-chevron-down'} />
                        </button>
                      : <span className="fold" />)}
                    <button className="app-link" onClick={() => open({ type: 'details', appId: app.id })}>{app.name}</button>
                    {!envColumn && !filters.showEnv && envs.length > 0 &&
                      <span className="env-count" title={envs.map(e => e.name).join(', ')}>{envs.length} env{envs.length === 1 ? '' : 's'}</span>}
                    {!app.in_pce && <span className="flag" title="This application label was deleted in the PCE. Its tracking data is kept."><Icon name="bi-exclamation-octagon-fill" />Deleted in PCE</span>}
                    {!!app.excluded && <span className="flag off">Excluded</span>}
                    </div>
                  </td>
                  {editable && (
                    <td className="keep">
                      <div className="form-check form-switch m-0">
                        <input className="form-check-input" type="checkbox" role="switch" checked={!!app.excluded}
                          aria-label={`Exclude ${app.name}`} title={app.excluded ? 'Excluded - click to track again' : 'Exclude this application'}
                          onChange={(e: any) => patchApp(app.id, { excluded: e.target.checked })} />
                      </div>
                    </td>
                  )}
                  {envColumn && <td>
                    {env ? <><span className="env-chip">{env.name}</span>
                        {!env.in_pce && <span className="flag ms-0" title="This environment no longer exists for this application in the PCE. Its tracking data is kept."><Icon name="bi-exclamation-octagon-fill" />Deleted in PCE</span>}</>
                      : envs.length === 0 ? <span className="muted">—</span>
                      : <>{envs.slice(0, 2).map(e => <span key={e.id} className={`env-chip${e.in_pce ? '' : ' gone'}`}
                            title={e.in_pce ? undefined : 'Deleted in the PCE'}>{!e.in_pce && <Icon name="bi-exclamation-octagon-fill" />}{e.name}</span>)}
                          {envs.length > 2 && <span className="muted">+{envs.length - 2}</span>}</>}
                  </td>}
                  <td>
                    <PillSelect label={`Status for ${app.name}`} value={scope.status} options={STATUSES}
                      tone={STATUS_META[scope.status].tone} onChange={v => patchRow(r, { status: v })} />
                  </td>
                  <td>
                    <PillSelect label={`Priority for ${app.name}`} value={app.priority} options={PRIORITIES}
                      tone={PRIORITY_META[app.priority].tone} onChange={v => patchApp(app.id, { priority: v })} />
                  </td>
                  {editable
                    ? cell(r, 'progress', 'progress', <ProgressBar value={scope.progress} />, () => (
                        <InlineInput type="number" width={70} min={0} max={100} initial={String(scope.progress)}
                          label={`Progress percent for ${app.name}`} onCancel={stop}
                          onCommit={v => { stop(); patchRow(r, { progress: Math.max(0, Math.min(100, Math.round(Number(v)) || 0)) }); }} />
                      ))
                    : <td><ProgressBar value={scope.progress} onClick={() => open({ type: 'progress', appId: app.id, envId: env?.id })} /></td>}
                  {dateCell(r, 'start_date', 'start date', <span title={fmtDate(app.start_date)}>{fmtDateShort(app.start_date)}</span>)}
                  {dateCell(r, 'end_date', 'end date', isOverdue(app)
                    ? <span className="due-late" title="Past the end date and not fully enforced"><Icon name="bi-exclamation-circle-fill" />{fmtDateShort(app.end_date)}</span>
                    : <span title={fmtDate(app.end_date)}>{fmtDateShort(app.end_date)}</span>)}
                  {showCol('ps') && personCell(r, 'ps_id', 'ps')}
                  {showCol('pm') && personCell(r, 'pm_id', 'pm')}
                  {showCol('owner') && personCell(r, 'owner_id', 'owner')}
                  {cell(r, 'comment', 'comments',
                    <div className="cell-comment"
                      title={app.comments || ''}>{app.comments || <span className="muted">—</span>}</div>,
                    () => (
                      <InlineInput type="text" width={190} initial="" placeholder="Add a comment…" label={`New comment for ${app.name}`} onCancel={stop}
                        onCommit={v => save(() => api('POST', `/api/applications/${app.id}/comments`, { text: v }))} />
                    ), commentsGrow ? 'grow' : '')}
                  <td className="sticky-end">
                    <button className="kebab" aria-label={`Actions for ${app.name}`} aria-haspopup="menu"
                      onClick={(e: any) => setMenu({ row: r, anchor: e.currentTarget.getBoundingClientRect() })}>
                      <Icon name="bi-three-dots" />
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {rows.length === 0 && (
          data.apps.length === 0
            ? <Empty icon="bi-cloud-download" title="No applications yet">Use <b>Sync from PCE</b> to import application and environment labels.</Empty>
            : <Empty icon="bi-funnel" title="No applications match these filters">
                <button className="btn btn-light btn-sm mt-2" onClick={() => onFilters(clearedFilters(filters))}>Clear Filters</button>
              </Empty>
        )}
      </div>
      {rows.length > 0 && (
        <div className="table-foot">
          <span className="num">
            Showing {current * pageSize + 1}–{Math.min(rows.length, (current + 1) * pageSize)} of {rows.length}
            {filters.showEnv ? ` rows (${appCount} applications and their environments)` : ' applications'}
          </span>
          <span className="ms-auto">Rows</span>
          <select className="form-select form-select-sm" aria-label="Rows per page" value={pageSize}
            onChange={(e: any) => setPageSize(Number(e.target.value))}>
            {[10, 25, 50, 100].map(n => <option key={n}>{n}</option>)}
          </select>
          <button className="btn btn-light btn-sm" disabled={current === 0} onClick={() => setPage(current - 1)} aria-label="Previous page">
            <Icon name="bi-chevron-left" />
          </button>
          <span className="num">{current + 1} / {pages}</span>
          <button className="btn btn-light btn-sm" disabled={current >= pages - 1} onClick={() => setPage(current + 1)} aria-label="Next page">
            <Icon name="bi-chevron-right" />
          </button>
        </div>
      )}
      {menu && <RowMenu row={menu.row} anchor={menu.anchor} onClose={() => setMenu(null)} />}
      {colsMenu && (
        <Menu anchor={colsMenu} onClose={() => setColsMenu(null)} keepOnScroll>
          <div style={{ padding: '6px 12px 4px', minWidth: 210 }}>
            <div className="form-label">Show columns</div>
            {(['ps', 'pm', 'owner'] as RoleCode[]).map(role => (
              <Toggle key={role} id={`col-${role}`} checked={showCol(role)} onChange={v => setColShown(role, v)} label={ROLE_LABEL[role]} />
            ))}
            <hr style={{ margin: '8px 0', borderColor: 'var(--line)', opacity: 1 }} />
            <button className="btn btn-light btn-sm w-100" disabled={!Object.keys(colW).length}
              onClick={() => setColWState(saveColW({}))}>Reset column widths</button>
          </div>
        </Menu>
      )}
    </section>
  );
}
