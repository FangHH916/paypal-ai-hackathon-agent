// AG Studio's data sources, built from Star's own activity API (GET /api/activity): the same rows the Activity &
// audit page shows, with emails already masked by the server. Three tables: the payments she signed, the requests
// Star stopped, and the rules behind each stop. Nothing here can move money; it only reads.
import { createFormats, type AgDataSourcesDefinition, type AgFieldDefinition, type AgFormat } from 'ag-studio';

export type ActivityRow = {
  row_id: string; time: number; type: string; kind: string; to: string; amount_cents: number; status: string;
  verified: boolean; verified_by: string; stopped_by: string; why: string; signed_with: string; family: string;
  reference: string; audit_hash: string; outcome: 'moved' | 'open' | 'stopped'; codes?: string[];
};

const CALENDAR = 'calendar';
type Spec = [id: string, name: string, format: AgFormat, hide?: boolean];
const fields = (specs: Spec[]): AgFieldDefinition[] =>
  specs.map(([id, name, format, hide]) => ({ id, name, format, ...(hide && { hide }) }));
const iso = (t: number) => new Date(t * 1000).toISOString();

// How a payment was confirmed, in words: Star says "sent" only from a signature-checked webhook or a status check.
const confirmedBy = (r: ActivityRow) =>
  !r.verified ? 'Not confirmed yet' : r.verified_by.startsWith('webhook') ? 'PayPal webhook (signature checked)' : 'PayPal status check';

// Plain names for the rule codes a stop can carry; anything else is turned into words from its code.
const RULE_WORDS: Record<string, string> = {
  SCAM_PRE_SCREEN: 'Scam words, caught before any model',
  GUARDIAN_DENIED: 'Family said no',
  R10_NEW_PAYEE: 'Someone new',
  R15_NEW_PAYEE_RAMP: 'Someone new: family checks first',
  R50_AMOUNT_NOT_SAID: 'Amount she never said',
  R51_PAYEE_NOT_SAID: 'Person she never named',
  R60_UNKNOWN_OUTCOME: 'Earlier payment not confirmed yet',
  R61_RECENT_DUPLICATE: 'Looks like a repeat',
  R94_GIFT_CARD: 'Gift cards',
  R96_REFUND_SCAM: 'Send-the-difference scam',
  RULES: 'Fixed rules',
};
const ruleName = (code: string) =>
  RULE_WORDS[code] ?? code.replace(/^R\d+_/, '').replace(/_/g, ' ').toLowerCase().replace(/^./, (c) => c.toUpperCase());

export const transfers = (rows: ActivityRow[]) => rows.filter((r) => r.outcome !== 'stopped' && r.kind === 'transfer');
export const stops = (rows: ActivityRow[]) => rows.filter((r) => r.outcome === 'stopped');
export const hasRows = (rows: ActivityRow[]) => transfers(rows).length + stops(rows).length > 0;

export function buildData(rows: ActivityRow[], currency: string): AgDataSourcesDefinition {
  const payments = transfers(rows).map((r) => ({
    id: r.row_id,
    date: iso(r.time),
    to: r.to || 'Unknown',
    amount: Math.abs(r.amount_cents) / 100,
    sent: r.outcome === 'moved' ? Math.abs(r.amount_cents) / 100 : 0,
    state: r.outcome === 'moved' ? 'Sent' : 'In progress',
    confirmedBy: confirmedBy(r),
    isConfirmed: r.verified ? 1 : 0,
    one: 1,
    signedWith: r.signed_with || '-',
    family: r.family || 'Not needed',
    reference: r.reference || '-',
    auditHash: r.audit_hash || '-',
  }));
  const stopRows = stops(rows).map((r) => ({
    id: r.row_id,
    date: iso(r.time),
    what: r.type,
    stoppedBy: r.stopped_by || 'Fixed rules',
    why: r.why,
    asked: Math.abs(r.amount_cents) / 100,
    paypalCalls: 0,
    one: 1,
  }));
  const rules = stops(rows).flatMap((r) =>
    (r.codes?.length ? r.codes : ['RULES']).map((code, i) => ({ id: `${r.row_id}-${i}`, stopId: r.row_id, code, rule: ruleName(code), one: 1 })),
  );
  const dates = [...payments, ...stopRows].map((r) => r.date.slice(0, 10)).sort();
  const today = new Date().toISOString().slice(0, 10);

  // Money shows as money ($50.00), in the wallet's one currency.
  const formats = createFormats({
    overrides: { currencyFormat: { formatOptions: { format: new Intl.NumberFormat('en-US', { style: 'currency', currency }) } } },
  });

  return {
    formats,
    description:
      `Star is a voice payment agent for an older customer, Mrs. Lee, and her family. Payments are money she sent to family through ` +
      `PayPal Payouts after signing the exact card on her own device, in ${currency}. A payment counts as sent only once PayPal ` +
      `confirmed it (a signature-checked webhook or a status check). Stops are requests Star refused before any money moved; ` +
      `every stop made 0 money-moving PayPal calls. Rules lists the checks behind each stop.`,
    sources: [
      {
        id: 'payments',
        name: 'Payments',
        description: 'Money she sent to family, one row per card she signed.',
        data: payments,
        fields: fields([
          ['id', 'Payment ID', 'textFormat', true],
          ['date', 'Date', 'dateTimeFormat'],
          ['to', 'To', 'textFormat'],
          ['amount', 'Amount', 'currencyFormat'],
          ['sent', 'Sent amount', 'currencyFormat'],
          ['state', 'State', 'textFormat'],
          ['confirmedBy', 'Confirmed by', 'textFormat'],
          ['isConfirmed', 'Confirmed by PayPal', 'integerFormat'],
          ['one', 'Payments', 'integerFormat'],
          ['signedWith', 'Signed with', 'textFormat'],
          ['family', 'Family approval', 'textFormat'],
          ['reference', 'PayPal reference', 'textFormat'],
          ['auditHash', 'Audit record', 'textFormat'],
        ]),
      },
      {
        id: 'stops',
        name: 'Stops',
        description: 'Requests Star stopped before any money moved. Each one made 0 money-moving PayPal calls.',
        data: stopRows,
        fields: fields([
          ['id', 'Stop ID', 'textFormat', true],
          ['date', 'Date', 'dateTimeFormat'],
          ['what', 'What happened', 'textFormat'],
          ['stoppedBy', 'Stopped by', 'textFormat'],
          ['why', 'Why', 'textFormat'],
          ['asked', 'Amount asked', 'currencyFormat'],
          ['paypalCalls', 'Money-moving PayPal calls', 'integerFormat'],
          ['one', 'Stops', 'integerFormat'],
        ]),
      },
      {
        id: 'rules',
        name: 'Rules',
        description: 'The checks behind each stop; one stop can have several.',
        data: rules,
        fields: fields([
          ['id', 'Rule hit ID', 'textFormat', true],
          ['stopId', 'Stop ID', 'textFormat', true],
          ['code', 'Rule code', 'textFormat'],
          ['rule', 'Rule', 'textFormat'],
          ['one', 'Requests stopped', 'integerFormat'],
        ]),
      },
    ],
    relationships: [
      { id: 'rule-stop', source: { tableId: 'rules', fieldId: 'stopId' }, target: { tableId: 'stops', fieldId: 'id' }, type: 'many-to-one' },
      { id: 'payments-date-calendar', source: { tableId: 'payments', fieldId: 'date' }, target: { calendarId: CALENDAR }, truncate: 'day' },
      { id: 'stops-date-calendar', source: { tableId: 'stops', fieldId: 'date' }, target: { calendarId: CALENDAR }, truncate: 'day' },
    ],
    calendars: [
      {
        id: CALENDAR,
        label: 'Calendar',
        range: { from: { type: 'date', value: dates[0] ?? today }, to: { type: 'date', value: dates.at(-1) ?? today } },
        fragments: ['year', 'month', 'week', 'day', 'dayOfWeek'],
      },
    ],
    expressions: [
      {
        id: 'confirmedShare',
        name: 'Confirmed by PayPal (share)',
        description: 'Share of her payments that PayPal has confirmed.',
        format: 'percentageFormat',
        isMeasure: true,
        expression: { operator: 'divide', inputs: [{ id: 'payments.isConfirmed', aggregation: 'sum' }, { id: 'payments.one', aggregation: 'sum' }] },
      },
    ],
  };
}
