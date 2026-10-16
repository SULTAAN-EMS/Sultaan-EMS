(function () {
  'use strict';

  const pad = document.getElementById('sigpad');
  const canvas = document.getElementById('sigCanvas');
  const input = document.getElementById('signature_data');
  if (!pad || !canvas || !input) return;

  const context = canvas.getContext('2d');
  const hint = document.getElementById('sigHint');
  const status = document.getElementById('sigStatus');
  const clearButton = document.getElementById('sigClearBtn');
  const saveButton = document.getElementById('sigSaveBtn');
  const reuseButton = document.getElementById('signatureReuse');
  const colorButtons = pad.querySelectorAll('.sigpad__color');
  const sizeButtons = pad.querySelectorAll('.sigpad__size');
  let color = '#16233F';
  let width = 3;
  let drawing = false;
  let hasStrokes = false;
  let lastPoint = null;
  let resizeFrame = 0;

  function announceChange() {
    input.dispatchEvent(new Event('input', { bubbles: true }));
  }

  function setCanvasSize(preserve = true) {
    const rect = canvas.getBoundingClientRect();
    if (!rect.width || !rect.height) return;

    const ratio = Math.min(window.devicePixelRatio || 1, 2);
    const targetWidth = Math.round(rect.width * ratio);
    const targetHeight = Math.round(rect.height * ratio);
    if (canvas.width === targetWidth && canvas.height === targetHeight) return;

    const data = preserve && hasStrokes ? canvas.toDataURL('image/png') : '';
    canvas.width = targetWidth;
    canvas.height = targetHeight;
    context.setTransform(ratio, 0, 0, ratio, 0, 0);
    context.lineCap = 'round';
    context.lineJoin = 'round';

    if (data) {
      const image = new Image();
      image.onload = () => {
        context.drawImage(image, 0, 0, rect.width, rect.height);
      };
      image.src = data;
    }
  }

  function pointFrom(event) {
    const rect = canvas.getBoundingClientRect();
    return { x: event.clientX - rect.left, y: event.clientY - rect.top };
  }

  function showError(message) {
    pad.classList.add('is-error');
    hint.classList.remove('is-hidden');
    hint.textContent = message;
  }

  function resetPad() {
    setCanvasSize(false);
    context.clearRect(0, 0, canvas.width, canvas.height);
    drawing = false;
    hasStrokes = false;
    lastPoint = null;
    input.value = '';
    hint.textContent = 'Halkan ku saxiix (far ama qalin)';
    hint.classList.remove('is-hidden');
    pad.classList.remove('is-locked', 'is-error');
    saveButton.classList.remove('is-saved');
    status.textContent = 'La kaydiyay';
    announceChange();
  }

  function restoreSignature(dataUrl) {
    return new Promise((resolve, reject) => {
      const image = new Image();
      image.onload = () => {
        setCanvasSize(false);
        context.clearRect(0, 0, canvas.width, canvas.height);
        context.drawImage(image, 0, 0, canvas.clientWidth, canvas.clientHeight);
        hasStrokes = true;
        input.value = canvas.toDataURL('image/png');
        hint.classList.add('is-hidden');
        pad.classList.remove('is-error');
        pad.classList.add('is-locked');
        saveButton.classList.add('is-saved');
        status.textContent = 'La kaydiyay';
        announceChange();
        resolve();
      };
      image.onerror = () => reject(new Error('Saxeexa lama furi karin.'));
      image.src = dataUrl;
    });
  }

  canvas.addEventListener('pointerdown', (event) => {
    if (pad.classList.contains('is-locked')) return;
    event.preventDefault();
    drawing = true;
    lastPoint = pointFrom(event);
    hint.classList.add('is-hidden');
    pad.classList.remove('is-error');
    if (canvas.setPointerCapture) canvas.setPointerCapture(event.pointerId);

    context.fillStyle = color;
    context.beginPath();
    context.arc(lastPoint.x, lastPoint.y, Math.max(width / 2, 1), 0, Math.PI * 2);
    context.fill();
    hasStrokes = true;
  });

  canvas.addEventListener('pointermove', (event) => {
    if (!drawing || pad.classList.contains('is-locked')) return;
    const point = pointFrom(event);
    context.strokeStyle = color;
    context.lineWidth = width;
    context.beginPath();
    context.moveTo(lastPoint.x, lastPoint.y);
    context.lineTo(point.x, point.y);
    context.stroke();
    lastPoint = point;
    hasStrokes = true;
  });

  function stopDrawing() {
    if (!drawing) return;
    drawing = false;
    lastPoint = null;
    if (hasStrokes) {
      input.value = canvas.toDataURL('image/png');
      announceChange();
    }
  }

  canvas.addEventListener('pointerup', stopDrawing);
  canvas.addEventListener('pointercancel', stopDrawing);
  window.addEventListener('pointerup', stopDrawing);

  colorButtons.forEach((button) => {
    button.addEventListener('click', () => {
      colorButtons.forEach((item) => {
        const active = item === button;
        item.classList.toggle('is-active', active);
        item.setAttribute('aria-pressed', String(active));
      });
      color = button.dataset.color;
    });
  });

  sizeButtons.forEach((button) => {
    button.addEventListener('click', () => {
      sizeButtons.forEach((item) => {
        const active = item === button;
        item.classList.toggle('is-active', active);
        item.setAttribute('aria-pressed', String(active));
      });
      width = Number(button.dataset.width) || 3;
    });
  });

  clearButton.addEventListener('click', resetPad);
  saveButton.addEventListener('click', () => {
    if (!hasStrokes) {
      showError('Fadlan marka hore ku saxiix, kadibna kaydi');
      return;
    }
    input.value = canvas.toDataURL('image/png');
    pad.classList.add('is-locked');
    pad.classList.remove('is-error');
    saveButton.classList.add('is-saved');
    status.textContent = 'La kaydiyay';
    announceChange();
  });

  reuseButton?.addEventListener('click', async () => {
    if (reuseButton.disabled) return;
    reuseButton.disabled = true;
    try {
      const response = await fetch(reuseButton.dataset.signatureUrl, {
        credentials: 'same-origin',
        headers: { Accept: 'application/json' },
      });
      if (!response.ok) throw new Error('Saxeexa lama soo heli karin.');
      const payload = await response.json();
      if (!payload.signature_data) throw new Error('Saxeex kaydsan lama helin.');
      await restoreSignature(payload.signature_data);
    } catch (error) {
      showError(error.message || 'Saxeexa lama soo ceshan karin.');
    } finally {
      reuseButton.disabled = false;
    }
  });

  function scheduleResize() {
    cancelAnimationFrame(resizeFrame);
    resizeFrame = requestAnimationFrame(() => setCanvasSize());
  }

  setCanvasSize(false);
  window.addEventListener('resize', scheduleResize, { passive: true });
})();
