(() => {
  const pop = document.getElementById('center-notify');
  if (!pop) return;

  const paths = {
    check: 'm5 12 5 5L20 7',
    edit: 'M12 20h9M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z',
    power: 'M12 2v10M18.4 6.6a9 9 0 1 1-12.8 0',
    trash: 'M3 6h18M8 6V4h8v2M6 6l1 14h10l1-14M10 11v6M14 11v6',
    alert: 'M12 8v4m0 4h.01M10.3 3.6 2.4 17a2 2 0 0 0 1.7 3h15.8a2 2 0 0 0 1.7-3L13.7 3.6a2 2 0 0 0-3.4 0z',
  };
  const notices = {
    add: { a: '#0E7C66', b: '#1FB090', icon: 'check', title: 'Waa la diiwaangeliyey!', message: kind => `${kind === 'c' ? 'Xarun' : 'Madbacad'} cusub waa lagu daray liiska si guul leh.` },
    edit: { a: '#2A45C9', b: '#6B8BFF', icon: 'edit', title: 'Waa la cusboonaysiiyey', message: () => 'Isbeddelada waa la keydiyey.' },
    on: { a: '#0E7C66', b: '#1FB090', icon: 'power', title: 'Waa la furay' },
    off: { a: '#B8400A', b: '#F59A4A', icon: 'power', title: 'Waa la xiray' },
    del: { a: '#B91C1C', b: '#F87171', icon: 'trash', title: 'Waa la tirtiray', message: () => 'Si buuxda ayaa looga saaray nidaamka.' },
    error: { a: '#B42318', b: '#F87171', icon: 'alert', title: 'Hawshu way fashilantay' },
  };
  const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, char => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  })[char]);

  function close() {
    pop.classList.remove('show');
    pop.setAttribute('aria-hidden', 'true');
  }

  function notify(type, kind, name, customMessage = '', customTitle = '', customCode = '') {
    const notice = notices[type];
    if (!notice || !['c', 'p', 'code'].includes(kind)) return;
    const itemName = escapeHtml(name);
    const message = customMessage || notice.message?.(kind);
    const itemKind = type === 'error' ? 'Soo jeedin' : kind === 'c' ? 'Xarun dugsi' : 'Madbacad';
    const itemSymbol = kind === 'c' ? 'building' : 'printer';
    const itemIcon = type === 'error'
      ? '<i class="fa-solid fa-key" aria-hidden="true"></i>'
      : `<svg aria-hidden="true"><use href="#cn-i-${itemSymbol}"></use></svg>`;
    const codePreview = type === 'error' && customCode
      ? `<div class="cn-code-preview"><small>Code-ka la geliyey</small><b>${escapeHtml(customCode)}</b></div>`
      : '';
    const confetti = type === 'add' ? Array.from({ length: 16 }, (_, index) => {
      const angle = index / 16 * Math.PI * 2;
      const distance = 95 + Math.random() * 45;
      const colors = ['#F5B400', '#fff', '#A7F3D0', '#FDE68A'];
      return `<span class="cn-confetti" style="--x:${Math.cos(angle) * distance}px;--y:${Math.sin(angle) * distance}px;--r:${Math.random() * 360 | 0}deg;--k:${colors[index % colors.length]}"></span>`;
    }).join('') : '';

    pop.innerHTML = `<div class="cn-card" style="--a:${notice.a};--b:${notice.b}">
      <div class="cn-header">${confetti}<div class="cn-icon-orbit"><i class="cn-icon-disc"><svg viewBox="0 0 24 24"><path pathLength="1" d="${paths[notice.icon]}"></path></svg></i></div></div>
      <div class="cn-body"><h2>${escapeHtml(customTitle || notice.title)}</h2>${message ? `<p>${escapeHtml(message)}</p>` : ''}${codePreview}
        <div class="cn-item"><span class="cn-item-icon">${itemIcon}</span><div class="cn-item-copy"><small>${itemKind}</small><b>${itemName}</b></div></div>
        <button class="cn-go" type="button" data-center-notify-close>Hagaag</button>
      </div>
    </div>`;
    pop.classList.add('show');
    pop.setAttribute('aria-hidden', 'false');
    window.setTimeout(() => pop.querySelector('[data-center-notify-close]')?.focus(), 60);
  }

  window.notify = notify;
  window.SultaanCenterNotify = { notify, close };

  pop.addEventListener('click', event => {
    if (event.target === pop || event.target.closest('[data-center-notify-close]')) close();
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && pop.classList.contains('show')) close();
  });

  document.querySelectorAll('[data-center-notify]').forEach(node => {
    try {
      const payload = JSON.parse(node.dataset.centerNotify);
      notify(payload.type, payload.kind, payload.name, payload.message, payload.title, payload.code_value);
    } catch (error) {
      console.error('Invalid center popup payload', error);
    }
    node.remove();
  });
})();
