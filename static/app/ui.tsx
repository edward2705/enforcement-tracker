// Small presentational building blocks: pills, progress, modal, menu, charts.

function Icon({ name, className = '' }: { name: string; className?: string }) {
  return <i className={`bi ${name} ${className}`} aria-hidden="true" />;
}

function StatusPill({ status }: { status: Status }) {
  return <span className={`pill t-${STATUS_META[status].tone}`}><span className="dot" />{status}</span>;
}

function PriorityPill({ priority }: { priority: Priority }) {
  return <span className={`pill t-${PRIORITY_META[priority].tone}`}><span className="dot" />{priority}</span>;
}

// A pill that is also a native <select>, so it stays keyboard and screen-reader friendly.
function PillSelect<T extends string>({ value, options, tone, label, onChange }: {
  value: T; options: T[]; tone: string; label: string; onChange: (v: T) => void;
}) {
  return (
    <span className={`pill pill-select t-${tone}`}>
      <span className="dot" />{value}
      <Icon name="bi-chevron-down" />
      <select aria-label={label} value={value} onChange={(e: any) => onChange(e.target.value)}>
        {options.map(o => <option key={o} value={o}>{o}</option>)}
      </select>
    </span>
  );
}

function ProgressBar({ value, wide, onClick }: { value: number; wide?: boolean; onClick?: () => void }) {
  const inner = (
    <>
      <span className="pv num">{value}%</span>
      <span className="pt"><i style={{ width: `${value}%`, background: progressColor(value) }} /></span>
    </>
  );
  const cls = `prog${wide ? ' wide' : ''}`;
  return onClick
    ? <button className={cls} onClick={onClick} title="Update progress">{inner}</button>
    : <span className={cls}>{inner}</span>;
}

function Avatar({ name, size = 22 }: { name: string; size?: number }) {
  return <span className="avatar" style={{ width: size, height: size, fontSize: size * 0.42 }}>{initials(name)}</span>;
}

function PersonCell({ id, plain }: { id: number | null; plain?: boolean }) {
  const p = useStore().person(id);
  if (!p) return <span className="muted">Unassigned</span>;
  if (plain) return <span>{p.name}</span>;
  return <span className="person"><Avatar name={p.name} />{p.name}</span>;
}

function Toggle({ checked, onChange, label, id }: { checked: boolean; onChange: (v: boolean) => void; label: any; id: string }) {
  return (
    <div className="form-check form-switch">
      <input className="form-check-input" type="checkbox" role="switch" id={id} checked={checked}
        onChange={(e: any) => onChange(e.target.checked)} />
      <label className="form-check-label" htmlFor={id}>{label}</label>
    </div>
  );
}

function Empty({ icon, title, children }: { icon: string; title: string; children?: any }) {
  return <div className="empty"><Icon name={icon} /><h3>{title}</h3><div>{children}</div></div>;
}

function Modal({ title, icon, size = '', onClose, footer, children }: {
  title: string; icon: string; size?: '' | 'sm' | 'lg'; onClose: () => void; footer?: any; children: any;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);
  return (
    <div className="overlay" onMouseDown={(e: any) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className={`dialog ${size}`} role="dialog" aria-modal="true" aria-label={title}>
        <header>
          <Icon name={icon} />
          <h3>{title}</h3>
          <button className="x-btn" onClick={onClose} aria-label="Close"><Icon name="bi-x-lg" /></button>
        </header>
        <div className="body">{children}</div>
        {footer && <footer>{footer}</footer>}
      </div>
    </div>
  );
}

// Dropdown menu rendered at fixed coordinates so scrolling containers never clip it.
function Menu({ anchor, onClose, children, align = 'right', keepOnScroll = false }: {
  anchor: DOMRect; onClose: () => void; children: any; align?: 'left' | 'right';
  keepOnScroll?: boolean; // for menus whose choices change the page height underneath them
}) {
  const ref = useRef(null as HTMLDivElement | null);
  const [pos, setPos] = useState({ top: anchor.bottom + 6, left: anchor.left, ready: false });
  useEffect(() => {
    const el = ref.current!;
    const { width, height } = el.getBoundingClientRect();
    let left = align === 'right' ? anchor.right - width : anchor.left;
    left = Math.max(8, Math.min(left, window.innerWidth - width - 8));
    let top = anchor.bottom + 6;
    if (top + height > window.innerHeight - 8) top = Math.max(8, anchor.top - height - 6);
    setPos({ top, left, ready: true });
    const close = (e: Event) => { if (!el.contains(e.target as Node)) onClose(); };
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    document.addEventListener('mousedown', close);
    document.addEventListener('keydown', onKey);
    if (!keepOnScroll) window.addEventListener('scroll', close, true);
    window.addEventListener('resize', onClose);
    return () => {
      document.removeEventListener('mousedown', close);
      document.removeEventListener('keydown', onKey);
      window.removeEventListener('scroll', close, true);
      window.removeEventListener('resize', onClose);
    };
  }, []);
  return ReactDOM.createPortal(
    <div ref={ref} className="menu" role="menu" style={{ top: pos.top, left: pos.left, visibility: pos.ready ? 'visible' : 'hidden' }}>
      {children}
    </div>, document.body);
}

// Hover tooltip shared by the charts.
function useTip() {
  const [tip, setTip] = useState(null as null | { x: number; y: number; body: any });
  const bind = (body: any) => ({
    onMouseEnter: (e: any) => setTip({ x: e.clientX, y: e.clientY, body }),
    onMouseMove: (e: any) => setTip({ x: e.clientX, y: e.clientY, body }),
    onMouseLeave: () => setTip(null),
  });
  const el = tip && ReactDOM.createPortal(<div className="tip" style={{ left: tip.x, top: tip.y }}>{tip.body}</div>, document.body);
  return { bind, el };
}

interface Slice { label: string; value: number; color: string; }

function Donut({ slices, centerLabel, active, onPick }: {
  slices: Slice[]; centerLabel: string; active?: string; onPick?: (label: string) => void;
}) {
  const tip = useTip();
  const [hot, setHot] = useState('');
  const total = slices.reduce((s, x) => s + x.value, 0);
  const R = 42, C = 2 * Math.PI * R, GAP = 2.5;
  let offset = 0;
  const arcs = slices.filter(s => s.value > 0).map(s => {
    const len = (s.value / total) * C;
    const arc = { ...s, dash: Math.max(len - GAP, 0.5), offset };
    offset += len;
    return arc;
  });
  return (
    <div className="donut-wrap">
      <div className="donut">
        <svg viewBox="0 0 100 100" role="img" aria-label={`${total} ${centerLabel}`}>
          {total === 0 && <circle cx="50" cy="50" r={R} style={{ stroke: 'var(--line)' }} strokeWidth="13" />}
          {arcs.map(a => (
            <circle key={a.label} cx="50" cy="50" r={R} style={{ stroke: a.color }}
              strokeWidth={hot === a.label || active === a.label ? 16 : 13}
              strokeDasharray={`${a.dash} ${C - a.dash}`} strokeDashoffset={-a.offset}
              onClick={() => onPick && onPick(a.label)}
              {...tip.bind(<><b>{a.label}</b> · {a.value} ({pct(a.value, total)}%)</>)} />
          ))}
        </svg>
        <div className="center"><b className="num">{total}</b><span>{centerLabel}</span></div>
      </div>
      <div className="legend">
        {slices.map(s => (
          <button key={s.label} className={`legend-row${active === s.label ? ' hot' : ''}`}
            onMouseEnter={() => setHot(s.label)} onMouseLeave={() => setHot('')}
            onClick={() => onPick && onPick(s.label)}>
            <span className="swatch" style={{ background: s.color }} />
            <span>{s.label}</span>
            <span className="n num">{s.value}</span>
            <span className="p num">{pct(s.value, total)}%</span>
          </button>
        ))}
      </div>
      {tip.el}
    </div>
  );
}

function niceMax(v: number): number {
  if (v <= 4) return 4;
  const step = Math.pow(10, Math.floor(Math.log10(v)));
  const m = [1, 2, 4, 5, 8, 10].find(k => k * step >= v) || 10;
  return m * step;
}

function ColumnChart({ bars, onPick }: { bars: Slice[]; onPick?: (label: string) => void }) {
  const tip = useTip();
  const total = bars.reduce((s, b) => s + b.value, 0);
  const max = niceMax(Math.max(...bars.map(b => b.value), 1));
  const ticks = [0, 1, 2, 3, 4].map(i => (max / 4) * i);
  return (
    <div className="bars">
      <div className="axis num">{ticks.map(t => <span key={t}>{t}</span>)}</div>
      <div className="plot">
        <div className="grid">{ticks.map(t => <i key={t} />)}</div>
        {bars.map(b => (
          <button key={b.label} className="bar-col" onClick={() => onPick && onPick(b.label)}
            {...tip.bind(<><b>{b.label}</b> · {b.value} ({pct(b.value, total)}%)</>)}>
            <span className="v num">{b.value}</span>
            <span className="b" style={{ height: `${(b.value / max) * 82}%`, background: b.color }} />
            <span className="x">{b.label}</span>
          </button>
        ))}
      </div>
      {tip.el}
    </div>
  );
}

function BarList({ rows, unit = 'applications' }: { rows: Slice[]; unit?: string }) {
  const tip = useTip();
  const total = rows.reduce((s, r) => s + r.value, 0);
  const max = Math.max(...rows.map(r => r.value), 1);
  return (
    <div className="hbars">
      {rows.map(r => (
        <div key={r.label} className="hbar" {...tip.bind(<><b>{r.label}</b> · {r.value} of {total} {unit}</>)}>
          <span>{r.label}</span>
          <span className="track">{r.value > 0 && <i style={{ width: `${(r.value / max) * 100}%`, background: r.color }} />}</span>
          <span className="n num"><b>{r.value}</b> <span className="muted">({pct(r.value, total)}%)</span></span>
        </div>
      ))}
      {tip.el}
    </div>
  );
}

// Managed workloads by PCE enforcement mode, as one thin stacked bar.
function WorkloadBar({ w }: { w: WorkloadCounts }) {
  const parts = [
    { label: 'Full', value: w.wl_full, color: 'var(--st-fe)' },
    { label: 'Selective', value: w.wl_selective, color: 'var(--st-se)' },
    { label: 'Visibility only', value: w.wl_visibility, color: 'var(--st-ip)' },
    { label: 'Idle', value: w.wl_idle, color: 'var(--st-ns)' },
  ].filter(p => p.value > 0);
  const total = managedCount(w);
  const text = parts.map(p => `${p.value} ${p.label.toLowerCase()}`).join(', ');
  if (!total) return <span className="muted">No managed workloads{w.wl_unmanaged ? ` (${w.wl_unmanaged} unmanaged)` : ''}</span>;
  return (
    <div title={text}>
      <div className="wl-bar">{parts.map(p => <i key={p.label} style={{ flex: p.value, background: p.color }} />)}</div>
      <div className="muted" style={{ fontSize: 11.5, marginTop: 3 }}>{text}{w.wl_unmanaged ? `, ${w.wl_unmanaged} unmanaged` : ''}</div>
    </div>
  );
}

function TextArea({ value, onChange, max = 500, rows = 3, placeholder, autoFocus }: {
  value: string; onChange: (v: string) => void; max?: number; rows?: number; placeholder?: string; autoFocus?: boolean;
}) {
  return (
    <>
      <textarea className="form-control" rows={rows} maxLength={max} value={value} placeholder={placeholder}
        autoFocus={autoFocus} onChange={(e: any) => onChange(e.target.value)} />
      <div className="counter num">{value.length}/{max}</div>
    </>
  );
}

// Drag handle for the right edge of a table header cell. Drag or use the arrow keys to resize;
// double-click returns the column to its automatic width.
function ColResizer({ label, min, max, onChange }: { label: string; min: number; max: number; onChange: (w: number | null) => void }) {
  const drag = useRef(null as null | { x: number; w: number });
  const [active, setActive] = useState(false);
  const clamp = (n: number) => Math.round(Math.max(min, Math.min(max, n)));
  const current = (el: any) => el.parentElement.getBoundingClientRect().width;
  return (
    <span className={`col-resize in-th${active ? ' on' : ''}`} role="separator" tabIndex={0} aria-orientation="vertical"
      aria-label={`Resize the ${label} column`} title={`Drag to resize the ${label} column · double-click for automatic width`}
      onClick={(e: any) => e.stopPropagation()}
      onPointerDown={(e: any) => { e.preventDefault(); e.stopPropagation(); e.currentTarget.setPointerCapture(e.pointerId); drag.current = { x: e.clientX, w: current(e.currentTarget) }; setActive(true); }}
      onPointerMove={(e: any) => { if (drag.current) onChange(clamp(drag.current.w + e.clientX - drag.current.x)); }}
      onPointerUp={(e: any) => { e.currentTarget.releasePointerCapture(e.pointerId); drag.current = null; setActive(false); }}
      onPointerCancel={() => { drag.current = null; setActive(false); }}
      onDoubleClick={(e: any) => { e.stopPropagation(); onChange(null); }}
      onKeyDown={(e: any) => {
        if (e.key === 'ArrowLeft') { e.preventDefault(); onChange(clamp(current(e.currentTarget) - 16)); }
        if (e.key === 'ArrowRight') { e.preventDefault(); onChange(clamp(current(e.currentTarget) + 16)); }
        if (e.key === 'Enter' || e.key === ' ') e.stopPropagation();
      }} />
  );
}

// In-cell editors for the tracking grid. Enter or leaving the field saves, Escape cancels.
function InlineInput({ type, initial, label, width, min, max, placeholder, allowEmpty, onCommit, onCancel }: {
  type: 'text' | 'number' | 'date'; initial: string; label: string; width: number; min?: number; max?: number;
  placeholder?: string; allowEmpty?: boolean; onCommit: (value: string) => void; onCancel: () => void;
}) {
  const [value, setValue] = useState(initial);
  const done = useRef(false);
  const finish = (save: boolean) => {
    if (done.current) return;
    done.current = true;
    const v = value.trim();
    if (save && v !== initial && (v || allowEmpty)) onCommit(v); else onCancel();
  };
  return (
    <input type={type} className="form-control form-control-sm cell-input num" style={{ width }} autoFocus aria-label={label}
      min={min} max={max} maxLength={type === 'text' ? 500 : undefined} placeholder={placeholder} value={value}
      onFocus={(e: any) => { if (type !== 'date') e.target.select(); }} onChange={(e: any) => setValue(e.target.value)}
      onBlur={() => finish(true)}
      onKeyDown={(e: any) => {
        if (e.key === 'Enter') finish(true);
        if (e.key === 'Escape') finish(false);
      }} />
  );
}

function InlinePerson({ role, value, label, onCommit, onCancel }: {
  role: RoleCode; value: number | null; label: string; onCommit: (id: number | null) => void; onCancel: () => void;
}) {
  const { data, toast } = useStore();
  const [adding, setAdding] = useState(false);
  const switching = useRef(false);
  if (adding) {
    return <InlineInput type="text" initial="" width={150} label={`New ${ROLE_LABEL[role]} name`} placeholder="Full name" onCancel={onCancel}
      onCommit={async name => {
        try { onCommit((await api('POST', '/api/people', { name, ['is_' + role]: 1 })).id); }
        catch (e: any) { toast(e.message, 'err'); onCancel(); }
      }} />;
  }
  return (
    <select className="form-select form-select-sm cell-input" style={{ width: 150 }} autoFocus aria-label={label} value={value ?? ''}
      onBlur={() => { if (!switching.current) onCancel(); }}
      onKeyDown={(e: any) => { if (e.key === 'Escape') onCancel(); }}
      onChange={(e: any) => {
        if (e.target.value === '__new') { switching.current = true; setAdding(true); }
        else onCommit(e.target.value ? Number(e.target.value) : null);
      }}>
      <option value="">Unassigned</option>
      {data.people.filter(p => (p as any)['is_' + role] || p.id === value).map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
      <option value="__new">＋ Add a new person…</option>
    </select>
  );
}

// Person dropdown limited to one role, with an inline "add someone new" path.
function PersonSelect({ value, role, onChange }: { value: number | null; role: RoleCode; onChange: (id: number | null) => void }) {
  const { data, reload, toast } = useStore();
  const [adding, setAdding] = useState(false);
  const [name, setName] = useState('');
  const options = data.people.filter(p => (p as any)['is_' + role] || p.id === value);
  const add = async () => {
    if (!name.trim()) return;
    try {
      const res = await api('POST', '/api/people', { name: name.trim(), ['is_' + role]: 1 });
      await reload();
      onChange(res.id);
      setAdding(false);
      setName('');
    } catch (e: any) { toast(e.message, 'err'); }
  };
  if (adding) {
    return (
      <div className="input-group">
        <input className="form-control" autoFocus placeholder="Full name" value={name}
          onChange={(e: any) => setName(e.target.value)}
          onKeyDown={(e: any) => { if (e.key === 'Enter') { e.preventDefault(); add(); } }} />
        <button type="button" className="btn btn-primary" onClick={add} disabled={!name.trim()}>Add</button>
        <button type="button" className="btn btn-light" onClick={() => setAdding(false)} aria-label="Cancel"><Icon name="bi-x-lg" /></button>
      </div>
    );
  }
  return (
    <select className="form-select" value={value ?? ''} onChange={(e: any) => {
      if (e.target.value === '__new') setAdding(true);
      else onChange(e.target.value ? Number(e.target.value) : null);
    }}>
      <option value="">Unassigned</option>
      {options.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}
      <option value="__new">＋ Add a new person…</option>
    </select>
  );
}
