setTimeout(() => {
  document.querySelectorAll(".toast").forEach((toast) => {
    toast.style.opacity = "0";
    toast.style.transform = "translateY(-8px)";
    setTimeout(() => toast.remove(), 250);
  });
}, 4500);

if (window.APP_TRANSLATIONS) {
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  const skipTags = new Set(["SCRIPT", "STYLE", "TEXTAREA"]);
  const nodes = [];
  while (walker.nextNode()) {
    const node = walker.currentNode;
    if (!node.parentElement || skipTags.has(node.parentElement.tagName)) continue;
    nodes.push(node);
  }
  nodes.forEach((node) => {
    const original = node.nodeValue.trim();
    if (original && window.APP_TRANSLATIONS[original]) {
      node.nodeValue = node.nodeValue.replace(original, window.APP_TRANSLATIONS[original]);
    }
  });
}

const resultPanel = document.querySelector(".premium-result");
if (resultPanel) {
  const status = resultPanel.dataset.resultStatus;
  const isPass = status === "Gudbay";
  const burst = document.createElement("div");
  burst.className = `confetti-burst ${isPass ? "pass" : "support"}`;
  for (let i = 0; i < 34; i += 1) {
    const piece = document.createElement("span");
    piece.style.setProperty("--x", `${Math.random() * 100}%`);
    piece.style.setProperty("--d", `${Math.random() * 1.8}s`);
    piece.style.setProperty("--r", `${Math.random() * 360}deg`);
    burst.appendChild(piece);
  }
  document.body.appendChild(burst);
  setTimeout(() => burst.remove(), 10000);
}

/* ============================================================
   AMBIENT BACKGROUND PARTICLE SYSTEM — result page only
   Targets .premium-homepage.has-result exclusively.
   All layers appended to document.body (bypasses overflow:hidden
   and stacking-context clipping on the parent element).
   CSS z-index: 1 — above background, below all foreground cards.
   Suppressed by prefers-reduced-motion.
   ============================================================ */
(() => {
  const homepage = document.querySelector('.premium-homepage.has-result');
  if (!homepage) {
    // Not on the result page — exit silently
    return;
  }
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
    console.log('[BG particles] prefers-reduced-motion active — skipped');
    return;
  }

  console.log('[BG particles] Result page detected — initialising particle layers');

  /* ---- Helpers ---- */
  const rnd  = (min, max) => min + Math.random() * (max - min);
  const pick = arr => arr[Math.floor(Math.random() * arr.length)];

  const SIZES    = ['sz-xs', 'sz-sm', 'sz-md', 'sz-lg'];
  const COLORS   = ['col-cyan', 'col-gold', 'col-white', 'col-blue'];
  const BTCOLORS = ['col-blue', 'col-cyan', 'col-gold', 'col-white', 'col-teal'];
  const BTSIZES  = ['dot-sm', 'dot-md', 'dot-lg'];

  /* Append layers to document.body — avoids parent overflow:hidden clipping */
  function makeLayer(className) {
    const layer = document.createElement('div');
    layer.className = className;
    document.body.appendChild(layer);
    return layer;
  }

  /* ================================================================
     LAYER 1 — Drifting ambient dust  (90 particles)
     ================================================================ */
  const dustLayer = makeLayer('ambient-dust-layer');

  for (let i = 0; i < 60; i++) {
    const el = document.createElement('div');
    el.className = `ambient-dust ${pick(SIZES)} ${pick(COLORS)}`;
    el.style.left = `${rnd(0, 100)}%`;
    el.style.top  = `${rnd(-10, 110)}%`;
    el.style.setProperty('--drift-x',   `${rnd(-70, 70)}px`);
    el.style.setProperty('--drift-y',   `${rnd(-90, -20)}px`);
    el.style.setProperty('--dust-dur',  `${rnd(12, 26)}s`);
    el.style.setProperty('--dust-peak', `${rnd(0.22, 0.42)}`);
    el.style.animationDelay = `${rnd(0, 22)}s`;
    dustLayer.appendChild(el);
  }

  /* ================================================================
     LAYER 2 — Stationary twinkle specks  (50 particles)
     ================================================================ */
  const twinkleLayer = makeLayer('ambient-twinkle-layer');

  for (let i = 0; i < 36; i++) {
    const el = document.createElement('div');
    el.className = `ambient-twinkle ${pick(SIZES)} ${pick(COLORS)}`;
    el.style.left = `${rnd(0, 100)}%`;
    el.style.top  = `${rnd(0, 100)}%`;
    el.style.setProperty('--twinkle-dur',  `${rnd(4, 10)}s`);
    el.style.setProperty('--twinkle-peak', `${rnd(0.22, 0.50)}`);
    el.style.animationDelay = `${rnd(0, 14)}s`;
    twinkleLayer.appendChild(el);
  }

  /* ================================================================
     LAYER 3 — Festive rising burst + sparkle ring
     ================================================================ */
  const festiveLayer = makeLayer('festive-burst-layer');

  let activeBursts = 0;
  const MAX_ACTIVE = 60;

  function createFestiveBurst() {
    if (activeBursts >= MAX_ACTIVE) return;
    activeBursts++;

    const dot = document.createElement('div');
    dot.className = `festive-burst ${pick(BTCOLORS)} ${pick(BTSIZES)}`;
    dot.style.position = 'fixed';
    dot.style.left = `${rnd(5, 95)}vw`;
    dot.style.top  = `${rnd(65, 95)}vh`;
    dot.style.setProperty('--rise-height', `${rnd(30, 55)}vh`);
    dot.style.setProperty('--start-y', '0px');

    const riseDur = rnd(3.5, 6.0);
    dot.style.animation = `festiveRise ${riseDur}s ease-out forwards`;
    festiveLayer.appendChild(dot);

    /* Read position BEFORE removal to get accurate screen coords */
    const burstAt = riseDur * 0.65 * 1000;
    setTimeout(() => {
      const rect = dot.getBoundingClientRect();
      const cx = rect.left + rect.width  / 2;
      const cy = rect.top  + rect.height / 2;
      dot.remove();
      activeBursts = Math.max(0, activeBursts - 1);
      createSparkleRing(cx, cy);
    }, burstAt);
  }

  function createSparkleRing(cx, cy) {
    const dotCount  = Math.round(rnd(8, 14));
    const radius    = rnd(30, 65);
    const baseColor = pick(BTCOLORS);

    /* Central petal bloom */
    const petal = document.createElement('div');
    petal.className = `festive-burst ${baseColor} dot-md`;
    petal.style.position  = 'fixed';
    petal.style.left      = `${cx}px`;
    petal.style.top       = `${cy}px`;
    petal.style.transform = 'translate(-50%, -50%)';
    petal.style.animation = `festivePetal ${rnd(0.8, 1.3)}s ease-out forwards`;
    festiveLayer.appendChild(petal);
    setTimeout(() => petal.remove(), 1400);

    /* Radial scatter ring */
    for (let i = 0; i < dotCount; i++) {
      const angle = (i / dotCount) * Math.PI * 2 + rnd(-0.25, 0.25);
      const dist  = rnd(radius * 0.5, radius);
      const sx    = Math.cos(angle) * dist;
      const sy    = Math.sin(angle) * dist;

      const sparkle = document.createElement('div');
      sparkle.className     = `festive-burst ${pick(BTCOLORS)} ${pick(BTSIZES)}`;
      sparkle.style.position  = 'fixed';
      sparkle.style.left      = `${cx}px`;
      sparkle.style.top       = `${cy}px`;
      sparkle.style.transform = 'translate(-50%, -50%)';
      sparkle.style.setProperty('--sparkle-x', `${sx}px`);
      sparkle.style.setProperty('--sparkle-y', `${sy}px`);

      const dur = rnd(1.1, 2.2);
      sparkle.style.animation      = `festiveSparkle ${dur}s ease-out forwards`;
      sparkle.style.animationDelay = `${rnd(0, 0.15)}s`;
      festiveLayer.appendChild(sparkle);
      setTimeout(() => sparkle.remove(), (dur + 0.4) * 1000);
    }
  }

  /* 10 staggered initial bursts (0.3s–8s after load) */
  for (let i = 0; i < 10; i++) {
    setTimeout(createFestiveBurst, rnd(300, 8000));
  }

  /* Self-rescheduling recurring burst (never mechanical) */
  function scheduleNext() {
    setTimeout(() => {
      createFestiveBurst();
      scheduleNext();
    }, rnd(2200, 5000));
  }
  scheduleNext();

  console.log('[BG particles] ✓ Layers created — dust:90 twinkle:50 festive:recurring');
})();
