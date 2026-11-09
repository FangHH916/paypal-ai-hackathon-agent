// A custom AG Studio widget: the path every request takes in Star, with live counts from the data engine. It asks the
// engine for two aggregates (payments and stops are separate tables), so page filters and cross filters apply to it
// like to any built-in widget. Styles are classes in web/styles.css (.sp-*), drawn in Star's own tokens.
import { useEffect, useState } from 'react';
import type { AgWidgetApi, AgWidgetField, AgWidgetParams } from 'ag-studio';

// A field from the engine's schema, summed. Fields are found by their qualified id ('payments.one').
const summed = (api: AgWidgetApi, id: string): AgWidgetField => {
  const table = id.split('.')[0];
  const t = api.getSchema().find((x) => x.id === table);
  const f = t?.fields.find((x) => String(x.fieldId) === id || x.key === id);
  if (!f) throw new Error(`no field ${id}`);
  return { ...f, type: 'implicitMeasure', aggregation: 'sum' } as AgWidgetField;
};
type Counts = { signed: number; confirmed: number; sent: number; stopped: number; calls: number };

// One aggregate row: read the value of the field asked for, whatever key the engine gives it.
const first = (rows: Record<string, unknown>[], i: number) => {
  const row = rows[0] ?? {};
  const v = Object.values(row)[i];
  return typeof v === 'number' ? v : Number(v ?? 0) || 0;
};

export function SafetyPath({ widgetApi }: AgWidgetParams) {
  const [n, setN] = useState<Counts | null>(null);

  useEffect(() => {
    let live = true;
    (async () => {
      widgetApi.setDisplayState('loading', { prominent: false });
      try {
        const f = (id: string) => summed(widgetApi, id);
        const [p, s] = await Promise.all([
          widgetApi.getData({ fields: [f('payments.one'), f('payments.isConfirmed'), f('payments.sent')] }, { queryId: 'payments' }),
          widgetApi.getData({ fields: [f('stops.one'), f('stops.paypalCalls')] }, { queryId: 'stops' }),
        ]);
        if (!live) return;
        const pr = p.results.rows, sr = s.results.rows;
        setN({ signed: first(pr, 0), confirmed: first(pr, 1), sent: first(pr, 2), stopped: first(sr, 0), calls: first(sr, 1) });
        widgetApi.setDisplayState('displayed');
      } catch {
        if (live) widgetApi.setDisplayState('noData');
      }
    })();
    return () => {
      live = false;
    };
  }, [widgetApi]);

  if (!n) return null;
  const asked = n.signed + n.stopped;
  const money = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(n.sent);
  return (
    <div className="sp" role="group" aria-label="The path every request takes in Star">
      <ol className="sp-path">
        <li className="sp-step">
          <span className="sp-k">She asked</span>
          <b className="sp-n">{asked}</b>
          <small>in her own words</small>
        </li>
        <li className="sp-step sp-signed">
          <span className="sp-k">Signed on her device</span>
          <b className="sp-n">{n.signed}</b>
          <small>the exact card she saw</small>
        </li>
        <li className="sp-step">
          <span className="sp-k">Sent through PayPal</span>
          <b className="sp-n">{n.signed}</b>
          <small>Payouts, one request id each</small>
        </li>
        <li className="sp-step sp-ok">
          <span className="sp-k">Confirmed by PayPal</span>
          <b className="sp-n">{n.confirmed}</b>
          <small>{money} arrived</small>
        </li>
      </ol>
      <div className="sp-branch">
        <span className="sp-k">Stopped before money moved</span>
        <b className="sp-n">{n.stopped}</b>
        <span className="sp-zero">
          <b>{n.calls}</b> money-moving PayPal calls
        </span>
      </div>
    </div>
  );
}

const ICON =
  '<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M3 12h4l3-7 4 14 3-7h4"/></svg>';

// Typed loosely: AG Studio's registry generics are for libraries of widgets; Star adds one.
export const safetyPathWidget = {
  id: 'safety-path',
  label: 'Safety path',
  icon: ICON,
  form: (params: { createDefaults: (o: { dataMappingItems: unknown[] }) => unknown }) => params.createDefaults({ dataMappingItems: [] }),
  comp: SafetyPath,
  defaultSize: { width: 900, height: 200 },
  minSize: { width: 420, height: 160 },
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
} as any;
