/* Connects the supplied Falcelin & Cabasho widget to the live EMS API.
   The widget markup, visual language, motion and signature pad remain in its
   source document; this bridge only replaces the former demo data/actions. */
(function () {
  'use strict';

  var params = new URLSearchParams(window.location.search);
  var token = params.get('token') || '';
  var overlay = document.getElementById('overlay');
  var modal = document.getElementById('sheetModal');
  var fab = document.getElementById('fabBtn');
  var fabDot = document.getElementById('fabDot');
  var repliesBadge = document.getElementById('repliesBadge');
  var msgList = document.getElementById('msgList');
  var commentForm = document.getElementById('commentForm');
  var complaintForm = document.getElementById('cabashoForm');
  var savedSignature = '';
  var complaintRecap = document.querySelector('#screen-cabasho .recap');

  if (!token || !overlay || !modal || !fab) return;

  var embedStyle = document.createElement('style');
  embedStyle.textContent = [
    'html,body{background:transparent!important;min-height:100%;}',
    '.host-note,#fabBtn{display:none!important;}',
    'body.falcelin-embedded{overflow:visible!important;}'
  ].join('');
  document.head.appendChild(embedStyle);
  document.body.classList.add('falcelin-embedded');

  function escapeHtml(value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function requestJson(url, options) {
    options = options || {};
    options.headers = Object.assign({ 'Content-Type': 'application/json' }, options.headers || {});
    return fetch(url, options).then(function (response) {
      return response.json().catch(function () { return {}; }).then(function (payload) {
        if (!response.ok || !payload.ok) throw new Error(payload.message || 'Codsiga lama fulin karo hadda.');
        return payload;
      });
    });
  }

  function showFormError(form, message) {
    var existing = form.querySelector('.widget-api-error');
    if (!existing) {
      existing = document.createElement('p');
      existing.className = 'widget-api-error field__note';
      existing.style.color = '#8C3B32';
      existing.style.fontWeight = '600';
      form.prepend(existing);
    }
    existing.textContent = message;
    existing.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }

  function setBusy(button, busy, fallback) {
    if (!button) return;
    if (!button.dataset.originalText) button.dataset.originalText = button.textContent;
    button.disabled = busy;
    button.textContent = busy ? 'La dirayaa...' : (fallback || button.dataset.originalText);
  }

  function itemMarkup(item) {
    var badgeText = { pending: 'Sugaya Jawaab', answered: 'La Jawaabay', received: 'La Diray' };
    var badgeClass = { pending: 'badge--pending', answered: 'badge--answered', received: 'badge--received' };
    var typeText = { cabasho: 'CABASHO', falcelin: 'FALCELIN' };
    var html = '<details class="msg"><summary><div class="msg__main">' +
      '<span class="msg__type">' + (typeText[item.type] || 'FALCELIN') + (item.subject ? ' · ' + escapeHtml(item.subject) : '') + '</span>' +
      '<span class="msg__ref">' + escapeHtml(item.ref) + '</span>' +
      '<span class="msg__excerpt">' + escapeHtml(item.excerpt) + '</span></div>' +
      '<span class="badge ' + (badgeClass[item.status] || 'badge--received') + '">' + (badgeText[item.status] || 'La Diray') + '</span>' +
      '<span class="msg__chevron" aria-hidden="true">▾</span></summary><div class="msg__body">' +
      '<p class="msg__original">' + escapeHtml(item.details) + '</p>';
    if (item.reply) {
      html += '<div class="reply-slip"><div class="reply-slip__head"><span class="reply-slip__seal">✓</span>' +
        '<span class="reply-slip__office-lockup"><span class="reply-slip__office">' + escapeHtml(item.reply.office) + '</span><span class="reply-slip__verified" aria-label="Xafiis la xaqiijiyey"><span class="seal-shape"></span><svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M5 12.5l4.2 4.2L19 7" stroke="#fff" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/></svg></span></span>' +
        '<span class="reply-slip__date">' + escapeHtml(item.reply.date) + '</span></div><p>' + escapeHtml(item.reply.message) + '</p></div>';
    } else if (item.status === 'pending') {
      html += '<div class="pending-note">⌛ La sugayo jawaabta xafiiska imtixaannada.</div>';
    }
    return html + '</div></details>';
  }

  function paintReplyBadge(count) {
    var visible = Number(count) > 0;
    if (fabDot) fabDot.style.display = visible ? '' : 'none';
    if (repliesBadge) {
      repliesBadge.hidden = !visible;
      var countNode = repliesBadge.querySelector('b');
      if (countNode) countNode.textContent = visible ? String(count) : '';
    }
  }

  function applyVerifiedSealShape() {
    var points = [], total = 240, bumps = 12, baseRadius = 44.6, amplitude = 3.4;
    for (var i = 0; i < total; i++) {
      var theta = (i / total) * Math.PI * 2;
      var radius = baseRadius + amplitude * Math.cos(bumps * theta);
      points.push((50 + radius * Math.cos(theta)).toFixed(2) + '% ' + (50 + radius * Math.sin(theta)).toFixed(2) + '%');
    }
    var clip = 'polygon(' + points.join(',') + ')';
    document.querySelectorAll('.reply-slip__verified .seal-shape').forEach(function (shape) {
      shape.style.clipPath = clip;
      shape.style.webkitClipPath = clip;
    });
  }

  function loadResultSummary() {
    return requestJson('/api/falcelin/result-summary?token=' + encodeURIComponent(token)).then(function (payload) {
      var rows = document.getElementById('resultSummaryRows');
      if (rows) rows.innerHTML = payload.subjects.map(function (item) {
        return '<tr><td>' + escapeHtml(item.subject) + '</td><td>' + escapeHtml(item.score) + '</td><td>' + escapeHtml(item.grade) + '</td></tr>';
      }).join('') || '<tr><td colspan="3">Natiijo ma jirto.</td></tr>';
      var stats = document.querySelectorAll('.stat-strip__value');
      if (stats[0]) stats[0].textContent = String(payload.total == null ? '--' : payload.total) + '/' + String(payload.max_total == null ? '--' : payload.max_total);
      if (stats[1]) stats[1].textContent = (payload.average == null ? '--' : payload.average) + '%';
      if (stats[2]) stats[2].textContent = (payload.grade || '--') + (payload.grade ? ' · PASS' : '');
    }).catch(function () {});
  }

  function loadReplies(markRead) {
    return requestJson('/api/falcelin/replies?token=' + encodeURIComponent(token)).then(function (payload) {
      msgList.innerHTML = payload.items.length
        ? payload.items.map(itemMarkup).join('')
        : '<div class="empty-state"><span class="empty-state__icon">📭</span><strong>Weli wax falcelin ama cabasho ah ma jirto.</strong><p>Marka aad wax dirto, xaaladdeeda iyo jawaabta xafiiska halkan ayaad ka arki doontaa.</p></div>';
      applyVerifiedSealShape();
      paintReplyBadge(payload.unread_count || 0);
      if (markRead && payload.unread_count) {
        requestJson('/api/falcelin/replies/read', {
          method: 'PATCH',
          body: JSON.stringify({ token: token })
        }).then(function () { paintReplyBadge(0); }).catch(function () {});
      }
      return payload;
    }).catch(function (error) {
      msgList.innerHTML = '<div class="pending-note">' + escapeHtml(error.message) + '</div>';
    });
  }

  function loadSubjects() {
    return requestJson('/api/falcelin/subjects?token=' + encodeURIComponent(token)).then(function (payload) {
      var select = document.getElementById('subjectSelect');
      if (!select) return;
      select.innerHTML = '<option value="">— Dooro maaddada —</option>' + payload.subjects.map(function (subject) {
        return '<option value="' + escapeHtml(subject) + '">' + escapeHtml(subject) + '</option>';
      }).join('');
    }).catch(function () {});
  }

  function paintSavedSignature(signature) {
    var canvas = document.getElementById('sigCanvas');
    var input = document.getElementById('signatureInput');
    var pad = document.getElementById('sigpad');
    var hint = document.getElementById('sigHint');
    var save = document.getElementById('sigSaveBtn');
    if (!canvas || !signature) return;
    savedSignature = signature;
    var image = new Image();
    image.onload = function () {
      var rect = canvas.getBoundingClientRect();
      var ratio = Math.min(window.devicePixelRatio || 1, 2);
      canvas.width = Math.max(1, Math.round(rect.width * ratio));
      canvas.height = Math.max(1, Math.round(rect.height * ratio));
      var ctx = canvas.getContext('2d');
      ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
      ctx.clearRect(0, 0, rect.width, rect.height);
      ctx.drawImage(image, 0, 0, rect.width, rect.height);
      input.value = signature;
      pad.classList.add('is-locked');
      hint.classList.add('is-hidden');
      if (save) save.classList.add('is-saved');
      var status = document.getElementById('sigStatus');
      if (status) status.textContent = '✓ Saxeex hore loo kaydiyay';
    };
    image.src = signature;
  }

  function loadSavedSignature() {
    return requestJson('/api/falcelin/signature?token=' + encodeURIComponent(token))
      .then(function (payload) { paintSavedSignature(payload.signature || ''); })
      .catch(function () {});
  }

  var signatureSaveButton = document.getElementById('sigSaveBtn');
  if (signatureSaveButton) {
    signatureSaveButton.addEventListener('click', function () {
      window.setTimeout(function () {
        var input = document.getElementById('signatureInput');
        if (!input || !input.value) return;
        requestJson('/api/falcelin/signature', {
          method: 'POST',
          body: JSON.stringify({ token: token, signature: input.value })
        }).then(function () {
          savedSignature = input.value;
          var status = document.getElementById('sigStatus');
          if (status) status.textContent = '✓ Saxeexa si joogto ah ayaa loo kaydiyay';
        }).catch(function (error) { showFormError(complaintForm, error.message); });
      }, 0);
    });
  }

  var signatureClearButton = document.getElementById('sigClearBtn');
  if (signatureClearButton) {
    signatureClearButton.addEventListener('click', function () {
      var input = document.getElementById('signatureInput');
      if (!savedSignature) return;
      requestJson('/api/falcelin/signature', {
        method: 'DELETE',
        body: JSON.stringify({ token: token })
      }).then(function () {
        savedSignature = '';
        if (input) input.value = '';
      }).catch(function (error) { showFormError(complaintForm, error.message); });
    });
  }

  function notifyParent(state) {
    if (window.parent !== window) window.parent.postMessage({ type: 'falcelin:' + state }, window.location.origin);
  }

  new MutationObserver(function () {
    if (modal.hidden || !overlay.classList.contains('is-open')) notifyParent('close');
  }).observe(overlay, { attributes: true, attributeFilter: ['class'] });

  window.addEventListener('message', function (event) {
    if (event.origin !== window.location.origin || !event.data || event.data.type !== 'falcelin:open') return;
    fab.click();
  });

  // In an iframe the parent can reveal the host before this bridge receives its
  // postMessage. Prepare the modal on load so the first click is never lost.
  if (params.get('embedded') === '1') {
    window.setTimeout(function () { if (modal.hidden) fab.click(); }, 0);
  }

  document.querySelector('[data-screen="screen-replies"]').addEventListener('click', function () {
    window.setTimeout(function () { loadReplies(true); }, 0);
  });

  document.querySelector('[data-screen="screen-cabasho"]').addEventListener('click', function () {
    if (complaintRecap) complaintRecap.hidden = false;
    window.setTimeout(loadSavedSignature, 0);
  });

  commentForm.addEventListener('submit', function (event) {
    event.preventDefault();
    event.stopImmediatePropagation();
    var rating = Number(document.getElementById('ratingValue').value || 0);
    var reaction = document.querySelector('.reaction.is-active');
    var comment = (document.getElementById('commentInput').value || '').trim();
    var button = commentForm.querySelector('[type="submit"]');
    setBusy(button, true);
    requestJson('/api/falcelin', {
      method: 'POST',
      body: JSON.stringify({ token: token, rating: rating, reaction: reaction ? reaction.dataset.value : '', comment: comment })
    }).then(function (payload) {
      document.getElementById('commentConfirmRef').textContent = payload.ref;
      commentForm.hidden = true;
      document.getElementById('commentConfirm').hidden = false;
      loadReplies(false);
    }).catch(function (error) {
      showFormError(commentForm, error.message);
    }).finally(function () { setBusy(button, false); });
  }, true);

  complaintForm.addEventListener('submit', function (event) {
    event.preventDefault();
    event.stopImmediatePropagation();
    var selectedType = complaintForm.querySelector('input[name="ctype"]:checked');
    var button = document.getElementById('cabSubmitBtn');
    setBusy(button, true);
    requestJson('/api/cabasho', {
      method: 'POST',
      body: JSON.stringify({
        token: token,
        type: selectedType ? selectedType.value : '',
        subject: (document.getElementById('subjectSelect').value || '').trim(),
        details: (document.getElementById('detailsInput').value || '').trim(),
        signature: document.getElementById('signatureInput').value || ''
      })
    }).then(function (payload) {
      document.getElementById('cabashoRef').textContent = payload.ref;
      document.getElementById('cabashoConfirmRef').textContent = payload.ref;
      if (complaintRecap) complaintRecap.hidden = true;
      complaintForm.hidden = true;
      document.getElementById('cabashoConfirm').hidden = false;
      loadReplies(false);
    }).catch(function (error) {
      showFormError(complaintForm, error.message);
    }).finally(function () { setBusy(button, false); });
  }, true);

  // Replace the reference file's demonstration reply cards before any screen is opened.
  msgList.innerHTML = '<div class="pending-note">Jawaabaha xafiiska waa la soo qaadaya...</div>';
  loadSubjects();
  loadResultSummary();
  loadReplies(false);
  loadSavedSignature();
}());
