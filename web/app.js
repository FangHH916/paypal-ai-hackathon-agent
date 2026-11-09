// Star: the family payment agent. The customer's side of the app.
// The agent, the kernel, the gateway and PayPal are all on the server. This page shows their state and holds the signing key.
// Success is only ever shown from verified state read back from the server, never from a redirect.
// No inline script or style anywhere, so the server's strict Content-Security-Policy applies.
import {$, esc, store, api, download, money, clock, icon, ppBadge, modeTag, statusInfo, sourceWords, b64, deviceKeys, mockFingerprint, loadFailed} from '/common.js';
import {mountGrid} from '/activity.js';
import {pageIn, stopFx, verifiedFx, caughtFx, cardIn, sealSvg, particles} from '/motion.js';

// Online demo (server PUBLIC_DEMO=1): this browser's own demo customer, so testers never see each other's payments,
// resets or passkeys. Locally the server doesn't ask for it and everyone is the same Mrs. Lee.
const VISITOR = (() => {
  let v = store.get('vid', null);
  if(!/^[a-f0-9]{16}$/.test(v || '')){ v = [...crypto.getRandomValues(new Uint8Array(8))].map(b => b.toString(16).padStart(2, '0')).join(''); store.set('vid', v); }
  return v;
})();
const visitorMode = () => !!state.health.public_demo;

const state = {
  sid: store.get('sid', null), page: 'home', data: null, health: {},
  care: store.get('care', false), large: store.get('large', false), readAloud: store.get('readAloud', null),
  speechRate: store.get('speechRate', null),
  messages: [], check: null, card: null, verify: null, coolTimer: null, guardTimer: null, busy: false,
  calls: {calls: [], money_moving_this_task: 0, money_moving_total: 0}, payments: [], grid: null, gridTimer: null, invite: null,
};

const NAV = [['home', 'Home', 'home'], ['star', 'Talk to Star', 'spark'], ['bills', 'Bills', 'bolt'], ['family', 'Family', 'family'], ['shop', 'Shop', 'cart'],
  ['activity', 'Activity & audit', 'audit'], ['dashboard', 'Dashboard', 'chart'], ['settings', 'Settings', 'settings'], ['lab', 'Security lab', 'lab']];
const NAV_FIRST = new Set(['home', 'star', 'family', 'activity']);     // a phone shows these and More
// One job, done well: sending money to family by voice. Bills and Shop still work (and are tested) but stay off the screen;
// they can be reached at #/bills and #/shop. The Security lab is at #/lab.
const OFF_SCREEN = new Set(['bills', 'shop', 'lab']);
const CARE_HIDES = new Set(['star', 'lab', 'dashboard']);
const KIND = {user:'You', thought:'Thought', action:'Action', observation:'Observe', rule:'Rule', guard:'Guard', judge:'Judge',
  state:'State', sign:'Sign', exec:'Execute', plan:'Plan', asr:'Speech', fallback:'Fallback', handoff:'Handoff', evidence:'Evidence', ui:'UI',
  verify:'Verifier', gateway:'Gateway', model:'Model', api:'PayPal call', paypal:'PayPal call'};

// ---------------------------------------------------------------- Basics
const mode = () => state.data?.paypal?.label || state.health.paypal?.label || 'SIMULATED';
const isSim = () => (state.data?.paypal?.mode || state.health.paypal?.mode || 'simulator') === 'simulator';
const misconfigured = () => { const p = state.data?.paypal || state.health.paypal; return p && p.configured === false ? (p.error || 'PayPal is not configured.') : ''; };
const cash = cents => money(cents, state.data?.currency || 'USD');
function toast(msg){ const t = $('#toast'); t.textContent = msg; t.classList.add('show'); clearTimeout(toast.t); toast.t = setTimeout(() => t.classList.remove('show'), 3200); }
const dueDate = iso => { const d = new Date(`${iso}T12:00:00`); return isNaN(d) ? String(iso) : d.toLocaleDateString('en-US', {month:'short', day:'numeric', year:'numeric'}); };
const today = () => new Date().toLocaleDateString('en-US', {weekday:'long', month:'long', day:'numeric', year:'numeric'});
// One clock for the greeting and the late-night rule: this browser's zone. Only its name is sent; the server checks it
// and works the hour out itself.
const TZ = (() => { try{ return Intl.DateTimeFormat().resolvedOptions().timeZone || ''; }catch{ return ''; } })();
const hourHere = () => { try{ return Number(new Intl.DateTimeFormat('en-US', {hour: 'numeric', hourCycle: 'h23', ...(TZ ? {timeZone: TZ} : {})}).format(new Date())) % 24; }catch{ return new Date().getHours(); } };
const greeting = () => { const h = hourHere(); return h < 12 ? 'Good morning' : h < 18 ? 'Good afternoon' : 'Good evening'; };
const who = () => state.data?.user || 'Mrs. Lee';
const readAloudOn = () => state.readAloud === null ? state.care : state.readAloud;
const SPEECH_RATES = [0.65, 0.82, 1, 1.2, 1.5];
const speechRate = () => SPEECH_RATES.includes(state.speechRate) ? state.speechRate : (state.care ? 0.82 : 0.95);
const live = () => state.health.llm && !String(state.health.llm).startsWith('offline');
const serverAsr = () => !!state.health.asr && state.health.asr !== 'browser-fallback';
const protLevel = () => state.data?.protection?.level || 'enhanced';
const protPending = () => protLevel() === 'enhanced' && !!state.data?.protection?.pending;
const protLabel = () => protLevel() === 'enhanced'
  ? (protPending() ? '<span>Enhanced · Standard in <span class="prot-count"></span></span>' : 'Enhanced protection') : 'Standard protection';
const hotline = () => state.data?.hotline || {name:'the National Elder Fraud Hotline', number:'833-372-8311', spoken:'833-FRAUD-11'};
const guardianName = () => (state.data?.guardian?.name || 'Anna Lee').split(' ')[0];
const guardianPaired = () => state.data?.guardian?.status === 'paired';
const assistantPage = () => state.care ? 'home' : 'star';

function saveChat(){
  try{ sessionStorage.setItem('chat', JSON.stringify({sid: state.sid, messages: state.messages.filter(m => m.type !== 'typing').slice(-60), check: state.check})); }catch{}
}
function loadChat(){
  try{
    const c = JSON.parse(sessionStorage.getItem('chat') || 'null');
    if(c && c.sid === state.sid){ state.messages = c.messages || []; state.check = c.check || null; }
  }catch{}
}

// The server forgets sessions when it is redeployed: start a fresh one for this browser (its own demo customer)
async function newSession(){
  state.sid = null; store.set('sid', null);
  try{ const r = await api('/api/reset', visitorMode() ? {visitor: VISITOR} : {}); state.sid = r.session_id; store.set('sid', r.session_id); }catch{}
}
async function refresh(){
  try{
    const d = await api('/api/state' + (state.sid ? `?session_id=${encodeURIComponent(state.sid)}` : ''));
    state.data = d; state.sid = d.session_id; store.set('sid', d.session_id);
    const pill = $('#prot-pill'); if(pill){ pill.className = `pill prot ${protLevel()}${protPending() ? ' queued' : ''}`; pill.innerHTML = icon('shield') + protLabel(); }
    renderBand(); protCountdown();
  }catch(e){
    if(e.data?.code === 'SESSION_NOT_FOUND'){ await newSession(); return refresh(); }
    console.warn(e);
  }
}
async function loadCalls(){
  if(!state.sid) return;
  try{ state.calls = await api(`/api/paypal/calls?session_id=${encodeURIComponent(state.sid)}`); renderTicker(); }catch{}
}
async function loadPayments(){
  try{ state.payments = (await api(`/api/payments?session_id=${encodeURIComponent(state.sid)}`)).payments; state.paymentsFailed = false; }catch{ state.paymentsFailed = true; }
  return state.payments;
}
// On a phone the voice dock and the navigation are fixed to the bottom. Their real heights, at any font size, decide how far content must clear them.
function measureBars(){
  const narrow = matchMedia('(max-width:900px)').matches, root = document.documentElement.style;
  root.setProperty('--nav-h', `${narrow ? $('.sidebar')?.offsetHeight || 0 : 0}px`);
  root.setProperty('--dock-h', `${narrow ? $('.composer-wrap')?.offsetHeight || 0 : 0}px`);   // docked only on narrow screens
}
const barWatch = 'ResizeObserver' in window ? new ResizeObserver(measureBars) : null;
function watchBars(){
  barWatch?.disconnect();
  document.querySelectorAll('.sidebar,.composer-wrap').forEach(el => barWatch?.observe(el));
  measureBars();
}
window.addEventListener('resize', measureBars);

// ---------------------------------------------------------------- Read aloud (browser speech; slower in Care mode)
const VOICE_PREFS = ['Samantha','Google US English','Microsoft Aria','Microsoft Jenny','Karen','Moira','Tessa','Serena','Google UK English Female','Victoria'];
let voiceCache = null;
function pickVoice(){
  if(voiceCache) return voiceCache;
  const en = speechSynthesis.getVoices().filter(v => /^en[-_]/i.test(v.lang));
  voiceCache = VOICE_PREFS.map(n => en.find(v => v.name.startsWith(n))).find(Boolean) || en.find(v => /female/i.test(v.name)) || null;
  return voiceCache;
}
if('speechSynthesis' in window) speechSynthesis.onvoiceschanged = () => { voiceCache = null; };
// Spoken question -> spoken answer, in every mode. Typed questions follow the "Read every answer aloud" setting.
const speechQueue = [];
function syncSpeechButtons(){
  const current = speechQueue[0];
  document.querySelectorAll('.replay').forEach(b => {
    const m = state.messages[Number(b.dataset.i)];
    const active = current && m && (current.message === m || current.text === (m.text || ''));
    const label = active ? (current.paused ? 'Resume reading' : 'Pause reading') : 'Read this aloud';
    b.innerHTML = icon(active ? (current.paused ? 'play' : 'pause') : 'speaker');
    b.setAttribute('aria-label', label); b.title = label;
  });
}
function toggleMessageSpeech(message){
  if(!('speechSynthesis' in window)) return;
  const current = speechQueue[0];
  if(current && (current.message === message || current.text === message.text)){
    current.paused = !current.paused;
    if(current.paused) speechSynthesis.pause(); else speechSynthesis.resume();
    syncSpeechButtons();
  }else{
    state.queueSpeech = false;
    speak(message.text, true, message);
  }
}
function speak(text, force = false, message = state.messages.at(-1)){
  const want = force || readAloudOn() || state.voiceTurn;
  if(!want || !('speechSynthesis' in window) || !text) return;
  if(!state.queueSpeech) stopSpeaking();
  state.queueSpeech = false;
  const u = new SpeechSynthesisUtterance(text); const v = pickVoice();
  if(v){ u.voice = v; u.lang = v.lang; } else u.lang = 'en-US';
  u.rate = speechRate();
  const entry = {utterance:u, message:message?.role === 'bot' ? message : null, text, paused:false};
  speechQueue.push(entry);
  const finish = () => {
    const i = speechQueue.indexOf(entry);
    if(i < 0) return; // Ignore late callbacks from cancelled readings.
    speechQueue.splice(i, 1); syncSpeechButtons();
  };
  u.onend = finish; u.onerror = finish;
  u.onstart = () => { if(speechQueue.includes(entry)) syncSpeechButtons(); };
  u.onpause = () => { if(speechQueue.includes(entry)){ entry.paused = true; syncSpeechButtons(); } };
  u.onresume = () => { if(speechQueue.includes(entry)){ entry.paused = false; syncSpeechButtons(); } };
  speechSynthesis.speak(u);
  syncSpeechButtons();
}
function stopSpeaking(){
  speechQueue.length = 0;
  try{ if('speechSynthesis' in window){ speechSynthesis.cancel(); speechSynthesis.resume(); } }catch{}
  syncSpeechButtons();
}

// ---------------------------------------------------------------- Shell
function bandHtml(){
  const bad = misconfigured();
  if(bad) return `<div class="band band-bad" role="alert"><b>PayPal isn't set up.</b> ${esc(bad)} Nothing can be paid, and Star will not pretend otherwise.</div>`;
  if(isSim()) return `<div class="band band-sim">${modeTag('SIMULATED')}<span>No real money. <span class="band-more">A built-in simulator stands in for PayPal, following its documented API shapes.</span></span></div>`;
  return `<div class="band band-sandbox">${modeTag(mode())}<span>Real PayPal sandbox calls with test money. Not live.</span></div>`;
}
function renderBand(){ const b = $('#band'); if(b) b.innerHTML = bandHtml(); }
function render(){
  document.body.classList.toggle('care', state.care);
  document.body.classList.toggle('large', state.large && !state.care);
  const items = NAV.filter(([id]) => !OFF_SCREEN.has(id) && !(state.care && CARE_HIDES.has(id)));
  const navBtn = ([id, label, ic]) => `<button class="nav-btn" type="button" data-page="${id}" ${state.page === id ? 'aria-current="page"' : ''}>${icon(ic)}<span class="nav-text">${label}</span></button>`;
  const extra = items.filter(([id]) => !NAV_FIRST.has(id));
  const navHtml = `${items.filter(([id]) => NAV_FIRST.has(id)).map(navBtn).join('')}
    <button class="nav-btn nav-more" id="nav-more" type="button" aria-expanded="false" aria-controls="nav-extra" ${extra.some(([id]) => id === state.page) ? 'aria-current="page"' : ''}>${icon('settings')}<span class="nav-text">More</span></button>
    <div class="nav-extra" id="nav-extra">${extra.filter(([id]) => id !== 'lab').map(navBtn).join('')}
      ${extra.some(([id]) => id === 'lab') ? `<p class="nav-label">For judges</p>${extra.filter(([id]) => id === 'lab').map(navBtn).join('')}` : ''}</div>`;
  $('#app').innerHTML = `
  <div class="layout">
    <nav class="sidebar" aria-label="Main">
      <div class="brand side-brand"><img src="/logo.svg" alt="" width="34" height="34"><span>Star</span></div>
      <div class="nav">${navHtml}</div>
      <div class="side-bottom">
        <div class="trust-mini">${icon('shield')}<p>Star can only prepare payments. Your device signs every one.</p></div>
        <div class="profile"><span class="avatar" aria-hidden="true">ML</span><div>${esc(state.data?.full_name || 'Margaret Lee')}<small>Fictional demo customer</small></div></div>
      </div>
    </nav>
    <div class="workspace">
      <div id="band">${bandHtml()}</div>
      <header class="topbar">
        <div class="top-title"><span class="brand mobile-brand"><img src="/logo.svg" alt="" width="28" height="28"><span>Star</span></span>
          <span class="pill model-pill" title="The model behind Star"><span class="dot ${live() ? 'on' : ''}"></span>${esc(state.health.engine || (live() ? 'Live model' : 'Offline rules'))}</span></div>
        <div class="top-actions">
          <button class="pill prot ${protLevel()}${protPending() ? ' queued' : ''}" id="prot-pill" type="button" aria-label="Account protection: ${protLevel()}. Open settings">${icon('shield')}${protLabel()}</button>
          <button class="icon-btn" id="trace-toggle" type="button" aria-pressed="${$('#trace').classList.contains('open')}">${icon('trace')}<span class="label">Agent trace</span></button>
          <button class="care-toggle" id="care-toggle" type="button" aria-pressed="${state.care}">${icon('care')}<span>${state.care ? 'Standard<span class="care-more"> view</span>' : 'Care mode'}</span></button>
        </div>
      </header>
      <main id="main" tabindex="-1"></main>
      <footer class="page-foot">Star is a hackathon project with a fictional family. Designed for the PayPal sandbox${isSim() ? ', running here on a built-in simulator' : ''}; not affiliated with PayPal.</footer>
    </div>
  </div>`;
  $('#app').querySelectorAll('.nav-btn[data-page]').forEach(b => b.onclick = () => go(b.dataset.page));
  $('#nav-more').onclick = e => { e.stopPropagation(); setMore(!$('#nav-extra').classList.contains('open')); };
  $('#care-toggle').onclick = () => setCare(!state.care);
  $('#trace-toggle').onclick = () => toggleTrace();
  $('#prot-pill').onclick = () => go('settings');
  renderPage(); protCountdown();
}
function setMore(open){
  $('#nav-extra')?.classList.toggle('open', open);
  $('#nav-more')?.setAttribute('aria-expanded', String(open));
}
document.addEventListener('click', e => { if(!e.target.closest?.('#nav-extra')) setMore(false); });
document.addEventListener('keydown', e => { if(e.key === 'Escape') setMore(false); });
// Each page is a history entry, so a phone's back button walks back through the app instead of leaving it.
const pageFromHash = () => { const p = location.hash.replace(/^#\/?/, ''); return NAV.some(n => n[0] === p) ? p : null; };
function go(page, push = true){
  if(state.care && CARE_HIDES.has(page)) page = 'home';
  state.page = page; try{ sessionStorage.setItem('page', page); }catch{}
  if(push && pageFromHash() !== page) history.pushState({page}, '', `#/${page}`);
  render(); $('#main')?.focus({preventScroll:true}); window.scrollTo(0, 0); pageIn($('#main'));
}
window.addEventListener('popstate', () => {
  if($('#app').hidden || location.hash === '#main') return;
  if($('#detail').open) $('#detail').close();
  go(pageFromHash() || 'home', false);
});
function setCare(on, quiet = false){
  state.care = on; store.set('care', on);
  if(on && CARE_HIDES.has(state.page)) state.page = 'home';
  state.messages.forEach(m => { if(m.type === 'choices' && m.care) m.done = true; });   // the switch answers the offer
  if(on) state.messages = state.messages.filter(m => !m.careOffer && !(m.type === 'choices' && m.care));
  if(state.sid) api('/api/care', {session_id: state.sid, on}).catch(() => {});   // Star talks to match the screen
  render(); if(!quiet) toast(on ? 'Care mode is on.' : 'Standard view');
}
// When the customer arrives, Star asks once whether they'd like the easier version (answer by tapping, typing or voice)
async function offerCare(){
  if(state.care || state.messages.length) return;
  try{
    const r = await api('/api/care/offer', {session_id: state.sid});
    if(r.offer){ state.messages.push({role:'bot', text: r.offer, careOffer: true}, {type:'choices', options: r.options, care: true}); renderStage(); saveChat(); }
  }catch{}
}
function toggleTrace(force){
  const t = $('#trace'); const open = force ?? !t.classList.contains('open');
  t.classList.toggle('open', open); t.setAttribute('aria-hidden', String(!open));
  $('#trace-toggle')?.setAttribute('aria-pressed', String(open));
  if(open) $('#trace-close').focus();
}
$('#trace-close').onclick = () => { toggleTrace(false); $('#trace-toggle')?.focus(); };

function renderPage(){
  const m = $('#main'); if(!m) return;
  if(state.grid && state.page !== 'activity'){ clearInterval(state.gridTimer); state.grid.destroy(); state.grid = null; }
  state.sig = pageSig();
  ({home: state.care ? careHome : homePage, star: starPage, shop: shopPage, bills: billsPage, family: familyPage, activity: activityPage, dashboard: dashboardPage, lab: labPage, settings: settingsPage}[state.page] || homePage)(m);
  watchBars();
}
const head = (_section, title, sub) => `<div class="page-head"><h1>${title}</h1>${sub ? `<p>${sub}</p>` : ''}</div>`;

// ---------------------------------------------------------------- Home
const openInvoice = i => !['PAID', 'CANCELLED', 'MARKED_AS_PAID', 'REFUNDED'].includes(i.status);
const WAITING = new Set(['AWAITING_APPROVAL', 'AWAITING_PAYMENT', 'UNKNOWN', 'HELD', 'UNCLAIMED', 'ANOMALY']);
function attentionItems(){
  const d = state.data, out = [];
  if(d.frozen?.on) out.push({tone:'bad', ic:'freeze', text:`Payments are frozen by ${d.frozen.by}. Nothing can be sent until it's lifted.`, act:'Open Family', page:'family'});
  if(['AWAITING_AUTH', 'COOLING'].includes(d.phase)) out.push({tone:'warn', ic:'fingerprint', text:'A payment is waiting for you to check it and sign.', act:'Open it', page:assistantPage()});
  state.payments.filter(p => WAITING.has(p.state)).forEach(p => out.push(p.next_step?.type === 'redirect'
    ? {tone:'warn', ic:'transfer', text:`${p.amount} to ${p.to} is waiting for you on the approval page.`, act:'Continue', href:p.next_step.url, track:p.payment_id}
    : {tone:'warn', ic:'alert', text:`${p.amount} to ${p.to}: ${statusInfo(p.state, p.verified, mode()).word}.`, act:'Open Activity', page:'activity'}));
  if(OFF_SCREEN.has('bills')) return out;
  d.invoices.filter(i => openInvoice(i) && !i.known_biller).forEach(i => out.push({tone:'bad', ic:'alert', text:`${i.amount} invoice from ${i.from}, someone you've never paid. Check it with Star before doing anything.`, act:"What's this?", say:`What's this bill?`}));
  d.invoices.filter(i => openInvoice(i) && i.known_biller).forEach(i => out.push({tone:'info', ic:'bolt', text:`${i.title}: ${i.amount}, due ${dueDate(i.due)}.`, act:'Pay with Star', say:`Pay the ${i.from} bill`}));
  return out;
}
function attentionHtml(){
  const items = attentionItems();
  if(!items.length) return '<p class="muted">Nothing needs you right now.</p>';
  return `<ul class="attention">${items.map((a, i) => `<li class="tone-line tone-${a.tone}">${icon(a.ic)}<span>${esc(a.text)}</span><button class="btn secondary small" type="button" data-attn="${i}">${esc(a.act)}</button></li>`).join('')}</ul>`;
}
function bindAttention(root){
  const items = attentionItems();
  root.querySelectorAll('[data-attn]').forEach(b => b.onclick = () => {
    const a = items[b.dataset.attn];
    if(a.href){ store.set('tracking', a.track); saveChat(); location.assign(a.href); }
    else if(a.say) send(a.say);
    else go(a.page);
  });
}
function allowanceHtml(){
  const a = state.data?.allowance || {};
  return `<div class="stat stat-main"><span class="stat-label">Star can spend</span><span class="stat-value">${esc(a.left || '…')}</span>
    <span class="stat-foot">left of ${esc(a.monthly || '')} this month</span><span class="muted small">${esc(a.note || '')}</span></div>`;
}
function familyStat(){
  const g = state.data?.guardian || {};
  return `<button class="stat stat-btn" type="button" data-go="family"><span class="stat-label">Family guardian</span><span class="stat-value small-value">${esc(g.name || '')}</span>
    <span class="stat-foot">${g.status === 'paired' ? `${icon('shield')}Paired, watching risky payments` : 'Not paired yet. Tap to invite'}</span></button>`;
}
function freezeStat(){
  const f = state.data?.frozen || {};
  return `<div class="stat ${f.on ? 'stat-bad' : ''}"><span class="stat-label">Freeze</span><span class="stat-value small-value">${f.on ? 'Frozen' : 'Payments on'}</span>
    ${f.on ? '<button class="btn secondary small" type="button" data-go="family">Manage</button>'
      : `<button class="btn danger small" type="button" id="home-freeze">${icon('freeze')}Freeze now</button>`}</div>`;
}
// One tap, right here: it's the button for the moment something feels wrong. Lifting it is what takes signatures.
function bindFreeze(root){
  const b = root.querySelector('#home-freeze'); if(!b) return;
  b.onclick = async () => {
    b.disabled = true;
    try{
      await api('/api/freeze', {session_id: state.sid});
      toast('All payments are frozen. Nothing can be sent.'); speak('All payments are frozen. Nothing can be sent.');
      await refresh(); renderPage();
    }catch(e){ b.disabled = false; toast(e.message); }
  };
}
const paymentRow = p => `<li class="pay-row"><span class="pay-main"><b>${esc(p.what)}</b><small>${esc(p.to)} · ${esc(clock(p.created_at))}</small></span>
  <span class="pay-amt">${esc(p.amount)}</span>${ppBadge(p.state, p.mode_label, p.verified)}</li>`;
function homePage(m){
  m.innerHTML = `
    <section class="hero engraved"><div><h1>${greeting()}, ${esc(who())}.</h1>
      <p>${today()}. Tell Star what you need. Nothing is paid until you check it and sign.</p></div>
      <button class="btn primary big" type="button" data-go="${assistantPage()}">${icon('mic')}Talk to Star</button></section>
    <section class="stats" aria-label="Overview">${allowanceHtml()}${familyStat()}${freezeStat()}</section>
    <div class="two-col">
      <section class="card pad" aria-labelledby="attn-h"><h2 id="attn-h">Needs your attention</h2><div id="attention">${attentionHtml()}</div></section>
      <section class="card pad" aria-labelledby="last-h"><h2 id="last-h">Last payments</h2><div id="last-pay"><p class="muted">Loading…</p></div>
        <button class="text-btn" type="button" data-go="activity">See all activity and stops</button></section>
    </div>`;
  m.querySelectorAll('[data-go]').forEach(b => b.onclick = () => go(b.dataset.go));
  bindAttention(m); bindFreeze(m);
  const lastPay = () => loadPayments().then(rows => {
    const el = $('#last-pay'); if(!el) return;
    if(state.paymentsFailed) return loadFailed(el, 'your last payments', lastPay);
    el.innerHTML = rows.length ? `<ul class="pay-list">${rows.slice(0, 5).map(paymentRow).join('')}</ul>` : '<p class="muted">No payments yet in this demo.</p>';
    const a = $('#attention'); if(a){ a.innerHTML = attentionHtml(); bindAttention(a); }
  });
  lastPay();
}
function careHome(m){
  const a = state.data?.allowance || {};
  m.innerHTML = `
    <section class="care-hello"><div><h1>Hello, ${esc(who())}</h1><p>One thing at a time. You're in control.</p></div>
      <div class="care-left"><span>You can spend</span><b>${esc(a.left || '…')}</b></div></section>
    <div id="care-attn"></div>
    ${assistantHtml()}
    ${state.data?.frozen?.on ? '' : `<p class="care-freeze"><button class="btn danger" type="button" id="home-freeze">${icon('freeze')}Something feels wrong? Freeze payments</button></p>`}`;
  bindAssistant(); bindFreeze(m);
  loadPayments().then(() => {
    const el = $('#care-attn'), items = attentionItems().filter(x => x.tone !== 'info');
    if(el && items.length){ el.innerHTML = `<section class="card pad" aria-label="Needs your attention">${attentionHtml()}</section>`; bindAttention(el); }
  });
}

// ---------------------------------------------------------------- Star page: the assistant, what's behind it, the PayPal ticker
function starPage(m){
  m.innerHTML = `<h1 class="sr">Talk to Star</h1><div class="star-grid">${assistantHtml()}
    <div class="star-side">
      <section class="card pad ticker-card" aria-labelledby="tick-h"><h2 id="tick-h">PayPal calls</h2><div class="ticker-host"></div></section>
      <section class="card pad safety" id="safety" aria-live="polite"></section>
    </div></div>`;
  bindAssistant(); renderSafety(); renderTicker(); loadCalls();
}
function tickerHtml(compact = false){
  const c = state.calls || {}, calls = (c.calls || []).slice(compact ? -3 : -7);
  return `<div class="counter ${c.money_moving_this_task ? '' : 'zero'}"><b>${esc(c.money_moving_this_task ?? 0)}</b>
      <span>Payment calls to PayPal for this request ${modeTag(c.mode_label || mode())}</span></div>
    ${calls.length ? `<p class="muted small ticker-label">Recent calls, newest last</p><ol class="ticker">${calls.map(x => `<li class="${x.write ? 'write' : ''}"><code>${esc(x.method)} ${esc(x.path)}</code><span class="code ${x.status == null || x.status >= 400 ? 'err' : ''}">${x.status ?? 'no answer'}</span></li>`).join('')}</ol>`
      : compact ? '' : '<p class="muted small">Nothing has been sent to PayPal yet. This list comes from the server\'s log.</p>'}`;
}
function renderTicker(){ document.querySelectorAll('.ticker-host').forEach(el => { el.innerHTML = tickerHtml(el.dataset.compact === '1'); }); }

// "Behind your request": the checks behind the latest request, in plain words
const CHECK_LABELS = {intent:'Faithful to your words', plan:'Earlier steps completed', policy:'Safety rules', guardian_approval:'Family approval', authorization:'Your signature', execution:'Sent to PayPal'};
function decisionPanel(record){
  const marks = {pass:'PASS', fail:'FAILED', pending:'PENDING', not_applicable:'NOT REQUIRED'};
  const ex = record.verifier?.extracted || {};
  const cents = v => Number.isInteger(v) ? cash(v) : 'Not stated';
  return `<details class="decision-panel"><summary>How Star checked this</summary>
    <p class="muted small">Policy ${esc(record.policy_version)} · ${esc(record.stage)}<br>${esc(record.evidence_scope)}</p>
    <blockquote>${esc(record.raw_text || 'No transcript recorded')}</blockquote>
    <div class="evidence-compare"><div><b>AI draft</b><p>${esc(record.draft?.payee)}<br>${esc(cents(record.draft?.amount_cents))}</p></div>
    <div><b>Second reading</b><p>${esc(ex.payee || 'Not stated')}<br>${esc(ex.amount == null ? 'Not stated' : cash(Math.round(ex.amount * 100)))}</p></div></div>
    <p class="muted small">${esc(record.verifier?.engine)}${record.verifier?.mode === 'offline_shared_parser' ? ' · Shared offline parser; not two independent models.' : ''}</p>
    <ol class="contract-checks">${Object.entries(record.checks || {}).map(([key, c]) => `<li class="${esc(c.status)}"><b>${esc(CHECK_LABELS[key] || key)}</b><span>${esc(marks[c.status] || c.status)}</span><small>${esc(c.detail)}</small></li>`).join('')}</ol>
    <p class="muted small">Rules: ${esc((record.reason_codes || []).join(', ') || 'No risk flags')}<br>Still needed: ${esc((record.required_evidence || []).join(', ') || 'None')}</p>
    </details>`;
}
function renderSafety(){
  const el = $('#safety'); if(!el) return;
  const c = state.check;
  const row = (st, title, detail) => `<div class="check ${st}"><span class="s" aria-hidden="true">${{pass:'✓', warn:'!', pending:'…', fail:'✕', idle:'·', not_applicable:'·'}[st] || '·'}</span><div>${esc(title)}<small>${esc(detail)}</small></div></div>`;
  const rows = c ? c.rows.map(r => row(...r)).join('') : [
    row('idle', 'Star prepares, never pays', 'The assistant can only draft. It has no tool that moves money.'),
    row('idle', 'Checked against what you said', 'The person and the amount must come from your own words.'),
    live() ? row('idle', 'A second, independent check', 'A separate model re-reads only your words. If anything differs, the payment is frozen.')
      : row('idle', 'A second reading', 'Your words are read again on their own. Offline this uses the same rules that prepared the card, so read the card yourself. If anything differs, the payment is frozen.'),
    row('idle', 'Safety rules', 'Limits, new people, unusual amounts, scam signs.'),
    row('idle', 'Your device signs', 'Your device signs the exact payment. Only then can anything reach PayPal.'),
  ].join('');
  el.innerHTML = `<h2>Behind your request</h2><p class="muted small">${c ? esc(c.title) : 'What happens before any money moves'}</p>
    <div class="checks">${rows}</div>${c?.decision ? decisionPanel(c.decision) : ''}
    <button class="text-btn" id="open-trace" type="button">Open the full agent trace</button>`;
  $('#open-trace').onclick = () => toggleTrace(true);
}
function checksFromCard(card){
  if(!card.decision) return {title:`${card.amount} to ${card.payee_name}`, rows:[]};
  const r = card.decision;
  return {title:`${card.amount} to ${card.payee_name}`, decision:r,
    rows:Object.entries(r.checks).map(([key, c]) => [c.status === 'not_applicable' ? 'idle' : c.status, CHECK_LABELS[key] || key, c.detail])};
}

// ---------------------------------------------------------------- Assistant
function assistantHtml(){
  return `<section class="card assistant" aria-label="Star assistant">
    <div class="stage" id="stage"></div>
    <div class="composer-wrap">
      <form class="composer ${state.typing ? 'typing-open' : ''}" id="composer">
        <button type="button" class="mic" id="mic" aria-label="Tap to speak, tap again to stop">${icon('mic')}<span>${state.care ? 'Tap to speak' : 'Speak'}</span></button>
        <button type="button" class="type-open" id="type-open" aria-expanded="${!!state.typing}" aria-controls="text">${state.typing ? 'Close' : 'Type'}</button>
        <label class="sr" for="text">Type your request</label>
        <textarea id="text" rows="1" maxlength="500" placeholder="${state.care ? 'Or type here' : 'Send 50 to Anna'}"></textarea>
        <button class="send" type="submit" aria-label="Send">${icon('send')}<span class="label">Send</span></button>
      </form>
      <div class="hint" id="hint" role="status" aria-live="polite" aria-atomic="true"></div>
      <div class="composer-foot">${icon('lock')}Star prepares. You check. Your device signs. No money moves without you.</div>
      ${serverAsr() ? `<div class="composer-foot">${icon('mic')}For your security, what you say is recorded and kept in this session's audit trail.</div>` : ''}
    </div></section>`;
}
function bindAssistant(){
  const ta = $('#text');
  $('#composer').onsubmit = e => { e.preventDefault(); send(ta.value); };
  ta.onkeydown = e => { if(e.key === 'Enter' && !e.shiftKey){ e.preventDefault(); send(ta.value); } };
  $('#mic').onclick = micToggle;
  $('#type-open').onclick = () => setTyping(!state.typing);
  renderStage();
}
// Care mode on a phone: one big button to speak. Typing is one tap away, so the dock doesn't take a third of a small screen.
function setTyping(on, focus = true){
  state.typing = on;
  $('#composer')?.classList.toggle('typing-open', on);
  const b = $('#type-open'); if(b){ b.textContent = on ? 'Close' : 'Type'; b.setAttribute('aria-expanded', String(on)); }
  if(on && focus) $('#text')?.focus();
  if(!on && document.activeElement === $('#text')) document.activeElement.blur();
  measureBars();
}
const clockNow = at => at ? new Date(at).toLocaleTimeString('en-US', {hour:'numeric', minute:'2-digit'}) : '';
const msgSide = (m, name, i) => `<div class="msg-side"><span class="who">${name}</span>${m.at ? `<time>${esc(clockNow(m.at))}</time>` : ''}${i === undefined ? '' : `<button class="replay" type="button" data-i="${i}" aria-label="Read this aloud">${icon('speaker')}</button>`}</div>`;
const SUGGEST = [
  ['family', 'Send 50 to my daughter'], ['family', 'Send 20 to my son'], ['audit', 'What have I paid recently?'],
  ['alert', 'The police say I must move my savings to a safe account', 'danger'],
];
const SUGGEST_CARE = [['family', 'Send money to my daughter'], ['family', 'Send money to my son'], ['phone', 'Talk to a real person']];
function whyHtml(reasons){ return reasons?.length ? `<ul class="why-list">${reasons.map(r => `<li>${esc(r)}</li>`).join('')}</ul>` : ''; }
function stageHtml(m, i){
  if(m.role === 'user') return `<div class="msg user">${msgSide(m, 'You')}<div class="msg-body">${m.demo ? '<small class="demo-tag">Demo input</small>' : ''}${esc(m.text)}</div></div>`;
  if(m.type === 'retry') return m.done ? '' : `<div class="next-chips"><button class="btn primary small re-send" type="button" data-i="${i}">Try again</button></div>`;
  if(m.type === 'demo-intro') return `<div class="demo-intro" role="note"><b>${icon('lab')} Safety test: a broken AI model</b>
    For this test we swap in a model with a deliberate bug: it writes <strong>10&times; the amount</strong> you say.
    The checks after it are the real ones. Watch whether the wrong amount can reach you.</div>`;
  if(m.type === 'caught') return caughtHTML(m, i);
  if(m.type === 'typing') return `<div class="typing"><span class="thinking-dot" aria-hidden="true"></span>I'm working on your request…</div>`;
  if(m.type === 'tracker') return trackerHtml(m, i);
  if(m.type === 'choices') return m.done ? '' : `<div class="choices" role="group" aria-label="Choose one">${m.options.map((o, j) =>
    `<button class="choice" type="button" data-i="${i}" data-j="${j}"><span>${esc(o.label)}</span><small>${esc(o.detail || '')}</small></button>`).join('')}</div>`;
  if(m.type === 'plan') return `<div class="plan" role="note"><b>I'll do this in ${m.items.length} steps. You check and confirm each one.</b>
    <ol>${m.items.map(x => `<li>${esc(x)}</li>`).join('')}</ol></div>`;
  if(m.type === 'frozen') return `<div class="stop-block frozen" data-fx="${i}"><h3>${icon('freeze')}Frozen: this didn't match what you said</h3>
    <ul class="why-list">${m.items.map(x => `<li>${esc(x)}</li>`).join('')}</ul><p class="muted small">A second reading of your own words caught it. Nothing went to PayPal.</p>
    ${stopCount(m)}<div class="acts"><button class="btn secondary again" type="button">Say it again</button></div></div>`;
  if(m.type === 'blocked' && state.care && m.scam !== false) return careStopHtml(m, i);
  if(m.type === 'blocked') return `<div class="stop-block" data-fx="${i}"><h3>${icon('shield')}${m.scam === false ? "I can't do this one" : "I've stopped this for you"}</h3>
    <p>${esc(m.text)}</p>${state.care || m.reasons?.length ? `<details class="why" ${state.care ? 'open' : ''}><summary>Why I stopped this</summary>${whyHtml(m.reasons?.length ? m.reasons : [m.text])}</details>` : ''}
    ${m.hint ? `<small class="muted">${esc(m.hint)}</small>` : ''}${stopCount(m)}
    <div class="acts">${m.scam === false ? '' : `<a class="btn danger" href="tel:${esc(hotline().number)}">${icon('phone')}<span>Call <span class="nowrap">${esc(hotline().spoken)}</span></span></a>`}
      <button class="btn secondary notify" type="button">${icon('family')}Tell ${esc(guardianName())}</button>
      <button class="replay btn secondary small" type="button" data-i="${i}" aria-label="Read this aloud">${icon('speaker')}</button></div>
    ${m.scam === false ? '' : `<p class="muted small">${esc(hotline().name[0].toUpperCase() + hotline().name.slice(1))}, ${esc(hotline().number)}.</p>`}</div>`;
  if(m.type === 'next') return m.done ? '' : `<div class="next-chips"><button class="btn primary small nx-go" type="button" data-i="${i}">Continue: ${esc(m.task)}</button><button class="btn secondary small nx-no" type="button" data-i="${i}">Not now</button></div>`;
  return `<div class="msg bot">${msgSide(m, 'Star', i)}<div class="msg-body">${esc(m.text)}</div></div>`;
}
// Care on a phone: the way to get help comes first, then one line (short for a scam stop, whose reasons follow; any other
// stop keeps its own words, such as the family's no); the full reasons stay open below.
const careStopHtml = (m, i) => `<div class="stop-block care-stop" data-fx="${i}"><h3>${icon('shield')}I've stopped this for you</h3>
    <div class="acts"><a class="btn danger" href="tel:${esc(hotline().number)}">${icon('phone')}<span>Call <span class="nowrap">${esc(hotline().spoken)}</span></span></a>
      <button class="btn secondary notify" type="button">${icon('family')}Tell ${esc(guardianName())}</button>
      <button class="replay btn secondary small" type="button" data-i="${i}" aria-label="Read this aloud">${icon('speaker')}</button></div>
    <p>${m.scamStop ? "Please don't send any money. If someone asked you to, hang up and call this number or your family." : esc(m.text)}</p>
    <details class="why" open><summary>Why I stopped this</summary>${whyHtml(m.reasons?.length ? m.reasons : [m.text])}</details>
    ${m.hint ? `<small class="muted">${esc(m.hint)}</small>` : ''}${stopCount(m)}
    <p class="muted small">${esc(hotline().name[0].toUpperCase() + hotline().name.slice(1))}, ${esc(hotline().number)}.</p></div>`;
const stopCount = m => m.calls === undefined ? '' : `<p class="stop-count"><b>${esc(m.calls)}</b> money-moving PayPal calls for this request ${modeTag(mode())}</p>`;
function renderStage(){
  const st = $('#stage'); if(!st) return;
  st.classList.toggle('empty', !state.messages.length);
  if(!state.messages.length){
    const list = state.care ? SUGGEST_CARE : SUGGEST;
    st.innerHTML = `<div class="welcome-msg"><span class="spark" aria-hidden="true">${icon('spark')}</span><div>
        <h3>${state.care ? 'What can I say?' : 'What can I help you with today?'}</h3>
        <p>${state.care ? 'Tap one of these, or tap Speak and tell me in your own words.' : "Tell me in your own words. We'll take it one step at a time."}</p></div></div>
      <div class="suggestions">${list.map(([ic, t, cls]) => `<button class="suggestion ${cls || ''}" type="button" data-say="${esc(t)}">${icon(ic)}<span>${esc(t)}</span></button>`).join('')}</div>`;
    st.querySelectorAll('.suggestion').forEach(b => b.onclick = () => send(b.dataset.say));
    return;
  }
  st.innerHTML = state.messages.map(stageHtml).join('');
  playMoments(st);
  st.querySelectorAll('.choice').forEach(b => b.onclick = () => { const m = state.messages[b.dataset.i]; m.done = true; send(m.options[b.dataset.j].send); });
  st.querySelectorAll('.replay').forEach(b => b.onclick = () => toggleMessageSpeech(state.messages[b.dataset.i]));
  syncSpeechButtons();
  st.querySelectorAll('.again').forEach(b => b.onclick = () => { if(state.care) micToggle(); else $('#text')?.focus(); });
  st.querySelectorAll('.re-send').forEach(b => b.onclick = () => { const m = state.messages[b.dataset.i]; m.done = true; send(m.text, m.endpoint); });
  st.querySelectorAll('.notify').forEach(b => b.onclick = () => push({role:'bot', text: guardianPaired()
    ? `${guardianName()} can see this on her guardian page. This demo doesn't send real texts or emails, so please call her too.`
    : `${guardianName()} isn't paired yet, so she can't see this. This demo doesn't send real texts. Please call her, or invite her from the Family page.`}));
  st.querySelectorAll('.nx-go').forEach(b => b.onclick = () => { const m = state.messages[b.dataset.i]; m.done = true; send(m.task); });
  st.querySelectorAll('.nx-no').forEach(b => b.onclick = () => { state.messages[b.dataset.i].done = true; push({role:'bot', text:'OK. Just call me whenever you need me.'}); });
  st.querySelectorAll('.tr-go').forEach(b => b.onclick = () => { const m = state.messages[b.dataset.i]; store.set('tracking', m.pay.payment_id); saveChat(); location.assign(m.pay.next_step.url); });
  st.querySelectorAll('.tr-receipt').forEach(b => b.onclick = () => { const r = state.messages[b.dataset.i].pay.receipt; download(`receipt-${r.txn_id}.json`, r); });
  st.querySelectorAll('.tr-refund').forEach(b => b.onclick = () => send('I want my money back'));
  st.scrollTop = st.scrollHeight;
  // In Care mode and on phones the page itself scrolls. A long stop is read from its first line.
  const last = st.lastElementChild;
  last?.scrollIntoView({block: last.getBoundingClientRect().height > innerHeight * .55 ? 'start' : 'nearest'});
}
// Each moment plays once, when it first happens, not on every re-render or reload.
function playMoments(st){
  st.querySelectorAll('[data-fx]').forEach(el => {
    const m = state.messages[el.dataset.fx]; if(!m) return;
    if(m.type === 'tracker'){ if(m.pay?.verified && !m.sealed){ m.sealed = true; verifiedFx(el); } }
    else if(!m.played){ m.played = true; (m.type === 'caught' ? caughtFx : stopFx)(el); }
  });
}
function announce(text, urgent = false){ const el = $(urgent ? '#sr-alert' : '#sr-live'); if(el && text){ el.textContent = ''; setTimeout(() => { el.textContent = text; }, 30); } }
function push(m){
  m.at ??= Date.now();
  if(m.type === 'blocked' && state.care && state.typing) setTyping(false, false);   // a stop needs the screen, not the keyboard
  state.messages.push(m); renderStage(); saveChat();
  if(m.role === 'bot' && m.type === 'blocked') announce(`Stopped. ${m.text}`, true);
  else if(m.role === 'bot') announce(m.text);
  else if(m.type === 'frozen') announce("Frozen: this didn't match what you said. Nothing went to PayPal.", true);
  else if(m.type === 'caught') announce('Caught and frozen. The wrong amount never reached you.', true);
}

// ---------------------------------------------------------------- Tracker: what PayPal really says about one payment
const FLOW = {
  transfer: [['Sent to PayPal', ['PROCESSING', 'HELD', 'UNCLAIMED', 'SENT']], ['Delivered', ['SENT']]],
  purchase: [['Sent to PayPal', ['AWAITING_APPROVAL', 'APPROVED', 'CAPTURING', 'CAPTURE_PENDING', 'PAID', 'REFUNDED', 'REVERSED', 'BUYER_CANCELLED', 'DECLINED', 'EXPIRED']],
             ['Approved by you', ['APPROVED', 'CAPTURING', 'CAPTURE_PENDING', 'PAID', 'REFUNDED', 'REVERSED']], ['Payment taken', ['PAID', 'REFUNDED']]],
  bill: [['Sent to PayPal', ['AWAITING_PAYMENT', 'PAID', 'REFUNDED']], ['Paid by you', ['PAID', 'REFUNDED']]],
  refund: [['Asked PayPal', ['REFUND_PENDING', 'REFUNDED']], ['Money on its way back', ['REFUNDED']]],
  money_request: [['Request sent', ['SENT_TO_PAYER', 'PAID']], ['They paid', ['PAID']]],
};
const SETTLED = new Set(['SENT', 'PAID', 'REFUNDED', 'RETURNED', 'REVERSED', 'FAILED', 'DECLINED', 'BUYER_CANCELLED', 'EXPIRED', 'ABANDONED', 'INVOICE_CANCELLED', 'ANOMALY']);
const BAD_END = new Set(['RETURNED', 'REVERSED', 'FAILED', 'DECLINED', 'BUYER_CANCELLED', 'EXPIRED', 'ABANDONED', 'INVOICE_CANCELLED', 'ANOMALY']);
function verifiedWords(by){
  if(!by) return '';
  return by.startsWith('webhook') ? 'a signed PayPal webhook' : 'a PayPal status check';
}
function trackerHtml(m, i){
  const p = m.pay;
  if(!p) return `<div class="tracker"><h3>${esc(sourceWords('Checking with PayPal…', mode()))}</h3><p class="muted small">${esc(sourceWords('Coming back from an approval page proves nothing, so Star is asking PayPal itself.', mode()))}</p></div>`;
  const flow = FLOW[p.kind] || FLOW.transfer, end = BAD_END.has(p.state), s = p.state;
  const steps = [['Signed by you', true], ...flow.map(([label, states]) => [isSim() ? label.replace('PayPal', 'the simulator') : label, states.includes(s)])];
  let currentSet = false;
  const items = steps.map(([label, done]) => {
    let cls = done ? 'done' : 'todo';
    if(!done && !currentSet && !end){ cls = 'now'; currentSet = true; }
    return `<li class="${cls}"><span aria-hidden="true">${done ? '✓' : ''}</span>${esc(label)}<span class="sr">${done ? ' done' : cls === 'now' ? ' in progress' : ' not yet'}</span></li>`;
  }).join('');
  const verified = p.verified ? `<li class="done verified"><span aria-hidden="true">✓</span>Verified by ${esc(isSim() ? verifiedWords(p.verified_by).replace('PayPal', 'simulator') : verifiedWords(p.verified_by))}</li>` : (end ? '' : '<li class="todo"><span aria-hidden="true"></span>Verified<span class="sr"> not yet</span></li>');
  const redirect = p.next_step?.type === 'redirect';
  const canRefund = p.state === 'PAID' && p.verified && p.kind === 'purchase';
  return `<div class="tracker tone-${statusInfo(s, p.verified, mode()).tone}" data-fx="${i}">
    ${p.verified && !BAD_END.has(s) ? sealSvg() : ''}<header><h3>${esc(p.what)}</h3><span class="tracker-amt">${esc(p.amount)}</span></header>
    <p class="tracker-status">${ppBadge(s, p.mode_label, p.verified)}<span class="muted small">${p.what === p.to ? '' : esc(p.to)}${p.handle ? ` ${p.what === p.to ? '' : '· '}${esc(p.handle)}` : ''}</span></p>
    <ol class="steps">${items}${verified}${end ? `<li class="stopped"><span aria-hidden="true">✕</span>${esc(statusInfo(s, false, mode()).word)}</li>` : ''}</ol>
    ${s === 'UNKNOWN' ? '<p class="warn-text">Please don\'t pay again. Star is asking PayPal about this same payment, with the same key, so it can\'t go out twice.</p>' : ''}
    <div class="acts">
      ${redirect ? `<button class="btn primary tr-go" type="button" data-i="${i}">${icon('transfer')}${isSim() ? 'Open the simulated approval page' : 'Continue on PayPal'}</button>` : ''}
      ${canRefund ? `<button class="btn secondary tr-refund" type="button">I want my money back</button>` : ''}
      ${p.verified && p.receipt ? `<button class="btn secondary tr-receipt" type="button" data-i="${i}">${icon('download')}Signed receipt</button>` : ''}
    </div></div>`;
}
function liveTracker(m){
  // Poll only while something can still change. A webhook or a status check moves it on the server; here we only read.
  let tries = 0, spoken = false;
  const tick = async () => {
    tries++;
    if(!state.messages.includes(m)) return;
    try{
      const r = await api(`/api/payments/${encodeURIComponent(state.sid)}/${encodeURIComponent(m.pay.payment_id)}`);
      const was = `${m.pay.state}|${m.pay.verified}`; m.pay = r.payment;
      if(`${m.pay.state}|${m.pay.verified}` !== was){ renderStage(); saveChat(); refresh(); announce(`${m.pay.what}: ${statusInfo(m.pay.state, m.pay.verified, mode()).word}`); }
      loadCalls();
    }catch{}
    const done = SETTLED.has(m.pay.state) && (m.pay.verified || BAD_END.has(m.pay.state));
    if(done && !spoken && !m.announced){
      spoken = true; m.announced = true;
      try{
        const r = await api(`/api/transaction/${encodeURIComponent(state.sid)}/${encodeURIComponent(m.pay.draft_id)}`);
        state.queueSpeech = true; push({role:'bot', text:r.reply}); speak(r.speak);
      }catch{}
      return;
    }
    if(m.pay.next_step?.type === 'redirect' || tries > 80) return;
    setTimeout(tick, tries < 20 ? 1200 : 3000);
  };
  setTimeout(tick, 900);
}
function pushTracker(pay){ const m = {type:'tracker', pay}; push(m); if(pay) liveTracker(m); return m; }

// "Simulate a model mistake": show WHY it stopped, side by side, so nobody wonders why a $50 payment "failed"
function showCaught(r){
  const c = r.ui.card, d = r.demo, frozen = r.ui.type === 'frozen';
  const heard = d.heard_amount != null ? cash(Math.round(Number(d.heard_amount) * 100)) : 'no amount';
  push({type:'caught', said: d.said, drafted: c.amount, payee: c.payee_name, heard, frozen, calls: state.calls.money_moving_this_task});
  speak(`Safety test passed. The faulty model wrote ${c.amount}, but the second reading heard ${heard}. ` +
        `It was stopped before you were asked to confirm. Nothing was sent.`);
  state.check = {title: 'Safety test passed', rows: [
    ['fail', 'Faulty AI draft', `${c.amount} to ${c.payee_name}: the planted 10× bug`],
    ['pass', 'Second reading', `Re-read your words, heard ${heard}: mismatch found`],
    ['pass', frozen ? 'Frozen' : 'Stopped', 'No confirmation card, nothing to sign'],
    ['pass', 'PayPal', 'No money-moving call was made']]};
  renderSafety(); refresh(); loadCalls();
}
function caughtHTML(m, i){
  const stage = (label, cls, note) => `<li class="${cls}"><span>${label}</span><small>${note}</small></li>`;
  return `<div class="caught" data-fx="${i}">
    <div class="caught-head"><span class="caught-badge">${m.frozen ? 'Caught and frozen' : 'Caught and stopped'}</span>
      <b>The wrong amount never reached you. Nothing went to PayPal.</b></div>
    <div class="caught-grid">
      <div class="cg said"><small>1. You said</small><strong class="quote">“${esc(m.said)}”</strong><span>to ${esc(m.payee)}</span></div>
      <div class="cg bad"><small>2. The faulty model drafted</small><strong>${esc(m.drafted)}</strong><span>10&times; too much (the planted bug)</span></div>
      <div class="cg good"><small>3. The second reading heard</small><strong>${esc(m.heard)}</strong><span>it re-read your own words only</span></div>
    </div>
    <p class="caught-zero"><b>${esc(m.calls ?? 0)}</b><span>money-moving PayPal calls</span> ${modeTag(mode())}</p>
    <ol class="caught-path">
      ${stage('AI draft', 'x', 'wrong amount')}${stage('Second check', 'ok', 'mismatch found')}
      ${stage('Card', 'skip', 'never shown')}${stage('Signature', 'skip', 'never asked')}${stage('PayPal', 'ok', '0 calls')}
    </ol>
    <small class="muted">An AI can mishear or make things up. Here it can only write a draft, and a
      second reading compares that draft with what you actually said before anything is shown or signed.</small>
  </div>`;
}
// After a payment is confirmed, the next step of the user's own plan is prepared right away (it still needs its own
// confirmation). After a cancel we ask instead, because the user may have changed their mind.
function continueWith(task, payment = true){
  setTimeout(() => {
    push({role:'bot', text: payment ? `Next step: “${task}”. I'm preparing it now. You'll check and confirm it the same way.`
                                    : `Next: “${task}”.`});
    state.queueSpeech = true;                        // don't cut off the earlier read-out; queue after it
    send(task, '/api/chat', state.voiceTurn, true);
  }, 300);
}
function offerNext(task){
  setTimeout(() => { push({role:'bot', text:`You also asked: “${task}”. Shall we do that now?`}); push({type:'next', task}); }, 500);
}
// After a page refresh the server may still hold a payment waiting for the signature: bring its card back.
async function restorePending(){
  if(state.data?.pending_change && !['AWAITING_AUTH', 'COOLING'].includes(state.data?.phase)){
    push({role:'bot', text:`You have a change waiting: ${state.data.pending_change.title}. Please check it and confirm, or cancel it.`});
    return openChange(state.data.pending_change);
  }
  if(!['AWAITING_AUTH', 'COOLING'].includes(state.data?.phase)) return;
  try{
    const r = await api('/api/card/' + encodeURIComponent(state.sid));
    if(!r.card) return;
    state.check = checksFromCard(r.card); renderSafety();
    push({role:'bot', text:`You have a payment waiting: ${r.card.amount} to ${r.card.payee_name}. Please check it and confirm, or cancel it.`});
    openConfirm(r.card);
  }catch{}
}

async function send(text, endpoint = '/api/chat', voice = false, auto = false, demo = false){
  text = String(text || '').trim(); if(!text || state.busy) return;
  if(!auto) stopSpeaking();                         // a new question interrupts the previous answer
  state.voiceTurn = voice;
  if(state.page !== assistantPage()) go(assistantPage());
  state.busy = true; $('#text') && ($('#text').value = '');
  state.messages = state.messages.filter(m => m.type !== 'choices' || m.done || m.care);   // the easier-version invite waits for its own answer
  if(demo) push({type:'demo-intro'});
  push({role:'user', text, demo}); push({type:'typing'});
  try{
    const audio = voice ? state.nextAudio : null; state.nextAudio = null;   // the recording these words came from
    let r;
    try{ r = await api(endpoint, {session_id: state.sid, text, care: !!state.care, tz: TZ, ...(audio ? {audio_sha256: audio} : {})}); }
    catch(e){
      if(e.data?.code !== 'SESSION_NOT_FOUND') throw e;
      await newSession(); await refresh();               // the server was restarted: carry on in a fresh session
      r = await api(endpoint, {session_id: state.sid, text, care: !!state.care, tz: TZ});
    }
    state.sid = r.session_id; store.set('sid', r.session_id);
    state.messages = state.messages.filter(m => m.type !== 'typing');
    addTrace(r.trace);
    await loadCalls();
    handleTurn(r);
  }catch(e){
    state.messages = state.messages.filter(m => m.type !== 'typing' && m.type !== 'retry');
    // A chat turn can only read and draft: no tool it can reach moves money, so "no money has moved" is safe to say here.
    // The server refused the message (too long, too many, not allowed): its own words, and no retry of the same text.
    // "Can't reach" only when the network failed; a server fault says so and offers a retry.
    const refused = e.status >= 400 && e.status < 500;
    const said = refused ? e.message : e.offline ? "I can't reach Star right now. No money has moved."
      : "Star had a problem with that. No money has moved. Please try again.";
    push({role:'bot', text: said}); console.warn(e);
    if(!refused) push({type:'retry', text, endpoint});
  }finally{ state.busy = false; saveChat(); }
}
function handleTurn(r){
  const ui = r.ui || {type:'text'};
  // "Yes please" / "make the text bigger": Star switched Care mode, so the screen follows before the reply is read
  if(typeof ui.care_mode === 'boolean'){ state.messages.forEach(m => { if(m.type === 'choices' && m.care) m.done = true; }); if(ui.care_mode !== state.care){ setCare(ui.care_mode, true); go(assistantPage()); } }
  if(r.demo?.kind === 'model_mistake' && ui.card){ showCaught(r); return; }
  if(ui.plan?.length > 1) push({type:'plan', items: ui.plan});
  const calls = state.calls.money_moving_this_task;
  if(ui.type === 'blocked' && !ui.card){
    push({type:'blocked', role:'bot', text:r.reply, reasons: ui.reasons, hint: ui.hint, calls, scamStop: !!(ui.hotline || ui.locked)}); speak(r.speak, true);
    state.check = {title:'Stopped before reaching the payment step', rows:[
      ['pass', 'Request heard', 'We listened to everything you said.'],
      ['fail', 'Looks like a scam', (ui.reasons || []).join(' ') || 'This matches common scam patterns.'],
      ['idle', 'No payment prepared', 'Star did not draft anything.'], ['idle', 'Nothing to sign', 'Nothing went to PayPal.']]};
    renderSafety(); refresh(); return;
  }
  if(ui.card && ui.type === 'blocked'){
    // The hotline is for scam signs (R8x invoices, R9x sellers). A daily limit or a PayPal outage isn't a scam.
    const scam = (ui.card.decision?.reason_codes || []).some(code => /^R[89]\d_/.test(code));
    push({type:'blocked', role:'bot', text:r.reply, reasons: ui.card.reasons, calls, scam}); speak(r.speak, true);
    state.check = checksFromCard(ui.card); renderSafety();
    if(ui.offer_next) offerNext(ui.offer_next);
    refresh(); return;
  }
  push({role:'bot', text:r.reply}); speak(r.speak);
  if(ui.type === 'change'){ openChange(ui.card); if(ui.next_task) continueWith(ui.next_task, false); refresh(); return; }
  if(ui.card){
    state.check = checksFromCard(ui.card); renderSafety();
    if(ui.type === 'frozen') push({type:'frozen', items: ui.card.verification.mismatches, calls});
    else openConfirm(ui.card);
  }
  if(ui.type === 'choose') push({type:'choices', options: ui.options});
  if(ui.next_task) continueWith(ui.next_task, false);
  if(ui.offer_next) offerNext(ui.offer_next);
  refresh();
}

// ---------------------------------------------------------------- Account change card (add a person / raise the limit)
// Same idea as a payment: code builds the card from the request, the device signs exactly this change.
function openChange(c){
  state.card = null; state.change = c;
  renderChange(); const dlg = $('#confirm'); if(!dlg.open){ dlg.showModal(); cardIn(dlg); }
  setTimeout(() => $('#c-go')?.focus(), 50);
}
function renderChange(){
  const c = state.change; if(!c) return;
  $('#confirm').innerHTML = `
    <div class="cbody">
      <h2 id="c-title" tabindex="-1">${esc(c.title)}</h2>
      <p class="review-intro">Take a moment to check the details. Nothing changes until you confirm. You can cancel below.</p>
      ${c.you_said ? `<div class="said"><span class="k">Based on what you said</span><q>${esc(c.you_said)}</q></div>` : ''}
      ${c.rows.map(([k, v]) => `<div class="crow"><span class="k">${esc(k)}</span><span class="v">${esc(v)}</span></div>`).join('')}
      <div class="risk MEDIUM"><b>Before you confirm</b><ul><li>${esc(c.note)}</li></ul></div>
    </div>
    <div class="cfoot">
      <button type="button" class="btn secondary" id="c-cancel">Cancel</button>
      <button type="button" class="btn primary" id="c-go" ${state.signMode === 'none' ? 'disabled' : ''}>${icon('fingerprint')}Confirm with fingerprint</button>
    </div>
    <div class="signnote">Confirmation code ${esc(c.digest.slice(0, 8))}…${esc(c.digest.slice(-4))}. Star can only ask for this change; it happens only after your device signs exactly this card.</div>`;
  $('#c-cancel').onclick = async () => {
    const ch = state.change; closeConfirm();
    try{ const r = await api('/api/change/cancel', {session_id: state.sid, change_id: ch.change_id}); push({role:'bot', text:r.reply}); speak(r.speak); }catch(e){ push({role:'bot', text:e.message}); }
    refresh();
  };
  $('#c-go').onclick = () => { stopSpeaking(); confirmChange(); };
}
async function confirmChange(){
  const c = state.change; const btn = $('#c-go'); btn.disabled = true; btn.textContent = 'Touch the sensor…';
  try{
    if(state.signMode === 'device' && !state.data?.passkey?.enrolled) await mockFingerprint($('#confirm'), c.title);
    await ensureSigner();
    const ch = await api('/api/change/challenge', {session_id: state.sid, change_id: c.change_id});
    const assertion = await signPayload(ch.payload, ch.passkey);
    const r = await api('/api/change/confirm', {session_id: state.sid, change_id: c.change_id, assertion});
    closeConfirm(); addTrace(r.trace); push({role:'bot', text:r.reply}); speak(r.speak);
    if(r.next_task) continueWith(r.next_task);
    refresh();
  }catch(e){
    const msg = e.name === 'NotAllowedError' ? 'The fingerprint check was cancelled. Nothing was changed.' : e.message;
    addTrace(e.data?.trace); push({role:'bot', text:msg}); speak(msg);
    if(['NO_CHANGE', 'EXPIRED'].includes(e.data?.code)) closeConfirm(); else renderChange();
  }
}

// ---------------------------------------------------------------- Confirmation card
function openConfirm(c){
  state.change = null;
  state.card = c; state.verify = null; clearInterval(state.coolTimer); clearInterval(state.guardTimer);
  renderConfirm(); const dlg = $('#confirm');
  // She reads first: focus starts on the title, and Confirm only wakes after a moment, so Enter can't run through the card.
  const go1 = $('#c-go:not([disabled])');
  if(go1){ go1.disabled = true; setTimeout(() => { const b = $('#c-go'); if(state.card === c && b && b.disabled && (canGoNow(c) || awaitingAnswer(c))) b.disabled = false; }, 1000); }
  if(!dlg.open){ dlg.showModal(); cardIn(dlg); }
  setTimeout(() => $('#c-title')?.focus(), 50);
  if(c.cooldown_left > 0){
    state.coolTimer = setInterval(async () => {
      state.card.cooldown_left = Math.max(0, state.card.cooldown_left - 1);
      if(state.card.cooldown_left === 0){
        clearInterval(state.coolTimer);
        try{ const r = await api('/api/card/' + state.sid); if(r.card) state.card = r.card; }catch{}
        renderConfirm(); return;
      }
      const left = $('#c-cool-left'); if(left) left.textContent = coolWords(state.card.cooldown_left);
    }, 1000);
  }
  if(c.guardian_required) waitForGuardian();
}
// The card waits for the family. The app only reads here: the approval is the guardian's signature, checked on the server.
function waitForGuardian(){
  clearInterval(state.guardTimer);
  state.guardTimer = setInterval(async () => {
    if(!state.card) return clearInterval(state.guardTimer);
    try{
      const r = await api('/api/card/' + state.sid);
      if(!r.card || r.card.guardian_denied || !['AWAITING_AUTH', 'COOLING'].includes(r.phase)){
        clearInterval(state.guardTimer); const c = state.card; closeConfirm();
        const t = `${guardianName()} said no to ${c.amount} to ${c.payee_name}, so nothing was sent.`;
        push({type:'blocked', role:'bot', text:t, reasons:['Your family guardian denied this exact card on her own device.'], calls: state.calls.money_moving_this_task}); speak(t, true);
        refresh(); return;
      }
      if(!r.card.guardian_required){
        clearInterval(state.guardTimer); const left = state.card.cooldown_left; state.card = r.card; state.card.cooldown_left = Math.min(left, r.card.cooldown_left); renderConfirm();
        speak(`${guardianName()} approved this exact payment. Now it's up to you.`); $('#c-go:not([disabled])')?.focus();
      }
    }catch{}
  }, 2500);
}
const canGoNow = c => c.can_confirm && !(c.cooldown_left > 0) && (!c.verify_question || state.verify === true) && state.signMode !== 'none' && !state.confirming;
// The card can be signed once its question is answered, and the question is all that's missing
const awaitingAnswer = c => !!c && !!c.verify_question && state.verify !== true && c.can_confirm && !(c.cooldown_left > 0)
  && !c.guardian_required && state.signMode !== 'none' && !state.confirming;
function showQuestion(){
  const v = $('#confirm .verify'); if(!v) return;
  v.scrollIntoView({block: 'center', behavior: matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth'});
  v.classList.remove('nudge'); void v.offsetWidth; v.classList.add('nudge');
  v.querySelector('.yesno button')?.focus({preventScroll: true});
}
const coolWords = n =>`${n} more second${n === 1 ? '' : 's'}`;
function renderConfirm(){
  const c = state.card; if(!c) return;
  const needVerify = !!c.verify_question, cooling = c.cooldown_left > 0;
  const canGo = c.can_confirm && !cooling && (!needVerify || state.verify === true) && state.signMode !== 'none';
  const toQuestion = awaitingAnswer(c);   // only the question is missing: the button takes her to it (it may be below the fold)
  const rel = c.payee_relation && c.payee_relation !== 'payee' ? ` (${esc(c.payee_relation)})` : '';
  const goLabel = state.signMode === 'none' ? "Can't sign in this browser" : c.guardian_required ? (guardianPaired() ? `Waiting for ${esc(guardianName())}` : `Needs ${esc(guardianName())}'s OK`) : cooling ? 'Please wait'
    : needVerify && state.verify !== true ? 'Answer the question first' : 'Confirm with fingerprint';
  const goIcon = toQuestion ? icon('alert') : icon('fingerprint');
  const off = c.offers_checked || [];
  const trust = c.trust?.reasons?.length ? c.trust.reasons : [];
  $('#confirm').innerHTML = `
    <div class="cbody">
      <p class="card-mode">${modeTag(c.paypal_mode || mode())}</p>
      <h2 id="c-title" tabindex="-1">${esc(c.title)}</h2>
      <p class="review-intro">${state.care && c.readback ? esc(c.readback) : 'Check who it goes to and how much. Nothing is sent until you confirm. You can cancel below.'}</p>
      ${c.you_said ? `<div class="said"><span class="k">Based on what you said</span><q>${esc(c.you_said)}</q>${(c.you_said_audio || []).length ? `
        <span class="said-audio">${icon('mic')}From your voice recording ${esc(c.you_said_audio[0].slice(0, 8))}…, signed with this card
          <button type="button" class="text-btn" id="c-play">Play</button></span>` : ''}</div>` : ''}
      ${(c.items || []).length ? `<div class="crow"><span class="k">Buying</span><span class="v">${c.items.map(i => `${esc(i.quantity || 1)} × ${esc(i.name)}`).join('<br>')}<small>from ${esc(c.merchant)}</small></span></div>` : ''}
      ${c.invoice ? `<div class="crow"><span class="k">Invoice</span><span class="v">${esc(c.invoice.title)}<small>${esc(c.invoice.number)}, due ${esc(dueDate(c.invoice.due))}</small></span></div>` : ''}
      <div class="crow ${c.payee_from_user === false ? 'unsaid' : ''}"><span class="k">${c.kind === 'refund' ? 'From' : c.kind === 'money_request' ? 'Ask' : 'To'}</span><span class="v">${esc(c.payee_name)}${rel}<small>${esc(c.payee_account)}</small>
        ${c.payee_from_user === false ? `<span class="flag">You didn't mention this person</span>` : ''}</span></div>
      <div class="crow amount ${c.amount_from_user === false ? 'unsaid' : ''}"><span class="k">Amount</span>
        <div class="amt">${esc(c.amount)}</div><div class="words">${esc(c.amount_words)}${c.amount_note ? `, ${esc(c.amount_note)}` : ''}</div>
        ${c.amount_from_user === false && !['catalogue', 'bill', 'payment'].includes(c.amount_source) ? '<span class="flag">You didn\'t say this amount. Please check it</span>' : ''}</div>
      ${['refund', 'money_request'].includes(c.kind) ? '' : `<div class="crow"><span class="k">Counts against</span><span class="v">${esc(c.from_account)}</span></div>`}
      ${off.length ? `<details class="offers" ${c.kind === 'purchase' ? 'open' : ''}><summary>Star checked ${off.length} offer${off.length === 1 ? '' : 's'}</summary><ul>${off.map(o => `<li class="offer-line verdict-${esc(o.verdict)}"><span><b>${esc(o.merchant)}</b> ${esc(o.price)}</span><span class="verdict">${esc(verdictWord(o.verdict))}</span>${o.reasons?.length ? `<small>${esc(o.reasons[0])}</small>` : ''}</li>`).join('')}</ul></details>` : ''}
      ${trust.length ? `<div class="risk ${c.trust.level === 'BLOCK' ? 'BLOCK' : 'MEDIUM'}"><b>Seller check</b><ul>${trust.map(x => `<li>${esc(x)}</li>`).join('')}</ul></div>` : ''}
      ${c.risk_level !== 'LOW' ? `<div class="risk ${esc(c.risk_level)}"><b>${c.risk_level === 'MEDIUM' ? 'Please take a second look' : "For your safety, let's wait a little"}</b><ul>${c.reasons.map(x => `<li>${esc(x)}</li>`).join('')}</ul></div>` : ''}
      ${needVerify ? `<div class="verify"><div class="q" id="c-q">${esc(c.verify_question)}</div><div class="yesno">
        <button type="button" class="btn secondary" data-v="yes" aria-pressed="${state.verify === true}">${c.verify_kind === 'new_payee' ? 'Yes, my own idea' : 'Yes'}</button>
        <button type="button" class="btn secondary" data-v="no" aria-pressed="${state.verify === false}">${c.verify_kind === 'new_payee' ? 'No, someone asked me' : 'No, or not sure'}</button></div></div>` : ''}
      ${c.guardian_required ? (guardianPaired()
        ? `<div class="family-wait" role="status"><b>${icon('family')} Waiting for ${esc(guardianName())}</b><p>This one needs your family's OK. ${esc(guardianName())} sees this exact card on her own device.</p></div>`
        : `<div class="family-wait" role="status"><b>${icon('family')} This needs your family's OK</b><p>${esc(guardianName())} isn't set up as your family guardian yet, so this can't be sent. You can invite her now, or cancel this.</p><button type="button" class="btn secondary" id="c-invite">${icon('family')}Invite ${esc(guardianName())}</button></div>`) : ''}
      ${c.guardian_approved ? `<div class="family-ok" role="status">${icon('family')}<span>${esc(guardianName())} approved this exact card, signed on her device.</span></div>` : ''}
      ${cooling ? `<div class="cool" role="timer">Please wait <span id="c-cool-left">${coolWords(c.cooldown_left)}</span><small>A good moment to call your family and check.</small></div>` : ''}
      ${state.uvFail ? `<div class="uvhelp">${icon('fingerprint')}<div><b>Your fingerprint wasn't checked</b><small>A password manager signed without asking for your fingerprint, face or PIN, so Star refused it. Set it up again and choose <b>Windows Hello / this device</b> when asked where to save it.</small>
        <button type="button" class="btn secondary" id="c-repk">Set up fingerprint again</button></div></div>` : ''}
      <div class="next-step">${icon('transfer')}<span>${esc(c.next_step_words)}</span></div>
      ${c.verification?.status === 'match' ? (c.verification.independent ? `<div class="vcheck">${icon('shield')}<span>A separate model re-read your words and agrees.</span></div>`
        : `<div class="vcheck plain">${icon('alert')}<span>Star read your words again with the same offline rules that prepared this card. That's not a second opinion, so please check it yourself.</span></div>`) : ''}
      ${c.decision ? decisionPanel(c.decision) : ''}
      ${state.care ? '<details class="decision-panel"><summary>Calls to PayPal</summary><div class="ticker-host compact" data-compact="1"></div></details>' : '<div class="ticker-host compact" data-compact="1"></div>'}
    </div>
    <div class="cfoot">
      <button type="button" class="btn secondary" id="c-cancel">Cancel</button>
      <button type="button" class="btn primary${toQuestion ? ' to-question' : ''}" id="c-go" ${canGo || toQuestion ? '' : 'disabled'} ${toQuestion ? 'aria-describedby="c-q"' : ''}>${goIcon}${goLabel}</button>
    </div>
    <div class="signnote">Confirmation code ${esc(c.digest.slice(0, 8))}…${esc(c.digest.slice(-4))}. ${state.signMode === 'none' ? esc(NO_SIGNER) : state.signMode === 'device'
      ? "Demo: the fingerprint sensor is simulated. Your tap unlocks this browser's private key, which signs exactly this card. Star and its payment gateway each verify that signature."
      : 'Your device checks your fingerprint or face, then signs exactly this card. Star and its payment gateway each verify the signature.'}</div>`;
  renderTicker();
  $('#confirm').querySelectorAll('.yesno button').forEach(b => b.onclick = () => {
    state.verify = b.dataset.v === 'yes';
    if(state.verify === false && c.verify_kind === 'new_payee') return stopNewPayee(c);
    if(state.verify === false){ const t = "That's OK. If you're not sure, let's not send it yet. You can check with your family first."; push({role:'bot', text:t}); speak(t); }
    renderConfirm();
    (state.verify ? $('#c-go:not([disabled])') : $('#c-cancel'))?.focus();
  });
  $('#c-cancel').onclick = cancelCard;
  $('#c-go').onclick = () => {
    if(awaitingAnswer(state.card)) return showQuestion();     // never signs: it only takes her to the question
    stopSpeaking(); confirmCard();
  };
  $('#c-invite') && ($('#c-invite').onclick = () => { closeConfirm(); go('family'); });
  $('#c-play') && ($('#c-play').onclick = () => playClip(c.you_said_audio[0]));
  $('#c-repk') && ($('#c-repk').onclick = async () => {
    try{
      await removePasskey(); await refresh(); await enrolPasskey();
      state.uvFail = false; renderConfirm(); toast('Fingerprint set up again. Check the card and confirm.');
    }catch(e){ toast(e.name === 'NotAllowedError' ? 'Set-up was cancelled. Nothing changed.' : e.message); }
  });
}
const verdictWord = v => ({ok:'Looks fine', check:'Take a second look', family:"Needs your family's OK", stop:'Stopped'}[v] || v);
$('#confirm').addEventListener('cancel', e => { e.preventDefault(); if(state.card) cancelCard(); });
function closeConfirm(){ clearInterval(state.coolTimer); clearInterval(state.guardTimer); const d = $('#confirm'); if(d.open) d.close(); state.card = null; state.change = null; state.uvFail = false; }
// "No" to "is paying this new person your own idea?": stop it at once and point to the hotline, like a scam stop
async function stopNewPayee(c){
  try{
    const r = await api('/api/cancel', {session_id: state.sid, draft_id: c.draft_id, reason: 'new_payee_not_own_idea'});
    closeConfirm(); push({type:'blocked', role:'bot', text:r.reply, reasons:["You said paying this new person wasn't your own idea."], calls: state.calls.money_moving_this_task}); speak(r.speak, true);
    state.check = checksFromCard({...c, decision:r.decision}); renderSafety();
  }catch(e){ push({role:'bot', text:'Cancellation could not be confirmed. The payment is still waiting. Please try again.'}); renderConfirm(); }
}
async function cancelCard(){
  const c = state.card; if(!c || state.confirming) return;
  try{
    const r = await api('/api/cancel', {session_id: state.sid, draft_id: c.draft_id}); closeConfirm(); push({role:'bot', text:r.reply}); speak(r.speak);
    if(r.next_task) offerNext(r.next_task);
    state.check = checksFromCard({...c, decision:r.decision}); renderSafety();
  }catch(e){
    if(e.data?.code === 'ALREADY_SENT'){ push({role:'bot', text:e.message}); speak(e.message); return recoverPayment(c); }
    push({role:'bot', text:'Cancellation could not be confirmed. The payment is still waiting. Please try again.'}); renderConfirm();
  }
}
async function confirmCard(){
  const c = state.card; const btn = $('#c-go');
  if(!c || state.confirming) return;
  state.confirming = true; let submitted = false;
  btn.disabled = true; btn.textContent = 'Touch the sensor…';
  const verify_answer = c.verify_question ? state.verify : null;
  try{
    if(state.signMode === 'device' && !state.data?.passkey?.enrolled) await mockFingerprint($('#confirm'), `${c.amount} to ${c.payee_name}`);
    btn.textContent = 'Signing…';
    await ensureSigner();
    const ch = await api('/api/challenge', {session_id: state.sid, draft_id: c.draft_id, digest: c.digest, verify_answer});
    const assertion = await signPayload(ch.payload, ch.passkey);
    submitted = true; store.set('pendingPayment', {sid:state.sid, card:c});
    const r = await api('/api/confirm', {session_id: state.sid, draft_id: c.draft_id, digest: c.digest, assertion, verify_answer});
    store.set('pendingPayment', null); closeConfirm(); addTrace(r.trace);
    push({role:'bot', text:r.reply}); speak(r.speak);
    pushTracker(r.payment);
    state.check = checksFromCard({...c, decision:r.receipt?.verifiable?.decision || c.decision}); renderSafety();
    if(r.next_task) continueWith(r.next_task);      // multi-step request: go straight on to the next step
    refresh(); loadCalls();
    setTimeout(() => $('.tr-go')?.focus(), 50);
  }catch(e){
    if(submitted && (!e.status || e.status >= 500)){ await recoverPayment(c); return; }
    store.set('pendingPayment', null);
    const msg = e.name === 'NotAllowedError' ? 'The fingerprint check was cancelled or timed out. Nothing was sent.'
      : e.name === 'SecurityError' ? `Fingerprint signing needs a website name, not an address like ${location.hostname}. Please open http://localhost:${location.port || 80} and try again. Nothing was sent.`
      : e.message;
    addTrace(e.data?.trace);
    if(e.data?.decision){ state.check = checksFromCard({...c, decision:e.data.decision}); renderSafety(); }
    if(e.data?.code === 'UV_REQUIRED'){ state.uvFail = true; push({role:'bot', text:UV_HELP}); speak(UV_HELP); }
    else { push({role:'bot', text:msg}); speak(msg); }
    if(['NOT_AWAITING_AUTH', 'RECHECK_BLOCKED'].includes(e.data?.code) || String(e.data?.code || '').startsWith('GW_')) closeConfirm(); else renderConfirm();
  }finally{ state.confirming = false; }
}
// The answer was lost after the signed card went out. Never offer to pay again: ask about this same payment.
async function recoverPayment(c){
  try{
    const r = await api(`/api/transaction/${encodeURIComponent(state.sid)}/${encodeURIComponent(c.draft_id)}`);
    if(r.payment){
      store.set('pendingPayment', null); closeConfirm();
      push({role:'bot', text:r.reply}); pushTracker(r.payment); await refresh(); return;
    }
  }catch{}
  push({role:'bot', text:"I can't confirm that went through yet. Please don't pay again. Check its status first."});
  renderConfirm();
  const btn = $('#c-go'); btn.disabled = false; btn.textContent = 'Check payment status';
  btn.onclick = () => recoverPayment(c);
  $('#c-cancel').disabled = true;
}

async function removePasskey(){
  const ch = await api('/api/passkey/remove/challenge', {session_id:state.sid});
  const assertion = await signPayload(ch.payload, ch.passkey);
  await api('/api/passkey/remove', {session_id:state.sid, assertion});
}

// ---------------------------------------------------------------- Signing: passkey (real fingerprint / face) first
// A passkey lives in the device's secure hardware and is unlocked by the OS's own biometric prompt (Touch ID,
// Face ID, Windows Hello). We ask for userVerification "required" and sign SHA-256 of the exact payment payload.
// Devices without one fall back to a non-extractable browser key (no biometric; the UI says so).
const NO_SIGNER = "This browser can't check your fingerprint or face, so it can't sign payments. On iPhone open the link in Safari, " +
  "on Android or a computer in Chrome (not inside another app), on a device with a screen lock. Nothing was sent.";
const UV_HELP = "Your device signed without checking your fingerprint, face or PIN, so Star refused it. Nothing was sent. " +
  "This happens when a password manager holds the passkey. Tap “Set up fingerprint again”, and when your computer asks where to save it, choose Windows Hello / this device.";
const b64u = buf => b64(buf).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
const unb64u = s => Uint8Array.from(atob(s.replace(/-/g, '+').replace(/_/g, '/') + '==='.slice((s.length + 3) % 4)), ch => ch.charCodeAt(0));
const IP_HOST = /^(\d{1,3}(\.\d{1,3}){3}|\[.*\])$/;
// 'device'      DEFAULT for the demo: a simulated fingerprint screen, then this browser's non-extractable key signs
//               the exact card (the signature and the gateway's checks are real; the fingerprint is labelled as simulated)
// 'passkey'     opt-in (Settings): this device's real Touch ID / Face ID / Windows Hello
// 'passkey-any' opt-in on a device without a sensor: the browser offers a phone (QR code) or a security key
// 'none'        this browser can't sign at all
const browserKeyAllowed = () => state.health.browser_key !== false;
const passkeyMode = () => state.signMode === 'passkey' || state.signMode === 'passkey-any';
const realBioOn = () => !!store.get('realBio', false);
const canDeviceSign = () => !!(window.crypto?.subtle && window.indexedDB);
async function passkeyCapability(){
  if(IP_HOST.test(location.hostname) || !window.PublicKeyCredential || !window.isSecureContext) return null;
  try{ return await PublicKeyCredential.isUserVerifyingPlatformAuthenticatorAvailable() ? 'passkey' : 'passkey-any'; }catch{ return null; }
}
async function detectSignMode(){
  const real = await passkeyCapability();
  if(real && (realBioOn() || state.data?.passkey?.enrolled || !browserKeyAllowed())) return real;
  return browserKeyAllowed() && canDeviceSign() ? 'device' : (real || 'none');
}
// A signed action outside the payment card (invite, freeze lift, protection): same sensor, its own small dialog.
async function signedAction(what){
  if(state.signMode !== 'device' || state.data?.passkey?.enrolled) return;
  const dlg = $('#confirm');
  dlg.innerHTML = `<div class="cbody"><h2 id="c-title">Confirm on this device</h2></div>`; dlg.showModal();
  try{ await mockFingerprint(dlg, what); }
  finally{ dlg.close(); }
}
async function ensureSigner(){
  if(state.signMode === 'none') throw new Error(NO_SIGNER);
  if(passkeyMode()){
    if(!state.data?.passkey?.enrolled){ toast('First time: your device will ask you to save a passkey for Star.'); await enrolPasskey(); }
  }else if(!state.data?.passkey?.enrolled){
    await ensureDeviceKey();
  }
}
async function enrolPasskey(){
  const o = await api('/api/passkey/options', {session_id: state.sid});
  const cred = await navigator.credentials.create({publicKey: {
    challenge: unb64u(o.challenge), rp: {id: o.rp_id, name: o.rp_name},
    user: {id: unb64u(o.user_id), name: o.user_name, displayName: o.display_name},
    pubKeyCredParams: [{type: 'public-key', alg: -7}, {type: 'public-key', alg: -257}],   // ES256 (Mac, phones) or RS256 (some Windows Hello)
    timeout: 60000, attestation: 'none',
    // A built-in sensor when there is one; otherwise let the browser offer a phone (QR code) or a security key
    authenticatorSelection: {...(state.signMode === 'passkey' ? {authenticatorAttachment: 'platform'} : {}),
                             userVerification: 'required', residentKey: 'discouraged'},
    excludeCredentials: o.exclude.map(id => ({type: 'public-key', id: unb64u(id)})),
  }});
  const r = await api('/api/passkey/register', {session_id: state.sid, credential_id: b64u(cred.rawId),
    client_data_json: b64u(cred.response.clientDataJSON), attestation_object: b64u(cred.response.attestationObject)});
  addTrace([{kind:'sign', label:`Passkey ${r.key_id} enrolled: your device's fingerprint / face check now signs your payments`}]);
  await refresh(); return r;
}
async function signPayload(payload, passkey){
  if(passkey?.enrolled && !await passkeyCapability())
    throw new Error('Your account signs payments with the real fingerprint or face check on the device where you set it up. ' +
      'Please use that device, or turn off "Real fingerprint or face" in Settings for this demo.');
  if(passkey?.enrolled){
    const challenge = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(payload));
    const a = await navigator.credentials.get({publicKey: {challenge, rpId: location.hostname, userVerification: 'required', timeout: 60000,
      allowCredentials: passkey.credential_ids.map(id => ({type: 'public-key', id: unb64u(id)}))}});
    addTrace([{kind:'sign', label:'Your device checked your fingerprint / face and signed the exact card (passkey, ES256)', detail: JSON.parse(payload)}]);
    return {type: 'webauthn', payload, credential_id: b64u(a.rawId), authenticator_data: b64u(a.response.authenticatorData),
      client_data_json: b64u(a.response.clientDataJSON), signature: b64u(a.response.signature)};
  }
  const keyId = await ensureDeviceKey();
  const signature = await deviceKeyStore.sign(payload);
  addTrace([{kind:'sign', label:`Simulated fingerprint approved; browser key ${keyId} signed the exact card (ECDSA P-256)`, detail: JSON.parse(payload)}]);
  return {payload, signature};
}
const deviceKeyStore = deviceKeys('star-device');
async function ensureDeviceKey(){
  const r = await api('/api/key', {session_id: state.sid, public_key: await deviceKeyStore.ensure()});
  state.keyId = r.key_id; return r.key_id;
}

// ---------------------------------------------------------------- Agent trace
function addTrace(items){
  const list = $('#tlist');
  (items || []).forEach(it => {
    const el = document.createElement('div');
    el.className = 'ti' + (it.kind === 'user' ? ' turn' : '');
    el.innerHTML = `<div class="k k-${esc(it.kind)}">${esc(KIND[it.kind] || it.kind)}</div><div class="l">${esc(it.label)}</div>` +
      (it.detail !== undefined ? `<details><summary>Details</summary><pre>${esc(JSON.stringify(it.detail, null, 2))}</pre></details>` : '');
    list.appendChild(el);
  });
  list.scrollTop = list.scrollHeight;
}

// ---------------------------------------------------------------- Shop
const SHOP_SAY = {bp_monitor:'Get me a blood pressure monitor', groceries:'Order the weekly groceries box', gift_card:'Buy a $500 gift card'};
async function shopPage(m){
  m.innerHTML = head('Agentic shopping', 'Shop', 'Star looks through this small demo catalogue, checks each seller with fixed rules, and prepares the purchase. All the sellers are fictional.') + '<div id="shop" aria-live="polite"><p class="muted">Loading…</p></div>';
  const draw = async () => {
    try{
      const {offers} = await api(`/api/catalogue?session_id=${encodeURIComponent(state.sid)}`);
      const el = $('#shop'); if(!el) return;
      el.innerHTML = `<div class="offer-grid">${offers.map(o => `
        <article class="offer verdict-${esc(o.verdict)}">
          <div class="offer-what"><header><h2>${esc(o.name)}</h2><span class="verdict">${esc(verdictWord(o.verdict))}</span></header>
            <p class="offer-merchant">${esc(o.merchant)}</p>
            <p class="muted small offer-listing">${esc(o.listing)}</p></div>
          <p class="offer-price">${esc(o.price)} <small>usually about ${esc(o.usual_price)}</small></p>
          ${o.verdict === 'stop' ? '' : `<button class="btn secondary" type="button" data-say="${esc(sayFor(o))}">${icon('spark')}Ask Star to get this</button>`}
          ${o.suspicious_removed ? `<p class="flag-line">${icon('alert')}This listing tried to give Star instructions. They were removed: seller text is data, never commands.</p>` : ''}
          ${o.reasons.length ? `<ul class="why-list">${o.reasons.map(r => `<li>${esc(r)}</li>`).join('')}</ul>` : ''}
          ${o.verdict === 'stop' ? `<p class="stop-line"><b class="scam-seal">Stopped</b><span>Star won't buy this.</span></p>` : ''}
        </article>`).join('')}</div>`;
      el.querySelectorAll('[data-say]').forEach(b => b.onclick = () => send(b.dataset.say));
    }catch(e){ loadFailed($('#shop'), 'the shop', draw); }
  };
  draw();
}
function sayFor(o){
  if(o.category === 'groceries') return SHOP_SAY.groceries;
  if(o.category === 'gift_card') return SHOP_SAY.gift_card;
  return `Get me a blood pressure monitor from ${o.merchant}`;
}

// ---------------------------------------------------------------- Bills
async function billsPage(m){
  m.innerHTML = head('Bills and invoices', 'Bills', 'Bills arrive as PayPal invoices. Star reads them, warns you about anything odd, and never takes instructions from them.') + `
    <section class="card pad" aria-labelledby="inv-h"><div class="section-top"><h2 id="inv-h">Invoices</h2>${isSim() ? `<button class="btn secondary small" id="inv-arrive" type="button">Demo: a new invoice arrives</button>` : ''}</div><div id="inv-list" aria-live="polite"><p class="muted">Loading…</p></div></section>
    <div class="two-col page-gap">
      <section class="card pad" aria-labelledby="ask-h"><h2 id="ask-h">Money coming back</h2>
        <div class="stack"><button class="btn secondary" type="button" data-say="Ask David to pay me back 20">${icon('coin')}Ask David to pay me back $20</button>
        <button class="btn secondary" type="button" data-say="I want my money back">${icon('refresh')}I want my money back</button></div>
        <p class="muted small">You can say these in your own words too. Each one still needs your signature.</p></section>
      <section class="card pad" aria-labelledby="dis-h"><h2 id="dis-h">Disputes</h2><div id="dis-list"><p class="muted">Loading…</p></div></section>
    </div>`;
  const draw = async (fresh = null) => {
    const {invoices, mode_label} = await api(`/api/invoices?session_id=${encodeURIComponent(state.sid)}`);
    const el = $('#inv-list'); if(!el) return;
    el.innerHTML = invoices.length ? `<ul class="inv-list">${invoices.map(i => `<li class="inv ${i.known_biller ? '' : 'inv-unknown'} ${fresh && fresh === i.number ? 'inv-new' : ''}">
        <div class="inv-due"><small>Due</small><b>${esc(dueDate(i.due))}</b></div>
        <div class="inv-main">${fresh && fresh === i.number ? '<span class="flag inv-fresh">Just arrived</span>' : ''}<b>${esc(i.title)}</b><small>${esc(i.from)} · ${esc(i.number)}</small>
          ${i.known_biller ? '' : `<span class="flag">${icon('alert')}Someone you've never paid</span>`}</div>
        <div class="inv-amt">${esc(i.amount)}</div><div class="inv-state">${ppBadge(i.status === 'SENT' ? 'UNPAID' : i.status, mode_label, i.status === 'PAID')}</div>
        ${openInvoice(i) ? `<div class="inv-acts"><button class="btn secondary small" type="button" data-say="What's this bill?">What's this?</button>
          <button class="btn primary small" type="button" data-say="Pay the ${esc(i.from)} bill">Pay with Star</button></div>` : ''}</li>`).join('')}</ul>` : '<p class="muted">No invoices.</p>';
    el.querySelectorAll('[data-say]').forEach(b => b.onclick = () => send(b.dataset.say));
  };
  m.querySelectorAll('.stack [data-say]').forEach(b => b.onclick = () => send(b.dataset.say));
  $('#inv-arrive') && ($('#inv-arrive').onclick = async () => {
    try{
      const r = await api('/api/demo/invoice-arrives', {session_id: state.sid});
      await draw(r.invoice.number); await refresh();                   // the row itself says it just arrived; nothing floats over it
      $('.inv-new')?.scrollIntoView({block:'nearest'});
      announce(`New invoice from ${r.invoice.from}.`);
    }catch(e){ toast(e.message); }
  });
  const bills = () => draw().catch(() => loadFailed($('#inv-list'), 'your bills', bills));
  bills();
  const disputes = () => api(`/api/disputes?session_id=${encodeURIComponent(state.sid)}`).then(r => {
    const el = $('#dis-list'); if(!el) return;
    el.innerHTML = !r.available ? '<p class="muted">PayPal did not answer this time, so disputes can\'t be shown. That doesn\'t mean there are none.</p>'
      : r.disputes.length ? `<ul class="pay-list">${r.disputes.map(x => `<li class="pay-row"><span class="pay-main"><b>${esc((s => s[0].toUpperCase() + s.slice(1))(String(x.reason || 'Dispute').replaceAll('_', ' ').toLowerCase()))}</b><small><code>${esc(x.dispute_id)}</code></small></span>${ppBadge(x.status, r.mode_label)}</li>`).join('')}</ul><p class="muted small">${esc(r.note || '')}</p>`
      : `<p class="muted">No open disputes. ${modeTag(r.mode_label)}</p><p class="muted small">${esc(r.note || '')}</p>`;
  }).catch(() => loadFailed($('#dis-list'), 'your disputes', disputes));
  disputes();
}

// ---------------------------------------------------------------- Family
function familyPage(m){
  const d = state.data, g = d.guardian, f = d.frozen;
  const people = d.payees.filter(p => p.type === 'person'), billers = d.payees.filter(p => p.type !== 'person');
  const card = p => `<div class="card contact"><span class="avatar" aria-hidden="true">${esc(p.name.split(' ').map(w => w[0]).join('').slice(0, 2))}</span><div><b>${esc(p.name)}</b><small>${esc(p.relation)} · ${esc(p.masked_account)}</small></div></div>`;
  const inv = state.invite;
  m.innerHTML = head('People you trust', 'Family', `Star can only send money to people saved here. A risky payment waits for ${esc(guardianName())}, on her own device.`) + `
    <div class="two-col">
      <section class="card pad" aria-labelledby="gd-h"><h2 id="gd-h">Family guardian</h2>
        <p class="status-line ${g.status === 'paired' ? 'ok' : ''}">${icon(g.status === 'paired' ? 'shield' : 'family')}<span><b>${esc(g.name)}</b>, your ${esc(g.relation)}: ${g.status === 'paired' ? 'paired' : 'not paired yet'}</span></p>
        ${g.status === 'paired' ? `<p class="muted small">Her device key is <code>${esc(g.key_id)}</code>. Risky payments wait for her signature, and she sees your alerts.</p>
          ${g.removal ? `<p class="warn-text">Removal starts ${esc(clock(g.removal.effective_at))}. The wait means nobody can rush this.</p>` : `<button class="btn secondary small" id="g-remove" type="button">Remove guardian (after a wait)</button>`}`
        : `<p>When ${esc(guardianName())} is paired, new people and large or unusual payments wait for her. She can't start a payment, and can't unlock what the rules stopped.</p>
           <button class="btn primary" id="g-invite" type="button">${icon('fingerprint')}Invite ${esc(guardianName())}</button>`}
        ${inv ? `<div class="invite-box" role="status"><b>Send ${esc(guardianName())} this link</b><p class="muted small">It works once and expires in 10 minutes. Open it on another device or another browser window.</p>
          <input id="g-link" readonly value="${esc(location.origin + inv.link)}" aria-label="Guardian invite link">
          <div class="row-actions"><button class="btn secondary small" id="g-copy" type="button">Copy link</button><a class="btn secondary small" href="${esc(inv.link)}" target="_blank" rel="noopener">Open in a new window</a></div></div>` : ''}
        <p class="muted small">Alerts appear on her page. This demo doesn't send real texts or emails.</p>
      </section>
      <section class="card pad ${f.on ? 'frozen-card' : ''}" aria-labelledby="fz-h"><h2 id="fz-h">Freeze payments</h2>
        ${f.on ? `<p class="status-line bad">${icon('freeze')}Frozen by ${esc(f.by)}. Nothing can be sent.</p>
          <p class="muted small">Lifting takes your signature${g.status === 'paired' ? ` and ${esc(guardianName())}'s` : ''}. Refunds still work during a freeze.
            ${f.lift?.owner ? `You've signed. ${f.lift?.guardian ? '' : `Waiting for ${esc(guardianName())}.`}` : ''}${f.lift?.guardian ? ` ${esc(guardianName())} has signed.` : ''}</p>
          ${f.lift?.owner ? '' : `<button class="btn secondary" id="f-lift" type="button">${icon('fingerprint')}Sign to lift the freeze</button>`}`
        : `<p>If something feels wrong, stop everything at once. You don't need to explain, and it starts immediately.</p>
           <button class="btn danger big" id="f-on" type="button">${icon('freeze')}Freeze all payments now</button>`}
      </section>
    </div>
    <section class="card pad page-gap" aria-labelledby="al-h"><h2 id="al-h">Alerts</h2>${d.alerts.length ? `<ul class="alert-list">${d.alerts.slice(0, 8).map(a => `<li class="alert-item level-${esc(a.level)}"><span class="when">${esc(clock(a.at))}</span><span>${a.kind === 'approval' ? `Asked ${esc(guardianName())}: ` : ''}${esc(a.text)}</span></li>`).join('')}</ul>` : '<p class="muted">No alerts yet.</p>'}</section>
    <h2 class="sub-head">People</h2><div class="contacts">${people.map(card).join('')}</div>
    <p class="muted small add-person"><button class="text-btn" type="button" id="add-person">Add a new person with Star</button></p>
    <h2 class="sub-head">Who sends you bills</h2><div class="contacts">${billers.map(card).join('')}</div>`;
  const after = async () => { await refresh(); renderPage(); };
  $('#g-invite') && ($('#g-invite').onclick = async () => {
    try{
      await signedAction(`Invite ${g.name} as your family guardian`); await ensureSigner();
      const ch = await api('/api/guardian/invite/challenge', {session_id: state.sid});
      const assertion = await signPayload(ch.payload, ch.passkey);
      state.invite = await api('/api/guardian/invite', {session_id: state.sid, assertion});
      await after(); $('#g-link')?.focus(); $('#g-link')?.select();
    }catch(e){ toast(e.name === 'NotAllowedError' ? 'Cancelled. Nothing changed.' : e.message); }
  });
  $('#g-copy') && ($('#g-copy').onclick = async () => {
    try{ await navigator.clipboard.writeText($('#g-link').value); toast('Link copied.'); }
    catch{ $('#g-link').select(); toast('Press Ctrl+C or long-press to copy.'); }
  });
  $('#g-remove') && ($('#g-remove').onclick = async () => {
    try{
      await signedAction(`Remove ${g.name} as guardian, after a wait`);
      const ch = await api('/api/guardian/remove/challenge', {session_id: state.sid});
      const assertion = await signPayload(ch.payload, ch.passkey);
      await api('/api/guardian/remove', {session_id: state.sid, assertion});
      toast('Signed. It only starts after a wait, and your family is told.'); await after();
    }catch(e){ toast(e.name === 'NotAllowedError' ? 'Cancelled. Nothing changed.' : e.message); }
  });
  $('#f-on') && ($('#f-on').onclick = async () => {
    try{ await api('/api/freeze', {session_id: state.sid}); toast('All payments are frozen.'); speak('All payments are frozen.'); await after(); $('#f-lift')?.focus(); }catch(e){ toast(e.message); }
  });
  $('#f-lift') && ($('#f-lift').onclick = async () => {
    try{
      await signedAction('Lift the freeze on payments'); await ensureSigner();
      const ch = await api('/api/freeze/lift/challenge', {session_id: state.sid});
      const assertion = await signPayload(ch.payload, ch.passkey);
      const r = await api('/api/freeze/lift', {session_id: state.sid, assertion});
      toast(r.frozen?.on ? `Signed. Waiting for ${guardianName()} too.` : 'The freeze is lifted.'); await after();
    }catch(e){ toast(e.name === 'NotAllowedError' ? 'Cancelled. Nothing changed.' : e.message); }
  });
  $('#add-person').onclick = () => { go(assistantPage()); const t = $('#text'); if(t){ t.value = 'Add a new person named '; t.focus(); } };
}

// ---------------------------------------------------------------- Activity & audit
let clip = null;
function playClip(sha){
  stopSpeaking(); try{ clip?.pause(); }catch{}
  clip = new Audio(`/api/voice/${encodeURIComponent(state.sid)}/${sha}`);
  clip.play().catch(() => toast('This recording is no longer on the demo server. It restarts on every update.'));
}
const AUDIT_WORDS = {'user.utterance':'You said something', 'voice.recorded':'A voice recording was kept', 'draft.created':'Star prepared a card', 'draft.frozen':'A card was frozen: it differed from your words',
  'draft.cancelled':'A card was cancelled', 'contract.decision':'The safety checks ran', 'trust.check':'The seller or bill was checked', 'auth.challenge_issued':'The card was ready to sign', 'auth.granted':'Your signature was verified',
  'auth.rejected':'A signature was refused', 'gateway.posted':'The gateway accepted the signed card', 'gateway.rejected':'The gateway refused it', 'commit.executed':'The payment was handed over', 'commit.rejected':'The payment was refused before it went out',
  'payment.state':'The payment changed state', 'wallet.frozen':'Payments were frozen', 'wallet.unfrozen':'The freeze was lifted', 'guardian.invited':'A family guardian was invited', 'guardian.paired':'A family guardian was paired',
  'guardian.approved':'Your family approved a card', 'guardian.denied':'Your family said no', 'guardian.alert':'Your family was alerted', 'guardian.replace_refused':'A guardian swap was refused', 'demo.world_reset':'The demo was reset', 'ui.care_mode':'Care mode changed',
  'agent.reply':'Star answered', 'llm.call':'The AI model was asked', 'tool.call':'Star used one of its tools', 'plan.model':'The AI model split the request into steps',
  'plan.reference_resolved':'Star worked out an amount from an earlier payment', 'scam.intercepted':'Star stopped words that looked like a scam', 'invoice.sanitized':"A bill's text was cleaned before Star read it",
  'limit.lowered':'A spending limit was lowered', 'settings.protection_raised':'Protection was raised', 'settings.protection_lowering_scheduled':'Lowering protection was asked for: it waits out a delay',
  'change.requested':'An account change was asked for', 'change.cancelled':'An account change was cancelled', 'change.applied':'An account change was made',
  'device.key_registered':"This device's signing key was registered", 'device.passkey_enrolled':'A passkey was added', 'device.passkey_removed':'A passkey was removed',
  'paypal.call':'Star called {pp}', 'paypal.webhook':'An update came from {pp}, its signature checked', 'paypal.webhook_rejected':'An update said to be from {pp} was refused',
  'guardian.removal_scheduled':'Removing the family guardian was asked for: it waits out a delay', 'guardian.alert_cleared':'Your family guardian cleared a scam alert'};
// Plain words for every audit record. In the simulator, the simulator is never called PayPal.
const auditWords = ev => (AUDIT_WORDS[ev] || 'A step was recorded').replace('{pp}', mode() === 'SIMULATED' ? 'the PayPal simulator' : 'PayPal');
// ---------------------------------------------------------------- Dashboard: AG Studio over the same activity rows
// It runs in a same-origin frame with a CSP of its own (DASHBOARD_CSP in app/security.py), so the app keeps its strict
// style policy. The frame only reads this session's activity; nothing on it can move money.
function dashboardPage(m){
  m.innerHTML = head('For the family', 'Dashboard', 'What she sent, whether PayPal confirmed it, and what Star stopped, from the same records as Activity &amp; audit. Edit dashboard rearranges it; your layout is kept on this browser.') +
    '<iframe class="dash-frame-el" src="/dashboard-frame" title="Dashboard: payments and stops, built with AG Studio"></iframe>';
}

async function activityPage(m){
  m.innerHTML = head('Trust and transparency', 'Activity &amp; audit', 'Every payment, and every payment that was stopped, with who stopped it and why. Open a row for its whole story.') + `
    <section class="card pad" aria-labelledby="ac-h"><h2 id="ac-h" class="sr">Activity table</h2><div id="grid-mount"><p class="muted">Loading the table…</p></div></section>
    <section class="card pad page-gap" aria-labelledby="ch-h"><div class="section-top"><h2 id="ch-h">Audit chain</h2><span id="integrity" class="status-line">Checking…</span></div>
      <p class="muted small">Every step is appended to a hash chain: each record carries the hash of the one before, so edits are detectable. Voice recordings, drafts, checks, PayPal calls and your signature are all in it.</p>
      <div class="row-actions"><button class="btn secondary small" id="a-refresh" type="button">${icon('refresh')}Refresh</button><button class="btn secondary small" id="a-export" type="button">${icon('download')}Export JSON</button></div>
      <div id="voices"></div><div id="entries" class="entry-list"></div></section>`;
  const load = async () => {
    const a = await api(`/api/audit/session/${encodeURIComponent(state.sid)}`);
    const el = $('#integrity'); if(!el) return;
    el.className = 'status-line ' + (a.integrity.ok ? 'ok' : 'bad');
    el.textContent = a.integrity.ok ? `✓ Chain intact, ${a.integrity.records} records` : `✕ Chain broken at line ${a.integrity.broken_at_line}`;
    const clips = a.entries.filter(e => e.event === 'voice.recorded');
    $('#voices').innerHTML = clips.length ? `<div class="voices"><h3>${icon('mic')}Voice recordings (${clips.length})</h3>${clips.slice().reverse().map(e => `
      <div class="voice-row"><audio controls preload="none" src="/api/voice/${encodeURIComponent(state.sid)}/${esc(e.data.audio_sha256)}"></audio>
        <span>“${esc(e.data.transcript)}”<small>${new Date(e.ts * 1000).toLocaleTimeString()} · ${esc(String(e.data.seconds))} s · SHA-256 ${esc(e.data.audio_sha256.slice(0, 12))}…</small></span></div>`).join('')}
      <p class="muted small">Each recording's SHA-256 is in the hash chain, and cards made from it carry the same hash, so your signature covers it. Demo recordings are deleted when the server restarts.</p></div>` : '';
    const entry = e => `<div class="audit-entry"><b>${esc(auditWords(e.event))}</b>
      <div class="muted small">${new Date(e.ts * 1000).toLocaleTimeString()} · <code>${esc(e.hash.slice(0, 16))}…</code></div>
      <details><summary>Details</summary><pre>${esc(JSON.stringify(e.data, null, 2))}</pre></details></div>`;
    const newest = a.entries.slice().reverse(), main = newest.filter(e => !/^(llm|tool|agent)\./.test(e.event));
    $('#entries').innerHTML = newest.length ? `${main.slice(0, 10).map(entry).join('')}
      <details class="audit-all"><summary>Show the newest ${Math.min(newest.length, 40)} records, with every model and tool step</summary>${newest.slice(0, 40).map(entry).join('')}</details>` : '<p class="muted small">No activity in this session yet.</p>';
    $('#a-export').onclick = () => download(`audit-${state.sid}.json`, a);
  };
  const audit = () => load().catch(() => { const el = $('#integrity'); if(el){ el.className = 'status-line'; el.textContent = "Can't check the chain right now"; } loadFailed($('#entries'), 'the audit records', audit); });
  $('#a-refresh').onclick = audit; audit();
  const open = async () => {
    try{
      const mount = $('#grid-mount');
      const grid = await mountGrid(mount, {role:'owner', care:state.care, currency:state.data?.currency, getMode:mode, onRow:showRowDetail});
      if(state.page !== 'activity' || !$('#grid-mount')){ grid.destroy(); return; }
      state.grid = grid;
      grid.update((await api(`/api/activity?session_id=${encodeURIComponent(state.sid)}`)).rows);
      const poll = async () => { try{ const r = await api(`/api/activity?session_id=${encodeURIComponent(state.sid)}`); state.grid?.update(r.rows); }catch{} };
      clearInterval(state.gridTimer); state.gridTimer = setInterval(poll, 3000);
    }catch(e){
      if(state.grid){ state.grid.destroy(); state.grid = null; }
      loadFailed($('#grid-mount'), 'your activity', open);
    }
  };
  await open();
}
async function showRowDetail(row){
  const d = $('#detail');
  const facts = (rows) => `<dl class="facts">${rows.filter(r => r[1]).map(([k, v, code]) => `<dt>${esc(k)}</dt><dd>${code ? `<code>${esc(v)}</code>` : esc(v)}</dd>`).join('')}</dl>`;
  let body = `${facts([['To / from', `${row.to}${row.handle ? ` (${row.handle})` : ''}`], ['Amount', row.amount_cents ? cash(Math.abs(row.amount_cents)) : ''], ['Stopped by', row.stopped_by], ['Why', row.why],
      ['Rule codes', (row.codes || []).join(', ')], ['Exact card', row.digest, true], ['Audit entry', row.audit_hash, true]])}`;
  let pay = null;
  if(!row.row_id.startsWith('stop_')){
    try{ pay = (await api(`/api/payments/${encodeURIComponent(state.sid)}/${encodeURIComponent(row.row_id)}`)).payment; }catch{}
  }
  if(pay){
    const a = pay.receipt?.authorization || {};
    body = `${facts([['To', `${pay.to}${pay.handle ? ` (${pay.handle})` : ''}`], ['Amount', pay.amount], ['Verified by', pay.verified_by, true], ['PayPal request key', pay.idempotency_key, true],
        ['Signed with', a.method === 'passkey' ? 'Passkey (fingerprint or face)' : 'Browser key after a simulated fingerprint'], ['Exact card', pay.digest, true], ['Audit entry', row.audit_hash, true]])}
      <h3>Timeline</h3><ol class="timeline">${pay.evidence.map(e => `<li><span class="when">${esc(clock(e.at))}</span><div>${ppBadge(e.state, pay.mode_label, !!e.verified_by)}${e.note ? `<small>${esc(e.note)}</small>` : ''}${e.source ? `<small>Source: ${esc(e.source)}${e.event_type ? `, ${esc(e.event_type)}` : ''}</small>` : ''}</div></li>`).join('')}</ol>
      ${pay.event_ids?.length ? `<h3>Verified webhook events</h3><ul class="plain-list">${pay.event_ids.map(x => `<li><code>${esc(x)}</code></li>`).join('')}</ul>` : ''}
      <div class="row-actions"><button class="btn secondary small" id="d-receipt" type="button">${icon('download')}Signed receipt</button></div>
      <p class="muted small">Anyone can check a receipt offline: <code>python scripts/verify_receipt.py receipt.json</code>. It proves this signature produced this PayPal request; PayPal itself remains the authority on the money.</p>`;
  }
  d.innerHTML = `<div class="side-body"><header><h2 id="d-title">${esc(row.type)}</h2><button class="btn secondary small" id="d-close" type="button">Close</button></header>
    <p>${ppBadge(row.status, mode(), row.verified)}</p>${body}</div>`;
  d.showModal();
  $('#d-close').onclick = () => d.close();
  $('#d-receipt') && ($('#d-receipt').onclick = () => download(`receipt-${pay.receipt.txn_id}.json`, pay.receipt));
}

// ---------------------------------------------------------------- Security lab
function labPage(m){
  m.innerHTML = head('Trust and transparency', 'Security lab', 'Real attacks against the real kernel, each in a fresh isolated copy. Your session is never touched.') + `
    <section class="card pad">
      <div id="lab-out" class="lab-hero" aria-live="polite"><p class="muted">Running the attacks now…</p></div>
      <div class="row-actions"><button class="btn primary" id="lab-run" type="button">${icon('lab')}Run all attacks again</button>
        <button class="btn secondary" id="lab-mistake" type="button">${icon('alert')}Simulate a model mistake</button></div>
      <p class="muted small">"Simulate a model mistake" happens live in your chat: "Send 50 to my daughter" goes to a deliberately faulty model that drafts $500, and you watch the real verifier, kernel and gateway stop it.</p>
      <h2 class="sub-head">The way to PayPal</h2>
      <ol class="rail" aria-label="Every step between her words and PayPal. Stamped steps are fixed code or her own device; the model can't reach them.">
        ${[['You speak'], ['Agent drafts'], ['Verifier re-reads your words', 1], ['Evidence and rules', 1], ['You check the card'], ['Your device signs', 1], ['Kernel verifies', 1], ['Gateway re-verifies', 1], ['PayPal']]
          .map(([t, edge]) => `<li class="${edge ? 'edge' : ''}"><span>${t}</span>${edge ? '<small>Checked</small>' : ''}</li>`).join('')}
      </ol>
      <p class="muted small">The model works only before the first step marked Checked. It has no tool that can sign, authorize or pay, and the only code path to a PayPal money call requires your device's signature.</p></section>
    <div class="two-col page-gap">
      <section class="card pad"><h2>Failure drill</h2><p>The network dies just after PayPal processed a payout, and she clicks again. Runs the real gateway on an isolated simulator.</p>
        <button class="btn primary" id="drill-run" type="button" ${isSim() ? '' : 'disabled'}>Run the drill</button>
        ${isSim() ? '' : '<p class="muted small">Only runs in simulator mode.</p>'}<div id="drill-out" aria-live="polite"></div></section>
      <section class="card pad"><h2>The same fake invoice, two agents</h2><p class="muted small">Scripted illustration, not a test of any real product.</p>
        <button class="btn primary" id="naive-run" type="button" ${isSim() ? '' : 'disabled'}>Show the comparison</button>
        ${isSim() ? '' : '<p class="muted small">Only runs in simulator mode.</p>'}<div id="naive-out" aria-live="polite"></div></section>
    </div>
    <section class="card pad page-gap contract-showcase">
      <p class="eyebrow">Intent, plan, authorization, execution</p>
      <h2>Transaction safety contract</h2>
      <p>Four enforceable promises: follow your intent, use completed dependencies, execute exactly what you sign, and recheck conditions before anything goes out.</p>
      <p class="muted small">Fault injection runs against a fresh synthetic wallet. No customer session or real model is used.</p>
      <button class="btn primary" id="contract-run" type="button">Run risk demonstration</button>
      <div id="contract-out" aria-live="polite"></div>
    </section>`;
  $('#lab-mistake').onclick = () => send('Send 50 to my daughter', '/api/demo/model-mistake', false, false, true);
  // The headline of this page is a result, so it is produced here, on this server, when the page opens. Nothing on it is a stored number.
  const showLab = results => {
    const el = $('#lab-out'); if(!el) return;
    const ok = results.filter(r => r.blocked).length;
    el.innerHTML = `<div class="lab-score ${ok === results.length ? '' : 'bad'}"><b>${ok} of ${results.length}</b> attacks blocked</div>
      <p class="muted">Run just now on this server, each in a fresh isolated copy. A fixed set of checks, not a penetration test. See docs/SECURITY.md for what is simulated.</p>
      <details class="lab-more" ${ok === results.length ? '' : 'open'}><summary>Show each attack</summary><div class="lab-list">${results.map(r =>
      `<div class="lab-item ${r.blocked ? 'pass' : 'fail'}"><span class="s" aria-hidden="true">${r.blocked ? '✓' : '✕'}</span><div><b>${esc(r.name)}</b> <code>${esc(r.code)}</code><small>${esc(r.detail)}</small></div></div>`).join('')}</div></details>`;
  };
  const runLab = async () => {
    const el = $('#lab-out'); if(el && !state.labResults) el.innerHTML = '<p class="muted">Running the attacks now…</p>';
    try{
      const {results} = await api('/api/lab', {});
      state.labResults = results; showLab(results);
      const ok = results.filter(r => r.blocked).length;
      addTrace([{kind:'guard', label:`Security lab: ${ok} of ${results.length} attacks blocked`, detail: results}]);
    }catch(e){ const out = $('#lab-out'); if(out) out.innerHTML = `<p class="muted">Could not run the lab: ${esc(e.message)}</p>`; }
  };
  $('#lab-run').onclick = runLab;
  if(state.labResults) showLab(state.labResults); else runLab();
  $('#drill-run').onclick = async () => {
    const out = $('#drill-out'); out.innerHTML = '<p class="muted small">Running…</p>';
    try{
      const r = await api('/api/lab/drill', {});
      out.innerHTML = `<div class="lab-score ${r.passed ? '' : 'bad'}">${esc(r.payouts_that_exist)} payout exists</div><p class="lab-tag">${modeTag('SIMULATED')}</p>
        <ol class="timeline plain">${r.timeline.map(t => `<li><div><b>${esc(t.step)}</b><small>${esc(t.detail)}</small></div></li>`).join('')}</ol>
        <p class="muted small">${esc(r.payout_requests_sent)} requests sent, ${r.same_key_every_time ? 'all with the same key' : 'with different keys'}. ${esc(r.note)}</p>`;
    }catch(e){ out.textContent = e.message; }
  };
  $('#naive-run').onclick = async () => {
    const out = $('#naive-out'); out.innerHTML = '<p class="muted small">Running…</p>';
    try{
      const r = await api('/api/lab/naive-agent', {});
      out.innerHTML = `<div class="compare">
        <div class="cmp bad"><h3>An agent holding a raw pay tool</h3><ol class="timeline plain">${r.naive.steps.map(s => `<li><div><b>${esc(s.step)}</b><small>${esc(s.detail)}</small></div></li>`).join('')}</ol><p class="cmp-result">${r.naive.paid ? 'It paid.' : 'It did not pay.'}</p></div>
        <div class="cmp good"><h3>Star</h3><p class="muted small">What the model saw, with the instruction removed:</p><blockquote>${esc(r.star.model_saw)}</blockquote>
          <ul class="why-list">${r.star.reasons.map(x => `<li>${esc(x)}</li>`).join('')}</ul><p class="cmp-result">${r.star.paid ? 'It paid.' : `Not paid. ${esc(r.star.money_moving_calls)} money-moving calls.`}</p></div></div>
        <p class="muted small">${esc(r.disclaimer)} ${modeTag('SIMULATED')}</p>`;
    }catch(e){ out.textContent = e.message; }
  };
  $('#contract-run').onclick = async () => {
    const button = $('#contract-run'); button.disabled = true;
    $('#contract-out').textContent = 'Running isolated fault injection…';
    try{
      const data = await api('/api/lab/contract', {});
      $('#contract-out').innerHTML = `<h3>${data.passed} of ${data.total} scenarios behaved as expected</h3>
        <details class="decision-panel"><summary>How these results were measured</summary><p class="muted small">${esc(data.methodology)}</p></details>
        <div class="contract-cases">${data.cases.map(r => `<article class="contract-case"><h3>${esc(r.title)}</h3>
          <p class="muted small">${esc(r.principle)} · ${r.fault_injected ? 'Injected fault' : 'Normal control'}</p>
          <p class="${r.expected_behavior_met ? 'contract-pass' : 'contract-fail'}">${r.expected_behavior_met ? 'Expected behavior observed' : 'Unexpected result'} · <code>${esc(r.code)}</code></p>
          <p class="small">${esc(Object.entries(r.decision.checks).filter(([k, c]) => k !== 'execution' && c.status === 'fail').map(([, c]) => c.detail).join(' '))}</p>
          ${decisionPanel(r.decision)}</article>`).join('')}</div>
        <h3>Which checks keep unsafe candidates from reaching authorization?</h3>
        <p class="muted small">Counterfactual gate replay on five fixed candidates, including one normal control. This is not a live-model benchmark.</p>
        <div class="table-scroll"><table class="table"><thead><tr><th scope="col">Checks enabled</th><th scope="col">Unsafe admitted</th><th scope="col">Normal admitted</th></tr></thead><tbody>
        ${data.comparison.map(r => `<tr><td>${esc(r.label)}</td><td>${r.unsafe_candidates_admitted} of ${r.unsafe_candidates}</td><td>${r.normal_candidates_admitted} of ${r.normal_candidates}</td></tr>`).join('')}</tbody></table></div>
        <button class="btn secondary" id="contract-download" type="button">Download measured evidence</button>`;
      $('#contract-download').onclick = () => download('contract-evidence.json', data);
    }catch(e){ $('#contract-out').textContent = e.message; }finally{ button.disabled = false; }
  };
}

// ---------------------------------------------------------------- Settings
function settingsPage(m){
  const sw = (id, on, title, sub) => `<div class="setting"><div>${title}<small>${sub}</small></div><button class="switch" type="button" role="switch" id="${id}" aria-checked="${on}" aria-label="${esc(title)}"><span aria-hidden="true">${on ? 'On' : 'Off'}</span></button></div>`;
  const pr = state.data?.protection || {level: 'enhanced'}, pk = state.data?.passkey || {};
  const when = t => new Date(t * 1000).toLocaleString('en-US', {weekday: 'short', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit'});
  const wait = pr.delay_words || '2 minutes';
  const pend = pr.pending && pr.level === 'enhanced';
  const usd = n => money(Math.round((n || 0) * 100), state.data?.currency || 'USD').replace('.00', '');
  m.innerHTML = head('Your choices', 'Settings') + `
    <section class="card settings protection">
      <div class="setting"><div>Account protection<small>Raising it is instant. Lowering it needs your fingerprint and starts ${esc(wait)} later, so nobody can rush you on the phone.</small></div></div>
      <div class="levels" role="radiogroup" aria-label="Protection level">
        <button role="radio" type="button" class="level ${pr.level === 'enhanced' ? 'on' : ''}" data-to="enhanced" aria-checked="${pr.level === 'enhanced'}"><b>Enhanced${pr.level === 'enhanced' ? ' <em class="lvl-tag">On now</em>' : ''}</b><small>Recommended for seniors. Waits before payments over ${esc(usd(pr.enhanced_threshold))}, stops scam-like requests, and double-checks late at night.</small></button>
        <button role="radio" type="button" class="level ${pr.level === 'standard' ? 'on' : ''} ${pend ? 'queued' : ''}" data-to="standard" aria-checked="${pr.level === 'standard'}"><b>Standard${pr.level === 'standard' ? ' <em class="lvl-tag">On now</em>' : pend ? ' <em class="lvl-tag wait">Starts in <span class="prot-count"></span></em>' : ''}</b><small>Fewer interruptions: waits only over ${esc(usd(pr.standard_threshold))}, and scam-like requests get a warning instead of a stop. "Safe account" requests are always stopped.</small></button>
      </div>
      ${pend ? `<div class="pending" role="status"><span>Switching to Standard in <b class="prot-count"></b> (at ${esc(when(pr.pending.effective_at))}). Until then Enhanced stays on.</span> <button class="btn secondary small" id="p-cancel" type="button">Keep Enhanced</button></div>` : ''}
    </section>
    <section class="card settings page-gap">
      ${sw('s-realbio', passkeyMode(), 'Real fingerprint or face (optional)', passkeyMode()
        ? (pk.enrolled ? `On. Passkey ${esc((pk.key_ids || [])[0] || '')} on this device signs every payment after its own fingerprint, face or PIN check.`
                       : 'On. The first payment asks this device to save a passkey (Touch ID, Face ID, Windows Hello).')
        : "Off: the demo shows a simulated fingerprint sensor. Turn on to use this device's real Touch ID, Face ID or Windows Hello instead.")}
    </section>
    <section class="card settings page-gap">
      ${sw('s-care', state.care, 'Care mode', 'Larger text, fewer choices, an easy-to-find voice button, and answers read aloud. The safety checks are exactly the same.')}
    </section>
    <section class="card settings page-gap">
      <div class="setting"><div>Reset demo<small>Start again with a new session. Old audit records are kept.</small></div><button class="btn secondary small" id="s-reset" type="button">${icon('refresh')}Reset</button></div>
    </section>`;
  $('#s-care').onclick = () => setCare(!state.care);
  $('#s-reset').onclick = resetDemo;
  m.querySelectorAll('.level').forEach(b => b.onclick = () => setProtection(b.dataset.to));
  $('#p-cancel') && ($('#p-cancel').onclick = () => setProtection('enhanced'));
  protCountdown();
  $('#s-realbio').onclick = async () => {
    const on = !passkeyMode();
    if(on){
      if(!await passkeyCapability()){ toast("This browser can't use a real fingerprint or face here. The simulated sensor stays on."); return; }
      store.set('realBio', true);
    }else{
      try{ if(pk.enrolled){ await removePasskey(); await refresh(); } }
      catch(e){ toast('Identity verification was not completed. Your passkey is still enabled.'); return; }
      store.set('realBio', false);
    }
    state.signMode = await detectSignMode(); renderPage();
    toast(on ? 'Real fingerprint or face is on.' : 'Back to the simulated fingerprint sensor.');
  };
}
// A scheduled lowering counts down on screen (Settings and the top pill) and the page updates when it starts
let protTimer = null;
function protCountdown(){
  clearInterval(protTimer);
  const pend = state.data?.protection?.pending;
  if(!pend || protLevel() !== 'enhanced') return;
  const tick = async () => {
    const left = Math.max(0, Math.round(pend.effective_at - Date.now() / 1000));
    const txt = left >= 3600 ? `${Math.floor(left / 3600)} h ${Math.round(left % 3600 / 60)} min` : `${Math.floor(left / 60)}:${String(left % 60).padStart(2, '0')}`;
    document.querySelectorAll('.prot-count').forEach(e => e.textContent = txt);
    const pill = $('#prot-pill'); if(pill) pill.title = `Standard protection starts in ${txt}`;
    if(left <= 0){
      clearInterval(protTimer); await refresh(); if(state.page === 'settings') renderPage();
      if(protLevel() === 'standard') toast('Standard protection is now on. You can switch back to Enhanced at any time, instantly.');
    }
  };
  tick(); protTimer = setInterval(tick, 1000);
}
async function setProtection(to){
  const cur = state.data?.protection;
  if(to === cur?.level && !cur?.pending) return;
  try{
    if(to === 'enhanced'){
      await api('/api/protection', {session_id: state.sid, to}); toast(cur?.pending ? 'Cancelled. Enhanced protection stays on.' : 'Enhanced protection is on.');
    }else{
      await signedAction('Lower your protection to Standard, after a wait');
      await ensureSigner();
      const ch = await api('/api/protection/challenge', {session_id: state.sid, to});
      const assertion = await signPayload(ch.payload, ch.passkey);
      const st = await api('/api/protection', {session_id: state.sid, to, assertion});
      toast(`Signed. Standard protection starts in ${st.delay_words || 'a while'}. You can keep Enhanced until then.`);
    }
  }catch(e){ toast(e.name === 'NotAllowedError' ? 'Cancelled. Nothing changed.' : e.message); }
  await refresh(); if(state.page === 'settings') renderPage();
}
async function resetDemo(){
  const body = {world: true, ...(visitorMode() ? {visitor: VISITOR} : {})};
  let r;
  try{ r = await api('/api/reset', {session_id: state.sid, ...body}); }
  catch(e){ if(e.data?.code !== 'SESSION_NOT_FOUND') throw e; r = await api('/api/reset', body); }  // server restarted
  state.sid = r.session_id; store.set('sid', r.session_id); store.set('tracking', null); state.invite = null;
  stopSpeaking(); state.messages = []; state.check = null; state.calls = {calls: [], money_moving_this_task: 0}; $('#tlist').innerHTML = ''; closeConfirm(); saveChat();
  await refresh(); go('home'); toast('Demo reset'); offerCare();
}

// ---------------------------------------------------------------- Voice input
// Preferred when configured: record 16 kHz mono WAV -> a speech model on the server (with the names of saved people as a hint).
// Fallback: the browser's own speech recognition when the server has no ASR keys.
// Recording ends when the customer taps the mic again, or by itself once they have been quiet for a while
// after speaking (longer in Care mode, because pauses mid-sentence are normal), or at the hard cap.
const rec = {ctx:null, stream:null, node:null, chunks:[], rate:48000, on:false, timer:null, sr:null,
             t0:0, heard:0, lastVoice:0, noise:null, final:'', interim:'', stopping:false, shown:0};
const MAX_SEC = 50;
const VOICE = {silenceMs: () => state.care ? 4000 : 3000, noSpeechMs: 12000, minSpeechMs: 300};
const hint = t => { const h = $('#hint'); if(h) h.textContent = t; };
async function micToggle(){
  if(rec.on) return stopRec();
  stopSpeaking();                                   // barge-in: tapping the mic cuts the answer off at once
  if(serverAsr()) return startWavRec();
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if(!SR){ push({role:'bot', text:"This browser doesn't support voice input. Please use Chrome, or type instead."}); return; }
  Object.assign(rec, {final:'', interim:'', stopping:false, t0:Date.now(), lastVoice:0, heard:0, err:''});
  startBrowserSR(SR); setRec(true);
  rec.timer = setInterval(() => {                   // the browser recogniser has no audio level, so its results count as speech
    const now = Date.now();
    if(now - rec.t0 > MAX_SEC * 1000) return stopRec();
    if(rec.lastVoice && now - rec.lastVoice > VOICE.silenceMs()) return stopRec();
    if(!rec.lastVoice && now - rec.t0 > VOICE.noSpeechMs) return stopRec();
    listeningHint(now);
  }, 250);
}
function startBrowserSR(SR){
  const sr = rec.sr = new SR();
  sr.lang = 'en-US'; sr.continuous = true; sr.interimResults = true; sr.maxAlternatives = 1;
  sr.onresult = e => {
    let fin = '', mid = '';
    for(let i = 0; i < e.results.length; i++){ const t = e.results[i][0].transcript; if(e.results[i].isFinal) fin += t + ' '; else mid += t; }
    rec.final = (rec.base || '') + fin; rec.interim = mid; rec.lastVoice = Date.now();
    const ta = $('#text'); if(ta) ta.value = (rec.final + rec.interim).trim();   // show what has been heard so far
  };
  sr.onerror = e => {
    if(e.error === 'not-allowed' || e.error === 'service-not-allowed'){ rec.stopping = true; rec.err = 'Microphone permission is off. You can type your request, or allow access in your browser and try again.'; }
    else if(e.error === 'network' || e.error === 'audio-capture'){ rec.stopping = true; rec.err = "Voice input isn't working right now. Please type instead."; }
  };
  sr.onend = () => {
    // Chrome ends a session on its own after a pause or a network blip; keep listening until we decide to stop.
    if(rec.on && !rec.stopping && rec.sr === sr){
      rec.base = rec.final; rec.interim && (rec.base += rec.interim + ' '); rec.interim = '';
      try{ sr.start(); return; }catch{ /* fall through and finish */ }
    }
    if(rec.sr === sr) finishBrowserSR();
  };
  rec.base = ''; sr.start();
}
function finishBrowserSR(){
  const text = (rec.final + ' ' + rec.interim).replace(/\s+/g, ' ').trim();
  rec.sr = null; rec.base = ''; setRec(false);
  if(rec.err){ const err = rec.err; rec.err = ''; if($('#text')) $('#text').value = text; hint(err + ' Your words were not sent. Check the text before sending.'); return; }
  if(!text){ hint(rec.err || "I didn't catch that. Tap Speak to try again, or type your request."); rec.err = ''; return; }
  rec.err = '';
  addTrace([{kind:'asr', label:'Browser speech recognition: ' + text}]);
  send(text, undefined, true);
}
function listeningHint(now){
  if(now - rec.shown < 500) return; rec.shown = now;
  const left = MAX_SEC - Math.floor((now - rec.t0) / 1000);
  hint(left <= 10 ? `Recording stops in ${left} s. Tap the mic when you're done.`
                  : "I'm listening. Tap when you're finished, or pause to send.");
}
async function startWavRec(){
  try{ rec.stream = await navigator.mediaDevices.getUserMedia({audio:{channelCount:1, echoCancellation:true, noiseSuppression:true}}); }
  catch{ push({role:'bot', text:'Microphone permission is off. You can type your request, or allow access in your browser and try again.'}); return; }
  rec.ctx = new (window.AudioContext || window.webkitAudioContext)(); rec.rate = rec.ctx.sampleRate; rec.chunks = [];
  Object.assign(rec, {t0:Date.now(), heard:0, lastVoice:0, noise:null, stopping:false});
  const src = rec.ctx.createMediaStreamSource(rec.stream);
  rec.node = rec.ctx.createScriptProcessor(4096, 1, 1);
  rec.node.onaudioprocess = e => {
    const x = new Float32Array(e.inputBuffer.getChannelData(0));
    rec.chunks.push(x);
    voiceActivity(x, x.length / rec.rate * 1000);
  };
  src.connect(rec.node); rec.node.connect(rec.ctx.destination);
  setRec(true); $('#mic')?.classList.add('live');
  rec.timer = setTimeout(stopRec, MAX_SEC * 1000);
}
// Simple energy-based voice activity detection. The noise floor is learnt from the quietest moments, so a soft voice
// in a quiet room still counts as speech; silence only ends the recording after real speech has been heard.
function voiceActivity(x, ms){
  if(!rec.on || rec.stopping) return;
  let sum = 0; for(let i = 0; i < x.length; i++) sum += x[i] * x[i];
  const rms = Math.sqrt(sum / x.length), now = Date.now();
  rec.noise = rec.noise === null ? Math.min(rms, 0.02) : Math.min(0.03, rms < rec.noise ? rms : rec.noise * 0.995 + rms * 0.005);
  const speaking = rms > Math.max(0.01, rec.noise * 3);
  if(speaking){ rec.heard += ms; rec.lastVoice = now; }
  $('#mic')?.style.setProperty('--lvl', Math.min(1, rms * 8).toFixed(2));
  const armed = rec.heard >= VOICE.minSpeechMs;
  if(armed && now - rec.lastVoice > VOICE.silenceMs()) return stopRec();
  if(!armed && now - rec.t0 > VOICE.noSpeechMs) return stopRec();   // send what we have; the recogniser decides
  listeningHint(now);
}
async function stopRec(){
  if(rec.stopping && !rec.sr) return;
  rec.stopping = true;
  clearTimeout(rec.timer); clearInterval(rec.timer);
  if(rec.sr){ try{ rec.sr.stop(); }catch{ finishBrowserSR(); } return; }   // onend sends what was heard
  setRec(false);
  rec.node?.disconnect(); rec.stream?.getTracks().forEach(t => t.stop()); await rec.ctx?.close();
  rec.node = rec.stream = rec.ctx = null;
  const wav = encodeWav(downsample(flatten(rec.chunks), rec.rate, 16000), 16000);
  rec.chunks = [];
  if(wav.byteLength < 16000){ hint('I need a little more to go on. Please try again or type your request.'); return; }
  hint('Recognizing…');
  try{
    const r = await api('/api/asr', {session_id: state.sid, audio_b64: b64(wav)});
    hint(''); addTrace([{kind:'asr', label:`Speech recognition (${r.engine}, ${r.latency_ms}ms): ${r.text}`}]);
    state.nextAudio = r.audio_sha256 || null;
    if(r.text) send(r.text, undefined, true); else hint("I didn't catch that. You can try again or type your request.");
  }catch(e){ hint('Voice input is unavailable right now. Please try again or type your request.'); }
}
function setRec(on){
  rec.on = on; const mic = $('#mic');
  if(mic){ mic.classList.toggle('rec', on); if(!on){ mic.classList.remove('live'); mic.style.removeProperty('--lvl'); } }
  const label = $('#mic span'); if(label) label.textContent = on ? (state.care ? 'Tap when done' : 'Listening… tap to stop') : (state.care ? 'Tap to speak' : 'Speak');
  rec.shown = 0;
  hint(on ? "I'm listening. Tap when you're finished, or pause to send." : '');
}
function flatten(chunks){ const n = chunks.reduce((a, c) => a + c.length, 0), out = new Float32Array(n); let o = 0; chunks.forEach(c => { out.set(c, o); o += c.length; }); return out; }
function downsample(buf, from, to){
  if(from === to) return buf;
  const ratio = from / to, len = Math.floor(buf.length / ratio), out = new Float32Array(len);
  for(let i = 0; i < len; i++){ const s = Math.floor(i * ratio), e = Math.min(buf.length, Math.floor((i + 1) * ratio)); let sum = 0; for(let j = s; j < e; j++) sum += buf[j]; out[i] = sum / Math.max(1, e - s); }
  return out;
}
function encodeWav(samples, rate){
  const buf = new ArrayBuffer(44 + samples.length * 2), v = new DataView(buf);
  const w = (o, s) => { for(let i = 0; i < s.length; i++) v.setUint8(o + i, s.charCodeAt(i)); };
  w(0, 'RIFF'); v.setUint32(4, 36 + samples.length * 2, true); w(8, 'WAVE'); w(12, 'fmt ');
  v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true); v.setUint32(24, rate, true);
  v.setUint32(28, rate * 2, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true); w(36, 'data'); v.setUint32(40, samples.length * 2, true);
  for(let i = 0; i < samples.length; i++){ const s = Math.max(-1, Math.min(1, samples[i])); v.setInt16(44 + i * 2, s < 0 ? s * 0x8000 : s * 0x7FFF, true); }
  return buf;
}


// ---------------------------------------------------------------- Coming back from the approval page
// A redirect proves nothing. It only tells this page to ask the server, which asks PayPal. Capture happens in that POST.
async function handleReturn(){
  const q = new URLSearchParams(location.search), pid = q.get('payment');
  const back = q.get('pp_return') === '1', cancelled = q.get('pp_cancel') === '1';
  if(q.size) history.replaceState(null, '', '/');
  const tracking = store.get('tracking', null);
  store.set('tracking', null);
  const id = pid || tracking;
  if(!id) return;
  state.messages = state.messages.filter(m => m.type !== 'tracker' || m.pay?.payment_id !== id);
  go(assistantPage());
  const m = {type:'tracker', pay:null}; push(m);
  try{
    let r;
    if(cancelled) r = await api('/api/paypal/orders/cancelled', {session_id: state.sid, payment_id: id});
    else if(back) r = await api('/api/paypal/orders/return', {session_id: state.sid, payment_id: id});
    else r = await api(`/api/payments/${encodeURIComponent(state.sid)}/${encodeURIComponent(id)}`);
    m.pay = r.payment; renderStage();
    if(r.reply){ state.messages.splice(state.messages.indexOf(m), 0, {role:'bot', text:r.reply}); renderStage(); saveChat(); speak(r.speak); }
    addTrace([{kind:'gateway', label:`Back from the approval page: PayPal says ${m.pay.state}`}]);
    if(SETTLED.has(m.pay.state) && m.pay.verified){ m.announced = true; }
    liveTracker(m); await refresh(); loadCalls();
  }catch(e){
    state.messages = state.messages.filter(x => x !== m);
    push({role:'bot', text: e.data?.code === 'SESSION_NOT_FOUND' ? "I've lost track of that payment, because the demo server restarted. Please check Activity." : e.message});
  }
}

// ---------------------------------------------------------------- Start
document.addEventListener('keydown', e => { if(e.key === 'Escape' && $('#trace').classList.contains('open') && !$('#confirm').open){ toggleTrace(false); $('#trace-toggle')?.focus(); } });
// Family pages change from another device (pairing, freezing): keep them current without taking the focus away.
const pageSig = () => { const d = state.data || {}; return JSON.stringify([d.guardian, d.frozen, (d.alerts || []).length]); };
setInterval(async () => {
  if(!state.data || document.hidden || !['home', 'family'].includes(state.page) || $('#confirm').open || $('#detail').open) return;
  await refresh();
  if(pageSig() !== state.sig){ if(state.data.guardian?.status === 'paired') state.invite = null; renderPage(); }
}, 4000);

async function init(){
  try{ state.health = await api('/api/health'); }catch{}
  if(visitorMode() && !state.sid){
    try{ const r = await api('/api/reset', {visitor: VISITOR}); state.sid = r.session_id; store.set('sid', r.session_id); }catch{}
  }
  await refresh();
  if(!state.data) throw new Error('The demo service is not ready. Please try again in a moment.');
  loadChat();
  try{ const p = pageFromHash() || sessionStorage.getItem('page'); if(NAV.some(n => n[0] === p)) state.page = (state.care && CARE_HIDES.has(p)) ? 'home' : p; }catch{}
  history.replaceState({page: state.page}, '', `#/${state.page}`);
  state.signMode = await detectSignMode();      // after refresh: an enrolled passkey keeps being used
  render();
  loadCalls();
}
// With the app on screen: "Checking with PayPal" must really be visible, and scrolling needs real sizes.
async function afterEnter(){
  const q = new URLSearchParams(location.search);
  const pending = store.get('pendingPayment', null);
  renderStage();
  if(q.get('pp_return') || q.get('pp_cancel') || store.get('tracking', null)) await handleReturn();
  else if(pending && pending.sid === state.sid){ state.card = pending.card; openConfirm(pending.card); await recoverPayment(pending.card); }
  else restorePending();
  if(state.care) api('/api/care', {session_id: state.sid, on: true}).catch(() => {});
  else offerCare();
}

// No landing page: the app opens straight away. If the service isn't ready, one sentence and Try again.
let stopField = () => {};
(async () => {
  try{ await init(); }
  catch{
    $('#app').innerHTML = `<div class="boot"><p role="status">Unable to connect to the demo. Please try again in a moment.</p>
      <button class="btn primary" type="button" id="boot-retry">Try again</button></div>`;
    $('#boot-retry').onclick = () => location.reload();
    return;
  }
  stopField = particles($('.sidebar'), {density:9000, max:40});
  await afterEnter();
})();
