(() => {
  const launch = document.querySelector('.js-top10-launch');
  const overlay = document.getElementById('top10TunnelOverlay');
  if (!launch || !overlay) return;

  const canvas = document.getElementById('top10TunnelCanvas');
  const loader = document.getElementById('top10TunnelLoader');
  const emptyState = document.getElementById('top10TunnelEmpty');
  const dotsWrap = document.getElementById('top10TunnelDots');
  const hint = document.getElementById('top10TunnelHint');
  const closeButton = document.getElementById('top10TunnelClose');
  const muteButton = document.getElementById('top10TunnelMute');
  const audio = document.getElementById('top10TunnelAudio');
  const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  const TUNNEL_WIDTH = 2, TUNNEL_HEIGHT = 1.8, SEGMENT_DEPTH = 1;
  const NUM_SEGMENTS = 15, LINE_RADIUS = 0.003, SCROLL_TO_Z = 0.05, CAMERA_CHASE = 0.1;
  const FADE_IN = 1, GRID = 3, GOLD = 0xd4af37, LINE_COLOR = 0x8b93a1;
  const FOG_FAR = NUM_SEGMENTS * SEGMENT_DEPTH * .95;
  const CARD_W = 1.15, CARD_CANVAS_W = 720, CARD_CANVAS_H = 940;
  const CARD_D_START = 20, CARD_D_END = 1.35, SECONDS_BETWEEN_CARDS = 10;

  let renderer, scene, camera, segments = [], fading = [], studentCards = [], populateSegment;
  let scrollPos = 0, pressed = false, last = 0, raf = 0, running = false, muted = false;
  let imageIndex = 0, colorIndex = 0, students = [], cardSpeed = 0;
  let dataLoaded = false;

  const setOverlayLoading = (message) => {
    overlay.classList.remove('is-ready');
    loader.hidden = false;
    emptyState.hidden = true;
    const text = loader.querySelector('strong');
    if (text && message) text.textContent = message;
  };
  const setOverlayEmpty = (message) => {
    overlay.classList.remove('is-ready');
    loader.hidden = true;
    emptyState.hidden = false;
    const text = emptyState.querySelector('strong');
    if (text && message) text.textContent = message;
  };
  const setOverlayReady = () => {
    overlay.classList.add('is-ready');
    loader.hidden = true;
    emptyState.hidden = true;
  };

  const fadeVolume = (target, duration = 700, reset = false) => {
    if (!audio) return;
    const start = Number(audio.volume || 0);
    const started = performance.now();
    const tick = (now) => {
      const progress = Math.min(1, (now - started) / duration);
      audio.volume = Math.max(0, Math.min(1, start + (target - start) * progress));
      if (progress < 1) requestAnimationFrame(tick);
      else if (reset && target === 0) {
        audio.pause();
        audio.currentTime = 0;
      }
    };
    requestAnimationFrame(tick);
  };
  const startMusic = () => {
    if (!audio || muted) return;
    const source = audio.dataset.customSrc || audio.dataset.defaultSrc;
    if (source && audio.src !== new URL(source, window.location.href).href) audio.src = source;
    audio.volume = 0;
    audio.play().then(() => fadeVolume(.22, 800)).catch(() => {});
  };
  const stopMusic = () => fadeVolume(0, 500, true);
  const toggleMute = () => {
    muted = !muted;
    muteButton.querySelector('i').className = muted ? 'fa-solid fa-volume-xmark' : 'fa-solid fa-volume-high';
    if (muted) fadeVolume(0, 280);
    else startMusic();
  };

  function drawStudentCard(context, student, photo) {
    const W = CARD_CANVAS_W, H = CARD_CANVAS_H, pad = 8;
    context.clearRect(0, 0, W, H);
    context.beginPath(); context.roundRect(pad, pad, W - pad * 2, H - pad * 2, 32);
    context.fillStyle = 'rgba(10,12,17,.9)'; context.fill();
    context.lineWidth = 3; context.strokeStyle = 'rgba(212,175,55,.55)'; context.stroke();
    const photoMargin = 56, px = photoMargin, py = photoMargin, pw = W - photoMargin * 2, ph = H * .54 - photoMargin;
    context.save(); context.beginPath(); context.roundRect(px, py, pw, ph, 24); context.clip();
    if (photo) {
      const imageRatio = photo.width / photo.height, boxRatio = pw / ph;
      const dw = imageRatio > boxRatio ? ph * imageRatio : pw;
      const dh = imageRatio > boxRatio ? ph : pw / imageRatio;
      context.drawImage(photo, px - (dw - pw) / 2, py - (dh - ph) / 2, dw, dh);
    } else {
      context.fillStyle = '#1a1f2b'; context.fillRect(px, py, pw, ph);
      context.fillStyle = '#d4af37'; context.font = "600 130px 'Fraunces', serif"; context.textAlign = 'center';
      context.fillText((student.name || '?').trim().charAt(0).toUpperCase(), W / 2, py + ph * .62);
    }
    context.restore(); context.beginPath(); context.roundRect(px, py, pw, ph, 24);
    context.lineWidth = 4; context.strokeStyle = '#d4af37'; context.stroke();
    const bx = px + pw - 10, by = py + 10, br = 60;
    context.beginPath(); context.arc(bx, by, br, 0, Math.PI * 2); context.fillStyle = 'rgba(10,12,17,.94)'; context.fill(); context.lineWidth = 4; context.strokeStyle = '#d4af37'; context.stroke();
    context.textAlign = 'center'; context.textBaseline = 'middle'; context.fillStyle = '#d4af37'; context.font = "700 40px 'Fraunces', serif"; context.fillText(`#${student.rank}`, bx, by - 6);
    context.fillStyle = '#8b93a1'; context.font = "600 13px 'Space Grotesk', sans-serif"; context.fillText('RANK', bx, by + 22);
    const infoTop = py + ph + 66;
    context.textBaseline = 'alphabetic'; context.fillStyle = '#f4f1ea'; context.font = "600 38px 'Fraunces', serif"; context.fillText(student.name, W / 2, infoTop);
    context.fillStyle = '#8b93a1'; context.font = "500 22px 'Space Grotesk', sans-serif"; context.fillText(student.class_name, W / 2, infoTop + 32);
    const lineY = infoTop + 58; context.strokeStyle = 'rgba(244,241,234,.14)'; context.lineWidth = 2; context.beginPath(); context.moveTo(40, lineY); context.lineTo(W - 40, lineY); context.stroke();
    const rows = [[[student.student_id, 'STUDENT ID'], [`${Number(student.average).toFixed(2)}%`, 'CELCELIS GUUD']], [[student.academic_year, 'SANAD DUGSIYEEDKA'], [student.exam_type, 'EXAM TYPE']]];
    const cellW = (W - 80) / 2;
    rows.forEach((row, rowIndex) => row.forEach((item, itemIndex) => {
      const x = 40 + itemIndex * cellW, y = lineY + 48 + rowIndex * 70;
      context.textAlign = 'left'; context.fillStyle = '#f4f1ea'; context.font = "600 22px 'Space Grotesk', sans-serif"; context.fillText(item[0] || '', x, y);
      context.fillStyle = '#8b93a1'; context.font = "500 13px 'Space Grotesk', sans-serif"; context.fillText(item[1], x, y + 22);
    }));
    context.textAlign = 'left';
  }

  function buildStudentCards() {
    const loop = CARD_D_START - CARD_D_END;
    const gap = loop / students.length;
    cardSpeed = (CARD_D_START - CARD_D_END) / (SECONDS_BETWEEN_CARDS * students.length);
    studentCards = students.map((student, index) => {
      const cardCanvas = document.createElement('canvas'); cardCanvas.width = CARD_CANVAS_W; cardCanvas.height = CARD_CANVAS_H;
      const context = cardCanvas.getContext('2d'); drawStudentCard(context, student, null);
      const texture = new THREE.CanvasTexture(cardCanvas);
      if (THREE.SRGBColorSpace) texture.colorSpace = THREE.SRGBColorSpace;
      const material = new THREE.MeshBasicMaterial({ map: texture, transparent: true, opacity: 0, side: THREE.DoubleSide, depthWrite: false, fog: true });
      const mesh = new THREE.Mesh(new THREE.PlaneGeometry(CARD_W, CARD_W * CARD_CANVAS_H / CARD_CANVAS_W), material); mesh.renderOrder = 2; scene.add(mesh);
      if (student.photo) {
        const image = new Image(); image.crossOrigin = 'anonymous';
        image.onload = () => { drawStudentCard(context, student, image); texture.needsUpdate = true; };
        image.src = student.photo;
      }
      return { mesh, material, texture, d: CARD_D_START - index * gap, index };
    });
  }

  function updateStudentCards(dt) {
    const loop = CARD_D_START - CARD_D_END;
    let nearest = -1, nearestDepth = Infinity;
    studentCards.forEach((card) => {
      card.d -= dt * cardSpeed;
      if (card.d < CARD_D_END) card.d += loop;
      card.mesh.position.set(0, 0, camera.position.z - card.d);
      const fadeIn = Math.min(1, Math.max(0, (CARD_D_START - card.d) / 3));
      const fadeOut = Math.min(1, Math.max(0, (card.d - CARD_D_END) / 1.6));
      card.material.opacity = Math.min(fadeIn, fadeOut) * .98;
      if (card.d > 0 && card.d < nearestDepth) { nearestDepth = card.d; nearest = card.index; }
    });
    if (nearest >= 0) [...dotsWrap.children].forEach((dot, index) => dot.classList.toggle('is-current', index === nearest));
  }

  function buildTunnel() {
    scene = new THREE.Scene(); scene.background = new THREE.Color(0x000000); scene.fog = new THREE.Fog(0x000000, Math.min(FOG_FAR * .35, FOG_FAR - .01), FOG_FAR);
    camera = new THREE.PerspectiveCamera(45, 1, 1, 1000); camera.position.set(0, 0, 0);
    renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: false, powerPreference: 'high-performance' }); renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    const lineMaterial = new THREE.MeshBasicMaterial({ color: LINE_COLOR, transparent: true, opacity: .5 });
    const textureLoader = new THREE.TextureLoader(); textureLoader.setCrossOrigin('anonymous');
    const imageMats = students.map((student) => {
      const material = new THREE.MeshBasicMaterial({ transparent: true, opacity: 0, side: THREE.DoubleSide });
      if (student.photo) textureLoader.load(student.photo, (texture) => { texture.minFilter = THREE.LinearFilter; texture.generateMipmaps = false; if (THREE.SRGBColorSpace) texture.colorSpace = THREE.SRGBColorSpace; material.map = texture; material.needsUpdate = true; fading.push(material); }, undefined, () => {});
      return material;
    });
    const palette = [0x1a1f2b, 0x14181f, GOLD, 0x2a2f3a];
    const colorMats = palette.map((hex) => new THREE.MeshBasicMaterial({ color: hex, side: THREE.DoubleSide }));
    const hw = TUNNEL_WIDTH / 2, hh = TUNNEL_HEIGHT / 2, colW = TUNNEL_WIDTH / GRID, rowH = TUNNEL_HEIGHT / GRID;
    const geoFloor = new THREE.PlaneGeometry(colW, SEGMENT_DEPTH), geoWall = new THREE.PlaneGeometry(SEGMENT_DEPTH, rowH);
    const tubeGeometry = (x, y, z) => new THREE.TubeGeometry(new THREE.LineCurve3(new THREE.Vector3(0, 0, 0), new THREE.Vector3(x, y, z)), 1, LINE_RADIUS, 8);
    const tubeZ = tubeGeometry(0, 0, -SEGMENT_DEPTH), tubeX = tubeGeometry(TUNNEL_WIDTH, 0, 0), tubeY = tubeGeometry(0, TUNNEL_HEIGHT, 0);
    const slots = [];
    for (let col = 0; col < GRID; col += 1) { const x = -hw + col * colW + colW / 2; slots.push({ geo: geoFloor, pos: new THREE.Vector3(x, -hh, -SEGMENT_DEPTH / 2), rot: new THREE.Euler(-Math.PI / 2, 0, 0) }, { geo: geoFloor, pos: new THREE.Vector3(x, hh, -SEGMENT_DEPTH / 2), rot: new THREE.Euler(Math.PI / 2, 0, 0) }); }
    for (let row = 0; row < GRID; row += 1) { const y = -hh + row * rowH + rowH / 2; slots.push({ geo: geoWall, pos: new THREE.Vector3(-hw, y, -SEGMENT_DEPTH / 2), rot: new THREE.Euler(0, Math.PI / 2, 0) }, { geo: geoWall, pos: new THREE.Vector3(hw, y, -SEGMENT_DEPTH / 2), rot: new THREE.Euler(0, -Math.PI / 2, 0) }); }
    const tube = (geometry, x, y) => { const mesh = new THREE.Mesh(geometry, lineMaterial); mesh.position.set(x, y, 0); return mesh; };
    const populate = (group) => group.userData.slabs.forEach((slab) => { const chance = Math.random(); if (chance < .72) { slab.visible = true; slab.material = imageMats[imageIndex++ % imageMats.length]; } else if (chance < .92) { slab.visible = true; slab.material = colorMats[(colorIndex++ % (colorMats.length - 1)) + 1]; } else slab.visible = false; });
    const createSegment = (z) => {
      const group = new THREE.Group(); group.position.z = z;
      for (let col = 0; col <= GRID; col += 1) { const x = -hw + col * colW; group.add(tube(tubeZ, x, -hh), tube(tubeZ, x, hh)); }
      for (let row = 1; row < GRID; row += 1) { const y = -hh + row * rowH; group.add(tube(tubeZ, -hw, y), tube(tubeZ, hw, y)); }
      group.add(tube(tubeX, -hw, -hh), tube(tubeX, -hw, hh), tube(tubeY, -hw, -hh), tube(tubeY, hw, -hh));
      group.userData.slabs = slots.map((slot) => { const mesh = new THREE.Mesh(slot.geo, colorMats[0]); mesh.position.copy(slot.pos); mesh.rotation.copy(slot.rot); mesh.visible = false; group.add(mesh); return mesh; });
      populate(group); return group;
    };
    populateSegment = populate; segments = [];
    for (let index = 0; index < NUM_SEGMENTS; index += 1) { const segment = createSegment(-index * SEGMENT_DEPTH); scene.add(segment); segments.push(segment); }
    buildStudentCards();
    const resize = () => { const width = Math.max(1, canvas.clientWidth || window.innerWidth), height = Math.max(1, canvas.clientHeight || window.innerHeight); camera.aspect = width / height; camera.updateProjectionMatrix(); renderer.setSize(width, height, false); };
    window.addEventListener('resize', resize, { passive: true }); resize();
  }

  function animate(now) {
    if (!running) return;
    raf = requestAnimationFrame(animate);
    const dt = last ? Math.min((now - last) / 1000, 1 / 30) : 1 / 60; last = now;
    scrollPos += pressed ? 3.2 : 1;
    camera.position.z += CAMERA_CHASE * (-SCROLL_TO_Z * scrollPos - camera.position.z);
    const span = NUM_SEGMENTS * SEGMENT_DEPTH, z = camera.position.z;
    segments.forEach((segment) => { if (segment.position.z > z + SEGMENT_DEPTH) { segment.position.z = Math.min(...segments.map((item) => item.position.z)) - SEGMENT_DEPTH; populateSegment(segment); } else if (segment.position.z < z - span - SEGMENT_DEPTH) { segment.position.z = Math.max(...segments.map((item) => item.position.z)) + SEGMENT_DEPTH; populateSegment(segment); } });
    fading = fading.filter((material) => { material.opacity = Math.min(1, material.opacity + dt / FADE_IN); return material.opacity < 1; });
    updateStudentCards(dt); renderer.render(scene, camera);
  }

  function destroyTunnel() {
    cancelAnimationFrame(raf); running = false;
    if (!renderer) return;
    scene.traverse((object) => { if (object.geometry) object.geometry.dispose?.(); if (object.material) { const materials = Array.isArray(object.material) ? object.material : [object.material]; materials.forEach((material) => { material.map?.dispose?.(); material.dispose?.(); }); } });
    renderer.dispose(); renderer = scene = camera = null; segments = []; fading = []; studentCards = []; dotsWrap.replaceChildren();
  }

  function startTunnel() {
    destroyTunnel(); scrollPos = 0; last = 0; imageIndex = 0; colorIndex = 0;
    students.forEach((_, index) => { const dot = document.createElement('span'); dot.classList.toggle('is-current', index === 0); dotsWrap.appendChild(dot); });
    buildTunnel(); running = true; raf = requestAnimationFrame(animate); setOverlayReady(); startMusic();
  }

  async function openTunnel() {
    overlay.classList.add('is-open'); overlay.setAttribute('aria-hidden', 'false'); setOverlayLoading('Loading Top 10 Students');
    try {
      const response = await fetch(launch.dataset.top10Url, { credentials: 'same-origin' });
      const payload = await response.json();
      if (!response.ok || !payload.ok) throw new Error(payload.message || 'Unable to load Top 10 Students.');
      students = (payload.students || []).map((entry) => ({ ...entry, academic_year: payload.academic_year, exam_type: payload.exam_type }));
      if (!students.length) { setOverlayEmpty('Top 10 is not available yet'); return; }
      hint.textContent = `Top 10 · ${payload.class_name} · Hold to speed up · Esc to close`;
      startTunnel();
    } catch (error) { setOverlayEmpty(error.message || 'Unable to load Top 10 Students.'); }
  }
  function closeTunnel() { destroyTunnel(); overlay.classList.remove('is-open', 'is-ready'); overlay.setAttribute('aria-hidden', 'true'); stopMusic(); }

  launch.addEventListener('click', openTunnel);
  closeButton.addEventListener('click', closeTunnel);
  muteButton.addEventListener('click', toggleMute);
  document.addEventListener('keydown', (event) => { if (event.key === 'Escape' && overlay.classList.contains('is-open')) closeTunnel(); });
  canvas.addEventListener('pointerdown', () => { pressed = true; });
  window.addEventListener('pointerup', () => { pressed = false; });
  canvas.addEventListener('pointerleave', () => { pressed = false; });
  window.addEventListener('pagehide', closeTunnel);
})();
