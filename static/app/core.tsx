// Shared types, constants, API client and helpers. All .tsx files are concatenated
// and compiled together in the browser, so everything here is in scope everywhere.
declare const React: any;
declare const ReactDOM: any;
const { useState, useEffect, useMemo, useRef, useCallback, createContext, useContext } = React;

type Status = 'Not Started' | 'In Progress' | 'Selective Enforcement' | 'Fully Enforced';
type Priority = 'Critical' | 'High' | 'Medium' | 'Low';
type RoleCode = 'ps' | 'pm' | 'owner';

interface WorkloadCounts {
  wl_full: number; wl_selective: number; wl_visibility: number; wl_idle: number; wl_unmanaged: number;
}
interface Env extends WorkloadCounts {
  id: number; app_id: number; environment_id: number; name: string; label_href: string; excluded: number; in_pce: number;
  status: Status; progress: number; pce_state: string;
  priority: Priority; priority_inherited: number; start_date: string | null; end_date: string | null; comments: string | null;
}
interface App extends WorkloadCounts {
  id: number; name: string; label_href: string; description: string; rank: number;
  priority: Priority; status: Status; progress: number;
  start_date: string | null; end_date: string | null;
  ps_id: number | null; pm_id: number | null; owner_id: number | null;
  comments: string | null; comment_count: number; pce_state: string;
  in_pce: number; hidden: number; excluded: number; updated_at: string;
  start_auto: boolean; end_auto: boolean; // the date is calculated from the application's environments environments: Env[];
}
interface Person { id: number; name: string; email: string; title: string; is_ps: number; is_pm: number; is_owner: number; }
interface Activity { id: number; app_id?: number; app_name?: string; kind: string; text: string; author: string; created_at: string; }
interface Bootstrap {
  apps: App[]; hidden: App[]; people: Person[]; activity: Activity[];
  statuses: Status[]; priorities: Priority[]; pce_state_status: Record<string, Status>;
  settings: { user_name: string; last_sync: string; traffic_days: number };
  pce: { name: string; fqdn: string; ui_base: string };
}
type ModalState =
  | { type: 'details' | 'edit' | 'exclude' | 'comment' | 'remove'; appId: number; tab?: string }
  | { type: 'progress' | 'status'; appId: number; envId?: number };
interface Store {
  data: Bootstrap;
  reload: () => Promise<void>;
  run: (fn: () => Promise<any>, okMessage?: string) => Promise<boolean>;
  toast: (message: string, kind?: 'ok' | 'err') => void;
  open: (modal: ModalState | null) => void;
  person: (id: number | null) => Person | undefined;
}

const STATUSES: Status[] = ['Not Started', 'In Progress', 'Selective Enforcement', 'Fully Enforced'];
const PRIORITIES: Priority[] = ['Critical', 'High', 'Medium', 'Low'];
const STATUS_META: Record<Status, { tone: string; color: string; icon: string; hint: string }> = {
  'Not Started': { tone: 'ns', color: 'var(--st-ns)', icon: 'bi-circle', hint: 'No policy work yet' },
  'In Progress': { tone: 'ip', color: 'var(--st-ip)', icon: 'bi-circle-half', hint: 'Visibility, building policy' },
  'Selective Enforcement': { tone: 'se', color: 'var(--st-se)', icon: 'bi-shield-check', hint: 'Enforcing selected services' },
  'Fully Enforced': { tone: 'fe', color: 'var(--st-fe)', icon: 'bi-shield-fill-check', hint: 'Default-deny in place' },
};
const PRIORITY_META: Record<Priority, { tone: string; color: string }> = {
  Critical: { tone: 'critical', color: 'var(--pr-critical)' },
  High: { tone: 'high', color: 'var(--pr-high)' },
  Medium: { tone: 'medium', color: 'var(--pr-medium)' },
  Low: { tone: 'low', color: 'var(--pr-low)' },
};
const ROLE_LABEL: Record<RoleCode, string> = { ps: 'Assigned PS', pm: 'Project Manager', owner: 'App Owner' };
const PROGRESS_BUCKETS = [
  { label: 'Completed', min: 100, max: 100, color: 'var(--st-fe)' },
  { label: '75 – 99%', min: 75, max: 99, color: 'var(--seq-4)' },
  { label: '50 – 74%', min: 50, max: 74, color: 'var(--seq-3)' },
  { label: '25 – 49%', min: 25, max: 49, color: 'var(--seq-2)' },
  { label: '0 – 24%', min: 0, max: 24, color: 'var(--seq-1)' },
];

const StoreCtx = createContext(null as any);
const useStore = (): Store => useContext(StoreCtx);

async function api<T = any>(method: string, url: string, payload?: any): Promise<T> {
  const res = await fetch(url, {
    method,
    headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'enforcement-tracker' },
    body: payload === undefined ? undefined : JSON.stringify(payload),
  });
  const json = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(json.error || `Request failed (${res.status})`);
  return json as T;
}

const todayISO = (): string => {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
};
const parseDay = (iso: string): Date => new Date(iso + 'T00:00:00');
const fmtDate = (iso: string | null): string =>
  iso ? parseDay(iso).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' }) : '—';
// Compact form for dense tables: the year is only shown when it is not the current one.
const fmtDateShort = (iso: string | null): string => {
  if (!iso) return '—';
  const d = parseDay(iso);
  return d.toLocaleDateString(undefined, d.getFullYear() === new Date().getFullYear()
    ? { month: 'short', day: 'numeric' } : { year: 'numeric', month: 'short', day: 'numeric' });
};
const fmtDateTime = (iso: string): string =>
  iso ? new Date(iso).toLocaleString(undefined, { month: 'short', day: 'numeric', year: 'numeric', hour: 'numeric', minute: '2-digit' }) : 'Never';
function relTime(iso: string): string {
  const mins = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60000));
  if (mins < 1) return 'just now';
  if (mins < 60) return `${mins}m ago`;
  if (mins < 1440) return `${Math.round(mins / 60)}h ago`;
  return `${Math.round(mins / 1440)}d ago`;
}
const pct = (n: number, total: number): number => (total ? Math.round((n / total) * 100) : 0);
const initials = (name: string): string =>
  name.split(/\s+/).filter(Boolean).slice(0, 2).map(w => w[0].toUpperCase()).join('') || '?';
const managedCount = (w: WorkloadCounts): number => w.wl_full + w.wl_selective + w.wl_visibility + w.wl_idle;
const isOverdue = (a: App): boolean => !!a.end_date && a.end_date < todayISO() && a.status !== 'Fully Enforced';
// Applications that are in scope: excluded ones stay listed (greyed out) but are left out of totals.
const activeApps = (apps: App[]): App[] => apps.filter(a => !a.excluded);
const trackedEnvs = (a: App): Env[] => a.environments.filter(e => !e.excluded);
function progressColor(value: number): string {
  const bucket = PROGRESS_BUCKETS.find(b => value >= b.min && value <= b.max);
  return bucket ? bucket.color : 'var(--seq-1)';
}
const pceLabelUrl = (data: Bootstrap, app: App): string =>
  `${data.pce.ui_base}/#/labels/${app.label_href.split('/').pop()}`;
