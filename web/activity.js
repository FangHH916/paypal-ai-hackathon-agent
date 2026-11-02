// The Activity & audit grid, built on AG Grid Community only. Set filters, master-detail and row grouping are Enterprise features,
// so this uses column filters, an external filter for the chips, pinned rows and a side panel instead.
// The same module draws the guardian's view (role "guardian"): masked handles and pending approvals pinned at the top.
import {$, esc, store, money, ppBadge, statusInfo} from '/common.js';

const STATE_KEY = 'star-grid-columns-v2';
const STOP_STATES = new Set(['STOPPED', 'FROZEN', 'BLOCKED', 'DENIED']);
const NEEDS = new Set(['UNKNOWN', 'AWAITING_APPROVAL', 'AWAITING_PAYMENT', 'HELD', 'ANOMALY', 'UNCLAIMED', 'PENDING_APPROVAL']);
const CHIPS = [
  ['all', 'All', () => true],
  ['out', 'Money out', r => r.amount_cents < 0 && r.outcome !== 'stopped'],
  ['stopped', 'Stopped', r => r.outcome === 'stopped'],
  ['needs', 'Needs attention', r => NEEDS.has(r.status)],
  ['family', 'Family decisions', r => !!r.family],
];

let loading = null;
function loadLibrary(){
  if(window.agGrid) return Promise.resolve(window.agGrid);
  loading ||= new Promise((ok, no) => {          // 2 MB: only when a grid actually opens. Same origin, so the script policy doesn't change.
    const s = document.createElement('script');
    s.src = '/vendor/ag-grid/ag-grid-community.min.js';
    s.onload = () => ok(window.agGrid); s.onerror = () => no(new Error('The grid could not be loaded.'));
    document.head.appendChild(s);
  });
  return loading;
}

function theme(agGrid, care){
  // App tokens, so it follows the light and dark palettes: a ledger page, rules and no box. Sized from the root, so browser font size is obeyed.
  const rem = parseFloat(getComputedStyle(document.documentElement).fontSize) || 16;
  return agGrid.themeQuartz.withParams({
    backgroundColor:'var(--bg)', foregroundColor:'var(--ink)', headerBackgroundColor:'var(--bg)', headerTextColor:'var(--ink)',
    borderColor:'var(--line)', accentColor:'var(--ink)', oddRowBackgroundColor:'var(--bg)', chromeBackgroundColor:'var(--bg)',
    rowHoverColor:'var(--surface-2)', selectedRowBackgroundColor:'var(--surface-2)',
    inputBackgroundColor:'var(--surface)', inputTextColor:'var(--ink)', inputBorder:{color:'var(--ink)', width:1},
    wrapperBorder:false, wrapperBorderRadius:0, borderRadius:0, columnBorder:false, headerColumnBorder:false,
    fontFamily:'inherit', fontSize: Math.round(rem * (care ? 1.2 : 1)), headerFontSize: Math.round(rem * (care ? 1.1 : .95)),
    rowHeight: Math.round(rem * (care ? 4.5 : 3.25)), headerHeight: Math.round(rem * (care ? 3.6 : 3)),
  });
}

const dateMatches = (filterDate, cell) => {
  if(!cell) return -1;
  const day = new Date(cell.getFullYear(), cell.getMonth(), cell.getDate());
  return day < filterDate ? -1 : day > filterDate ? 1 : 0;
};

export async function mountGrid(host, opts){
  const {role = 'owner', care = false, currency = 'USD', onRow = () => {}, getMode = () => 'SIMULATED'} = opts;
  const agGrid = await loadLibrary();
  const nonce = $('meta[name="style-nonce"]')?.content;
  host.innerHTML = `
    <div class="grid-tools">
      <div class="chips" role="group" aria-label="Show">${CHIPS.map(([id, label], i) =>
        `<button type="button" class="chip" data-chip="${id}" aria-pressed="${i === 0}">${label}</button>`).join('')}</div>
      <label class="grid-search"><span class="sr">Search all activity</span><input type="search" placeholder="Search names, reasons, references"></label>
      <button type="button" class="btn secondary small grid-more" data-act="more" aria-pressed="false">All columns and filters</button>
      <button type="button" class="btn secondary small" data-act="csv">Export CSV</button>
    </div>
    <p class="muted small grid-count" role="status" aria-live="polite"></p>
    <div class="grid-host" role="region" aria-label="Activity table"></div>`;
  let chip = 'all', rows = [], api = null, showAll = false;
  const moneyCell = p => {
    if(p.value == null) return '';
    const m = money(Math.abs(p.value), p.data.currency || currency);
    if(p.data.pinnedTotal) return m;
    if(p.data.outcome === 'stopped') return p.value ? `${m} not sent` : '$0 moved';
    if(p.data.status === 'PENDING_APPROVAL') return `${m} waiting`;
    return (p.value < 0 ? '−' : '+') + m;
  };
  const cardCell = p => {
    const d = p.data, m = p.colDef.cardMoney({value:d.amount_cents, data:d});
    if(d.pinnedTotal) return `<div class="grid-card one"><b>${esc(d.to)}</b><b>${esc(m)}</b></div>`;
    return `<div class="grid-card"><div class="grid-card-top"><b>${esc(d.to || d.type)}</b><b>${esc(m)}</b></div>${ppBadge(d.status, getMode(), d.verified)}<small>${esc(d.type)}, ${esc(new Date(d.time * 1000).toLocaleString('en-US', {month:'short', day:'numeric', hour:'numeric', minute:'2-digit'}))}</small></div>`;
  };
  const columns = [
    // Phones: one card-like column, so the table never needs sideways scrolling. Keyboard and sorting work as usual.
    {colId:'card', headerName:'Activity', flex:1, minWidth:200, hide:true, sortable:false, floatingFilter:false, filter:false, autoHeight:true, wrapText:true, cardMoney:moneyCell, cellRenderer:cardCell,
     valueGetter:p => `${p.data.to || ''} ${p.data.type || ''} ${p.data.pinnedTotal ? '' : statusInfo(p.data.status, p.data.verified, getMode()).word}`},
    {field:'time', headerName:'Time', colId:'time', sort:'desc', minWidth:230, flex:1, filter:'agDateColumnFilter', filterParams:{comparator:dateMatches},
     valueGetter:p => p.data.time ? new Date(p.data.time * 1000) : null,
     valueFormatter:p => p.value ? p.value.toLocaleString('en-US', {month:'short', day:'numeric', hour:'numeric', minute:'2-digit'}) : ''},
    {field:'type', headerName:'Type', minWidth:140, flex:1, filter:'agTextColumnFilter'},
    {field:'to', headerName:'To / from', minWidth:190, flex:1.3, filter:'agTextColumnFilter', wrapText:true, autoHeight:true, valueFormatter:p => p.value || 'No one: stopped before a draft',
     tooltipValueGetter:p => p.data.handle || ''},
    {field:'amount_cents', headerName:'Amount', type:'rightAligned', minWidth:210, flex:1, filter:'agNumberColumnFilter',
     filterValueGetter:p => Math.abs(p.data.amount_cents || 0) / 100, valueFormatter:moneyCell},
    // Status and its reason read as one sentence, so the story of a stop needs no sideways scrolling.
    {field:'status', colId:'status', headerName:'Status and why', minWidth:300, flex:3, filter:'agTextColumnFilter', enableCellChangeFlash:true, autoHeight:true,
     valueGetter:p => p.data.pinnedTotal ? '' : `${p.data.status}|${p.data.verified ? 1 : 0}|${p.data.why || ''}`,
     filterValueGetter:p => p.data.pinnedTotal ? '' : `${statusInfo(p.data.status, p.data.verified, getMode()).word} ${p.data.why || ''}`,
     valueFormatter:p => p.data.pinnedTotal ? '' : `${statusInfo(p.data.status, p.data.verified, getMode()).word} (${getMode()})`,
     cellRenderer:p => p.data.pinnedTotal ? '' : `${ppBadge(p.data.status, getMode(), p.data.verified)}${p.data.why ? `<span class="grid-why">${p.data.stopped_by ? `${esc(p.data.stopped_by)}: ` : ''}${esc(p.data.why)}</span>` : ''}`},
    {field:'stopped_by', headerName:'Stopped by', minWidth:220, flex:1.2, filter:'agTextColumnFilter'},
    {field:'why', headerName:'Why', minWidth:260, flex:2, filter:'agTextColumnFilter', tooltipField:'why'},
    {field:'reference', headerName:'PayPal reference', minWidth:170, filter:'agTextColumnFilter'},
    {field:'signed_with', headerName:'Signed with', minWidth:130, filter:'agTextColumnFilter'},
    {field:'family', headerName:'Family', minWidth:110, filter:'agTextColumnFilter', valueFormatter:p => p.value || (p.data.pinnedTotal ? '' : '—')},
    {field:'audit_hash', headerName:'Audit hash', minWidth:170, filter:'agTextColumnFilter',
     cellRenderer:p => p.value ? `<code class="hash">${esc(p.value)}</code> <span class="tick" title="Chain verified">✓ verified</span>` : ''},
  ];
  const isNarrow = () => (host.clientWidth || window.innerWidth) < 620;
  const sets = () => {
    if(isNarrow()) return ['card'];
    if(care) return ['time', 'to', 'amount_cents', 'status'];
    if(showAll) return columns.map(c => c.colId || c.field).filter(c => c !== 'card');
    return ['time', 'to', 'amount_cents', 'status'];
  };
  const applyColumns = () => {
    if(!api) return;
    const show = sets();
    api.setColumnsVisible(columns.map(c => c.colId || c.field), false);
    api.setColumnsVisible(show, true);
    const filters = showAll && !isNarrow() && !care;
    $('.grid-host', host).classList.toggle('filters-on', filters);
    api.setGridOption('floatingFiltersHeight', filters ? 52 : 0);
    const more = $('[data-act="more"]', host); more.hidden = isNarrow() || care;
    totals();          // fresh pinned rows take the height for this width; every other row is sized by its own content
  };
  const totals = () => {
    let sent = 0, back = 0, stops = 0;
    api.forEachNodeAfterFilter(n => {
      const r = n.data;
      if(r.outcome === 'stopped') stops += 1;     // counted, not added up: the same bill stopped three times isn't three bills
      else if(r.outcome === 'moved' && r.kind === 'refund') back += Math.abs(r.amount_cents);
      else if(r.outcome === 'moved' && r.amount_cents < 0) sent += Math.abs(r.amount_cents);
    });
    const line = (id, to, cents) => ({row_id:`total_${id}`, pinnedTotal:true, to, type:'This demo', amount_cents:cents, currency});
    const count = api.getDisplayedRowCount() + (api.getPinnedTopRowCount?.() || 0);
    api.setGridOption('pinnedBottomRowData', count ? [line('sent', 'Total sent', sent), line('back', 'Total refunded', back), line('stops', `Stops in this demo: ${stops}`, null)] : []);
    $('.grid-count', host).textContent = `${count} row${count === 1 ? '' : 's'} shown. Totals are this demo's numbers, from the rows shown.`;
  };
  api = agGrid.createGrid($('.grid-host', host), {
    theme: theme(agGrid, care), styleNonce: nonce, domLayout:'autoHeight', includeHiddenColumnsInQuickFilter:true,
    columnDefs: columns, rowData: [],
    defaultColDef:{sortable:true, resizable:true, floatingFilter:!care, filter:true, minWidth:90, wrapText:false},
    getRowId: p => p.data.row_id,
    getRowHeight: p => !isNarrow() || !p.data.pinnedTotal ? undefined : (care ? 56 : 48),
    getRowClass: p => p.data.pinnedTotal ? 'row-total' : p.data.outcome === 'stopped' ? 'row-stopped' : NEEDS.has(p.data.status) ? 'row-attention' : '',
    isExternalFilterPresent: () => chip !== 'all',
    doesExternalFilterPass: n => CHIPS.find(c => c[0] === chip)[2](n.data),
    onFilterChanged: totals, onRowDataUpdated: totals,
    multiSortKey:'ctrl', alwaysMultiSort:false, tooltipShowDelay:300, animateRows:false, suppressCellFocus:false,
    overlayNoRowsTemplate:'<span class="muted">Nothing here yet. Payments and stops appear as they happen.</span>',
    onRowClicked: e => { if(!e.data.pinnedTotal) onRow(e.data); },
    onCellKeyDown: e => { if(e.event?.key === 'Enter' && e.data && !e.data.pinnedTotal) onRow(e.data); },
    onGridSizeChanged: applyColumns,
    onColumnResized: e => { if(e.finished && e.source === 'uiColumnResized') saveColumns(); },
    onSortChanged: () => saveColumns(),
    onColumnMoved: e => { if(e.finished) saveColumns(); },
  });
  function saveColumns(){ if(role === 'owner' && !care) store.set(STATE_KEY, api.getColumnState().map(({colId, width, sort, sortIndex}) => ({colId, width, sort, sortIndex}))); }
  const saved = !care && role === 'owner' ? store.get(STATE_KEY, null) : null;
  if(Array.isArray(saved)) api.applyColumnState({state: saved, applyOrder:true});
  applyColumns();

  host.querySelectorAll('.chip').forEach(b => b.onclick = () => {
    chip = b.dataset.chip;
    host.querySelectorAll('.chip').forEach(x => x.setAttribute('aria-pressed', String(x === b)));
    api.onFilterChanged();
  });
  $('.grid-search input', host).oninput = e => api.setGridOption('quickFilterText', e.target.value);
  $('[data-act="more"]', host).onclick = e => { showAll = !showAll; e.currentTarget.setAttribute('aria-pressed', String(showAll)); applyColumns(); };
  $('[data-act="csv"]', host).onclick = () => api.exportDataAsCsv({
    fileName:'star-activity.csv', columnKeys:columns.map(c => c.colId || c.field), skipPinnedBottom:false,
    processCellCallback:p => {
      if(p.column.getColId() === 'time') return p.value ? p.value.toISOString() : '';
      if(p.column.getColId() === 'amount_cents') return p.value == null ? '' : (p.value / 100).toFixed(2);
      return p.formatValue ? p.formatValue(p.value) : p.value;
    },
  });
  return {
    // Live updates: only what changed, so the webhook's arrival flashes the Status cell.
    update(next){
      const before = new Map(rows.map(r => [r.row_id, r]));
      const ids = new Set(next.map(r => r.row_id));
      const add = next.filter(r => !before.has(r.row_id));
      const update = next.filter(r => before.has(r.row_id) && JSON.stringify(before.get(r.row_id)) !== JSON.stringify(r));
      const remove = rows.filter(r => !ids.has(r.row_id));
      rows = next;
      if(add.length || update.length || remove.length) api.applyTransaction({add, update, remove});
      else totals();
    },
    pinPending(list){
      api.setGridOption('pinnedTopRowData', list);
      api.setGridOption('suppressNoRowsOverlay', list.length > 0);
      if(list.length) api.hideOverlay?.();
      totals();
    },
    destroy(){ api.destroy(); },
    api,
  };
}
export const isStop = status => STOP_STATES.has(status);
