// The family guardian's own page: her own device, her own key. A decision here is a signature over the exact card,
// which the payment gateway re-verifies itself. She can't start a payment, and can't unlock what the rules stopped.
import {$, esc, api, store, icon, clock, modeTag, ppBadge, deviceKeys, mockFingerprint} from '/common.js';
import {mountGrid} from '/activity.js';

const keys = deviceKeys('star-guardian');
const TOKEN = 'guardianToken';
let token = store.get(TOKEN, null), grid = null, timer = null, mode = 'SIMULATED', lastPending = '', feed = null;

function toast(msg){ const t = $('#toast'); t.textContent = msg; t.classList.add('show'); clearTimeout(toast.t); toast.t = setTimeout(() => t.classList.remove('show'), 3200); }
async function health(){
  try{
    const h = await api('/api/health'); mode = h.paypal?.label || mode;
    $('#g-mode').innerHTML = modeTag(mode);
  }catch{}
}

// ---------------------------------------------------------------- Pairing
function showPair(invite, note = ''){
  $('#main').innerHTML = `<section class="card g-pair"><h1>${invite ? 'Become the family guardian' : 'This device is not paired'}</h1>
    ${note ? `<p class="alert-text" role="alert">${esc(note)}</p>` : ''}
    ${invite ? `<p>Pairing makes a private key on this device. From then on you see alerts, and risky payments wait for your signature. You can't start a payment, and you can't unlock one the rules have stopped.</p>
      <button class="btn primary big" id="g-pair" type="button">${icon('shield')}Pair this device</button>
      <p class="muted small">The invite works once and expires after 10 minutes.</p>`
    : `<p>Ask Mrs. Lee to open <b>Family</b> in Star and tap <b>Invite Anna</b>. She signs the invite on her own device, and you open the link it gives her.</p><p><a class="btn secondary" href="/">Open Star</a></p>`}
  </section>`;
  $('#g-pair') && ($('#g-pair').onclick = async () => {
    const b = $('#g-pair'); b.disabled = true;
    try{
      const r = await api('/api/guardian/pair', {invite_token: invite, public_key: await keys.ensure()});
      token = r.token; store.set(TOKEN, token);
      history.replaceState(null, '', '/guardian');
      toast(`Paired. You are now ${r.customer}'s family guardian.`);
      start();
    }catch(e){ showPair(invite, e.message); }
  });
  $('#g-pair')?.focus();
}

// ---------------------------------------------------------------- The dashboard
function shell(){
  $('#main').innerHTML = `
    <section class="g-head"><div><p class="eyebrow">Family guardian</p><h1 id="g-title">Looking after Mrs. Lee</h1><p class="muted" id="g-sub"></p></div>
      <div id="g-freeze"></div></section>
    <section aria-labelledby="g-pending-h"><h2 id="g-pending-h">Waiting for you</h2><div id="g-pending" aria-live="polite"></div></section>
    <section class="card pad" aria-labelledby="g-alerts-h"><h2 id="g-alerts-h">Alerts</h2><ul class="alert-list" id="g-alerts"></ul></section>
    <section class="card pad" aria-labelledby="g-grid-h"><h2 id="g-grid-h">Activity</h2><p class="muted small">Same records as Mrs. Lee's, with email addresses masked.</p><div id="g-grid"></div></section>`;
}
function renderPending(list){
  const el = $('#g-pending');
  if(!list.length){ el.innerHTML = '<p class="muted">Nothing is waiting for you.</p>'; return; }
  el.innerHTML = list.map(p => `<article class="card pad g-card">
      <div class="g-amount">${esc(p.amount)}</div>
      <dl class="facts"><dt>To</dt><dd>${esc(p.payee_name)}${p.payee_handle ? ` <small>${esc(p.payee_handle)}</small>` : ''}</dd>
        <dt>She said</dt><dd><q>${esc(p.you_said)}</q></dd>
        ${(p.items || []).length ? `<dt>Buying</dt><dd>${p.items.map(i => esc(i.name)).join(', ')}</dd>` : ''}
        <dt>Star told her</dt><dd><ul>${p.reasons.map(r => `<li>${esc(r)}</li>`).join('')}</ul></dd>
        <dt>Exact card</dt><dd><code>${esc(p.digest_short)}…</code> <small>Your signature covers this card only. If it changes, your approval no longer counts.</small></dd></dl>
      <div class="row-actions"><button class="btn danger" data-draft="${esc(p.draft_id)}" data-decide="deny" type="button">Deny</button>
        <button class="btn primary" data-draft="${esc(p.draft_id)}" data-decide="approve" type="button">${icon('fingerprint')}Approve</button></div>
      <p class="muted small">Approving doesn't send anything. Mrs. Lee still has to sign it herself.</p>
    </article>`).join('');
  el.querySelectorAll('[data-decide]').forEach(b => b.onclick = () => decide(b.dataset.draft, b.dataset.decide, list.find(p => p.draft_id === b.dataset.draft)));
}
async function signWithSensor(what){
  const dlg = $('#g-sign');
  dlg.innerHTML = '<div class="cbody"><h2>Confirm on this device</h2></div>'; dlg.showModal();
  try{ await mockFingerprint(dlg, what, 'The payment gateway checks that signature itself.'); }
  finally{ dlg.close(); }
}
async function decide(draftId, decision, row){
  try{
    await signWithSensor(`${decision === 'approve' ? 'Approve' : 'Deny'} ${row.amount} to ${row.payee_name}`);
    const {payload} = await api('/api/guardian/challenge', {token, draft_id: draftId, decision});
    const r = await api('/api/guardian/decide', {token, payload, signature: await keys.sign(payload)});
    toast(r.reply); lastPending = ''; await poll();
  }catch(e){ if(e.name !== 'NotAllowedError') toast(e.message); else toast('Cancelled. Nothing was decided.'); }
}
function renderFreeze(f){
  $('#g-freeze').innerHTML = f.on
    ? `<p class="status-line bad">${icon('freeze')}Payments are frozen by ${esc(f.by)}</p><button class="btn secondary" id="g-lift" type="button">Sign to lift the freeze</button>`
    : `<button class="btn danger" id="g-freeze-on" type="button">${icon('freeze')}Freeze all payments now</button><p class="muted small">Instant. Lifting takes both of you.</p>`;
  $('#g-freeze-on') && ($('#g-freeze-on').onclick = async () => {
    try{ await api('/api/guardian/freeze', {token}); toast('Payments frozen.'); await poll(true); }catch(e){ toast(e.message); }
  });
  $('#g-lift') && ($('#g-lift').onclick = async () => {
    try{
      await signWithSensor('Lift the freeze on payments');
      const {payload} = await api('/api/guardian/lift/challenge', {token});
      const r = await api('/api/guardian/lift', {token, payload, signature: await keys.sign(payload)});
      toast(r.frozen?.on ? 'Signed. Mrs. Lee has to sign too before payments start again.' : 'The freeze is lifted.'); await poll(true);
    }catch(e){ if(e.name !== 'NotAllowedError') toast(e.message); }
  });
}
function showDetail(row){
  const d = $('#detail');
  d.innerHTML = `<div class="side-body"><header><h2 id="d-title">${esc(row.type)}: ${esc(row.to)}</h2><button class="btn secondary small" id="d-close" type="button">Close</button></header>
    <p>${ppBadge(row.status, mode, row.verified)}</p>
    <dl class="facts">${row.handle ? `<dt>Account</dt><dd>${esc(row.handle)}</dd>` : ''}
      ${row.why ? `<dt>Why</dt><dd>${esc(row.why)}</dd>` : ''}${row.stopped_by ? `<dt>Stopped by</dt><dd>${esc(row.stopped_by)}</dd>` : ''}
      ${row.reference ? `<dt>PayPal reference</dt><dd><code>${esc(row.reference)}</code></dd>` : ''}
      ${row.verified_by ? `<dt>Verified by</dt><dd><code>${esc(row.verified_by)}</code></dd>` : ''}
      ${row.digest ? `<dt>Exact card</dt><dd><code>${esc(row.digest.slice(0, 24))}…</code></dd>` : ''}</dl></div>`;
  d.showModal(); $('#d-close').onclick = () => d.close();
}
async function poll(force = false){
  try{ feed = await api('/api/guardian/feed', {token}); }
  catch(e){
    if(e.data?.code === 'GUARDIAN_UNKNOWN'){ clearInterval(timer); store.set(TOKEN, null); token = null; showPair(null, 'This device is no longer paired. The demo may have been reset.'); }
    return;
  }
  if(feed.mode_label){ mode = feed.mode_label; $('#g-mode').innerHTML = modeTag(mode); }
  $('#g-sub').textContent = `You are ${feed.guardian.name}, ${feed.customer}'s ${feed.guardian.relation}. Key ${feed.guardian.key_id}.`;
  const pend = JSON.stringify(feed.pending);
  if(pend !== lastPending || force){ lastPending = pend; renderPending(feed.pending); }
  renderFreezeOnce(feed.frozen);
  $('#g-alerts').innerHTML = feed.alerts.length ? feed.alerts.slice(0, 12).map(a => `<li class="alert-item level-${esc(a.level)}"><span class="when">${esc(clock(a.at))}</span><span>${esc(a.text)}</span></li>`).join('') : '<li class="muted">No alerts yet.</li>';
  grid?.update(feed.activity);
  grid?.pinPending(feed.pending.map(p => ({row_id:`pending_${p.draft_id}`, time:p.requested_at, type:'Needs you', kind:p.kind, to:p.payee_name, handle:p.payee_handle,
    amount_cents:-p.amount_cents, currency:p.currency, status:'PENDING_APPROVAL', verified:false, why:p.reasons.join(' '), family:'Waiting', outcome:'open', digest:p.digest})));
}
let lastFreeze = '';
function renderFreezeOnce(f){ const s = JSON.stringify(f); if(s !== lastFreeze){ lastFreeze = s; renderFreeze(f); } }

async function start(){
  shell(); lastPending = ''; lastFreeze = '';
  await poll(true);
  if(!token) return;
  grid = await mountGrid($('#g-grid'), {role:'guardian', getMode:() => mode, onRow:showDetail, care:false}).catch(e => { $('#g-grid').textContent = e.message; return null; });
  if(feed) { grid?.update(feed.activity); }
  clearInterval(timer); timer = setInterval(() => poll(), 3000);
}
async function init(){
  await health();
  const invite = new URLSearchParams(location.search).get('invite');
  if(invite) return showPair(invite);
  if(token) return start();
  showPair(null);
}
init();
