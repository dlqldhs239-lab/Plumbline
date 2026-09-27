/* The landing page's field of plumb lines.
   One line per judge of a published event. A line starts out leaning by how
   far that judge's mean sits from the panel's, swings, and settles to
   vertical. Length is how many reviews the judge gave. Dashed is a judge who
   gave every project the same mark.
   No library. Colours come from the theme tokens. The loop stops once the
   field has settled or has left the screen. */
(function () {
  'use strict';
  var node = document.getElementById('field-data');
  var cv = document.getElementById('field');
  if (!node || !cv || !cv.getContext) return;
  var data = JSON.parse(node.textContent);
  if (!data.judges || !data.judges.length) return;

  var cx = cv.getContext('2d');
  var tip = document.getElementById('field-tip');
  var dev = document.getElementById('field-dev');
  var again = document.getElementById('field-again');
  var still = window.matchMedia('(prefers-reduced-motion: reduce)');

  var MAX_ANGLE = 0.62;                                 // radians, for the judge leaning the most
  var DAMPING = 0.42;
  var reach = Math.max(data.max_lean || 0, 0.5);        // rubric points that map to MAX_ANGLE
  var most = Math.max(data.max_n || 1, 1);
  var judges = data.judges.map(function (j, i) {
    var len = 0.36 + (j.n / most) * 0.58;               // share of the field's height
    return {
      n: j.n, mean: j.mean, lean: j.lean, flat: j.flat, len: len,
      theta0: Math.max(-1, Math.min(1, j.lean / reach)) * MAX_ANGLE,
      omega: 2.2 / Math.sqrt(len),
      phase: (i * 0.37) % 1,
      x: 0, y: 0
    };
  });

  var W = 0, H = 0, colour = {}, t0 = 0, running = false, visible = true, frame = 0;

  function tokens() {
    var css = getComputedStyle(document.documentElement);
    ['--accent', '--signal', '--caution', '--ink', '--mute'].forEach(function (name) {
      colour[name] = css.getPropertyValue(name).trim();
    });
  }

  function size() {
    var dpr = Math.min(window.devicePixelRatio || 1, 2);
    W = cv.clientWidth; H = cv.clientHeight;
    cv.width = Math.round(W * dpr); cv.height = Math.round(H * dpr);
    cx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }

  function angle(j, t) {
    if (still.matches) return 0;
    return j.theta0 * Math.exp(-DAMPING * t) * Math.cos(j.omega * t + j.phase);
  }

  function draw(t) {
    cx.clearRect(0, 0, W, H);
    var pad = Math.max(32, W * 0.04);
    var step = judges.length > 1 ? (W - pad * 2) / (judges.length - 1) : 0;
    var sum = 0, widest = 0;
    judges.forEach(function (j, i) {
      var a = angle(j, t);
      var x0 = judges.length > 1 ? pad + i * step : W / 2;
      var L = H * j.len;
      j.x = x0 + Math.sin(a) * L; j.y = Math.cos(a) * L;
      sum += Math.abs(a); widest = Math.max(widest, Math.abs(a));
      var settled = Math.abs(a) < 0.004;
      var line = j.flat ? colour['--caution'] : (settled ? colour['--accent'] : colour['--signal']);
      cx.beginPath(); cx.moveTo(x0, 0); cx.lineTo(j.x, j.y);
      cx.lineWidth = 1; cx.strokeStyle = line;
      cx.setLineDash(j.flat ? [4, 5] : []);
      cx.globalAlpha = settled ? 0.9 : 0.75;
      cx.stroke(); cx.setLineDash([]);
      cx.save(); cx.translate(j.x, j.y); cx.rotate(a + Math.PI / 4);
      cx.globalAlpha = 1;
      cx.fillStyle = j.flat ? colour['--caution'] : (settled ? colour['--accent'] : colour['--ink']);
      var s = 4 + (j.n / most) * 5;
      cx.fillRect(-s / 2, -s / 2, s, s); cx.restore();
    });
    cx.globalAlpha = 1;
    // Mean lean still on show, in rubric points: what the field has left to settle.
    var out = (sum / judges.length) / MAX_ANGLE * reach;
    if (dev) { dev.textContent = out.toFixed(2); dev.classList.toggle('true', out < 0.005); }
    return widest;
  }

  function tick(now) {
    if (!running) return;
    var widest = draw((now - t0) / 1000);
    if (widest < 0.0015) { running = false; draw(1e6); return; }
    frame = requestAnimationFrame(tick);
  }

  function release() {
    cancelAnimationFrame(frame);
    t0 = performance.now();
    if (still.matches || !visible) { running = false; draw(1e6); return; }
    running = true;
    frame = requestAnimationFrame(tick);
  }

  function hover(e) {
    if (!tip) return;
    var r = cv.getBoundingClientRect();
    var mx = e.clientX - r.left, my = e.clientY - r.top, best = null, near = 28;
    judges.forEach(function (j) {
      var d = Math.abs(mx - j.x);
      if (d < near && my < j.y + 28) { near = d; best = j; }
    });
    if (!best) { tip.style.opacity = 0; return; }
    var lean = (best.lean > 0 ? '+' : '') + best.lean.toFixed(2);
    tip.textContent = best.n + ' review' + (best.n === 1 ? '' : 's') + ' · mean ' + best.mean.toFixed(2) +
      ' · ' + (best.flat ? 'every mark the same' : lean + ' from the panel');
    tip.style.opacity = 1;
    tip.style.left = Math.max(8, Math.min(W - tip.offsetWidth - 8, best.x + 12)) + 'px';
    tip.style.top = Math.min(H - 40, best.y + 14) + 'px';
  }

  tokens(); size();
  window.addEventListener('resize', function () { size(); if (!running) draw(1e6); });
  cv.addEventListener('mousemove', hover);
  cv.addEventListener('mouseleave', function () { if (tip) tip.style.opacity = 0; });
  if (again && !still.matches) { again.hidden = false; again.addEventListener('click', release); }
  if (still.addEventListener) still.addEventListener('change', release);
  if ('IntersectionObserver' in window) {
    new IntersectionObserver(function (entries) {
      visible = entries[0].isIntersecting;
      if (!visible) { running = false; cancelAnimationFrame(frame); }
    }, { threshold: 0.01 }).observe(cv);
  }
  // Wait for the display face, so the first frame is not drawn behind a reflow.
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(release); else release();
})();
