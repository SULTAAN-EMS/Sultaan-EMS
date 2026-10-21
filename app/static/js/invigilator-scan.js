(function () {
  const startButton = document.getElementById('start-scanner');
  const stopButton = document.getElementById('stop-scanner');
  const cameraBox = document.getElementById('camera-box');
  const video = document.getElementById('qr-video');
  const status = document.getElementById('scan-status');
  const scanForm = document.getElementById('scan-submit');
  const scanValue = document.getElementById('scan-value');
  const studentCodeInput = document.getElementById('student-code');
  if (!startButton || !video || !scanForm) return;

  const storagePrefix = location.pathname === '/scan/student' ? 'publicStudent' : 'invigilator';
  const methodKey = storagePrefix + 'ScanMethod';
  const codeKey = storagePrefix + 'StudentCodeDraft';
  function readStored(key) {
    try { return window.sessionStorage.getItem(key) || ''; } catch (error) { return ''; }
  }
  function storeValue(key, value) {
    try {
      if (value) window.sessionStorage.setItem(key, value);
      else window.sessionStorage.removeItem(key);
    } catch (error) {}
  }

  if (location.pathname === '/scan/student' && new URLSearchParams(location.search).get('new') === '1') {
    storeValue(codeKey, '');
    storeValue(methodKey, '');
  }

  const signoutLink = document.querySelector('.signout-link');
  if (signoutLink) {
    signoutLink.addEventListener('click', function () {
      storeValue(codeKey, '');
      storeValue(methodKey, '');
    });
  }

  document.querySelectorAll('[data-clear-scan]').forEach(function (link) {
    link.addEventListener('click', function () {
      storeValue(codeKey, '');
      storeValue(methodKey, '');
    });
  });

  if (studentCodeInput) {
    const serverValue = studentCodeInput.value.trim();
    if (!serverValue) studentCodeInput.value = readStored(codeKey);
    else storeValue(codeKey, serverValue);
    studentCodeInput.addEventListener('input', function () {
      storeValue(codeKey, studentCodeInput.value.trim());
      if (studentCodeInput.value.trim()) storeValue(methodKey, 'manual');
    });
  }

  if (readStored(methodKey) === 'camera') {
    const buttonLabel = startButton.querySelector('span');
    if (buttonLabel) buttonLabel.textContent = 'Fur Kamarada';
    if (status) status.textContent = 'Kamaraddii hore ayaad adeegsatay. Taabo si aad mar kale u furto.';
  }

  const scriptUrl = new URL(document.currentScript.src);
  const workerUrl = scriptUrl.searchParams.get('worker');
  if (workerUrl && window.QrScanner) window.QrScanner.WORKER_PATH = workerUrl;

  let scanner = null;
  let isSubmitting = false;

  function cameraMessage(error) {
    if (error && error.name === 'NotAllowedError') return 'Fadlan oggolow isticmaalka kamaradda.';
    if (error && error.name === 'NotFoundError') return 'Kamarad lagama helin qalabkan.';
    if (error && error.name === 'NotReadableError') return 'Kamaraddu waa mashquul. Xir app kale oo adeegsanaya.';
    return 'Kamaradda lama furi karin. Hubi oggolaanshaha ama geli tira-taxanaha ardayga.';
  }

  async function stopScanner() {
    if (scanner) await scanner.stop();
    cameraBox.hidden = true;
    startButton.hidden = false;
    startButton.disabled = false;
  }

  startButton.addEventListener('click', async function () {
    storeValue(methodKey, 'camera');
    if (!window.QrScanner) {
      status.textContent = 'Scanner-ku ma diyaar aha. Geli tira-taxanaha ardayga.';
      return;
    }

    startButton.disabled = true;
    cameraBox.hidden = false;
    status.textContent = 'Sugaya oggolaanshaha kamaradda…';
    scanner = new window.QrScanner(video, function (result) {
      if (isSubmitting) return;
      isSubmitting = true;
      scanValue.value = typeof result === 'string' ? result : result.data;
      status.textContent = 'QR code-ka waa la helay. Fadlan sug…';
      Promise.resolve(scanner.stop()).then(function () { scanForm.submit(); });
    }, {
      preferredCamera: 'environment',
      maxScansPerSecond: 8,
      highlightScanRegion: false,
      returnDetailedScanResult: true,
      onDecodeError: function () {}
    });

    try {
      await scanner.start();
      startButton.hidden = true;
      status.textContent = 'Kaamirada ku beeg QR code-ka ardayga.';
    } catch (error) {
      await stopScanner();
      status.textContent = cameraMessage(error);
    }
  });

  stopButton.addEventListener('click', function () {
    stopScanner();
    status.textContent = 'Kaamirada waa la xiray.';
  });

  window.addEventListener('pagehide', function () {
    if (scanner) scanner.destroy();
  });
})();
