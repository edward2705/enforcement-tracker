// Dialogs opened from the row actions menu.

function AppHead({ app, env, right }: { app: App; env?: Env; right?: any }) {
  return (
    <div className="app-head">
      <div className="ic"><Icon name="bi-box-seam" /></div>
      <div style={{ minWidth: 0, flex: 1 }}>
        <div className="nm">{app.name}</div>
        <div className="muted">{env ? `Environment: ${env.name}` : app.description || 'Application label from PCE'}</div>
      </div>
      {right}
    </div>
  );
}

function Feed({ items, empty }: { items: Activity[]; empty: string }) {
  const icon: Record<string, string> = {
    sync: 'bi-cloud-check', status: 'bi-arrow-repeat', progress: 'bi-speedometer2', priority: 'bi-flag',
    edit: 'bi-pencil', comment: 'bi-chat-left-text', environment: 'bi-eye-slash',
  };
  if (!items.length) return <div className="muted">{empty}</div>;
  return (
    <div className="feed">
      {items.map(it => (
        <div key={it.id} className="feed-item">
          <span className="fi"><Icon name={icon[it.kind] || 'bi-chat-left-text'} /></span>
          <div>
            {it.app_name && <b>{it.app_name} · </b>}{it.text}
            <div className="meta">{it.author} · <span title={fmtDateTime(it.created_at)}>{relTime(it.created_at)}</span></div>
          </div>
        </div>
      ))}
    </div>
  );
}

function DetailsModal({ app, initialTab, onClose }: { app: App; initialTab?: string; onClose: () => void }) {
  const { data, run, open, person } = useStore();
  const [tab, setTab] = useState(initialTab || 'overview');
  const [detail, setDetail] = useState({ comments: [] as Activity[], events: [] as Activity[] });
  const [draft, setDraft] = useState('');
  const load = () => api('GET', `/api/applications/${app.id}`).then(setDetail).catch(() => {});
  useEffect(() => { load(); }, [app.id, app.updated_at, app.comment_count]);
  const post = async () => {
    if (await run(() => api('POST', `/api/applications/${app.id}/comments`, { text: draft }))) { setDraft(''); load(); }
  };
  const patchEnv = (env: Env, payload: any) => run(() => api('PATCH', `/api/environments/${env.id}`, payload)).then(load);
  const Fact = ({ k, cls = '', children }: { k: string; cls?: string; children: any }) =>
    <div className={cls}><div className="k">{k}</div><div>{children}</div></div>;
  const name = (id: number | null) => person(id)?.name || <span className="muted">Unassigned</span>;
  return (
    <Modal title="Application Details" icon="bi-info-circle" size="lg" onClose={onClose}
      footer={<>
        <a className="btn btn-light me-auto" href={pceLabelUrl(data, app)} target="_blank" rel="noopener noreferrer">
          <Icon name="bi-box-arrow-up-right" /> View in PCE
        </a>
        <button className="btn btn-light" onClick={onClose}>Close</button>
        <button className="btn btn-primary" onClick={() => open({ type: 'edit', appId: app.id })}>Edit</button>
      </>}>
      <AppHead app={app} right={<StatusPill status={app.status} />} />
      {!app.in_pce && (
        <div className="note mb-3" style={{ background: 'var(--danger-bg)' }}>
          <Icon name="bi-exclamation-octagon-fill" className="text-danger" />
          <div>This application label was deleted in the PCE. Its tracking data is kept; remove it from the tracker when you no longer need it.</div>
        </div>
      )}
      <div className="tabs" role="tablist">
        {[['overview', 'Overview', null], ['environments', 'Environments', app.environments.length],
          ['timeline', 'Timeline', null], ['comments', 'Comments', app.comment_count]].map(([id, label, count]: any) => (
          <button key={id} role="tab" aria-selected={tab === id} className={tab === id ? 'on' : ''} onClick={() => setTab(id)}>
            {label}{count != null && <span className="cnt num">{count}</span>}
          </button>
        ))}
      </div>

      {tab === 'overview' && (
        <div className="facts">
          <Fact k="Application (from PCE)">{app.name}</Fact>
          <Fact k="Description" cls="span2">{app.description || <span className="muted">No description</span>}</Fact>
          <Fact k="Priority"><PriorityPill priority={app.priority} /></Fact>
          <Fact k="Progress" cls="span2"><ProgressBar value={app.progress} wide /></Fact>
          <Fact k="Start Date"><span className="num">{fmtDate(app.start_date)}</span></Fact>
          <Fact k="End Date">
            <span className="num">{fmtDate(app.end_date)}</span>
            {isOverdue(app) && <span className="flag"><Icon name="bi-exclamation-circle-fill" />Overdue</span>}
          </Fact>
          <Fact k="Priority Rank">{app.excluded ? <span className="muted">Not ranked (excluded)</span> : <span className="num">#{app.rank}</span>}</Fact>
          <Fact k="Assigned PS">{name(app.ps_id)}</Fact>
          <Fact k="Project Manager">{name(app.pm_id)}</Fact>
          <Fact k="App Owner">{name(app.owner_id)}</Fact>
          <Fact k="Enforcement in PCE">{app.pce_state}</Fact>
          <Fact k="Managed workloads by enforcement mode" cls="span2"><WorkloadBar w={app} /></Fact>
          <Fact k="Latest comment" cls="span3">{app.comments || <span className="muted">No comments yet</span>}</Fact>
        </div>
      )}

      {tab === 'environments' && (
        app.environments.length === 0
          ? <Empty icon="bi-layers" title="No environments">No workloads in the PCE carry this application label together with an environment label.</Empty>
          : <div className="table-wrap">
              <table className="grid">
                <thead><tr><th>Environment</th><th>Status</th><th>Progress</th><th>Workloads in PCE</th><th>Tracked</th></tr></thead>
                <tbody>
                  {app.environments.map(env => (
                    <tr key={env.id} style={{ opacity: env.excluded ? 0.55 : 1 }}>
                      <td>
                        <b style={env.in_pce ? undefined : { color: 'var(--danger)' }}>{env.name}</b>
                        {!env.in_pce && <span className="flag" title="This environment no longer exists for this application in the PCE"><Icon name="bi-exclamation-octagon-fill" />Deleted in PCE</span>}
                      </td>
                      <td>
                        <PillSelect label={`Status for ${env.name}`} value={env.status} options={STATUSES}
                          tone={STATUS_META[env.status].tone} onChange={v => patchEnv(env, { status: v })} />
                      </td>
                      <td><ProgressBar value={env.progress} onClick={() => open({ type: 'progress', appId: app.id, envId: env.id })} /></td>
                      <td style={{ whiteSpace: 'normal', minWidth: 170 }}><WorkloadBar w={env} /></td>
                      <td>
                        <Toggle id={`trk-${env.id}`} checked={!env.excluded} label={<span className="visually-hidden">Track {env.name}</span>}
                          onChange={v => patchEnv(env, { excluded: !v })} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
      )}

      {tab === 'timeline' && <Feed items={detail.events} empty="No activity recorded yet." />}

      {tab === 'comments' && (
        <>
          <TextArea value={draft} onChange={setDraft} rows={2} placeholder="Write a comment…" />
          <div className="text-end mb-3">
            <button className="btn btn-primary btn-sm" disabled={!draft.trim()} onClick={post}>Add Comment</button>
          </div>
          <Feed items={detail.comments.map(c => ({ ...c, kind: 'comment' }))} empty="No comments yet." />
        </>
      )}
    </Modal>
  );
}

function EditModal({ app, onClose }: { app: App; onClose: () => void }) {
  const { run } = useStore();
  const [form, setForm] = useState({
    description: app.description, priority: app.priority, start_date: app.start_date || '', end_date: app.end_date || '',
    ps_id: app.ps_id, pm_id: app.pm_id, owner_id: app.owner_id,
  });
  const [comment, setComment] = useState('');
  const [envId, setEnvId] = useState(app.environments[0]?.id ?? 0);
  const [excluded, setExcluded] = useState(
    Object.fromEntries(app.environments.map(e => [e.id, !!e.excluded])) as Record<number, boolean>);
  const set = (patch: any) => setForm({ ...form, ...patch });
  const badDates = !!form.start_date && !!form.end_date && form.end_date < form.start_date;
  const save = async () => {
    const ok = await run(async () => {
      await api('PATCH', `/api/applications/${app.id}`, { ...form, comment: comment.trim() || undefined });
      for (const env of app.environments) {
        if (!!env.excluded !== excluded[env.id]) await api('PATCH', `/api/environments/${env.id}`, { excluded: excluded[env.id] });
      }
    }, 'Application updated');
    if (ok) onClose();
  };
  return (
    <Modal title="Edit Application" icon="bi-pencil-square" size="lg" onClose={onClose}
      footer={<>
        <button className="btn btn-light" onClick={onClose}>Cancel</button>
        <button className="btn btn-primary" disabled={badDates} onClick={save}>Save Changes</button>
      </>}>
      <div className="row g-3">
        <div className="col-md-6">
          <label className="form-label">Application (from PCE)</label>
          <input className="form-control" value={app.name} disabled />
        </div>
        <div className="col-md-6">
          <label className="form-label">Priority <span className="req">*</span></label>
          <select className="form-select" value={form.priority} onChange={(e: any) => set({ priority: e.target.value })}>
            {PRIORITIES.map(p => <option key={p}>{p}</option>)}
          </select>
        </div>
        <div className="col-md-6">
          <label className="form-label">Environment (from PCE)</label>
          {app.environments.length === 0
            ? <input className="form-control" value="No environments in PCE" disabled />
            : <select className="form-select" value={envId} onChange={(e: any) => setEnvId(Number(e.target.value))}>
                {app.environments.map(e => <option key={e.id} value={e.id}>{e.name}{excluded[e.id] ? ' (excluded)' : ''}</option>)}
              </select>}
        </div>
        <div className="col-md-6">
          <label className="form-label">Exclude Environment</label>
          <div style={{ paddingTop: 6 }}>
            {app.environments.length === 0
              ? <span className="muted">Nothing to exclude</span>
              : <Toggle id="edit-excl" checked={!!excluded[envId]} onChange={v => setExcluded({ ...excluded, [envId]: v })}
                  label="Skip tracking for the selected environment" />}
          </div>
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
        <div className="col-md-6">
          <label className="form-label">Start Date</label>
          <input type="date" className="form-control" value={form.start_date} disabled={app.start_auto}
            onChange={(e: any) => set({ start_date: e.target.value })} />
          {app.start_auto && <div className="muted mt-1"><Icon name="bi-calculator" /> Earliest start date of its environments.</div>}
        </div>
        <div className="col-md-6">
          <label className="form-label">End Date</label>
          <input type="date" className={`form-control${badDates ? ' is-invalid' : ''}`} value={form.end_date} min={form.start_date || undefined}
            disabled={app.end_auto} onChange={(e: any) => set({ end_date: e.target.value })} />
          {badDates && <div className="invalid-feedback">End date cannot be before the start date.</div>}
          {app.end_auto && <div className="muted mt-1"><Icon name="bi-calculator" /> Latest end date of its environments.</div>}
        </div>
        <div className="col-12">
          <label className="form-label">Description</label>
          <input className="form-control" maxLength={500} value={form.description} placeholder="What this application does"
            onChange={(e: any) => set({ description: e.target.value })} />
        </div>
        <div className="col-12">
          <label className="form-label">Comments</label>
          <TextArea value={comment} onChange={setComment} placeholder={app.comments ? `Latest: ${app.comments}` : 'Add a comment with this change (optional)'} />
        </div>
      </div>
    </Modal>
  );
}

function ProgressModal({ app, env, onClose }: { app: App; env?: Env; onClose: () => void }) {
  const { run } = useStore();
  const scope = env || app;
  const [value, setValue] = useState(scope.progress);
  const [note, setNote] = useState('');
  const save = async () => {
    const url = env ? `/api/environments/${env.id}` : `/api/applications/${app.id}`;
    if (await run(() => api('PATCH', url, { progress: value, comment: note.trim() || undefined }), 'Progress updated')) onClose();
  };
  const clamp = (n: number) => Math.max(0, Math.min(100, Math.round(n) || 0));
  return (
    <Modal title="Update Progress" icon="bi-speedometer2" onClose={onClose}
      footer={<>
        <button className="btn btn-light" onClick={onClose}>Cancel</button>
        <button className="btn btn-primary" disabled={value === scope.progress && !note.trim()} onClick={save}>Save Progress</button>
      </>}>
      <AppHead app={app} env={env} />
      <label className="form-label">Current Progress</label>
      <div className="mb-3"><ProgressBar value={scope.progress} wide /></div>
      <label className="form-label" htmlFor="new-progress">New Progress</label>
      <div className="range-row mb-3">
        <input id="new-progress" type="range" min={0} max={100} step={5} value={value} onChange={(e: any) => setValue(Number(e.target.value))} />
        <div className="input-group">
          <input type="number" className="form-control num" min={0} max={100} value={value} aria-label="New progress percent"
            onChange={(e: any) => setValue(clamp(Number(e.target.value)))} />
          <span className="input-group-text">%</span>
        </div>
      </div>
      <label className="form-label">Milestone / Notes</label>
      <TextArea value={note} onChange={setNote} placeholder="What moved this forward? (optional)" />
    </Modal>
  );
}

function StatusModal({ app, env, onClose }: { app: App; env?: Env; onClose: () => void }) {
  const { run } = useStore();
  const scope = env || app;
  const [value, setValue] = useState(scope.status as Status);
  const save = async () => {
    const url = env ? `/api/environments/${env.id}` : `/api/applications/${app.id}`;
    if (await run(() => api('PATCH', url, { status: value }), `Status changed to ${value}`)) onClose();
  };
  return (
    <Modal title="Change Status" icon="bi-arrow-repeat" onClose={onClose}
      footer={<>
        <button className="btn btn-light" onClick={onClose}>Cancel</button>
        <button className="btn btn-primary" disabled={value === scope.status} onClick={save}>Change Status</button>
      </>}>
      <AppHead app={app} env={env} />
      <div className="d-flex gap-4 mb-3">
        <div><div className="form-label">Current Status</div><StatusPill status={scope.status} /></div>
        <div><div className="form-label">Enforcement in PCE</div><div style={{ paddingTop: 3 }}>{scope.pce_state}</div></div>
      </div>
      <div className="form-label">New Status <span className="req">*</span></div>
      <div className="status-list" role="radiogroup" aria-label="New status">
        {STATUSES.map(s => (
          <button key={s} role="radio" aria-checked={value === s} className={`status-opt${value === s ? ' on' : ''}`} onClick={() => setValue(s)}>
            <span className="dot" style={{ background: STATUS_META[s].color }} />
            <b>{s}</b>
            <span className="d">{STATUS_META[s].hint}</span>
          </button>
        ))}
      </div>
      {value === 'Fully Enforced' && scope.status !== 'Fully Enforced' && scope.progress < 100 &&
        <div className="muted mt-2">Progress will be set to 100%.</div>}
    </Modal>
  );
}

function ExcludeModal({ app, onClose }: { app: App; onClose: () => void }) {
  const { run } = useStore();
  const [excluded, setExcluded] = useState(
    Object.fromEntries(app.environments.map(e => [e.id, !!e.excluded])) as Record<number, boolean>);
  const changed = app.environments.filter(e => !!e.excluded !== excluded[e.id]);
  const save = async () => {
    const ok = await run(async () => {
      for (const env of changed) await api('PATCH', `/api/environments/${env.id}`, { excluded: excluded[env.id] });
    }, 'Environment tracking updated');
    if (ok) onClose();
  };
  return (
    <Modal title="Exclude Environment" icon="bi-eye-slash" onClose={onClose}
      footer={<>
        <button className="btn btn-light" onClick={onClose}>Cancel</button>
        <button className="btn btn-primary" disabled={!changed.length} onClick={save}>Save</button>
      </>}>
      <AppHead app={app} />
      <div className="note mb-3">
        <Icon name="bi-info-circle-fill" />
        <div>Excluding an environment tracks the application without it. The application is still listed, but
          environment-specific tracking is skipped. Nothing changes in the PCE.</div>
      </div>
      {app.environments.map(env => (
        <div key={env.id} className="pce-row">
          <div style={{ flex: 1 }}>
            <b>{env.name}</b>
            <div className="muted">{managedCount(env)} managed workload(s) · {env.pce_state}</div>
          </div>
          <Toggle id={`ex-${env.id}`} checked={!!excluded[env.id]} label="Exclude"
            onChange={v => setExcluded({ ...excluded, [env.id]: v })} />
        </div>
      ))}
    </Modal>
  );
}

function CommentModal({ app, onClose }: { app: App; onClose: () => void }) {
  const { run } = useStore();
  const [text, setText] = useState('');
  const save = async () => {
    if (await run(() => api('POST', `/api/applications/${app.id}/comments`, { text }), 'Comment added')) onClose();
  };
  return (
    <Modal title="Add Comment" icon="bi-chat-left-text" onClose={onClose}
      footer={<>
        <button className="btn btn-light" onClick={onClose}>Cancel</button>
        <button className="btn btn-primary" disabled={!text.trim()} onClick={save}>Add Comment</button>
      </>}>
      <AppHead app={app} />
      <label className="form-label">Comment <span className="req">*</span></label>
      <TextArea value={text} onChange={setText} autoFocus placeholder="e.g. Waiting for the application team to confirm open ports." />
    </Modal>
  );
}

function RemoveModal({ app, onClose }: { app: App; onClose: () => void }) {
  const { run } = useStore();
  const remove = async () => {
    if (await run(() => api('DELETE', `/api/applications/${app.id}`), `${app.name} removed from the tracker`)) onClose();
  };
  return (
    <Modal title="Remove from tracker" icon="bi-trash3" size="sm" onClose={onClose}
      footer={<>
        <button className="btn btn-light" onClick={onClose}>Cancel</button>
        <button className="btn btn-danger" onClick={remove}>Remove</button>
      </>}>
      <p className="mb-2">Stop tracking <b>{app.name}</b>?</p>
      <p className="muted mb-0">Its history is kept and it can be restored from Settings. Nothing is deleted in the PCE.</p>
    </Modal>
  );
}

function ModalHost({ modal, onClose }: { modal: ModalState | null; onClose: () => void }) {
  const { data } = useStore();
  if (!modal) return null;
  const app = data.apps.find(a => a.id === modal.appId);
  if (!app) return null;
  const env = 'envId' in modal && modal.envId ? app.environments.find(e => e.id === modal.envId) : undefined;
  switch (modal.type) {
    case 'details': return <DetailsModal key={app.id} app={app} initialTab={modal.tab} onClose={onClose} />;
    case 'edit': return <EditModal app={app} onClose={onClose} />;
    case 'progress': return <ProgressModal app={app} env={env} onClose={onClose} />;
    case 'status': return <StatusModal app={app} env={env} onClose={onClose} />;
    case 'exclude': return <ExcludeModal app={app} onClose={onClose} />;
    case 'comment': return <CommentModal app={app} onClose={onClose} />;
    case 'remove': return <RemoveModal app={app} onClose={onClose} />;
  }
}
