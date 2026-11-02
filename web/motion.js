// Star: choreography. Every element is already in its final place and readable before any of this runs, so a safety message or a
// call button is never waited for: motion only explains what just happened. Web Animations only, so the strict policy is unchanged.
// With prefers-reduced-motion nothing here runs, and the page is the same, complete, still.
export const calm = () => !window.matchMedia || matchMedia('(prefers-reduced-motion: reduce)').matches || !Element.prototype.animate;
const EASE = 'cubic-bezier(.2,.7,.2,1)', SLAM = 'cubic-bezier(.3,1.4,.5,1)';

function run(el, frames, opts){ if(!el || calm()) return null; return el.animate(frames, {easing:EASE, fill:'backwards', ...opts}); }
const STAMP = rotate => [{transform:`rotate(${rotate}deg) scale(2.3)`, opacity:0}, {transform:`rotate(${rotate}deg) scale(.96)`, opacity:1, offset:.72}, {transform:`rotate(${rotate}deg) scale(1)`, opacity:1}];
const JOLT = [{transform:'translate(0,0)'}, {transform:'translate(-3px,2px)', offset:.3}, {transform:'translate(2px,-1px)', offset:.6}, {transform:'translate(0,0)'}];

// A page change: the new content settles in. Short, so it never holds up a click.
export function pageIn(root){
  if(!root || calm()) return;
  [...root.children].slice(0, 6).forEach((el, i) => run(el, [{opacity:.01, transform:'translateY(14px)'}, {opacity:1, transform:'none'}], {duration:300, delay:i * 35}));
}

// A number struck down to zero. The text is already 0; this is only the count landing.
function landZero(el, delay = 0){
  run(el, [{transform:'translateY(.14em) scale(1.5)', opacity:0}, {transform:'scale(1)', opacity:1}], {duration:420, delay, easing:SLAM});
}

// A scam or a mismatch is stopped: the seal comes down on the block, and the block takes the hit.
export function stopFx(block){
  if(!block || calm()) return;
  const frozen = block.classList.contains('frozen');
  run(block, [{opacity:.2}, {opacity:1}], {duration:120});
  run(block, STAMP(-7), {duration:300, delay:60, pseudoElement:'::after', easing:'cubic-bezier(.5,0,.8,.4)'});
  run(block, [{opacity:0, transform:'scale(1.08) rotate(-6deg)'}, {opacity:.14, transform:'none'}], {duration:900, pseudoElement:'::before'});
  setTimeout(() => run(block, JOLT, {duration:180, fill:'none'}), 310);
  const zero = block.querySelector('.stop-count b');
  landZero(zero, frozen ? 180 : 240);
}

// Money verified: the line work closes around a tick. The word "verified" was on screen before this started.
export function verifiedFx(tracker){
  if(!tracker || calm()) return;
  const seal = tracker.querySelector('.vseal'); if(!seal) return;
  seal.querySelectorAll('path').forEach((p, i) => run(p, [{strokeDashoffset:1}, {strokeDashoffset:0}], {duration:p.classList.contains('tickmark') ? 260 : 900, delay:p.classList.contains('tickmark') ? 780 : i * 60}));
  run(seal, [{transform:'rotate(-24deg)'}, {transform:'none'}], {duration:1100});
}

// "The model tried, code stopped it, 0 money-moving calls": the same three beats on the landing and in her chat.
export function caughtFx(el){
  if(!el || calm()) return;
  const bad = el.querySelector('.cg.bad'), good = el.querySelector('.cg.good'), zero = el.querySelector('.caught-zero');
  run(el.querySelector('.cg.said'), [{opacity:.01, transform:'translateY(10px)'}, {opacity:1, transform:'none'}], {duration:280});
  run(bad, [{opacity:.01, transform:'translateY(10px)'}, {opacity:1, transform:'none'}], {duration:240, delay:110});
  run(good, [{opacity:.01, transform:'translateY(10px)'}, {opacity:1, transform:'none'}], {duration:240, delay:240});
  run(el.querySelector('.caught-badge'), STAMP(-3), {duration:240, delay:340, easing:'cubic-bezier(.5,0,.8,.4)'});
  if(zero){ run(zero, [{clipPath:'inset(0 100% 0 0)'}, {clipPath:'inset(0 0 0 0)'}], {duration:280, delay:420}); landZero(zero.querySelector('b'), 520); }
}

export function plateFx(root){
  if(!root || calm()) return [];
  const plate = root.querySelector('.plate'), out = [];
  const add = a => { if(a) out.push(a); };
  root.querySelectorAll('h1 span').forEach((s, i) => add(run(s, [{opacity:.01, transform:'translateY(.18em)'}, {opacity:1, transform:'none'}], {duration:520, delay:i ? 1250 : 80})));
  const [said, draft, reading, zero] = plate.children;
  add(run(said, [{opacity:.01}, {opacity:1}], {duration:360, delay:200}));
  add(run(draft, [{opacity:.01}, {opacity:1}], {duration:360, delay:640}));
  add(run(reading, [{opacity:.01}, {opacity:1}], {duration:360, delay:1220}));
  add(run(draft.querySelector('.fig'), [{transform:'scale(1)'}, {transform:'translate(-2px,1px) scale(1.02)', offset:.5}, {transform:'scale(1)'}], {duration:170, delay:1730, fill:'none'}));
  add(run(draft.querySelector('.fig'), [{transform:'scaleX(1)'}], {duration:1}));
  add(draft.querySelector('.fig').animate([{transform:'scaleX(0)'}, {transform:'scaleX(1)'}], {duration:340, delay:1460, easing:'cubic-bezier(.7,0,.3,1)', pseudoElement:'::after', fill:'backwards'}));
  add(run(draft.querySelector('.seal-stamp'), STAMP(-8), {duration:300, delay:1640, easing:'cubic-bezier(.5,0,.8,.4)'}));
  add(run(zero, [{clipPath:'inset(100% 0 0 0)'}, {clipPath:'inset(0 0 0 0)'}], {duration:420, delay:1960}));
  add(run(zero.querySelector('.num'), [{transform:'translateY(.2em) scale(1.45)', opacity:0}, {transform:'none', opacity:1}], {duration:460, delay:2240, easing:SLAM}));
  return out;
}

// The dialog is printed up from the bottom of the page.
export function cardIn(dialog){
  run(dialog, [{transform:'translateY(36px) rotate(.8deg)', opacity:.01}, {transform:'none', opacity:1}], {duration:320});
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
