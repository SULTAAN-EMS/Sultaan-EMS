(() => {
  const form = document.querySelector('[data-autosave-form]');
  const indicator = document.getElementById('adminAutosaveStatus');
  if (!form || !indicator) return;

  let timer;
  let saveVersion = 0;
  const icon = indicator.querySelector('i');
  const text = indicator.querySelector('span');
  const setStatus = (state, message) => {
    indicator.className = `autosave-status ${state ? `is-${state}` : ''}`;
    icon.className = state === 'saving' ? 'fa-solid fa-arrows-rotate' : state === 'saved' ? 'fa-solid fa-circle-check' : state === 'error' ? 'fa-solid fa-circle-exclamation' : 'fa-solid fa-cloud';
    text.textContent = message;
  };

  const save = async () => {
    const version = ++saveVersion;
    setStatus('saving', 'Saving changes…');
    try {
      const request = window.__rawFetch || window.fetch;
      const response = await request(form.action || window.location.href, {
        method: 'POST',
        body: new FormData(form),
        credentials: 'same-origin',
        headers: { 'X-Requested-With': 'XMLHttpRequest', 'X-No-Loader': '1' },
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok || !payload.success) throw new Error(payload.message || 'Unable to save changes.');
      if (version !== saveVersion) return;
      form.querySelectorAll('input[type="file"]').forEach((input) => { input.value = ''; });
      setStatus('saved', 'Saved');
      window.setTimeout(() => { if (version === saveVersion) setStatus('', 'All changes saved'); }, 2200);
    } catch (error) {
      if (version === saveVersion) setStatus('error', error.message || 'Save failed');
    }
  };

  const schedule = (immediate = false) => {
    window.clearTimeout(timer);
    timer = window.setTimeout(save, immediate ? 0 : 650);
  };
  form.addEventListener('input', (event) => {
    if (event.target.matches('input, textarea')) schedule();
  });
  form.addEventListener('change', (event) => {
    if (event.target.matches('input, select, textarea')) schedule(event.target.type === 'file');
  });
  form.addEventListener('submit', (event) => { event.preventDefault(); schedule(true); });
})();
