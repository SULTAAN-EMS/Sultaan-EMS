/* ============================================================
   ADMIN GLOBAL ANIMATED LOADING SYSTEM — JS ENGINE
   Guarantees: One loading event = One fixed design from 0% -> 100%
   Next loading event = Randomly picks again
   ============================================================ */

(function () {
  'use strict';

  let activeOverlay = null;
  let currentPct = 0;
  let animFrame = null;
  let activeDesignId = null;
  let isEventActive = false;

  // Segmented bars heights array for Design 6
  const SEG_BAR_HEIGHTS = [24, 38, 48, 32, 52, 42, 48, 30, 44, 28];

  function buildLoaderHTML(designId) {
    switch (designId) {
      case 1: // Circular Ring
        return `
          <div class="loader-card circular-ring-card">
            <div class="circular-ring-wrap">
              <svg class="ring-svg" viewBox="0 0 100 100">
                <defs>
                  <linearGradient id="ringGrad" x1="0%" y1="0%" x2="100%" y2="100%">
                    <stop offset="0%" stop-color="#06b6d4" />
                    <stop offset="100%" stop-color="#3b82f6" />
                  </linearGradient>
                </defs>
                <circle class="ring-bg" cx="50" cy="50" r="42" />
                <circle class="ring-fill" id="ringFillCircle" cx="50" cy="50" r="42" style="stroke-dasharray: 263.89; stroke-dashoffset: 263.89;" />
              </svg>
              <div class="ring-pct"><span class="pct-val" id="loaderPctText">0</span>%</div>
            </div>
            <div class="loader-subtitle">Loading workspace...</div>
          </div>
        `;

      case 2: // Glow Bar
        return `
          <div class="loader-card glow-bar-card">
            <div class="loader-subtitle">Processing Request</div>
            <div class="glow-bar-track">
              <div class="glow-bar-fill" id="glowBarFill" style="width: 0%;"></div>
            </div>
            <div class="glow-bar-pct"><span class="pct-val" id="loaderPctText">0</span>%</div>
          </div>
        `;

      case 3: // Dual Ring Spinner
        return `
          <div class="loader-card dual-ring-card">
            <div class="dual-ring-stage">
              <div class="ring-outer"></div>
              <div class="ring-inner"></div>
              <div class="dual-ring-pct"><span class="pct-val" id="loaderPctText">0</span>%</div>
            </div>
            <div class="loader-subtitle">Loading...</div>
          </div>
        `;

      case 4: // Dot Pulse
        return `
          <div class="loader-card dot-pulse-card">
            <div class="dot-pulse-stage">
              <span class="pulse-dot d1"></span>
              <span class="pulse-dot d2"></span>
              <span class="pulse-dot d3"></span>
            </div>
            <div class="dot-pulse-pct"><span class="pct-val" id="loaderPctText">0</span>%</div>
            <div class="loader-subtitle">Please wait...</div>
          </div>
        `;

      case 5: // Liquid Fill
        return `
          <div class="loader-card liquid-fill-card">
            <div class="liquid-circle">
              <div class="liquid-wave" id="liquidWave" style="top: 100%;"></div>
              <div class="liquid-pct"><span class="pct-val" id="loaderPctText">0</span>%</div>
            </div>
            <div class="loader-subtitle">Loading Data</div>
          </div>
        `;

      case 6: // Segmented Bars
        return `
          <div class="loader-card segmented-bars-card">
            <div class="seg-bars-row" id="segBarsRow">
              ${SEG_BAR_HEIGHTS.map((h, i) => `<span class="seg-bar" id="segBar_${i}" style="height: ${h}px;"></span>`).join('')}
            </div>
            <div class="seg-pct"><span class="pct-val" id="loaderPctText">0</span>%</div>
            <div class="loader-subtitle">Gathering Information</div>
          </div>
        `;

      case 7: // Minimal Badge
        return `
          <div class="minimal-top-line" id="minimalTopLine" style="width: 0%;"></div>
          <div class="loader-card minimal-badge-card">
            <div class="minimal-badge-pill">
              <i class="fa-solid fa-circle-notch fa-spin"></i>
              <span>Loading</span>
              <strong class="minimal-pct"><span class="pct-val" id="loaderPctText">0</span>%</strong>
            </div>
          </div>
        `;

      case 8: // Skeleton Pulse
        return `
          <div class="loader-card skeleton-card">
            <div class="skel-line skel-title"></div>
            <div class="skel-line skel-body1"></div>
            <div class="skel-line skel-body2"></div>
            <div class="skel-pct"><span class="pct-val" id="loaderPctText">0</span>%</div>
            <div class="loader-subtitle">Preparing View</div>
          </div>
        `;

      case 9: // Orbit Loader
        return `
          <div class="loader-card orbit-card">
            <div class="orbit-stage">
              <div class="orbit-center"></div>
              <div class="orbit-path">
                <div class="orbit-dot"></div>
              </div>
            </div>
            <div class="orbit-pct"><span class="pct-val" id="loaderPctText">0</span>%</div>
            <div class="loader-subtitle">Syncing Data</div>
          </div>
        `;

      case 10: // Wave Bars
      default:
        return `
          <div class="loader-card wave-bars-card">
            <div class="wave-bars-row">
              <span class="wb wb1"></span>
              <span class="wb wb2"></span>
              <span class="wb wb3"></span>
              <span class="wb wb4"></span>
              <span class="wb wb5"></span>
            </div>
            <div class="wave-pct"><span class="pct-val" id="loaderPctText">0</span>%</div>
            <div class="loader-subtitle">Processing...</div>
          </div>
        `;
    }
  }

  function createOverlay() {
    let overlay = document.getElementById('admin-global-loader');
    if (!overlay) {
      overlay = document.createElement('div');
      overlay.id = 'admin-global-loader';
      overlay.className = 'admin-loader-overlay';
      document.body.appendChild(overlay);
    }
    return overlay;
  }

  function updatePctVisuals(pct) {
    const textEl = document.getElementById('loaderPctText');
    if (textEl) textEl.textContent = Math.round(pct);

    switch (activeDesignId) {
      case 1: {
        const ring = document.getElementById('ringFillCircle');
        if (ring) {
          const offset = 263.89 - (263.89 * pct / 100);
          ring.style.strokeDashoffset = offset;
        }
        break;
      }
      case 2: {
        const fill = document.getElementById('glowBarFill');
        if (fill) fill.style.width = `${pct}%`;
        break;
      }
      case 5: {
        const wave = document.getElementById('liquidWave');
        if (wave) wave.style.top = `${100 - pct}%`;
        break;
      }
      case 6: {
        const litCount = Math.floor((pct / 100) * 10);
        for (let i = 0; i < 10; i++) {
          const bar = document.getElementById(`segBar_${i}`);
          if (bar) bar.classList.toggle('lit', i < litCount);
        }
        break;
      }
      case 7: {
        const line = document.getElementById('minimalTopLine');
        if (line) line.style.width = `${pct}%`;
        break;
      }
    }
  }

  function getOrPickDesignId(explicitDesignId) {
    if (explicitDesignId) {
      return explicitDesignId;
    }
    // Check if an event is currently active in session
    const storedDesign = sessionStorage.getItem('admin_loader_design');
    const isStoredActive = sessionStorage.getItem('admin_loader_active') === 'true';

    if (isStoredActive && storedDesign) {
      return parseInt(storedDesign, 10);
    }

    // New event -> pick a brand new random design 1..10
    const newDesignId = Math.floor(Math.random() * 10) + 1;
    sessionStorage.setItem('admin_loader_design', newDesignId.toString());
    sessionStorage.setItem('admin_loader_active', 'true');
    return newDesignId;
  }

  function showLoader(specifiedDesignId, initialPct = 0) {
    const overlay = createOverlay();
    activeDesignId = getOrPickDesignId(specifiedDesignId);
    isEventActive = true;

    overlay.innerHTML = buildLoaderHTML(activeDesignId);
    overlay.classList.add('active');
    activeOverlay = overlay;
    currentPct = initialPct;
    updatePctVisuals(initialPct);
  }

  function hideLoader() {
    if (!activeOverlay) return;
    updatePctVisuals(100);
    setTimeout(() => {
      if (activeOverlay) {
        activeOverlay.classList.remove('active');
      }
      const topLine = document.getElementById('minimalTopLine');
      if (topLine) topLine.remove();

      // Complete current loading event -> clear session storage so NEXT event picks a new design
      isEventActive = false;
      activeDesignId = null;
      sessionStorage.removeItem('admin_loader_active');
      sessionStorage.removeItem('admin_loader_design');
    }, 220);
  }

  function animatePctTo(targetPct, durationMs, onComplete) {
    if (animFrame) cancelAnimationFrame(animFrame);
    const startPct = currentPct;
    const startTime = performance.now();

    function step(now) {
      const elapsed = now - startTime;
      const progress = Math.min(elapsed / durationMs, 1);
      currentPct = startPct + (targetPct - startPct) * progress;
      updatePctVisuals(currentPct);

      if (progress < 1) {
        animFrame = requestAnimationFrame(step);
      } else if (onComplete) {
        onComplete();
      }
    }
    animFrame = requestAnimationFrame(step);
  }

  // Intercept navigation & page loads
  document.addEventListener('DOMContentLoaded', () => {
    const isStoredActive = sessionStorage.getItem('admin_loader_active') === 'true';

    // If page load is the continuation of an ongoing loading event:
    if (isStoredActive) {
      showLoader(null, 75); // Use same fixed design, continue from 75% -> 100%
      animatePctTo(100, 350, hideLoader);
    }

    // Intercept form submissions
    document.addEventListener('submit', (e) => {
      const form = e.target;
      if (form && !form.dataset.noLoader) {
        showLoader(); // Fixes design for this event
        animatePctTo(90, 800);
      }
    });

    // Intercept links and Result Hub navigation tabs
    document.addEventListener('click', (e) => {
      const link = e.target.closest('a[href]');
      const navBtn = e.target.closest('.rh-tab, button[onclick*="location"], [data-nav-loader]');

      if (link) {
        const href = link.getAttribute('href');
        if (
          href &&
          !href.startsWith('#') &&
          !href.startsWith('javascript:') &&
          !link.getAttribute('target') &&
          !link.dataset.noLoader
        ) {
          sessionStorage.removeItem('admin_loader_active');
          showLoader(); // Fixes random design for this event (0% -> 100%)
          animatePctTo(90, 800);
        }
      } else if (navBtn && !navBtn.dataset.noLoader) {
        sessionStorage.removeItem('admin_loader_active');
        showLoader(); // Fixes random design for this navigation event
        animatePctTo(90, 800);
      }
    });
  });

  // Intercept fetch requests
  const originalFetch = window.fetch;
  if (originalFetch) {
    window.fetch = async function (...args) {
      const url = typeof args[0] === 'string' ? args[0] : (args[0] && args[0].url);
      const isAutosave = url && url.includes('autosave');
      if (!isAutosave) {
        showLoader();
        animatePctTo(75, 400);
      }
      try {
        const response = await originalFetch.apply(this, args);
        if (!isAutosave) {
          animatePctTo(100, 180, hideLoader);
        }
        return response;
      } catch (err) {
        if (!isAutosave) hideLoader();
        throw err;
      }
    };
  }

  // Expose API
  window.AdminLoader = {
    show: function (designId) {
      showLoader(designId);
      animatePctTo(90, 600);
    },
    setProgress: function (pct) {
      currentPct = pct;
      updatePctVisuals(pct);
    },
    hide: function () {
      animatePctTo(100, 180, hideLoader);
    }
  };

})();
