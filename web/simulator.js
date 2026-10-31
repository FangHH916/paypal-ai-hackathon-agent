// The simulator's stand-in for PayPal's approval page. It says what it is, and it looks nothing like PayPal.
// A click changes only the simulator's state. Star finds out the way it would from PayPal: by asking, and from a signed event.
import {$, esc, api, money, ppBadge} from '/common.js';

const q = new URLSearchParams(location.search);
const isOrder = location.pathname.endsWith('/checkout');
const id = isOrder ? q.get('token') : q.get('id');

async function decide(kind, decision){
  document.querySelectorAll('button').forEach(b => { b.disabled = true; });
  try{
    const r = await api('/simulator/api/decide', {kind, id, decision});
    location.assign(r.redirect);
  }catch(e){
    $('#sim-body').insertAdjacentHTML('beforeend', `<p class="alert-text" role="alert">${esc(e.message)}</p>`);
  }
}
function fmt(amount){ return money(Math.round(Number(amount?.value ?? amount ?? 0) * 100), amount?.currency_code || 'USD'); }

async function main(){
  const body = $('#sim-body');
  let info;
  try{
    info = await api(`/simulator/api/info?${isOrder ? 'token' : 'id'}=${encodeURIComponent(id || '')}`);
  }catch{
    body.innerHTML = '<p>This simulated request no longer exists. The demo may have been reset.</p><p><a class="btn secondary" href="/">Back to Star</a></p>';
    return;
  }
  if(info.kind === 'order'){
    $('#sim-title').textContent = 'Pretend buyer: approve this order?';
    const items = (info.items || []).map(i => `<li>${esc(i.quantity || 1)} × ${esc(i.name)}</li>`).join('');
    body.innerHTML = `<dl class="sim-facts">
        <dt>What</dt><dd>${esc(info.description)}${items ? `<ul>${items}</ul>` : ''}</dd>
        <dt>Total</dt><dd><b>${esc(fmt(info.amount))}</b></dd>
        <dt>Order</dt><dd><code>${esc(info.id)}</code> ${ppBadge(info.status, 'SIMULATED')}</dd></dl>
      <p class="muted">On the real sandbox the buyer logs in to PayPal's own page here. Approving doesn't pay yet: Star captures it afterwards and checks the result with PayPal.</p>
      <div class="sim-actions"><button class="btn primary big" id="sim-yes" type="button">Approve (simulated)</button>
        <button class="btn secondary big" id="sim-no" type="button">Cancel and go back</button></div>`;
    $('#sim-yes').onclick = () => decide('order', 'approve');
    $('#sim-no').onclick = () => decide('order', 'cancel');
  }else{
    $('#sim-title').textContent = 'Pretend payer: pay this invoice?';
    body.innerHTML = `<dl class="sim-facts">
        <dt>From</dt><dd>${esc(info.from?.business_name || info.from?.name?.full_name || info.from?.email_address || 'Unknown')}</dd>
        <dt>Amount</dt><dd><b>${esc(fmt(info.amount))}</b></dd>
        <dt>Note</dt><dd>${esc(info.note)}</dd>
        <dt>Invoice</dt><dd><code>${esc(info.id)}</code> ${ppBadge(info.status, 'SIMULATED')}</dd></dl>
      <p class="muted">On the real sandbox this is PayPal's own invoice page.</p>
      <div class="sim-actions"><button class="btn primary big" id="sim-yes" type="button">Pay (simulated)</button>
        <button class="btn secondary big" id="sim-no" type="button">Not now, go back</button></div>`;
    $('#sim-yes').onclick = () => decide('invoice', 'pay');
    $('#sim-no').onclick = () => decide('invoice', 'later');
  }
  $('#sim-yes').focus();
}
main();
