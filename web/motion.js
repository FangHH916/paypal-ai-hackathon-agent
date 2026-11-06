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

// A field of light points behind the landing and the rail. They drift, join up when close, lean away from the pointer and
// draw a thread to it; a click sends out a small burst. Decorative only: behind every word, no pointer events of its own,
// paused when out of view or the tab is hidden, and never started for reduced motion or in Care mode.
export function particles(host, {density = 14000, max = 110} = {}){
  if(!host || calm() || document.body.classList.contains('care') || !window.HTMLCanvasElement) return () => {};
  const canvas = document.createElement('canvas');
  canvas.className = 'particles'; canvas.setAttribute('aria-hidden', 'true');
  host.prepend(canvas);
  const ctx = canvas.getContext('2d'); if(!ctx){ canvas.remove(); return () => {}; }
  const LIGHT = '111,227,255', REACH = 120, PUSH = 140;
  let w = 0, h = 0, dpr = 1, dots = [], frame = 0, visible = true, alive = true;
  const pointer = {x:-1e4, y:-1e4, on:false};
  const spawn = (x, y, burst) => {
    const a = Math.random() * Math.PI * 2, s = burst ? 1.4 + Math.random() * 2.2 : .12 + Math.random() * .28;
    return {x, y, vx:Math.cos(a) * s, vy:Math.sin(a) * s, r:.8 + Math.random() * 1.4, life:burst ? 70 + Math.random() * 40 : Infinity};
  };
  const size = () => {
    const box = host.getBoundingClientRect();
    dpr = Math.min(window.devicePixelRatio || 1, 2); w = box.width; h = Math.min(box.height, 2400);
    canvas.width = Math.round(w * dpr); canvas.height = Math.round(h * dpr);
    canvas.style.width = `${w}px`; canvas.style.height = `${h}px`;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    const want = Math.min(max, Math.round(w * h / density));
    dots = dots.filter(d => d.life === Infinity).slice(0, want);
    while(dots.length < want) dots.push(spawn(Math.random() * w, Math.random() * h, false));
  };
  const step = () => {
    frame = 0; if(!alive) return;
    ctx.clearRect(0, 0, w, h);
    if(document.body.classList.contains('care')) return;           // Care mode switched on later: the field goes still and empty
    for(const d of dots){
      const dx = d.x - pointer.x, dy = d.y - pointer.y, dist = Math.hypot(dx, dy);
      if(pointer.on && dist < PUSH && dist > .1){ const f = (1 - dist / PUSH) * .6; d.vx += dx / dist * f; d.vy += dy / dist * f; }
      d.x += d.vx; d.y += d.vy;
      if(d.life === Infinity){
        d.vx *= .96; d.vy *= .96;
        if(Math.hypot(d.vx, d.vy) < .1){ d.vx += (Math.random() - .5) * .06; d.vy += (Math.random() - .5) * .06; }
        if(d.x < 0) d.x += w; else if(d.x > w) d.x -= w;
        if(d.y < 0) d.y += h; else if(d.y > h) d.y -= h;
      } else { d.vx *= .95; d.vy *= .95; d.life -= 1; }
    }
    dots = dots.filter(d => d.life > 0);
    for(let i = 0; i < dots.length; i++){
      const a = dots[i];
      for(let j = i + 1; j < dots.length; j++){
        const b = dots[j], dist = Math.hypot(a.x - b.x, a.y - b.y);
        if(dist < REACH){ ctx.strokeStyle = `rgba(${LIGHT},${(1 - dist / REACH) * .22})`; ctx.lineWidth = 1; ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y); ctx.stroke(); }
      }
      if(pointer.on){
        const dist = Math.hypot(a.x - pointer.x, a.y - pointer.y);
        if(dist < REACH * 1.6){ ctx.strokeStyle = `rgba(${LIGHT},${(1 - dist / (REACH * 1.6)) * .45})`; ctx.beginPath(); ctx.moveTo(a.x, a.y); ctx.lineTo(pointer.x, pointer.y); ctx.stroke(); }
      }
      const fade = a.life === Infinity ? .75 : Math.max(0, a.life / 110);
      ctx.fillStyle = `rgba(${LIGHT},${fade})`; ctx.beginPath(); ctx.arc(a.x, a.y, a.r, 0, Math.PI * 2); ctx.fill();
    }
    if(visible && !document.hidden) frame = requestAnimationFrame(step);
  };
  const go = () => { if(!frame && alive && visible && !document.hidden) frame = requestAnimationFrame(step); };
  const at = e => { const box = canvas.getBoundingClientRect(); return [e.clientX - box.left, e.clientY - box.top]; };
  const move = e => { [pointer.x, pointer.y] = at(e); pointer.on = pointer.y >= 0 && pointer.y <= h; go(); };
  const leave = () => { pointer.on = false; };
  const click = e => { const [x, y] = at(e); if(y < 0 || y > h) return; for(let k = 0; k < 18; k++) dots.push(spawn(x, y, true)); go(); };
  const seen = new IntersectionObserver(([e]) => { visible = e.isIntersecting; go(); });
  const resized = new ResizeObserver(() => size());
  host.addEventListener('pointermove', move, {passive:true});
  host.addEventListener('pointerleave', leave);
  host.addEventListener('pointerdown', click, {passive:true});
  document.addEventListener('visibilitychange', go);
  seen.observe(host); resized.observe(host); size(); go();
  return () => {
    alive = false; cancelAnimationFrame(frame); seen.disconnect(); resized.disconnect();
    host.removeEventListener('pointermove', move); host.removeEventListener('pointerleave', leave); host.removeEventListener('pointerdown', click);
    document.removeEventListener('visibilitychange', go); canvas.remove();
  };
}
