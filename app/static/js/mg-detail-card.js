(function () {
  'use strict';
  var modal = document.getElementById('mgDetailModal');
  if (!modal) return;

  var card = modal.querySelector('.mg-detail-card');
  var token = modal.dataset.token || '';
  var endpoint = modal.dataset.endpoint || '';
  var subjectName = modal.querySelector('[data-mg-field="subject"]');
  var session = modal.querySelector('[data-mg-field="session"]');
  var examDate = modal.querySelector('[data-mg-field="exam-date"]');
  var examRoom = modal.querySelector('[data-mg-field="exam-room"]');
  var reason = modal.querySelector('[data-mg-field="reason"]');
  var sessionTag = modal.querySelector('[data-mg-session-tag]');
  var loading = modal.querySelector('.mg-detail-loading');
  var detailList = modal.querySelector('.mg-detail-list');
  var activeButton = null;

  var themes = [
    { id:'navy-gold', vars:{ '--mg-bg-top':'#132a56','--mg-bg-bottom':'#0a1730','--mg-card-border':'rgba(245,158,11,.28)','--mg-line':'rgba(255,255,255,.10)','--mg-glow':'rgba(245,158,11,.18)','--mg-accent':'#f59e0b','--mg-accent-deep':'#b8590a','--mg-accent-soft':'#f0c96b','--mg-accent-shadow':'rgba(245,158,11,.55)','--mg-icon-on-accent':'#1b1204','--mg-stamp-a':'#b8590a','--mg-stamp-b':'#a04c08','--mg-stamp-text':'#fff2dc','--mg-text-main':'#f6f3ec','--mg-text-dim':'rgba(246,243,236,.62)','--mg-text-faint':'rgba(246,243,236,.42)','--mg-tag-bg':'#1c3a70','--mg-tag-border':'rgba(212,165,55,.3)','--mg-icon-bg':'#132a56','--mg-reason-color':'#ffd9a8' } },
    { id:'emerald-ivory', vars:{ '--mg-bg-top':'#154a37','--mg-bg-bottom':'#0c2b20','--mg-card-border':'rgba(201,162,75,.30)','--mg-line':'rgba(255,255,255,.09)','--mg-glow':'rgba(201,162,75,.16)','--mg-accent':'#c9a24b','--mg-accent-deep':'#8f6f24','--mg-accent-soft':'#e6c976','--mg-accent-shadow':'rgba(201,162,75,.5)','--mg-icon-on-accent':'#231a05','--mg-stamp-a':'#8f6f24','--mg-stamp-b':'#785c17','--mg-stamp-text':'#fff3d6','--mg-text-main':'#f3f6ef','--mg-text-dim':'rgba(243,246,239,.62)','--mg-text-faint':'rgba(243,246,239,.42)','--mg-tag-bg':'#1f5c44','--mg-tag-border':'rgba(201,162,75,.32)','--mg-icon-bg':'#154a37','--mg-reason-color':'#f4d99c' } },
    { id:'burgundy-cream', vars:{ '--mg-bg-top':'#5c1530','--mg-bg-bottom':'#340a1b','--mg-card-border':'rgba(217,79,79,.30)','--mg-line':'rgba(255,255,255,.09)','--mg-glow':'rgba(217,79,79,.16)','--mg-accent':'#e8734f','--mg-accent-deep':'#a83a24','--mg-accent-soft':'#f0b088','--mg-accent-shadow':'rgba(232,115,79,.5)','--mg-icon-on-accent':'#2a0d05','--mg-stamp-a':'#a83a24','--mg-stamp-b':'#8c2f1c','--mg-stamp-text':'#ffe6d9','--mg-text-main':'#faf1ef','--mg-text-dim':'rgba(250,241,239,.62)','--mg-text-faint':'rgba(250,241,239,.42)','--mg-tag-bg':'#6e2038','--mg-tag-border':'rgba(232,115,79,.32)','--mg-icon-bg':'#5c1530','--mg-reason-color':'#f6c9a8' } },
    { id:'charcoal-teal', vars:{ '--mg-bg-top':'#232c30','--mg-bg-bottom':'#141a1c','--mg-card-border':'rgba(63,184,168,.28)','--mg-line':'rgba(255,255,255,.08)','--mg-glow':'rgba(63,184,168,.16)','--mg-accent':'#3fb8a8','--mg-accent-deep':'#1f7a6e','--mg-accent-soft':'#8fe0d3','--mg-accent-shadow':'rgba(63,184,168,.45)','--mg-icon-on-accent':'#04201c','--mg-stamp-a':'#c88a2c','--mg-stamp-b':'#a8721f','--mg-stamp-text':'#fff2da','--mg-text-main':'#eef3f2','--mg-text-dim':'rgba(238,243,242,.6)','--mg-text-faint':'rgba(238,243,242,.4)','--mg-tag-bg':'#2b3a3c','--mg-tag-border':'rgba(63,184,168,.32)','--mg-icon-bg':'#232c30','--mg-reason-color':'#e8c987' } },
    { id:'indigo-coral', vars:{ '--mg-bg-top':'#2a2263','--mg-bg-bottom':'#161038','--mg-card-border':'rgba(255,138,101,.30)','--mg-line':'rgba(255,255,255,.09)','--mg-glow':'rgba(255,138,101,.16)','--mg-accent':'#ff8a65','--mg-accent-deep':'#c65a37','--mg-accent-soft':'#ffb499','--mg-accent-shadow':'rgba(255,138,101,.5)','--mg-icon-on-accent':'#2a0f05','--mg-stamp-a':'#c65a37','--mg-stamp-b':'#a8492c','--mg-stamp-text':'#ffe4d8','--mg-text-main':'#f3f1fb','--mg-text-dim':'rgba(243,241,251,.62)','--mg-text-faint':'rgba(243,241,251,.42)','--mg-tag-bg':'#372a7a','--mg-tag-border':'rgba(255,138,101,.32)','--mg-icon-bg':'#2a2263','--mg-reason-color':'#ffcbb8' } },
    { id:'forest-mustard', vars:{ '--mg-bg-top':'#28381f','--mg-bg-bottom':'#16220f','--mg-card-border':'rgba(217,164,65,.30)','--mg-line':'rgba(255,255,255,.08)','--mg-glow':'rgba(217,164,65,.1)','--mg-accent':'#d9a441','--mg-accent-deep':'#9c6f1f','--mg-accent-soft':'#efc978','--mg-accent-shadow':'rgba(217,164,65,.5)','--mg-icon-on-accent':'#241a04','--mg-stamp-a':'#6f8f3f','--mg-stamp-b':'#5a7530','--mg-stamp-text':'#f1f6e4','--mg-text-main':'#f4f2e9','--mg-text-dim':'rgba(244,242,233,.62)','--mg-text-faint':'rgba(244,242,233,.42)','--mg-tag-bg':'#33461f','--mg-tag-border':'rgba(217,164,65,.32)','--mg-icon-bg':'#28381f','--mg-reason-color':'#f0d99c' } },
    { id:'slate-rose', vars:{ '--mg-bg-top':'#343a48','--mg-bg-bottom':'#1d212a','--mg-card-border':'rgba(232,134,159,.28)','--mg-line':'rgba(255,255,255,.08)','--mg-glow':'rgba(232,134,159,.15)','--mg-accent':'#e8869f','--mg-accent-deep':'#a84b64','--mg-accent-soft':'#f2b3c3','--mg-accent-shadow':'rgba(232,134,159,.45)','--mg-icon-on-accent':'#2a0c14','--mg-stamp-a':'#a84b64','--mg-stamp-b':'#8f3d54','--mg-stamp-text':'#ffe4ea','--mg-text-main':'#f1f2f5','--mg-text-dim':'rgba(241,242,245,.62)','--mg-text-faint':'rgba(241,242,245,.42)','--mg-tag-bg':'#3d4352','--mg-tag-border':'rgba(232,134,159,.3)','--mg-icon-bg':'#343a48','--mg-reason-color':'#f4c7d1' } },
    { id:'midnight-lime', vars:{ '--mg-bg-top':'#1a2233','--mg-bg-bottom':'#0b0f18','--mg-card-border':'rgba(168,224,95,.26)','--mg-line':'rgba(255,255,255,.08)','--mg-glow':'rgba(168,224,95,.14)','--mg-accent':'#a8e05f','--mg-accent-deep':'#6b9c2f','--mg-accent-soft':'#cdec9c','--mg-accent-shadow':'rgba(168,224,95,.4)','--mg-icon-on-accent':'#152005','--mg-stamp-a':'#5f7a2c','--mg-stamp-b':'#4c6323','--mg-stamp-text':'#eef7dd','--mg-text-main':'#eef1f7','--mg-text-dim':'rgba(238,241,247,.6)','--mg-text-faint':'rgba(238,241,247,.4)','--mg-tag-bg':'#232c42','--mg-tag-border':'rgba(168,224,95,.3)','--mg-icon-bg':'#1a2233','--mg-reason-color':'#dcecb0' } }
  ];

  var icons = {
    date:'<rect x="3" y="4" width="18" height="18" rx="3"/><line x1="16" y1="2" x2="16" y2="6"/><line x1="8" y1="2" x2="8" y2="6"/><line x1="3" y1="10" x2="21" y2="10"/>',
    hall:'<path d="M2 20h20"/><path d="M4 20V9l8-5 8 5v11"/><path d="M9 20v-6h6v6"/>',
    reason:'<circle cx="12" cy="12" r="9"/><polyline points="12 7 12 12 15.5 14"/>',
    source:'<path d="M9 11l3 3L22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/>'
  };
  var svg = function (body) { return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">' + body + '</svg>'; };

  function applyTheme(index) {
    var theme = themes[(Number(index) || 0) % themes.length];
    Object.keys(theme.vars).forEach(function (key) { card.style.setProperty(key, theme.vars[key]); });
  }

  function setValue(selector, value) {
    var node = modal.querySelector(selector);
    if (node) node.textContent = value || '-';
  }

  function showLoading(subject) {
    setValue('[data-mg-field="subject"]', subject);
    setValue('[data-mg-field="session"]', 'Soo qaadaya...');
    setValue('[data-mg-field="exam-date"]', 'Soo qaadaya...');
    setValue('[data-mg-field="exam-room"]', 'Soo qaadaya...');
    setValue('[data-mg-field="reason"]', 'Soo qaadaya...');
    if (sessionTag) sessionTag.textContent = 'Soo qaadaya...';
    if (loading) loading.hidden = false;
    if (detailList) detailList.style.opacity = '.48';
  }

  function showData(data) {
    setValue('[data-mg-field="subject"]', data.subject_name);
    setValue('[data-mg-field="session"]', data.session);
    setValue('[data-mg-field="exam-date"]', data.exam_date);
    setValue('[data-mg-field="exam-room"]', data.exam_room);
    setValue('[data-mg-field="reason"]', data.absence_reason);
    if (sessionTag) sessionTag.textContent = data.session || '-';
    if (loading) loading.hidden = true;
    if (detailList) detailList.style.opacity = '1';
  }

  function showError(message) {
    setValue('[data-mg-field="session"]', '-');
    setValue('[data-mg-field="exam-date"]', '-');
    setValue('[data-mg-field="exam-room"]', '-');
    setValue('[data-mg-field="reason"]', message || 'Macluumaadka lama heli karo.');
    if (sessionTag) sessionTag.textContent = '-';
    if (loading) loading.hidden = true;
    if (detailList) detailList.style.opacity = '1';
  }

  function close() {
    modal.classList.remove('is-open');
    modal.setAttribute('aria-hidden', 'true');
    document.body.classList.remove('mg-modal-open');
    if (activeButton) activeButton.focus();
    activeButton = null;
  }

  function open(button) {
    activeButton = button;
    applyTheme(button.dataset.mgThemeIndex);
    showLoading(button.dataset.mgSubject || 'Maadada');
    modal.classList.add('is-open');
    modal.setAttribute('aria-hidden', 'false');
    document.body.classList.add('mg-modal-open');
    var url = endpoint + '?token=' + encodeURIComponent(token) + '&subject_id=' + encodeURIComponent(button.dataset.mgSubjectId || '');
    fetch(url, { credentials:'same-origin', headers:{ 'X-Requested-With':'XMLHttpRequest' } })
      .then(function (response) { return response.json().then(function (data) { if (!response.ok || !data.ok) throw new Error(data.message || 'Macluumaadka lama heli karo.'); return data; }); })
      .then(showData)
      .catch(function (error) { showError(error.message); });
  }

  modal.querySelectorAll('[data-mg-icon]').forEach(function (node) {
    var body = icons[node.dataset.mgIcon];
    if (body) node.innerHTML = svg(body);
  });

  document.querySelectorAll('tr[data-mg-subject-id] .uf-result-indicator').forEach(function (badge) {
    var row = badge.closest('tr');
    badge.classList.add('mg-badge-trigger');
    badge.dataset.mgSubjectId = row.dataset.mgSubjectId;
    badge.dataset.mgSubject = row.dataset.mgSubject || '';
    badge.dataset.mgThemeIndex = row.dataset.mgThemeIndex || '0';
    badge.setAttribute('role', 'button');
    badge.setAttribute('tabindex', '0');
    badge.addEventListener('keydown', function (event) {
      if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); open(badge); }
    });
  });

  modal.querySelector('[data-mg-close]').addEventListener('click', close);
  modal.addEventListener('click', function (event) { if (event.target === modal) close(); });
  document.addEventListener('keydown', function (event) { if (event.key === 'Escape' && modal.classList.contains('is-open')) close(); });
  document.querySelectorAll('.mg-badge-trigger').forEach(function (button) {
    button.addEventListener('click', function () { open(button); });
  });
}());
