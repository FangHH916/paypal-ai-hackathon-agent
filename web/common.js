// Star: helpers shared by the app, the Activity grid, the guardian page and the simulator page.
// No inline script or style anywhere, so the server's strict Content-Security-Policy applies.

export const $ = (sel, el = document) => el.querySelector(sel);
export const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
export const store = {
  get(k, d){ try{ const v = localStorage.getItem(k); return v === null ? d : JSON.parse(v); }catch{ return d; } },
  set(k, v){ try{ localStorage.setItem(k, JSON.stringify(v)); }catch{} },
};

export async function api(path, body){
  const opt = body ? {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body)} : {};
  let r;
  try{ r = await fetch(path, opt); }
  catch(cause){ throw Object.assign(new Error(NO_LINK), {offline:true, cause}); }   // what went wrong in her words, not the browser's
  const data = await r.json().catch(() => ({}));
  if(!r.ok) throw Object.assign(new Error(data.error || "Star had a problem with that. Please try again."), {data, status: r.status});
  return data;
}
// Says nothing about money, because this message can follow any request. Whoever knows no money call was possible says so themselves.
export const NO_LINK = "I can't reach Star right now. Please try again.";
// A page section that couldn't load: one sentence, and a way to try again. Never the browser's error, never a spinner left turning.
export function loadFailed(el, what, retry){
  if(!el) return;
  el.innerHTML = `<div class="load-fail" role="alert"><p>I can't reach Star right now, so I can't show ${esc(what)}. Nothing was paid from this screen.</p><button class="btn secondary small" type="button">Try again</button></div>`;
  el.querySelector('button').onclick = retry;
}

export function download(name, obj){
  const a = document.createElement('a');
  a.href = URL.createObjectURL(new Blob([JSON.stringify(obj, null, 2)], {type:'application/json'}));
  a.download = name; document.body.appendChild(a); a.click(); a.remove();
}

export function money(cents, currency = 'USD'){
  return new Intl.NumberFormat('en-US', {style:'currency', currency}).format((cents || 0) / 100);
}
export const clock = ts => new Date(ts * 1000).toLocaleString('en-US', {month:'short', day:'numeric', hour:'numeric', minute:'2-digit'});

const P = {
  home:'M3 10 12 3l9 7v10a1 1 0 0 1-1 1h-5v-7H9v7H4a1 1 0 0 1-1-1z',
  transfer:'M4 7h15m-4-4 4 4-4 4M20 17H5m4-4-4 4 4 4',
  shield:'m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6zM8 12l3 3 5-6',
  audit:'M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8zm0 0v6h6M8 13h8m-8 4h6',
  lab:'M9 3h6m-5 0v7L4 20h16l-6-10V3M7 15h10',
  settings:'M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8M12 2v3m0 14v3M2 12h3m14 0h3M5 5l2 2m10 10 2 2M5 19l2-2M17 7l2-2',
  spark:'m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5z',
  mic:'M12 3a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V6a3 3 0 0 0-3-3M5 10v2a7 7 0 0 0 14 0v-2M12 19v3m-4 0h8',
  send:'M12 19V5m-6 6 6-6 6 6',
  lock:'M6 10h12v11H6zm2 0V6a4 4 0 0 1 8 0v4m-4 5v2',
  bolt:'m13 2-9 12h7l-1 8 10-13h-7z',
  coin:'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18m0-14v10m-3-7.5c0-1 1.3-1.8 3-1.8s3 .8 3 1.8-1.3 1.7-3 1.7-3 .8-3 1.8 1.3 1.8 3 1.8 3-.8 3-1.8',
  phone:'M5 4h4l2 5-2.5 1.5a11 11 0 0 0 5 5L15 13l5 2v4a2 2 0 0 1-2 2A16 16 0 0 1 3 6a2 2 0 0 1 2-2',
  alert:'M12 9v4m0 4h.01M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0',
  family:'M9 11a3 3 0 1 0 0-6 3 3 0 0 0 0 6m8 1a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5M3 20v-1a6 6 0 0 1 12 0v1m1-6.5a4.5 4.5 0 0 1 5 4.5v1',
  trace:'M4 6h16M4 12h10M4 18h7',
  download:'M12 3v12m-5-5 5 5 5-5M5 17v4h14v-4',
  refresh:'M3 5v5h5m13 9v-5h-5M4 10a8 8 0 0 1 13-6l4 4M3 16l4 4a8 8 0 0 0 13-6',
  care:'M12 21s-8-5-8-11a4.5 4.5 0 0 1 8-3 4.5 4.5 0 0 1 8 3c0 6-8 11-8 11',
  cart:'M3 4h2l2.5 12h11L21 8H7M9 21a1 1 0 1 0 0-2 1 1 0 0 0 0 2m9 0a1 1 0 1 0 0-2 1 1 0 0 0 0 2',
  freeze:'M12 2v20M4 7l16 10M20 7 4 17M9 4l3 3 3-3M9 20l3-3 3 3',
  speaker:'M4 9v6h4l5 4V5L8 9zm12 0a4 4 0 0 1 0 6',
  pause:'M8 5v14m8-14v14',
  play:'m7 4 13 8-13 8z',
  fingerprint:'M7 11c0-3 2.2-5 5-5s5 2 5 5v2M9.5 11c0-1.4 1.1-2.5 2.5-2.5s2.5 1.1 2.5 2.5v3c0 2-1 4-2 5M12 11v3c0 1.5-.5 3-1.5 4.5M7 14c0 2 .5 3.5 1.5 5M17 16c-.2 1.3-.6 2.5-1.2 3.5',
};
export const icon = (n, cls = '') => `<svg class="ico ${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="${P[n] || P.spark}"/></svg>`;

// ---------------------------------------------------------------- The one way a PayPal status reaches the screen
// Every status goes through ppBadge, which always carries the SIMULATED or SANDBOX tag. tests/test_frontend.py checks that
// no page writes one of these words on its own. "Paid", "Sent" and "Refunded" appear only with verified state.
const WORDS = {
  SUBMITTING:['Sending to PayPal','pending'], UNKNOWN:["Can't confirm yet: don't pay again",'warn'], PROCESSING:['PayPal is processing','pending'],
  HELD:['Held by PayPal','warn'], UNCLAIMED:['Waiting to be claimed','pending'], AWAITING_APPROVAL:['Waiting for your approval','pending'],
  APPROVED:['Approved, paying now','pending'], CAPTURING:['Paying now','pending'], CAPTURE_PENDING:['PayPal is processing','pending'],
  AWAITING_PAYMENT:['Waiting for you to pay','pending'], SENT_TO_PAYER:['Request sent','pending'], REFUND_PENDING:['Refund in progress','pending'],
  RETURNED:['Came back to you','bad'], REVERSED:['Reversed by PayPal','bad'], FAILED:['Failed: no money moved','bad'], DECLINED:['Declined','bad'],
  BUYER_CANCELLED:['You cancelled it','neutral'], EXPIRED:['Expired','neutral'], ABANDONED:['Not sent','neutral'], INVOICE_CANCELLED:['Invoice cancelled','neutral'],
  ANOMALY:["Stopped: PayPal's answer didn't match",'bad'],
  STOPPED:['Stopped','stop'], FROZEN:['Frozen','stop'], BLOCKED:['Stopped','stop'], DENIED:['Family said no','stop'], CANCELLED:['Cancelled','neutral'],
  PENDING_APPROVAL:['Waiting for you','warn'], DRAFT:['Draft','neutral'], SENT_INVOICE:['Due','pending'], UNPAID:['Due','pending'], PAYMENT_PENDING:['PayPal is processing','pending'], PARTIALLY_PAID:['Partly paid','pending'],
};
const SUCCESS_WORDS = {SENT:'Sent', PAID:'Paid', REFUNDED:'Refunded'};

// In the simulator nothing came from PayPal, so no status says it did (the same rule as the server's source_words).
export const sourceWords = (text, label) => !String(label || '').toUpperCase().startsWith('SIM') ? text
  : text.replace(/(^|[.!?]\s+)PayPal\b/g, (m, lead) => `${lead}The simulator`).replaceAll('PayPal', 'the simulator');
export function statusInfo(status, verified = false, label = ''){
  if(SUCCESS_WORDS[status]) return verified ? {word:`${SUCCESS_WORDS[status]}, verified`, tone:'ok'} : {word:sourceWords('Checking with PayPal', label), tone:'pending'};
  const hit = WORDS[status];
  return hit ? {word:sourceWords(hit[0], label), tone:hit[1]} : {word:String(status || 'Unknown').replaceAll('_', ' ').toLowerCase(), tone:'neutral'};
}
export function modeTag(label){
  const text = label || 'SIMULATED';
  return `<span class="mode-tag mode-${esc(text.toLowerCase())}">${esc(text)}</span>`;
}
export function ppBadge(status, label, verified = false){
  const {word, tone} = statusInfo(status, verified, label);
  return `<span class="pp-badge"><span class="pill-status tone-${tone}">${esc(word)}</span>${modeTag(label)}</span>`;
}

// ---------------------------------------------------------------- A device key that never leaves this browser
// ECDSA P-256, non-extractable, in IndexedDB. The server only ever sees the public half.
export function deviceKeys(dbName, id = 'device-key'){
  const open = () => new Promise((ok, no) => { const r = indexedDB.open(dbName, 1);
    r.onupgradeneeded = () => r.result.createObjectStore('keys'); r.onsuccess = () => ok(r.result); r.onerror = () => no(r.error); });
  const read = async () => { const db = await open(); return new Promise((ok, no) => { const t = db.transaction('keys').objectStore('keys').get(id); t.onsuccess = () => ok(t.result); t.onerror = () => no(t.error); }); };
  const write = async v => { const db = await open(); return new Promise((ok, no) => { const t = db.transaction('keys', 'readwrite').objectStore('keys').put(v, id); t.onsuccess = () => ok(); t.onerror = () => no(t.error); }); };
  let key = null;
  return {
    async ensure(){
      if(!window.crypto?.subtle) throw new Error('This browser cannot create a secure signing key. Please open the app via localhost or HTTPS.');
      if(!key) key = await read().catch(() => null);
      if(!key){
        const pair = await crypto.subtle.generateKey({name:'ECDSA', namedCurve:'P-256'}, false, ['sign', 'verify']);
        key = {privateKey: pair.privateKey, publicJwk: await crypto.subtle.exportKey('jwk', pair.publicKey)};
        await write(key).catch(() => {});
      }
      const {kty, crv, x, y} = key.publicJwk;
      return {kty, crv, x, y};
    },
    async sign(payload){
      await this.ensure();
      return b64(await crypto.subtle.sign({name:'ECDSA', hash:'SHA-256'}, key.privateKey, new TextEncoder().encode(payload)));
    },
  };
}
export function b64(buf){ let s = ''; const b = new Uint8Array(buf); for(let i = 0; i < b.length; i += 0x8000) s += String.fromCharCode.apply(null, b.subarray(i, i + 0x8000)); return btoa(s); }

// The demo's fingerprint sensor, clearly labelled. Tapping it is the explicit approval; what follows is real cryptography.
export function mockFingerprint(host, what, whoChecks = 'Star and its payment gateway each check that signature.'){
  return new Promise((resolve, reject) => {
    host.querySelector('.mockfp')?.remove();
    const el = document.createElement('div');
    el.className = 'mockfp'; el.setAttribute('role', 'group'); el.setAttribute('aria-label', 'Simulated fingerprint sensor');
    el.innerHTML = `<div class="mf-badge">Demo: simulated fingerprint sensor</div>
      <button type="button" class="mf-pad" aria-label="Touch, or press Space, to confirm">${icon('fingerprint')}</button>
      <b class="mf-title">Touch the sensor to confirm</b>
      <div class="mf-what">${esc(what)}</div>
      <small>On a real phone this is Face ID or a fingerprint. Here, your tap unlocks this browser's private key, which signs exactly this. ${esc(whoChecks)}</small>
      <button type="button" class="btn secondary mf-cancel">Cancel</button>`;
    host.appendChild(el);
    const pad = el.querySelector('.mf-pad'); pad.focus();
    let done = false;
    pad.onkeydown = e => { if(e.key === 'Enter') e.preventDefault(); };     // a deliberate press only: Enter may still be held from the card
    pad.onclick = () => {
      if(done) return; done = true;
      el.classList.add('scanning'); el.querySelector('.mf-title').textContent = 'Reading fingerprint…';
      setTimeout(() => {
        el.classList.remove('scanning'); el.classList.add('matched');
        el.querySelector('.mf-title').textContent = 'Fingerprint matched (simulated)';
        setTimeout(() => { el.remove(); resolve(); }, 350);
      }, 600);
    };
    el.querySelector('.mf-cancel').onclick = () => { el.remove(); const e = new Error('Cancelled'); e.name = 'NotAllowedError'; reject(e); };
  });
}
