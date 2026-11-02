// Star: choreography. Every element is in its final place and readable from the first frame: nothing here fades in or wipes in, so a
// safety message or a call button is never waited for. Motion only adds emphasis (a scale, a stamp coming down, a line being struck),
// and all of it is over within a second. Web Animations only, so the strict policy is unchanged.
// With prefers-reduced-motion nothing here runs, and the page is the same, complete, still.
export const calm = () => !window.matchMedia || matchMedia('(prefers-reduced-motion: reduce)').matches || !Element.prototype.animate;
const EASE = 'cubic-bezier(.2,.7,.2,1)', SLAM = 'cubic-bezier(.3,1.4,.5,1)', DROP = 'cubic-bezier(.5,0,.8,.4)';

function run(el, frames, opts){ if(!el || calm()) return null; return el.animate(frames, {easing:EASE, fill:'backwards', ...opts}); }
const STAMP = rotate => [{transform:`rotate(${rotate}deg) scale(1.8)`}, {transform:`rotate(${rotate}deg) scale(.96)`, offset:.72}, {transform:`rotate(${rotate}deg) scale(1)`}];
const JOLT = [{transform:'translate(0,0)'}, {transform:'translate(-3px,2px)', offset:.3}, {transform:'translate(2px,-1px)', offset:.6}, {transform:'translate(0,0)'}];
const RISE = [{transform:'translateY(8px)'}, {transform:'none'}];

// A page change: the new content settles in. Short, so it never holds up a click.
export function pageIn(root){
  if(!root || calm()) return;
  [...root.children].slice(0, 6).forEach((el, i) => run(el, RISE, {duration:260, delay:i * 30}));
}

// A number struck down to zero. The text is already 0; this is only the count landing.
function landZero(el, delay = 0){
  run(el, [{transform:'translateY(.1em) scale(1.3)'}, {transform:'scale(1)'}], {duration:360, delay, easing:SLAM});
}

// A scam or a mismatch is stopped: the seal comes down on the block, and the block takes the hit.
export function stopFx(block){
  if(!block || calm()) return;
  run(block, STAMP(-7), {duration:280, delay:40, pseudoElement:'::after', easing:DROP});
  run(block, [{opacity:0, transform:'scale(1.08) rotate(-6deg)'}, {opacity:.14, transform:'none'}], {duration:700, pseudoElement:'::before'});   // the line work behind, not a word of the message
  setTimeout(() => run(block, JOLT, {duration:180, fill:'none'}), 290);
  landZero(block.querySelector('.stop-count b'), 200);
}

// Money verified: the line work closes around a tick. The word "verified" was on screen before this started.
export function verifiedFx(tracker){
  if(!tracker || calm()) return;
  const seal = tracker.querySelector('.vseal'); if(!seal) return;
  seal.querySelectorAll('path').forEach((p, i) => run(p, [{strokeDashoffset:1}, {strokeDashoffset:0}], {duration:p.classList.contains('tickmark') ? 260 : 700, delay:p.classList.contains('tickmark') ? 640 : i * 40}));
  run(seal, [{transform:'rotate(-24deg)'}, {transform:'none'}], {duration:900});
}

// "The model tried, code stopped it, 0 money-moving calls": the same three beats on the landing and in her chat.
export function caughtFx(el){
  if(!el || calm()) return;
  run(el.querySelector('.cg.bad'), RISE, {duration:240, delay:60});
  run(el.querySelector('.cg.good'), RISE, {duration:240, delay:180});
  run(el.querySelector('.caught-badge'), STAMP(-3), {duration:240, delay:280, easing:DROP});
  landZero(el.querySelector('.caught-zero b'), 420);
}

// The landing. Each beat plays when its own element comes into view, so a phone reader sees what a desktop reader sees.
// Returns a function that cancels whatever is playing or still waiting.
export function plateFx(root){
  if(!root || calm()) return () => {};
  const draft = root.querySelector('.plate .draft'), fig = draft.querySelector('.fig'), played = [];
  const add = a => { if(a) played.push(a); };
  const beats = new Map([
    [fig, () => {
      add(fig.animate([{transform:'scaleX(0)'}, {transform:'scaleX(1)'}], {duration:260, delay:80, easing:'cubic-bezier(.7,0,.3,1)', pseudoElement:'::after', fill:'backwards'}));
      add(run(draft.querySelector('.seal-stamp'), STAMP(-8), {duration:260, delay:300, easing:DROP}));
    }],
    [root.querySelector('.plate .zero .num'), () => add(run(root.querySelector('.plate .zero .num'), [{transform:'translateY(.14em) scale(1.3)'}, {transform:'none'}], {duration:400, delay:460, easing:SLAM}))],
    [root.querySelector('.scam-seal'), () => add(run(root.querySelector('.scam-seal'), STAMP(-4), {duration:260, delay:120, easing:DROP}))],
  ]);
  const seen = new IntersectionObserver(entries => entries.forEach(e => {
    if(!e.isIntersecting) return;
    seen.unobserve(e.target); beats.get(e.target)?.();
  }), {threshold:.6});
  beats.forEach((_, el) => el && seen.observe(el));
  return () => { seen.disconnect(); played.forEach(a => { try{ a.cancel(); }catch{} }); };
}

// The dialog is printed up from the bottom of the page.
export function cardIn(dialog){
  run(dialog, [{transform:'translateY(24px) rotate(.6deg)'}, {transform:'none'}], {duration:260});
}

// An engraved seal: closed wavy lines, computed, not drawn. pathLength lets it plot itself in.
function sealLine(radius, swing, waves, shift){
  const n = waves * 8, parts = [];
  for(let i = 0; i < n; i++){
    const t = 2 * Math.PI * i / n, r = radius + swing * Math.sin(waves * t + shift);
    parts.push(`${i ? 'L' : 'M'}${(100 + r * Math.cos(t)).toFixed(1)} ${(100 + r * Math.sin(t)).toFixed(1)}`);
  }
  return `<path pathLength="1" stroke-dasharray="1" d="${parts.join('')}Z"/>`;
}
export function sealSvg(){
  let lines = '';
  for(let k = 0; k < 6; k++) lines += sealLine(82, 13, 18, k * Math.PI / 3) ;
  for(let k = 0; k < 4; k++) lines += sealLine(58, 9, 12, k * Math.PI / 2);
  return `<svg class="vseal" viewBox="0 0 200 200" aria-hidden="true" focusable="false">${lines}<path class="tickmark" pathLength="1" stroke-dasharray="1" d="M70 104 92 126 134 78"/></svg>`;
}
