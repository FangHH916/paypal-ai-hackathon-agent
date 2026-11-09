// The Dashboard frame (web/dashboard.html, served at /dashboard-frame). It reads the session the app already holds in
// this origin's storage, fetches the activity rows and the licence from Star's own API, and draws AG Studio. It only
// reads: no call it makes can move money.
import { useMemo, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import type { AgReportState, AgStudioApi, AgStudioMode } from 'ag-studio';
import { AgStudio, AgStudioProvider } from 'ag-studio-react';
import { buildData, hasRows, type ActivityRow } from './data';
import { PAGES, starReport } from './report';
import { sampleRows } from './sample';
import { starTheme } from './theme';

type Loaded = { rows: ActivityRow[]; currency: string; license: string };

const SAVED = 'star.dashboard.v1';
const read = (key: string) => {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
};
const loadSaved = (): AgReportState | null => {
  try {
    const raw = read(SAVED);
    return raw ? (JSON.parse(raw) as AgReportState) : null;
  } catch {
    return null;
  }
};
const save = (state: AgReportState | null) => {
  try {
    if (state) localStorage.setItem(SAVED, JSON.stringify(state));
    else localStorage.removeItem(SAVED);
  } catch {
    /* private mode: the layout just isn't kept */
  }
};

async function load(): Promise<Loaded> {
  const raw = read('sid');
  const sid = raw ? (JSON.parse(raw) as string) : '';
  if (!sid) throw new Error('no session');
  const get = async (path: string) => {
    const r = await fetch(path, { credentials: 'same-origin' });
    if (!r.ok) throw new Error(String(r.status));
    return r.json();
  };
  const q = `session_id=${encodeURIComponent(sid)}`;
  const [act, cfg] = await Promise.all([get(`/api/activity?${q}`), get(`/api/dashboard/config?${q}`)]);
  return { rows: act.rows as ActivityRow[], currency: cfg.currency as string, license: cfg.license as string };
}

function Dashboard({ rows, currency, license }: Loaded) {
  const api = useRef<AgStudioApi | null>(null);
  const real = hasRows(rows);
  const [sample, setSample] = useState(!real);
  const shown = sample ? sampleRows() : rows;
  const data = useMemo(() => buildData(shown, currency), [sample]); // eslint-disable-line react-hooks/exhaustive-deps
  const [initial] = useState<AgReportState>(() => loadSaved() ?? starReport);
  const [mode, setMode] = useState<AgStudioMode>('view');
  const [page, setPage] = useState(initial.selectedPageId ?? PAGES[0].id);

  const go = (id: string) => {
    const a = api.current;
    if (a) a.setState({ ...a.getState(), selectedPageId: id });
    setPage(id);
  };
  const done = () => {
    setMode('view');
    save(api.current?.getState() ?? null);
  };
  const reset = () => {
    save(null);
    api.current?.setState(starReport);
    setPage(starReport.selectedPageId ?? PAGES[0].id);
  };

  return (
    <div className="dash">
      {sample && (
        <p className="dash-sample" role="note">
          <span className="mode-tag">SAMPLE</span>
          {real
            ? 'A fictional sample month, to show the dashboard full. Your own activity is one click away.'
            : 'Nothing has happened in this session yet, so this is a fictional sample month. Send money in Talk to Star and your own activity appears here.'}
          {real && (
            <button type="button" className="text-btn" onClick={() => setSample(false)}>
              Show my activity
            </button>
          )}
        </p>
      )}
      <div className="dash-bar">
        <div className="dash-tabs" role="tablist" aria-label="Dashboard pages">
          {PAGES.map((p) => (
            <button key={p.id} type="button" role="tab" aria-selected={page === p.id} className="dash-tab" onClick={() => go(p.id)}>
              {p.title}
            </button>
          ))}
        </div>
        <div className="dash-actions">
          {!sample && (
            <button type="button" className="btn secondary small" onClick={() => setSample(true)}>
              Show a sample month
            </button>
          )}
          {mode === 'edit' ? (
            <>
              <button type="button" className="btn secondary small" onClick={reset}>Reset layout</button>
              <button type="button" className="btn primary small" onClick={done}>Done</button>
            </>
          ) : (
            <button type="button" className="btn secondary small" onClick={() => setMode('edit')}>Edit dashboard</button>
          )}
        </div>
      </div>
      <div className="dash-canvas">
        <AgStudioProvider licenseKey={license || undefined}>
          <AgStudio
            key={sample ? 'sample' : 'real'}
            style={{ height: '100%', width: '100%' }}
            theme={starTheme}
            loadThemeGoogleFonts={false}
            layout={{ widgetBorderEnabled: true }}
            panels={{ view: { right: [] } }}
            data={data}
            initialState={{ ...initial, selectedPageId: page }}
            mode={mode}
            onApiReady={(e) => {
              api.current = e.api;
            }}
          />
        </AgStudioProvider>
      </div>
    </div>
  );
}

const host = document.getElementById('star-dashboard');
if (host) {
  load()
    .then((loaded) => createRoot(host).render(<Dashboard {...loaded} />))
    .catch(() => {
      host.innerHTML =
        '<p class="dash-empty">The dashboard could not load. Open Star first, then come back to this page.</p>';
    });
}
