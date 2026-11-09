// The built-in report: "Her money" (what she sent, and whether PayPal confirmed it) and "What Star stopped" (requests
// refused before any money moved, and the checks behind them). Field ids ('payments.sent') refer to ./data.ts.
import type { AgPageState, AgReportState, AgWidgetLayoutState } from 'ag-studio';

// Position on the canvas: 24 columns wide, rows of studioCanvasRowHeight.
const at = (xTrack: number, yTrack: number, xSpan: number, ySpan: number): AgWidgetLayoutState => ({ xTrack, yTrack, xSpan, ySpan });
const title = (text: string) => ({ title: { enabled: true, text } });
const sum = (id: string) => ({ id, aggregation: 'sum' as const });
const FONT = "'Plain Figures', 'Atkinson Next', 'Segoe UI', 'Helvetica Neue', Arial, sans-serif";
const type = (fontSize: number, fontWeight: 'normal' | 'bold' = 'normal') => ({ fontFamily: FONT, fontSize, fontWeight, fontStyle: 'normal' as const });

const kpi = (text: string, value: { id: string; aggregation?: 'sum' }, caption = '') => ({
  type: 'value' as const,
  dataMapping: { value: [value] },
  format: {
    title: { enabled: true, text, typography: type(15, 'bold') },
    caption: { enabled: !!caption, text: caption, typography: type(14) },
    style: { typography: type(34) },
  },
});

const money: AgPageState = {
  id: 'money',
  widgets: {
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    path: { type: 'safety-path' } as any,
    'kpi-sent': kpi('Sent to family', sum('payments.sent'), 'Counted once PayPal confirms'),
    'kpi-payments': kpi('Cards she signed', sum('payments.one')),
    'kpi-stops': kpi('Requests stopped', sum('stops.one'), 'Before any money moved'),
    'kpi-calls': kpi('PayPal calls on stops', sum('stops.paypalCalls'), 'Money-moving calls: always zero'),
    'sent-by-person': {
      type: 'bar-chart-grouped',
      dataMapping: { categoryKey: [{ id: 'payments.to' }], valueKey: [sum('payments.sent')] },
      format: title('Sent to each person'),
    },
    confirmed: kpi('Confirmed by PayPal', { id: 'confirmedShare' }, 'Of the payments she signed'),
    'confirmed-how': {
      type: 'donut-chart',
      dataMapping: { categoryKey: [{ id: 'payments.confirmedBy' }], valueKey: [sum('payments.one')] },
      format: { ...title('How each payment was confirmed'), style: { theme: { common: { legend: { enabled: true, position: 'bottom' } } } } },
    },
    'by-day': {
      type: 'column-chart-grouped',
      dataMapping: { categoryKey: [{ id: 'calendar::day' }], valueKey: [sum('payments.sent')] },
      format: title('Sent by day'),
    },
    payments: {
      type: 'grid',
      dataMapping: {
        cols: [
          { id: 'payments.date' }, { id: 'payments.to' }, sum('payments.amount'), { id: 'payments.state' },
          { id: 'payments.confirmedBy' }, { id: 'payments.family' }, { id: 'payments.reference' }, { id: 'payments.auditHash' },
        ],
      },
      sort: [{ field: { id: 'payments.date' }, direction: 'desc' }],
      format: title('Every payment, with its PayPal reference and audit record'),
    },
  },
  widgetLayout: {
    'kpi-sent': at(0, 0, 6, 7),
    'kpi-payments': at(6, 0, 6, 7),
    'kpi-stops': at(12, 0, 6, 7),
    'kpi-calls': at(18, 0, 6, 7),
    path: at(0, 7, 24, 11),
    'sent-by-person': at(0, 18, 10, 16),
    confirmed: at(10, 18, 5, 16),
    'confirmed-how': at(15, 18, 9, 16),
    'by-day': at(0, 34, 24, 14),
    payments: at(0, 48, 24, 20),
  },
};

const stopped: AgPageState = {
  id: 'stopped',
  widgets: {
    'kpi-stops': kpi('Requests stopped', sum('stops.one')),
    'kpi-calls': kpi('Money-moving PayPal calls', sum('stops.paypalCalls'), 'For all of these together'),
    'by-rule': {
      type: 'bar-chart-grouped',
      dataMapping: { categoryKey: [{ id: 'rules.rule' }], valueKey: [sum('rules.one')] },
      format: title('Which check stopped it'),
    },
    'by-who': {
      type: 'donut-chart',
      dataMapping: { categoryKey: [{ id: 'stops.stoppedBy' }], valueKey: [sum('stops.one')] },
      format: { ...title('Stopped by'), style: { theme: { common: { legend: { enabled: true, position: 'bottom' } } } } },
    },
    stops: {
      type: 'grid',
      dataMapping: { cols: [{ id: 'stops.date' }, { id: 'stops.what' }, { id: 'stops.stoppedBy' }, { id: 'stops.why' }, sum('stops.paypalCalls')] },
      sort: [{ field: { id: 'stops.date' }, direction: 'desc' }],
      format: title('Every stop, in her words'),
    },
  },
  widgetLayout: {
    'kpi-stops': at(0, 0, 12, 7),
    'kpi-calls': at(12, 0, 12, 7),
    'by-rule': at(0, 7, 14, 16),
    'by-who': at(14, 7, 10, 16),
    stops: at(0, 23, 24, 22),
  },
};

export const PAGES = [
  { id: money.id, title: 'Her money' },
  { id: stopped.id, title: 'What Star stopped' },
];

export const starReport: AgReportState = { selectedPageId: money.id, pages: [money, stopped] };
