// A fictional sample month, shown only when the viewer asks for it and always labelled SAMPLE on screen. It has the
// shape of the real activity rows, so the same report reads it. Mrs. Lee, her family and every amount are invented.
import type { ActivityRow } from './data';

const DAY = 86400;
const PEOPLE = [
  ['Anna Lee', 'a***@family.example'],
  ['David Lee', 'd***@family.example'],
  ['Ben Lee', 'b***@family.example'],
] as const;

// [days ago, person index, dollars, confirmed by webhook?]
const PAID: [number, number, number, boolean][] = [
  [29, 0, 50, true], [27, 1, 20, true], [24, 2, 30, false], [22, 0, 50, true], [19, 1, 25, true],
  [16, 0, 120, true], [14, 2, 30, true], [12, 1, 20, false], [9, 0, 50, true], [7, 2, 45, true],
  [5, 1, 20, true], [3, 0, 60, true], [1, 2, 30, true],
];

// [days ago, what, stopped by, why, rule codes, dollars asked]
const STOPPED: [number, string, string, string, string[], number][] = [
  [26, 'Stopped', 'Scam signs in what was said', 'Real police, government and bank staff will never ask you to move money into a so-called "safe account".', ['SCAM_PRE_SCREEN'], 0],
  [21, 'Stopped', 'The amount differs from her words', 'The model drafted $500.00 but she said $50. The draft was frozen and never shown to sign.', ['R50_AMOUNT_NOT_SAID'], 500],
  [17, 'Stopped', 'Scam signs in what was said', 'Someone calling about a virus on your computer and asking for money is a well-known tech support scam.', ['SCAM_PRE_SCREEN'], 0],
  [13, 'Family said no', 'Anna, on her own device', 'A first payment of $400.00 to someone new needed Anna, and she said no.', ['R15_NEW_PAYEE_RAMP', 'GUARDIAN_DENIED'], 400],
  [8, 'Stopped', 'Fixed rules', 'Gift cards asked for by a caller are a scam.', ['R94_GIFT_CARD'], 300],
  [4, 'Stopped', 'Scam signs in what was said', 'A call saying your grandchild is in trouble and needs money right now is a common scam.', ['SCAM_PRE_SCREEN'], 900],
];

export function sampleRows(now = Date.now() / 1000): ActivityRow[] {
  const base = Math.floor(now / DAY) * DAY + 15 * 3600;
  const pays: ActivityRow[] = PAID.map(([ago, who, dollars, hook], i) => ({
    row_id: `sample_pay_${i}`, time: base - ago * DAY + i * 977, type: 'Money sent', kind: 'transfer', to: PEOPLE[who][0],
    amount_cents: -dollars * 100, status: 'SENT', verified: true, verified_by: hook ? 'webhook:SAMPLE' : 'get:/v1/payments/payouts',
    stopped_by: '', why: '', signed_with: i % 3 ? 'Browser key' : 'Passkey', family: dollars >= 100 ? 'Approved' : '',
    reference: `SAMPLE${(7310 + i * 37).toString(16).toUpperCase()}`, audit_hash: `sample${i.toString().padStart(10, '0')}`, outcome: 'moved',
  }));
  const stops: ActivityRow[] = STOPPED.map(([ago, what, by, why, codes, dollars], i) => ({
    row_id: `sample_stop_${i}`, time: base - ago * DAY + i * 613, type: what, kind: 'scam', to: '', amount_cents: dollars * 100,
    status: what === 'Family said no' ? 'DENIED' : 'STOPPED', verified: false, verified_by: '', stopped_by: by, why,
    signed_with: '', family: what === 'Family said no' ? 'Denied' : '', reference: '', audit_hash: '', outcome: 'stopped', codes,
  }));
  return [...pays, ...stops].sort((a, b) => b.time - a.time);
}
