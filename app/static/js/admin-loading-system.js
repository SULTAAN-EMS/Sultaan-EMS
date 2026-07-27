/* ============================================================
   ADMIN GLOBAL ANIMATED LOADING SYSTEM — JS ENGINE (42 DESIGNS)
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
          </div>`;

      case 2: // Glow Bar
        return `
          <div class="loader-card glow-bar-card">
            <div class="glow-bar-track">
              <div class="glow-bar-fill" id="glowBarFill" style="width: 0%;"></div>
            </div>
            <div class="glow-bar-pct"><span class="pct-val" id="loaderPctText">0</span>%</div>
          </div>`;

      case 3: // Dual Ring Spinner
        return `
          <div class="loader-card dual-ring-card">
            <div class="dual-ring-stage">
              <div class="ring-outer"></div>
              <div class="ring-inner"></div>
              <div class="dual-ring-pct"><span class="pct-val" id="loaderPctText">0</span>%</div>
            </div>
          </div>`;

      case 4: // Dot Pulse
        return `
          <div class="loader-card dot-pulse-card">
            <div class="dot-pulse-stage">
              <span class="pulse-dot d1"></span>
              <span class="pulse-dot d2"></span>
              <span class="pulse-dot d3"></span>
            </div>
            <div class="dot-pulse-pct"><span class="pct-val" id="loaderPctText">0</span>%</div>
          </div>`;

      case 5: // Liquid Fill
        return `
          <div class="loader-card liquid-fill-card">
            <div class="liquid-circle">
              <div class="liquid-wave" id="liquidWave" style="top: 100%;"></div>
              <div class="liquid-pct"><span class="pct-val" id="loaderPctText">0</span>%</div>
            </div>
          </div>`;

      case 6: // Segmented Bars
        return `
          <div class="loader-card segmented-bars-card">
            <div class="seg-bars-row" id="segBarsRow">
              ${SEG_BAR_HEIGHTS.map((h, i) => `<span class="seg-bar" id="segBar_${i}" style="height: ${h}px;"></span>`).join('')}
            </div>
            <div class="seg-pct"><span class="pct-val" id="loaderPctText">0</span>%</div>
          </div>`;

      case 7: // Minimal Badge
        return `
          <div class="minimal-top-line" id="minimalTopLine" style="width: 0%;"></div>
          <div class="loader-card minimal-badge-card">
            <div class="minimal-badge-pill">
              <i class="fa-solid fa-circle-notch fa-spin"></i>
              <strong class="minimal-pct"><span class="pct-val" id="loaderPctText">0</span>%</strong>
            </div>
          </div>`;

      case 8: // Skeleton Pulse
        return `
          <div class="loader-card skeleton-card">
            <div class="skel-line skel-title"></div>
            <div class="skel-line skel-body1"></div>
            <div class="skel-line skel-body2"></div>
            <div class="skel-pct"><span class="pct-val" id="loaderPctText">0</span>%</div>
          </div>`;

      case 9: // Orbit Loader
        return `
          <div class="loader-card orbit-card">
            <div class="orbit-stage">
              <div class="orbit-center"></div>
              <div class="orbit-path"><div class="orbit-dot"></div></div>
            </div>
            <div class="orbit-pct"><span class="pct-val" id="loaderPctText">0</span>%</div>
          </div>`;

      case 10: // Wave Bars
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
          </div>`;

      // 11-15: Liquid Fill Variants
      case 11:
      case 12:
      case 13:
      case 14:
      case 15: {
        const theme = ['ocean', 'emerald', 'purple', 'golden', 'lava'][designId - 11];
        return `
          <div class="loader-card liquid-fill-card">
            <div class="liquid-circle">
              <div class="liquid-wave ${theme}" id="liquidWave" style="top: 100%;"></div>
              <div class="liquid-pct"><span class="pct-val" id="loaderPctText">0</span>%</div>
            </div>
          </div>`;
      }

      // 16-20: Dual Ring Variants
      case 16:
      case 17:
      case 18:
      case 19:
      case 20: {
        const theme = ['fire', 'cyber', 'forest', 'solar', 'royal'][designId - 16];
        return `
          <div class="loader-card dual-ring-card">
            <div class="dual-ring-stage ${theme}">
              <div class="ring-outer"></div>
              <div class="ring-inner"></div>
              <div class="dual-ring-pct"><span class="pct-val" id="loaderPctText">0</span>%</div>
            </div>
          </div>`;
      }

      // 21: Gradient Ring Glow
      case 21:
        return `
          <div class="loader-card">
            <div class="ring-glow-stage">
              <svg class="ring-glow-svg" viewBox="0 0 100 100">
                <circle class="ring-bg" cx="50" cy="50" r="42" />
                <circle class="ring-fill" id="ringFillCircle" cx="50" cy="50" r="42" style="stroke-dasharray: 263.89; stroke-dashoffset: 263.89;" />
              </svg>
              <div class="ring-pct"><span class="pct-val" id="loaderPctText">0</span>%</div>
            </div>
          </div>`;

      // 22: Neumorphic Bar
      case 22:
        return `
          <div class="loader-card">
            <div class="neu-bar-track">
              <div class="neu-bar-fill" id="glowBarFill" style="width: 0%;"></div>
            </div>
            <div class="glow-bar-pct"><span class="pct-val" id="loaderPctText">0</span>%</div>
          </div>`;

      // 23: Hexagon Spinner
      case 23:
        return `
          <div class="loader-card">
            <div class="hex-spin-wrap">
              <svg viewBox="0 0 100 100" style="width:100%;height:100%;fill:none;stroke:#38bdf8;stroke-width:6;">
                <polygon points="50,5 90,25 90,75 50,95 10,75 10,25" />
              </svg>
            </div>
            <div class="dot-pulse-pct" style="margin-top:12px;"><span class="pct-val" id="loaderPctText">0</span>%</div>
          </div>`;

      // 24: Orbiting Particles
      case 24:
        return `
          <div class="loader-card">
            <div class="particle-orbit-wrap">
              <div class="p-dot" style="top:0;left:40px;"></div>
              <div class="p-dot" style="bottom:0;left:40px;"></div>
              <div class="dual-ring-pct"><span class="pct-val" id="loaderPctText">0</span>%</div>
            </div>
          </div>`;

      // 25: Morphing Blob
      case 25:
        return `
          <div class="loader-card">
            <div class="morph-blob">
              <span class="pct-val" id="loaderPctText" style="font-size:1.3rem;font-weight:800;">0</span>%
            </div>
          </div>`;

      // 26: Split Ring (Refined: live percentage number clearly visible in center while ring spins)
      case 26:
        return `
          <div class="loader-card">
            <div class="split-ring-wrap">
              <div class="split-ring-spin"></div>
              <div class="split-ring-pct"><span class="pct-val" id="loaderPctText">0</span>%</div>
            </div>
          </div>`;

      // 27: Pulse Ripple
      case 27:
        return `
          <div class="loader-card">
            <div class="ripple-wrap">
              <div class="ripple-ring"></div>
              <div class="ripple-ring"></div>
              <div class="dual-ring-pct"><span class="pct-val" id="loaderPctText">0</span>%</div>
            </div>
          </div>`;

      // 28: Rising Bars
      case 28:
        return `
          <div class="loader-card">
            <div class="rising-bars-wrap">
              <div class="r-bar" id="rBar_0" style="height:10px;"></div>
              <div class="r-bar" id="rBar_1" style="height:10px;"></div>
              <div class="r-bar" id="rBar_2" style="height:10px;"></div>
              <div class="r-bar" id="rBar_3" style="height:10px;"></div>
              <div class="r-bar" id="rBar_4" style="height:10px;"></div>
            </div>
            <div class="seg-pct"><span class="pct-val" id="loaderPctText">0</span>%</div>
          </div>`;

      // 29: Infinity Loop
      case 29:
        return `
          <div class="loader-card">
            <svg class="infinity-wrap" viewBox="0 0 100 50">
              <path d="M30,25 C10,25 10,5 30,5 C50,5 50,45 70,45 C90,45 90,25 70,25 C50,25 50,5 30,5" />
            </svg>
            <div class="glow-bar-pct" style="margin-top:8px;"><span class="pct-val" id="loaderPctText">0</span>%</div>
          </div>`;

      // 30: DNA Helix Dots
      case 30:
        return `
          <div class="loader-card">
            <div class="dna-wrap">
              <div class="dna-pair"><span class="pulse-dot"></span><span class="pulse-dot"></span></div>
              <div class="dna-pair" style="animation-delay:0.3s;"><span class="pulse-dot"></span><span class="pulse-dot"></span></div>
              <div class="dna-pair" style="animation-delay:0.6s;"><span class="pulse-dot"></span><span class="pulse-dot"></span></div>
            </div>
            <div class="dot-pulse-pct" style="margin-top:10px;"><span class="pct-val" id="loaderPctText">0</span>%</div>
          </div>`;

      // 31: Rotating Squares
      case 31:
        return `
          <div class="loader-card">
            <div class="squares-wrap">
              <div class="sq"></div>
              <div class="sq"></div>
            </div>
            <div class="glow-bar-pct" style="margin-top:14px;"><span class="pct-val" id="loaderPctText">0</span>%</div>
          </div>`;

      // 32: Glassmorphism Card
      case 32:
        return `
          <div class="glass-wrap">
            <i class="fa-solid fa-spinner fa-spin" style="font-size:2rem;color:#38bdf8;margin-bottom:10px;"></i>
            <div style="font-size:1.4rem;font-weight:800;color:#fff;"><span class="pct-val" id="loaderPctText">0</span>%</div>
          </div>`;

      // 33: Triangle Rotate
      case 33:
        return `
          <div class="loader-card">
            <div class="tri-wrap">
              <svg viewBox="0 0 100 100" style="width:100%;height:100%;fill:none;stroke:#38bdf8;stroke-width:6;">
                <polygon points="50,15 90,85 10,85" />
              </svg>
            </div>
            <div class="glow-bar-pct" style="margin-top:10px;"><span class="pct-val" id="loaderPctText">0</span>%</div>
          </div>`;

      // 34: Gradient Wave Circle
      case 34:
        return `
          <div class="loader-card">
            <div class="wave-circle-wrap">
              <div class="wave-circle-inner">
                <span class="pct-val" id="loaderPctText" style="font-size:1.2rem;font-weight:800;">0</span>%
              </div>
            </div>
          </div>`;

      // 35: Progress Necklace
      case 35:
        return `
          <div class="loader-card">
            <div class="necklace-wrap" id="necklaceWrap">
              <div class="ring-pct"><span class="pct-val" id="loaderPctText">0</span>%</div>
            </div>
          </div>`;

      // 36: Colorful Equalizer (Refined: active pulsing/moving bars)
      case 36:
        return `
          <div class="loader-card">
            <div class="eq-wrap">
              <span class="eq-bar"></span>
              <span class="eq-bar"></span>
              <span class="eq-bar"></span>
              <span class="eq-bar"></span>
              <span class="eq-bar"></span>
            </div>
            <div class="wave-pct"><span class="pct-val" id="loaderPctText">0</span>%</div>
          </div>`;

      // 37: Spiral Loader
      case 37:
        return `
          <div class="loader-card">
            <div class="spiral-wrap">
              <svg viewBox="0 0 100 100" style="width:100%;height:100%;fill:none;stroke:#06b6d4;stroke-width:5;">
                <path d="M50,50 A40,40 0 1,0 90,50 A30,30 0 1,0 80,50 A20,20 0 1,0 70,50" />
              </svg>
            </div>
            <div class="glow-bar-pct" style="margin-top:10px;"><span class="pct-val" id="loaderPctText">0</span>%</div>
          </div>`;

      // 38: Flip Card (Refined: active 3D flip/rotation animation while loading)
      case 38:
        return `
          <div class="loader-card">
            <div class="flip-card-stage">
              <div class="flip-card-inner">
                <div><span class="pct-val" id="loaderPctText">0</span>%</div>
              </div>
            </div>
          </div>`;

      // 39: Half-Circle Gauge
      case 39:
        return `
          <div class="loader-card">
            <div class="gauge-wrap">
              <div class="gauge-bg"></div>
              <div class="gauge-fill" id="gaugeFill"></div>
            </div>
            <div class="glow-bar-pct" style="margin-top:8px;"><span class="pct-val" id="loaderPctText">0</span>%</div>
          </div>`;

      // 40: Constellation Loader
      case 40:
        return `
          <div class="loader-card">
            <div class="constellation-wrap">
              <div class="c-star" style="top:10px;left:20px;"></div>
              <div class="c-star" style="top:30px;right:15px;animation-delay:0.3s;"></div>
              <div class="c-star" style="bottom:15px;left:35px;animation-delay:0.6s;"></div>
              <div class="ring-pct"><span class="pct-val" id="loaderPctText">0</span>%</div>
            </div>
          </div>`;

      // 41: Mathematical Symbol (Pi π)
      case 41:
        return `
          <div class="loader-card">
            <div class="pi-loader-wrap">
              <svg class="pi-svg" viewBox="0 0 100 100">
                <defs>
                  <linearGradient id="piGradient" x1="0%" y1="100%" x2="0%" y2="0%">
                    <stop offset="0%" stop-color="#06b6d4" />
                    <stop offset="100%" stop-color="#3b82f6" />
                  </linearGradient>
                  <mask id="piMask">
                    <path d="M 20,22 C 20,16 80,16 80,22 C 80,26 72,26 72,26 L 72,70 C 72,78 78,82 82,82 L 85,82 M 38,26 L 38,72 C 38,80 32,84 24,84 L 20,84" 
                          fill="none" stroke="#ffffff" stroke-width="12" stroke-linecap="round" stroke-linejoin="round" />
                  </mask>
                </defs>
                <!-- Background dimmed Pi -->
                <path d="M 20,22 C 20,16 80,16 80,22 C 80,26 72,26 72,26 L 72,70 C 72,78 78,82 82,82 L 85,82 M 38,26 L 38,72 C 38,80 32,84 24,84 L 20,84" 
                      fill="none" stroke="rgba(255,255,255,0.15)" stroke-width="12" stroke-linecap="round" stroke-linejoin="round" />
                <!-- Rising Gradient Fill Pi -->
                <rect x="0" y="0" width="100" height="100" fill="url(#piGradient)" mask="url(#piMask)" id="piFillRect" />
              </svg>
              <div class="pi-pct"><span class="pct-val" id="loaderPctText">0</span>%</div>
            </div>
          </div>`;

      // 42: Sultan Royal Loading Badge (Simplified Design)
      // Top: Gold Crown -> Circle with Gold Ring + Rising Blue Water Fill + Centered % -> "SULTAN" Text Below
      case 42:
      default:
        return `
          <div class="sultan-badge-container">
            <!-- 1. Crown at top (Gold) -->
            <svg class="sultan-crown-svg" viewBox="0 0 100 70">
              <defs>
                <linearGradient id="sultanGoldGrad" x1="0%" y1="0%" x2="100%" y2="100%">
                  <stop offset="0%" stop-color="#fff3c4" />
                  <stop offset="50%" stop-color="#d4af37" />
                  <stop offset="100%" stop-color="#8a6b1f" />
                </linearGradient>
              </defs>
              <path d="M 10,60 L 15,25 L 35,42 L 50,15 L 65,42 L 85,25 L 90,60 Z" fill="url(#sultanGoldGrad)" />
              <circle cx="15" cy="21" r="4" fill="#fff3c4" />
              <circle cx="35" cy="38" r="4" fill="#fff3c4" />
              <circle cx="50" cy="11" r="5" fill="#fff3c4" />
              <circle cx="65" cy="38" r="4" fill="#fff3c4" />
              <circle cx="85" cy="21" r="4" fill="#fff3c4" />
              <rect x="10" y="62" width="80" height="6" rx="3" fill="url(#sultanGoldGrad)" />
            </svg>

            <!-- 2. Circle loading area (Gold Ring + Blue Water Fill + % inside) -->
            <div class="sultan-circle-stage">
              <div class="sultan-water-fill" id="sultanWaterFill" style="top: 100%;"></div>
              <div class="sultan-circle-pct"><span class="pct-val" id="loaderPctText">0</span>%</div>
            </div>

            <!-- 3. "SULTAN" text directly below -->
            <div class="sultan-text-title">SULTAN</div>
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
      case 1:
      case 21: {
        const ring = document.getElementById('ringFillCircle');
        if (ring) {
          const offset = 263.89 - (263.89 * pct / 100);
          ring.style.strokeDashoffset = offset;
        }
        break;
      }
      case 2:
      case 22: {
        const fill = document.getElementById('glowBarFill');
        if (fill) fill.style.width = pct + '%';
        break;
      }
      case 5:
      case 11:
      case 12:
      case 13:
      case 14:
      case 15: {
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
      case 28: {
        for (let i = 0; i < 5; i++) {
          const rBar = document.getElementById('rBar_' + i);
          if (rBar) {
            const h = Math.max(10, Math.min(50, (pct / 100) * 50 + (i * 4)));
            rBar.style.height = h + 'px';
          }
        }
        break;
      }
      case 39: {
        const gauge = document.getElementById('gaugeFill');
        if (gauge) {
          const deg = -45 + (pct / 100) * 180;
          gauge.style.transform = `rotate(${deg}deg)`;
        }
        break;
      }
      case 41: {
        const piRect = document.getElementById('piFillRect');
        if (piRect) {
          const yVal = 100 - pct;
          piRect.setAttribute('y', yVal.toString());
          piRect.setAttribute('height', pct.toString());
        }
        break;
      }
      case 42: {
        const water = document.getElementById('sultanWaterFill');
        if (water) water.style.top = (100 - pct) + '%';
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

    // Pick a brand new random design from full combined pool (1 to 42)
    var newDesignId = Math.floor(Math.random() * 42) + 1;
    sessionStorage.setItem('admin_loader_design', newDesignId.toString());
    sessionStorage.setItem('admin_loader_active', 'true');
    return newDesignId;
  }

  function startLiveProgress(initialPct) {
    stopLiveProgress();
    currentPct = initialPct || 0;
    updatePctVisuals(currentPct);

    liveProgressTimer = setInterval(function () {
      if (currentPct < 55) {
        currentPct += Math.random() * 2.8 + 1.2;
      } else if (currentPct < 78) {
        currentPct += Math.random() * 1.2 + 0.6;
      } else if (currentPct < 92) {
        currentPct += (93 - currentPct) * 0.06;
      } else if (currentPct < 97) {
        currentPct += (98 - currentPct) * 0.03;
      } else {
        currentPct += 0.04;
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

  function showLoader(specifiedDesignId, initialPct) {
    if (isHiding) return;
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

    var startPct = currentPct;
    var startTime = performance.now();
    var glideDuration = 400;

    function glideStep(now) {
      var elapsed = now - startTime;
      var t = Math.min(elapsed / glideDuration, 1);
      var eased = 1 - Math.pow(1 - t, 3);
      currentPct = startPct + (100 - startPct) * eased;
      updatePctVisuals(currentPct);

      if (t < 1) {
        animFrame = requestAnimationFrame(glideStep);
      } else {
        if (activeOverlay) {
          activeOverlay.classList.add('fade-out');
        }
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

  // Intercept navigation & page loads
  window.addEventListener('pageshow', function (e) {
    if (e.persisted) {
      forceClear();
    } else {
      var isStoredActive = sessionStorage.getItem('admin_loader_active') === 'true';
      if (isStoredActive) {
        showLoader(null, 80);
        setTimeout(function () { hideLoader(); }, 80);
      }
    }
  });

  window.addEventListener('popstate', function () {
    forceClear();
  });

  document.addEventListener('DOMContentLoaded', function () {
    document.addEventListener('submit', function (e) {
      var form = e.target;
      if (form && !form.dataset.noLoader) {
        sessionStorage.removeItem('admin_loader_active');
        showLoader();
      }
    });

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

  // Intercept fetch
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

  // Intercept XHR
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

  // Global API
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
