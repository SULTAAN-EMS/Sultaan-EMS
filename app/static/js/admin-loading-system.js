/* ============================================================
   ADMIN GLOBAL ANIMATED LOADING SYSTEM — JS ENGINE
   - One loading event = One fixed design (0% -> 100%)
   - Continuous asymptotic live progress (never stalls)
   - Smooth graceful completion with fade-out (never jarring)
   - Back-navigation / bfcache safe (never freezes at 99%)
   ============================================================ */

(function () {
  'use strict';

  let activeOverlay = null;
  let currentPct = 0;
  let animFrame = null;
  let liveProgressTimer = null;
  let activeDesignId = null;
  let isHiding = false;

  const SEG_BAR_HEIGHTS = [24, 38, 48, 32, 52, 42, 48, 30, 44, 28];

  function buildLoaderHTML(designId) {
    switch (designId) {
      case 1:
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
          </div>`;

      case 2:
        return `
          <div class="loader-card glow-bar-card">
            <div class="loader-subtitle">Processing Request</div>
            <div class="glow-bar-track">
              <div class="glow-bar-fill" id="glowBarFill" style="width: 0%;"></div>
            </div>
            <div class="glow-bar-pct"><span class="pct-val" id="loaderPctText">0</span>%</div>
          </div>`;

      case 3:
        return `
          <div class="loader-card dual-ring-card">
            <div class="dual-ring-stage">
              <div class="ring-outer"></div>
              <div class="ring-inner"></div>
              <div class="dual-ring-pct"><span class="pct-val" id="loaderPctText">0</span>%</div>
            </div>
            <div class="loader-subtitle">Loading...</div>
          </div>`;

      case 4:
        return `
          <div class="loader-card dot-pulse-card">
            <div class="dot-pulse-stage">
              <span class="pulse-dot d1"></span>
              <span class="pulse-dot d2"></span>
              <span class="pulse-dot d3"></span>
            </div>
            <div class="dot-pulse-pct"><span class="pct-val" id="loaderPctText">0</span>%</div>
            <div class="loader-subtitle">Please wait...</div>
          </div>`;

      case 5:
        return `
          <div class="loader-card liquid-fill-card">
            <div class="liquid-circle">
              <div class="liquid-wave" id="liquidWave" style="top: 100%;"></div>
              <div class="liquid-pct"><span class="pct-val" id="loaderPctText">0</span>%</div>
            </div>
            <div class="loader-subtitle">Loading Data</div>
          </div>`;

      case 6:
        return `
          <div class="loader-card segmented-bars-card">
            <div class="seg-bars-row" id="segBarsRow">
              ${SEG_BAR_HEIGHTS.map((h, i) => `<span class="seg-bar" id="segBar_${i}" style="height: ${h}px;"></span>`).join('')}
            </div>
            <div class="seg-pct"><span class="pct-val" id="loaderPctText">0</span>%</div>
            <div class="loader-subtitle">Gathering Information</div>
          </div>`;

      case 7:
        return `
          <div class="minimal-top-line" id="minimalTopLine" style="width: 0%;"></div>
          <div class="loader-card minimal-badge-card">
            <div class="minimal-badge-pill">
              <i class="fa-solid fa-circle-notch fa-spin"></i>
              <span>Loading</span>
              <strong class="minimal-pct"><span class="pct-val" id="loaderPctText">0</span>%</strong>
            </div>
          </div>`;

      case 8:
        return `
          <div class="loader-card skeleton-card">
            <div class="skel-line skel-title"></div>
            <div class="skel-line skel-body1"></div>
            <div class="skel-line skel-body2"></div>
            <div class="skel-pct"><span class="pct-val" id="loaderPctText">0</span>%</div>
            <div class="loader-subtitle">Preparing View</div>
          </div>`;

      case 9:
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
          </div>`;

      case 10:
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
          </div>`;
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
        if (fill) fill.style.width = pct + '%';
        break;
      }
      case 5: {
        const wave = document.getElementById('liquidWave');
        if (wave) wave.style.top = (100 - pct) + '%';
        break;
      }
      case 6: {
        const litCount = Math.floor((pct / 100) * 10);
        for (let i = 0; i < 10; i++) {
          const bar = document.getElementById('segBar_' + i);
          if (bar) bar.classList.toggle('lit', i < litCount);
        }
        break;
      }
      case 7: {
        const line = document.getElementById('minimalTopLine');
        if (line) line.style.width = pct + '%';
        break;
      }
    }
  }

  function getOrPickDesignId(explicitDesignId) {
    if (explicitDesignId) return explicitDesignId;

    var storedDesign = sessionStorage.getItem('admin_loader_design');
    var isStoredActive = sessionStorage.getItem('admin_loader_active') === 'true';

    if (isStoredActive && storedDesign) {
      return parseInt(storedDesign, 10);
    }

    var newDesignId = Math.floor(Math.random() * 10) + 1;
    sessionStorage.setItem('admin_loader_design', newDesignId.toString());
    sessionStorage.setItem('admin_loader_active', 'true');
    return newDesignId;
  }

  /* ----------------------------------------------------------
     LIVE PROGRESS ENGINE
     Smooth continuous asymptotic curve that never freezes.
     Runs at 50ms intervals. Always creeping forward.
     ---------------------------------------------------------- */
  function startLiveProgress(initialPct) {
    stopLiveProgress();
    currentPct = initialPct || 0;
    updatePctVisuals(currentPct);

    liveProgressTimer = setInterval(function () {
      if (currentPct < 55) {
        currentPct += Math.random() * 2.8 + 1.2;          // Fast ramp 0-55
      } else if (currentPct < 78) {
        currentPct += Math.random() * 1.2 + 0.6;          // Steady 55-78
      } else if (currentPct < 92) {
        currentPct += (93 - currentPct) * 0.06;            // Decelerating 78-92
      } else if (currentPct < 97) {
        currentPct += (98 - currentPct) * 0.03;            // Slow creep 92-97
      } else {
        currentPct += 0.04;                                 // Micro-crawl 97+
      }
      if (currentPct > 98.6) currentPct = 98.6;
      updatePctVisuals(currentPct);
    }, 50);
  }

  function stopLiveProgress() {
    if (liveProgressTimer) {
      clearInterval(liveProgressTimer);
      liveProgressTimer = null;
    }
    if (animFrame) {
      cancelAnimationFrame(animFrame);
      animFrame = null;
    }
  }

  /* ----------------------------------------------------------
     FORCE-CLEAR: Immediately nuke all loader state.
     Used on back-navigation / bfcache restore to guarantee
     the page is never left frozen.
     ---------------------------------------------------------- */
  function forceClear() {
    stopLiveProgress();
    isHiding = false;
    currentPct = 0;
    activeDesignId = null;
    sessionStorage.removeItem('admin_loader_active');
    sessionStorage.removeItem('admin_loader_design');

    var overlay = document.getElementById('admin-global-loader');
    if (overlay) {
      overlay.classList.remove('active', 'fade-out');
      overlay.innerHTML = '';
    }
    var topLine = document.getElementById('minimalTopLine');
    if (topLine) topLine.remove();
    activeOverlay = null;
  }

  /* ----------------------------------------------------------
     SHOW / HIDE
     ---------------------------------------------------------- */
  function showLoader(specifiedDesignId, initialPct) {
    if (isHiding) return;  // Don't start new loader while fade-out is running
    var overlay = createOverlay();
    activeDesignId = getOrPickDesignId(specifiedDesignId);

    overlay.innerHTML = buildLoaderHTML(activeDesignId);
    overlay.classList.remove('fade-out');
    overlay.classList.add('active');
    activeOverlay = overlay;

    startLiveProgress(initialPct || 0);
  }

  function hideLoader() {
    if (!activeOverlay || isHiding) return;
    isHiding = true;
    stopLiveProgress();

    // Smooth final glide: current% -> 100% over 400ms with ease-out curve
    var startPct = currentPct;
    var startTime = performance.now();
    var glideDuration = 400;

    function glideStep(now) {
      var elapsed = now - startTime;
      var t = Math.min(elapsed / glideDuration, 1);
      // Ease-out cubic for smooth deceleration into 100%
      var eased = 1 - Math.pow(1 - t, 3);
      currentPct = startPct + (100 - startPct) * eased;
      updatePctVisuals(currentPct);

      if (t < 1) {
        animFrame = requestAnimationFrame(glideStep);
      } else {
        // 100% reached — now trigger gentle fade-out
        if (activeOverlay) {
          activeOverlay.classList.add('fade-out');
        }
        // Wait for CSS fade-out transition (350ms) then fully remove
        setTimeout(function () {
          if (activeOverlay) {
            activeOverlay.classList.remove('active', 'fade-out');
            activeOverlay.innerHTML = '';
          }
          var topLine = document.getElementById('minimalTopLine');
          if (topLine) topLine.remove();

          activeDesignId = null;
          activeOverlay = null;
          isHiding = false;
          sessionStorage.removeItem('admin_loader_active');
          sessionStorage.removeItem('admin_loader_design');
        }, 380);
      }
    }
    animFrame = requestAnimationFrame(glideStep);
  }


  /* ----------------------------------------------------------
     BACK-NAVIGATION / BFCACHE SAFETY
     pageshow fires on back/forward navigation (including
     bfcache restores where DOMContentLoaded does NOT re-fire).
     ---------------------------------------------------------- */
  window.addEventListener('pageshow', function (e) {
    if (e.persisted) {
      // Page was restored from bfcache (swipe-back / browser back)
      forceClear();
    } else {
      // Normal navigation arrival — check for stale loader state
      var isStoredActive = sessionStorage.getItem('admin_loader_active') === 'true';
      if (isStoredActive) {
        // Continuation of a forward navigation — show briefly then complete
        showLoader(null, 80);
        setTimeout(function () { hideLoader(); }, 80);
      }
    }
  });

  // Also listen for popstate (history back/forward without full page reload)
  window.addEventListener('popstate', function () {
    forceClear();
  });


  /* ----------------------------------------------------------
     EVENT INTERCEPTION — DOMContentLoaded
     ---------------------------------------------------------- */
  document.addEventListener('DOMContentLoaded', function () {

    // Intercept form submissions
    document.addEventListener('submit', function (e) {
      var form = e.target;
      if (form && !form.dataset.noLoader) {
        sessionStorage.removeItem('admin_loader_active');
        showLoader();
      }
    });

    // Intercept links and Result Hub navigation tabs
    document.addEventListener('click', function (e) {
      var link = e.target.closest('a[href]');
      var navBtn = e.target.closest('.rh-tab, button[onclick*="location"], [data-nav-loader]');

      if (link) {
        var href = link.getAttribute('href');
        if (
          href &&
          !href.startsWith('#') &&
          !href.startsWith('javascript:') &&
          !link.getAttribute('target') &&
          !link.dataset.noLoader
        ) {
          sessionStorage.removeItem('admin_loader_active');
          showLoader();
        }
      } else if (navBtn && !navBtn.dataset.noLoader) {
        sessionStorage.removeItem('admin_loader_active');
        showLoader();
      }
    });
  });


  /* ----------------------------------------------------------
     FETCH INTERCEPTION
     ---------------------------------------------------------- */
  var originalFetch = window.fetch;
  if (originalFetch) {
    window.fetch = function () {
      var args = arguments;
      var url = typeof args[0] === 'string' ? args[0] : (args[0] && args[0].url);
      var isAutosave = url && url.indexOf('autosave') !== -1;

      if (!isAutosave) {
        sessionStorage.removeItem('admin_loader_active');
        showLoader();
      }

      return originalFetch.apply(this, args).then(function (response) {
        if (!isAutosave) hideLoader();
        return response;
      }).catch(function (err) {
        if (!isAutosave) hideLoader();
        throw err;
      });
    };
  }


  /* ----------------------------------------------------------
     XHR INTERCEPTION — real progress for uploads/downloads
     ---------------------------------------------------------- */
  var originalOpen = XMLHttpRequest.prototype.open;
  var originalSend = XMLHttpRequest.prototype.send;

  XMLHttpRequest.prototype.open = function () {
    this._loaderUrl = arguments[1];
    return originalOpen.apply(this, arguments);
  };

  XMLHttpRequest.prototype.send = function () {
    var isAutosave = this._loaderUrl && this._loaderUrl.indexOf('autosave') !== -1;
    if (!isAutosave) {
      sessionStorage.removeItem('admin_loader_active');
      showLoader();

      this.addEventListener('progress', function (ev) {
        if (ev.lengthComputable && ev.total > 0) {
          var realPct = Math.round((ev.loaded / ev.total) * 97);
          if (realPct > currentPct) {
            currentPct = realPct;
            updatePctVisuals(currentPct);
          }
        }
      });

      this.addEventListener('load', function () { hideLoader(); });
      this.addEventListener('error', function () { forceClear(); });
      this.addEventListener('abort', function () { forceClear(); });
    }
    return originalSend.apply(this, arguments);
  };


  /* ----------------------------------------------------------
     GLOBAL API
     ---------------------------------------------------------- */
  window.AdminLoader = {
    show: function (designId) {
      sessionStorage.removeItem('admin_loader_active');
      showLoader(designId);
    },
    setProgress: function (pct) {
      currentPct = pct;
      updatePctVisuals(pct);
    },
    hide: function () {
      hideLoader();
    }
  };

})();
