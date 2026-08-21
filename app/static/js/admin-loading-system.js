/* Shared admin loading system. Only the four approved designs are available. */
(function () {
  'use strict';

  var ALLOWED_DESIGNS = [20, 36, 38, 42];
  var DEFAULT_DESIGN = 42;
  var activeOverlay = null;
  var activeDesignId = DEFAULT_DESIGN;
  var currentPct = 0;
  var progressTimer = null;
  var hideFrame = null;
  var safetyTimer = null;
  var isHiding = false;

  function normaliseDesign(value) {
    var id = Number(value);
    return ALLOWED_DESIGNS.indexOf(id) !== -1 ? id : DEFAULT_DESIGN;
  }

  function badgeMarkup() {
    return '<div class="sultan-badge-container">' +
      '<svg class="sultan-crown-svg crown" viewBox="0 0 100 60" xmlns="http://www.w3.org/2000/svg" aria-hidden="true"><defs><linearGradient id="sultanCrownGoldGrad" x1="0%" y1="0%" x2="0%" y2="100%"><stop offset="0%" stop-color="#fff2b0"/><stop offset="45%" stop-color="#f6d365"/><stop offset="100%" stop-color="#d9a521"/></linearGradient></defs><polygon points="5,55 5,25 22,40 32,15 50,35 68,15 78,40 95,25 95,55" fill="url(#sultanCrownGoldGrad)" stroke="#b9860f" stroke-width="1.5" stroke-linejoin="round"/><path d="M50,4 L52.3,9.4 L58.1,9.9 L53.7,13.8 L55,19.5 L50,16.2 L45,19.5 L46.3,13.8 L41.9,9.9 L47.7,9.4 Z" fill="url(#sultanCrownGoldGrad)" stroke="#b9860f" stroke-width="1.2" stroke-linejoin="round"/><path d="M20,15.5 L21.5,19 L25.2,19.3 L22.4,21.8 L23.2,25.4 L20,23.4 L16.8,25.4 L17.6,21.8 L14.8,19.3 L18.5,19 Z" fill="url(#sultanCrownGoldGrad)" stroke="#b9860f" stroke-width="1" stroke-linejoin="round"/><path d="M80,15.5 L81.5,19 L85.2,19.3 L82.4,21.8 L83.2,25.4 L80,23.4 L76.8,25.4 L77.6,21.8 L74.8,19.3 L78.5,19 Z" fill="url(#sultanCrownGoldGrad)" stroke="#b9860f" stroke-width="1" stroke-linejoin="round"/><rect x="6" y="54" width="88" height="3" rx="1.5" fill="url(#sultanCrownGoldGrad)" stroke="#b9860f" stroke-width=".8"/></svg>' +
      '<div class="sultan-circle-stage"><div class="sultan-water-fill" id="sultanWaterFill" style="top:100%"></div><div class="sultan-circle-pct"><span class="pct-val" id="loaderPctText">0</span>%</div></div><div class="sultan-text-title">SULTAAN</div></div>';
  }

  function buildLoaderHTML(designId) {
    switch (normaliseDesign(designId)) {
      case 20:
        return '<div class="loader-card dual-ring-card"><div class="dual-ring-stage royal"><div class="ring-outer"></div><div class="ring-inner"></div><div class="dual-ring-pct"><span class="pct-val" id="loaderPctText">0</span>%</div></div></div>';
      case 36:
        return '<div class="loader-card"><div class="eq-wrap"><span class="eq-bar"></span><span class="eq-bar"></span><span class="eq-bar"></span><span class="eq-bar"></span><span class="eq-bar"></span></div><div class="wave-pct"><span class="pct-val" id="loaderPctText">0</span>%</div></div>';
      case 38:
        return '<div class="loader-card"><div class="flip-card-stage"><div class="flip-card-inner"><div><span class="pct-val" id="loaderPctText">0</span>%</div></div></div></div>';
      case 42:
      default:
        return badgeMarkup();
    }
  }

  function parseIds(value) {
    if (typeof value === 'string') {
      try { value = JSON.parse(value); } catch (_) { value = []; }
    }
    return Array.isArray(value) ? value.map(Number) : [];
  }

  function currentDesign(explicit) {
    if (explicit) return normaliseDesign(explicit);
    var config = window.SYSTEM_LOADER_CONFIG || {};
    var deleted = parseIds(config.deletedDesigns).filter(function (id) { return id !== DEFAULT_DESIGN && ALLOWED_DESIGNS.indexOf(id) !== -1; });
    var pool = parseIds(config.rotationPool).filter(function (id) { return ALLOWED_DESIGNS.indexOf(id) !== -1 && deleted.indexOf(id) === -1; });
    if (config.rotationEnabled && pool.length) {
      if (!window.__adminLoaderPageDesign) window.__adminLoaderPageDesign = pool[Math.floor(Math.random() * pool.length)];
      return window.__adminLoaderPageDesign;
    }
    var chosen = normaliseDesign(config.design || window.SYSTEM_LOADER_DESIGN || DEFAULT_DESIGN);
    return deleted.indexOf(chosen) === -1 ? chosen : DEFAULT_DESIGN;
  }

  function overlay() {
    var element = document.getElementById('admin-global-loader');
    if (!element) {
      element = document.createElement('div');
      element.id = 'admin-global-loader';
      element.className = 'admin-loader-overlay';
      document.body.appendChild(element);
    }
    return element;
  }

  function updateProgress(value) {
    currentPct = Math.max(0, Math.min(100, Number(value) || 0));
    var text = document.getElementById('loaderPctText');
    if (text) text.textContent = Math.round(currentPct);
    var water = document.getElementById('sultanWaterFill');
    if (water) water.style.top = (100 - currentPct) + '%';
  }

  function clearTimers() {
    if (progressTimer) clearInterval(progressTimer);
    if (safetyTimer) clearTimeout(safetyTimer);
    if (hideFrame) cancelAnimationFrame(hideFrame);
    progressTimer = safetyTimer = hideFrame = null;
  }

  function showLoader(designId, initialPct) {
    clearTimers();
    isHiding = false;
    activeDesignId = currentDesign(designId);
    activeOverlay = overlay();
    activeOverlay.innerHTML = buildLoaderHTML(activeDesignId);
    activeOverlay.classList.remove('fade-out');
    activeOverlay.classList.add('active');
    updateProgress(initialPct || 0);
    progressTimer = setInterval(function () {
      updateProgress(Math.min(98, currentPct + (currentPct < 65 ? 2.2 : .55)));
    }, 65);
    safetyTimer = setTimeout(forceClear, 6000);
  }

  function forceClear() {
    clearTimers();
    if (activeOverlay) {
      activeOverlay.classList.remove('active', 'fade-out');
      activeOverlay.innerHTML = '';
    }
    activeOverlay = null;
    isHiding = false;
    currentPct = 0;
  }

  function hideLoader() {
    if (!activeOverlay || isHiding) return;
    isHiding = true;
    if (progressTimer) clearInterval(progressTimer);
    progressTimer = null;
    var started = performance.now();
    var initial = currentPct;
    function finish(now) {
      var step = Math.min((now - started) / 320, 1);
      updateProgress(initial + (100 - initial) * step);
      if (step < 1) { hideFrame = requestAnimationFrame(finish); return; }
      activeOverlay.classList.add('fade-out');
      setTimeout(forceClear, 220);
    }
    hideFrame = requestAnimationFrame(finish);
  }

  function skipLoader(url, options) {
    var value = String(url || '').toLowerCase();
    var headers = options && options.headers;
    return value.indexOf('autosave') !== -1 || value.indexOf('favicon') !== -1 || value.indexOf('status') !== -1 ||
      value.indexOf('/api/ping') !== -1 || value.indexOf('/delivered') !== -1 ||
      (options && (options.noLoader || (headers && (headers['X-No-Loader'] || headers['x-no-loader']))));
  }

  function showForNavigation() {
    sessionStorage.setItem('admin_loader_active', 'true');
    sessionStorage.setItem('admin_loader_design', String(currentDesign()));
    showLoader();
  }

  document.addEventListener('DOMContentLoaded', function () {
    document.addEventListener('submit', function (event) {
      var form = event.target;
      if (form && !form.dataset.noLoader && !form.matches('[data-autosave-form], [data-security-settings]')) showForNavigation();
    });
    document.addEventListener('click', function (event) {
      var link = event.target.closest('a[href], [data-nav-loader]');
      if (!link || link.dataset.noLoader || link.target) return;
      var href = link.getAttribute('href') || '';
      if (link.dataset.navLoader !== undefined || (href && !href.startsWith('#') && !href.startsWith('javascript:'))) showForNavigation();
    });
    setTimeout(function () { showLoader(null, 72); setTimeout(hideLoader, 180); }, 0);
  });

  window.addEventListener('pageshow', function (event) {
    if (event.persisted) { forceClear(); return; }
    if (sessionStorage.getItem('admin_loader_active') === 'true') {
      showLoader(sessionStorage.getItem('admin_loader_design'), 84);
      setTimeout(hideLoader, 180);
    }
  });
  window.addEventListener('popstate', forceClear);

  var originalFetch = window.fetch;
  window.__rawFetch = originalFetch;
  if (originalFetch) {
    window.fetch = function () {
      var args = arguments;
      var url = typeof args[0] === 'string' ? args[0] : args[0] && args[0].url;
      var bypass = skipLoader(url, args[1]);
      if (!bypass) showLoader();
      return originalFetch.apply(this, args).then(function (response) { if (!bypass) hideLoader(); return response; }, function (error) { if (!bypass) hideLoader(); throw error; });
    };
  }

  var originalOpen = XMLHttpRequest.prototype.open;
  var originalSend = XMLHttpRequest.prototype.send;
  XMLHttpRequest.prototype.open = function () { this._loaderUrl = arguments[1]; return originalOpen.apply(this, arguments); };
  XMLHttpRequest.prototype.send = function () {
    var bypass = skipLoader(this._loaderUrl);
    if (!bypass) {
      showLoader();
      this.addEventListener('loadend', hideLoader, { once: true });
      this.addEventListener('error', forceClear, { once: true });
    }
    return originalSend.apply(this, arguments);
  };

  window.AdminLoader = {
    show: function (designId) { showLoader(designId); },
    setProgress: updateProgress,
    hide: hideLoader,
    buildLoaderHTML: buildLoaderHTML
  };
  document.dispatchEvent(new CustomEvent('admin-loader-ready'));
})();
