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
  const pauseButton = document.getElementById('top10TunnelPause');
  const audio = document.getElementById('top10TunnelAudio');
  const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  const TUNNEL_WIDTH = 2, TUNNEL_HEIGHT = 1.8, SEGMENT_DEPTH = 1;
  const NUM_SEGMENTS = 15, LINE_RADIUS = 0.003, SCROLL_TO_Z = 0.05, CAMERA_CHASE = 0.1;
  const FADE_IN = 1, GRID = 3, GOLD = 0xd4af37, LINE_COLOR = 0x8b93a1;
  const FOG_FAR = NUM_SEGMENTS * SEGMENT_DEPTH * .95;
  const CARD_W = 1.15, CARD_CANVAS_W = 720, CARD_CANVAS_H = 940;
  const CARD_D_START = 20, CARD_D_END = 2.65, SECONDS_BETWEEN_CARDS = 7.0;

  let renderer, scene, camera, segments = [], fading = [], studentCards = [], populateSegment;
  let scrollPos = 0, pressed = false, last = 0, raf = 0, running = false, muted = false;
  let paused = false, autoPausedByVisibility = false;
  let imageIndex = 0, colorIndex = 0, students = [], cardSpeed = 0, nameAnimClock = 0;
  let combAvailable = true;
  let dataLoaded = false;
  let trackList = [], currentTrackIndex = 0;

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

  const syncPauseUI = () => {
    if (!pauseButton) return;
    const icon = pauseButton.querySelector('i');
    const label = pauseButton.querySelector('span');
    if (icon) icon.className = paused ? 'fa-solid fa-play' : 'fa-solid fa-pause';
    if (label) label.textContent = paused ? 'Resume' : 'Pause';
    pauseButton.title = paused ? 'Resume tunnel' : 'Pause tunnel';
  };

  const pauseTunnel = (reason = 'manual') => {
    if (!running || paused) return;
    paused = true;
    if (reason === 'visibility') autoPausedByVisibility = true;
    cancelAnimationFrame(raf);
    running = false;
    pressed = false;
    if (audio) audio.pause();
    syncPauseUI();
  };

  const resumeTunnel = () => {
    if (!overlay.classList.contains('is-open') || !paused) return;
    paused = false;
    autoPausedByVisibility = false;
    running = true;
    last = 0;
    raf = requestAnimationFrame(animate);
    if (audio && !muted) audio.play().catch(() => {});
    syncPauseUI();
  };

  const parseTrackSources = () => {
    if (!audio) return [];
    try {
      const raw = audio.dataset.customSources;
      const parsed = JSON.parse(raw || '[]');
      if (Array.isArray(parsed) && parsed.length > 0) {
        const urls = parsed.map(entry => (typeof entry === 'string' ? entry : entry.url)).filter(Boolean);
        if (urls.length > 0) return urls;
      }
    } catch (e) {}
    const single = audio.dataset.customSrc;
    if (single) return [single];
    return [audio.dataset.defaultSrc];
  };

  const playTrackAtIndex = (index) => {
    if (!audio || !trackList.length || muted) return;
    currentTrackIndex = index % trackList.length;
    const targetUrl = trackList[currentTrackIndex];
    if (targetUrl) {
      const fullUrl = new URL(targetUrl, window.location.href).href;
      if (audio.src !== fullUrl) {
        audio.src = fullUrl;
      }
      audio.volume = muted ? 0 : 0.22;
      if (running && !paused) {
        audio.play().catch(() => {});
      }
    }
  };

  const startMusic = () => {
    if (!audio || muted) return;
    trackList = parseTrackSources();
    currentTrackIndex = 0;
    playTrackAtIndex(0);
  };

  const stopMusic = () => {
    if (!audio) return;
    audio.pause();
    audio.currentTime = 0;
  };

  const toggleMute = () => {
    muted = !muted;
    if (muteButton) {
      const icon = muteButton.querySelector('i');
      if (icon) icon.className = muted ? 'fa-solid fa-volume-xmark' : 'fa-solid fa-volume-high';
    }
    if (audio) {
      if (muted) {
        audio.volume = 0;
        audio.pause();
      } else {
        audio.volume = 0.22;
        if (running && !paused) audio.play().catch(() => {});
      }
    }
  };

  if (audio) {
    audio.addEventListener('ended', () => {
      if (!running || paused) return;
      if (trackList.length > 1) {
        currentTrackIndex = (currentTrackIndex + 1) % trackList.length;
        playTrackAtIndex(currentTrackIndex);
      } else {
        audio.currentTime = 0;
        audio.play().catch(() => {});
      }
    });
  }

  function drawSparkle(context, x, y, radius, rotation) {
    context.save();
    context.translate(x, y); context.rotate(rotation);
    context.beginPath();
    for (let index = 0; index < 10; index += 1) {
      const angle = -Math.PI / 2 + index * Math.PI / 5;
      const length = index % 2 ? radius * .43 : radius;
      const pointX = Math.cos(angle) * length, pointY = Math.sin(angle) * length;
      if (index === 0) context.moveTo(pointX, pointY); else context.lineTo(pointX, pointY);
    }
    context.closePath(); context.fillStyle = '#d4af37'; context.shadowColor = 'rgba(212,175,55,.82)'; context.shadowBlur = 15; context.fill();
    context.restore();
  }

  function drawCardVectorIcon(ctx, type, x, y, size = 20, color = '#FFFFFF') {
    ctx.save();
    ctx.translate(x, y);
    ctx.strokeStyle = color;
    ctx.fillStyle = color;
    ctx.lineWidth = 2;
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';

    const s = size / 22;
    ctx.scale(s, s);

    const t = String(type || '').toLowerCase();
    if (t === 'class' || t.includes('class') || t.includes('level') || t === '🎓') {
      ctx.beginPath();
      ctx.moveTo(0, -9);
      ctx.lineTo(11, -3);
      ctx.lineTo(0, 3);
      ctx.lineTo(-11, -3);
      ctx.closePath();
      ctx.stroke();
      ctx.beginPath();
      ctx.moveTo(-7, 0);
      ctx.quadraticCurveTo(0, 5, 7, 0);
      ctx.lineTo(7, 4);
      ctx.quadraticCurveTo(0, 8, -7, 4);
      ctx.closePath();
      ctx.fill();
      ctx.beginPath();
      ctx.moveTo(6, -0.5);
      ctx.lineTo(9.5, 3);
      ctx.lineTo(9.5, 7);
      ctx.stroke();
    } else if (t === 'score' || t.includes('score') || t.includes('avg') || t.includes('percent') || t === '📊' || t.includes('%')) {
      ctx.beginPath(); ctx.roundRect(-8, 0, 4, 9, 1.5); ctx.fill();
      ctx.beginPath(); ctx.roundRect(-2, -7, 4, 16, 1.5); ctx.fill();
      ctx.beginPath(); ctx.roundRect(4, -4, 4, 13, 1.5); ctx.fill();
    } else if (t === 'year' || t === 'calendar' || t.includes('year') || t.includes('sanad') || t === '★') {
      ctx.beginPath();
      ctx.roundRect(-9, -7, 18, 16, 3);
      ctx.stroke();
      ctx.beginPath();
      ctx.moveTo(-9, -2); ctx.lineTo(9, -2);
      ctx.stroke();
      ctx.beginPath();
      ctx.moveTo(-5, -10); ctx.lineTo(-5, -6);
      ctx.moveTo(5, -10);  ctx.lineTo(5, -6);
      ctx.stroke();
      ctx.beginPath();
      ctx.arc(-4, 2, 1.2, 0, Math.PI * 2);
      ctx.arc(0, 2, 1.2, 0, Math.PI * 2);
      ctx.arc(4, 2, 1.2, 0, Math.PI * 2);
      ctx.arc(-4, 5.5, 1.2, 0, Math.PI * 2);
      ctx.arc(0, 5.5, 1.2, 0, Math.PI * 2);
      ctx.arc(4, 5.5, 1.2, 0, Math.PI * 2);
      ctx.fill();
    } else if (t === 'exam' || t === 'check' || t.includes('exam') || t === '✓') {
      ctx.beginPath();
      ctx.roundRect(-8, -9, 16, 18, 3);
      ctx.stroke();
      ctx.beginPath();
      ctx.moveTo(-4, 0);
      ctx.lineTo(-1, 3);
      ctx.lineTo(4, -3);
      ctx.stroke();
    } else if (t === 'id' || t.includes('id') || t === '🪪') {
      ctx.beginPath();
      ctx.roundRect(-10, -7, 20, 14, 3);
      ctx.stroke();
      ctx.beginPath();
      ctx.arc(0, -7, 2, 0, Math.PI * 2);
      ctx.fill();
      ctx.beginPath();
      ctx.roundRect(-7, -4, 6, 6, 1);
      ctx.stroke();
      ctx.beginPath();
      ctx.moveTo(1, -3); ctx.lineTo(7, -3);
      ctx.moveTo(1, 0);  ctx.lineTo(5, 0);
      ctx.moveTo(-7, 4); ctx.lineTo(7, 4);
      ctx.stroke();
    } else if (t === 'rank' || t.includes('rank') || t === '🏆') {
      ctx.beginPath();
      ctx.moveTo(-7, -9);
      ctx.lineTo(7, -9);
      ctx.lineTo(5, -1);
      ctx.quadraticCurveTo(0, 4, -5, -1);
      ctx.closePath();
      ctx.stroke();
      ctx.beginPath();
      ctx.moveTo(-7, -7); ctx.quadraticCurveTo(-11, -6, -9, -2); ctx.quadraticCurveTo(-7, 0, -5, -2);
      ctx.moveTo(7, -7);  ctx.quadraticCurveTo(11, -6, 9, -2);  ctx.quadraticCurveTo(7, 0, 5, -2);
      ctx.stroke();
      ctx.beginPath();
      ctx.moveTo(0, 3.5); ctx.lineTo(0, 7);
      ctx.stroke();
      ctx.beginPath();
      ctx.roundRect(-6, 7, 12, 3, 1);
      ctx.fill();
    } else {
      ctx.beginPath();
      ctx.arc(0, 0, 4, 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.restore();
  }

  function hexToRgba(hex, a){
    const v = String(hex).replace('#','');
    const n = parseInt(v, 16);
    return `rgba(${(n>>16)&255},${(n>>8)&255},${n&255},${a})`;
  }

  function drawStudentCard(ctx, student, photoImg, animPhase){
    const W = CARD_CANVAS_W, H = CARD_CANVAS_H;
    const t = animPhase || 0;
    ctx.clearRect(0,0,W,H);

    // Outer border rectangle (the whole card)
    const pad = 8;
    ctx.save();
    ctx.shadowColor = 'rgba(0,0,0,0.62)';
    ctx.shadowBlur = 34;
    ctx.shadowOffsetY = 16;
    ctx.beginPath();
    ctx.roundRect(pad,pad,W-pad*2,H-pad*2,32);
    ctx.fillStyle = 'rgba(10,12,17,0.9)';
    ctx.fill();
    ctx.restore();
    ctx.lineWidth = 3;
    ctx.strokeStyle = 'rgba(212,175,55,0.55)';
    ctx.stroke();

    // Big photo, centered inside the border rectangle with clear breathing room
    const photoMargin = 56;
    const px = photoMargin, py = photoMargin;
    const pw = W - photoMargin*2;
    const ph = H * 0.54 - photoMargin;
    ctx.save();
    ctx.beginPath();
    ctx.roundRect(px,py,pw,ph,24);
    ctx.closePath(); ctx.clip();
    if (photoImg){
      const imgR = photoImg.width/photoImg.height, boxR = pw/ph;
      let dw, dh, dx, dy;
      if (imgR > boxR){ dw = pw; dh = pw/imgR; dx = px; dy = py + (ph-dh)/2; }
      else { dh = ph; dw = ph*imgR; dx = px + (pw-dw)/2; dy = py; }
      ctx.fillStyle = '#0b0e13';
      ctx.fillRect(px, py, pw, ph);
      ctx.drawImage(photoImg, dx, dy, dw, dh);
    } else {
      ctx.fillStyle = '#1a1f2b'; ctx.fillRect(px,py,pw,ph);
      ctx.fillStyle = '#d4af37'; ctx.font = "600 130px 'Fraunces', serif"; ctx.textAlign = 'center';
      ctx.fillText(((student.name || student.student_name || '?')).trim().charAt(0).toUpperCase(), W / 2, py + ph * .62);
    }
    ctx.restore();
    ctx.beginPath();
    ctx.roundRect(px,py,pw,ph,24);
    ctx.lineWidth = 4; ctx.strokeStyle = '#D4AF37'; ctx.stroke();

    // Rank badge overlapping top-right corner if rank exists
    const rankVal = student.rank || student.position;
    if (rankVal !== undefined && rankVal !== null && rankVal !== '') {
      const bx = px+pw-10, by = py+10, br = 60;
      ctx.beginPath(); ctx.arc(bx,by,br,0,Math.PI*2);
      ctx.fillStyle = 'rgba(10,12,17,0.94)'; ctx.fill();
      ctx.lineWidth = 4; ctx.strokeStyle = '#D4AF37'; ctx.stroke();
      ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
      ctx.fillStyle = '#D4AF37';
      ctx.font = "700 40px 'Fraunces', serif";
      ctx.fillText('#' + rankVal, bx, by-6);
      ctx.fillStyle = '#8B93A1';
      ctx.font = "600 13px 'Space Grotesk', sans-serif";
      ctx.fillText('RANK', bx, by+22);
    }

    // Name + class, pulled well clear of the photo below it.
    // Faithful port of Section 1: Student Name
    const infoTop = py+ph+66;
    ctx.textBaseline = 'alphabetic'; ctx.textAlign = 'center';
    ctx.font = "600 40px 'Fraunces', serif";

    const NAME_A = '#D4AF37';   // gold
    const NAME_B = '#FFE9AE';   // soft champagne-gold highlight
    const studentNameStr = student.name || student.student_name || '';
    const nameWidth = Math.max(ctx.measureText(studentNameStr).width, 40);

    // Bounding box that hugs just the name text
    const boxPadX = 26, boxPadY = 10;
    const textTop = infoTop - 34, textBottom = infoTop + 10;
    const boxX = W/2 - nameWidth/2 - boxPadX;
    const boxY = textTop - boxPadY;
    const boxW = nameWidth + boxPadX*2;
    const boxH = (textBottom - textTop) + boxPadY*2;

    // 1) .name-glow-bg — soft pulsing radial highlight behind the name
    const glowPulse = 0.3 + 0.3 * (0.5 + 0.5*Math.sin(t * (2*Math.PI/2.6)));
    ctx.save();
    const glowR = ctx.createRadialGradient(
      W/2, boxY + boxH/2, 4,
      W/2, boxY + boxH/2, boxW*0.62
    );
    glowR.addColorStop(0, `rgba(212,175,55,${(0.30 + glowPulse*0.3).toFixed(3)})`);
    glowR.addColorStop(1, 'rgba(212,175,55,0)');
    ctx.fillStyle = glowR;
    ctx.fillRect(boxX-30, boxY-30, boxW+60, boxH+60);

    // Dark tinted highlight box directly behind the text
    ctx.beginPath();
    ctx.roundRect(boxX, boxY, boxW, boxH, 12);
    ctx.fillStyle = 'rgba(10,12,17,0.55)';
    ctx.fill();
    ctx.strokeStyle = 'rgba(212,175,55,0.28)';
    ctx.lineWidth = 1;
    ctx.stroke();
    ctx.restore();

    // 2) .animated-name — shimmering ivory→gold→ivory gradient fill + glow
    const shift = Math.sin(t * 0.6) * nameWidth * 0.65;
    const nameGrad = ctx.createLinearGradient(
      W/2 - nameWidth/2 + shift, 0,
      W/2 + nameWidth/2 + shift, 0
    );
    nameGrad.addColorStop(0.00, '#F4F1EA');
    nameGrad.addColorStop(0.32, '#F4F1EA');
    nameGrad.addColorStop(0.50, NAME_B);
    nameGrad.addColorStop(0.58, NAME_A);
    nameGrad.addColorStop(0.68, NAME_B);
    nameGrad.addColorStop(0.84, '#F4F1EA');
    nameGrad.addColorStop(1.00, '#F4F1EA');

    ctx.save();
    ctx.shadowColor = 'rgba(212,175,55,' + (0.4 + 0.3*Math.sin(t*2.4)).toFixed(3) + ')';
    ctx.shadowBlur = 20 + 10*Math.sin(t*2.4);
    ctx.fillStyle = nameGrad;
    ctx.fillText(studentNameStr, W/2, infoTop);
    ctx.restore();

    // 3) .name-sweep — soft diagonal light bar sweeping across the box
    {
      const cyclePos = (t / 2.8) % 1;
      let sweepOpacity = 0;
      if (cyclePos < 0.08) sweepOpacity = cyclePos/0.08;
      else if (cyclePos < 0.55) sweepOpacity = 1;
      else if (cyclePos < 0.65) sweepOpacity = 1 - (cyclePos-0.55)/0.10;
      if (sweepOpacity > 0.01){
        const sweepX = boxX - boxW*0.45 + cyclePos * (boxW*1.75);
        ctx.save();
        ctx.beginPath();
        ctx.roundRect(boxX, boxY, boxW, boxH, 12);
        ctx.clip();
        ctx.globalCompositeOperation = 'overlay';
        ctx.globalAlpha = sweepOpacity;
        ctx.translate(sweepX, boxY + boxH/2);
        ctx.rotate(-20 * Math.PI/180);
        const sweepGrad = ctx.createLinearGradient(-boxW*0.15, 0, boxW*0.15, 0);
        sweepGrad.addColorStop(0, 'rgba(255,255,255,0)');
        sweepGrad.addColorStop(0.5, 'rgba(255,255,255,0.65)');
        sweepGrad.addColorStop(1, 'rgba(255,255,255,0)');
        ctx.fillStyle = sweepGrad;
        ctx.fillRect(-boxW*0.15, -boxH, boxW*0.3, boxH*2);
        ctx.restore();
      }
    }

    // 4) .name-sparkle — twinkling stars flanking the name box
    {
      ctx.save();
      ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
      ctx.font = "20px 'Space Grotesk', sans-serif";
      const leftTw = 0.5 + 0.5*Math.sin(t * (2*Math.PI/2.2) - Math.PI/2);
      const rightTw = 0.5 + 0.5*Math.sin((t-1.4) * (2*Math.PI/2.2) - Math.PI/2);
      ctx.fillStyle = NAME_B;
      ctx.globalAlpha = leftTw;
      ctx.fillText('✦', boxX - 16, boxY + 14);
      ctx.globalAlpha = rightTw;
      ctx.fillText('✦', boxX + boxW + 16, boxY + boxH - 14);
      ctx.restore();
    }

    // 5) .name-underline — glowing gold bar beneath the name
    {
      const ulW = boxW * 0.62, ulX = W/2 - ulW/2, ulY = boxY + boxH + 10;
      const ulGlow = 0.55 + 0.35*Math.sin(t*2.4);
      ctx.save();
      ctx.shadowColor = `rgba(212,175,55,${ulGlow.toFixed(3)})`;
      ctx.shadowBlur = 10;
      const ulGrad = ctx.createLinearGradient(ulX, 0, ulX+ulW, 0);
      ulGrad.addColorStop(0, 'rgba(212,175,55,0)');
      ulGrad.addColorStop(0.5, NAME_A);
      ulGrad.addColorStop(1, 'rgba(212,175,55,0)');
      ctx.fillStyle = ulGrad;
      ctx.beginPath();
      ctx.roundRect(ulX, ulY, ulW, 2.4, 1.2);
      ctx.fill();
      ctx.restore();
    }

    const fasalText = student.fasal || student.class_name || '';
    const lineY = boxY + boxH + 32;
    ctx.strokeStyle = 'rgba(244,241,234,0.14)';
    ctx.lineWidth = 2;
    ctx.beginPath(); ctx.moveTo(40,lineY); ctx.lineTo(W-40,lineY); ctx.stroke();

    // Section 2 & 3: Exactly 4 cards total (Class/Level, Average/Score, Academic Year, Exam Type)
    const avgVal = student.average !== undefined ? `${Number(student.average).toFixed(1)}%` : (student.avg || '');
    const academicYearVal = student.academic_year || student.sanadDugsiyeedka || '2025 - 2026';
    const examTypeVal = student.exam_type || student.examType || 'Exam-ka Guud (Final)';

    // Dark Navy Dominant Color Palette (Section 4)
    const cards = [
      { value: fasalText, label: 'CLASS / LEVEL', icon: 'class', a: '#071526', mid: '#0F2643', b: '#173B66' },
      { value: avgVal, label: 'CELCELIS GUUD', icon: 'score', a: '#061324', mid: '#0E223E', b: '#15345B' },
      { value: academicYearVal, label: 'SANAD DUGSIYEEDKA', icon: 'year', a: '#07162A', mid: '#102747', b: '#193B6E' },
      { value: examTypeVal, label: 'EXAM TYPE', icon: 'exam', a: '#071428', mid: '#0E2240', b: '#173663' }
    ];

    const cardGap = 18;
    const cardW = (W - 80 - cardGap) / 2;
    const cardH = 88;
    const cardsTop = lineY + 28;

    cards.forEach((item, idx) => {
      const col = idx % 2;
      const row = Math.floor(idx / 2);
      const x = 40 + col * (cardW + cardGap);
      const y = cardsTop + row * (cardH + 16);

      // Tile radial back-glow
      {
        const tileGlow = 0.28 + 0.22 * (0.5 + 0.5*Math.sin((t + idx*0.6) * (2*Math.PI/2.6)));
        ctx.save();
        const tileGlowGrad = ctx.createRadialGradient(
          x + cardW/2, y + cardH/2, 4,
          x + cardW/2, y + cardH/2, cardW * 0.65
        );
        tileGlowGrad.addColorStop(0, hexToRgba(item.b, tileGlow.toFixed(3)));
        tileGlowGrad.addColorStop(1, hexToRgba(item.b, 0));
        ctx.fillStyle = tileGlowGrad;
        ctx.fillRect(x - 20, y - 20, cardW + 40, cardH + 40);
        ctx.restore();
      }

      // Gradient card background
      const grad = ctx.createLinearGradient(x, y, x + cardW, y + cardH);
      grad.addColorStop(0, item.a);
      grad.addColorStop(0.55, item.mid);
      grad.addColorStop(1, item.b);

      ctx.save();
      ctx.beginPath();
      ctx.roundRect(x, y, cardW, cardH, 18);
      ctx.fillStyle = grad;
      ctx.fill();
      ctx.strokeStyle = 'rgba(212,175,55,0.4)';
      ctx.lineWidth = 1.5;
      ctx.stroke();

      // Soft highlight
      const shine = ctx.createLinearGradient(x, y, x + cardW, y + cardH);
      shine.addColorStop(0, 'rgba(255,255,255,0.16)');
      shine.addColorStop(.42, 'rgba(255,255,255,0.03)');
      shine.addColorStop(1, 'rgba(255,255,255,0)');
      ctx.fillStyle = shine;
      ctx.fill();

      // Corner glow
      const corner = ctx.createRadialGradient(
        x + cardW - 10, y + 8, 2,
        x + cardW - 10, y + 8, cardW * 0.5
      );
      corner.addColorStop(0, 'rgba(255,255,255,0.14)');
      corner.addColorStop(1, 'rgba(255,255,255,0)');
      ctx.fillStyle = corner;
      ctx.fill();

      // Icon badge
      const ix = x + 34, iy = y + cardH / 2;
      ctx.beginPath();
      ctx.arc(ix, iy, 21, 0, Math.PI * 2);
      ctx.fillStyle = 'rgba(255,255,255,0.16)';
      ctx.fill();
      ctx.strokeStyle = 'rgba(255,255,255,0.24)';
      ctx.lineWidth = 1.5;
      ctx.stroke();

      drawCardVectorIcon(ctx, item.icon, ix, iy, 21, '#FFFFFF');

      // Label
      ctx.textAlign = 'left';
      ctx.textBaseline = 'alphabetic';
      ctx.fillStyle = '#FFFFFF';
      ctx.font = "700 12px 'Space Grotesk', sans-serif";
      ctx.fillText(item.label, x + 68, y + 29);

      // Value
      ctx.font = "700 20px 'Space Grotesk', sans-serif";
      let value = String(item.value ?? '');
      const maxWidth = cardW - 84;
      while (ctx.measureText(value).width > maxWidth && value.length > 3) {
        value = value.slice(0, -4) + '…';
      }
      const valueX = x + 68, valueY = y + 58;
      const valueWidth = Math.max(ctx.measureText(value).width, 30);

      ctx.save();
      ctx.shadowColor = item.b;
      ctx.shadowBlur = 8 + 5*Math.sin((t+idx*0.6)*2.4);
      ctx.fillStyle = '#FFFFFF';
      ctx.fillText(value, valueX, valueY);
      ctx.restore();

      // Value glow underline
      {
        const ulW = Math.min(valueWidth, cardW - 100);
        const ulX = valueX, ulY = valueY + 8;
        const ulGlow = 0.5 + 0.35*Math.sin((t+idx*0.6)*2.4);
        ctx.save();
        ctx.shadowColor = item.b;
        ctx.shadowBlur = 8;
        ctx.globalAlpha = ulGlow;
        const ulGrad = ctx.createLinearGradient(ulX, 0, ulX+ulW, 0);
        ulGrad.addColorStop(0, item.b);
        ulGrad.addColorStop(1, 'rgba(255,255,255,0)');
        ctx.fillStyle = ulGrad;
        ctx.beginPath();
        ctx.roundRect(ulX, ulY, ulW, 2, 1);
        ctx.fill();
        ctx.restore();
      }

      // Diagonal shine sweep crossing the whole tile
      {
        const cyclePos = ((t + idx*0.7) / 2.8) % 1;
        let sweepOpacity = 0;
        if (cyclePos < 0.08) sweepOpacity = cyclePos/0.08;
        else if (cyclePos < 0.55) sweepOpacity = 1;
        else if (cyclePos < 0.65) sweepOpacity = 1 - (cyclePos-0.55)/0.10;
        if (sweepOpacity > 0.01){
          const sweepX = x - cardW*0.45 + cyclePos * (cardW*1.75);
          ctx.save();
          ctx.beginPath();
          ctx.roundRect(x, y, cardW, cardH, 18);
          ctx.clip();
          ctx.globalCompositeOperation = 'overlay';
          ctx.globalAlpha = sweepOpacity;
          ctx.translate(sweepX, y + cardH/2);
          ctx.rotate(-20 * Math.PI/180);
          const tileSweepGrad = ctx.createLinearGradient(-cardW*0.12, 0, cardW*0.12, 0);
          tileSweepGrad.addColorStop(0, 'rgba(255,255,255,0)');
          tileSweepGrad.addColorStop(0.5, 'rgba(255,255,255,0.55)');
          tileSweepGrad.addColorStop(1, 'rgba(255,255,255,0)');
          ctx.fillStyle = tileSweepGrad;
          ctx.fillRect(-cardW*0.12, -cardH, cardW*0.24, cardH*2);
          ctx.restore();
        }
      }

      ctx.restore();
    });
  }

  function createCardMaterial(texture) {
    try {
      const material = new THREE.ShaderMaterial({
        transparent: true, depthWrite: false, side: THREE.DoubleSide, fog: false,
        uniforms: { map: { value: texture }, uOpacity: { value: 0 }, uComb: { value: 0 } },
        vertexShader: 'varying vec2 vUv; void main(){ vUv=uv; gl_Position=projectionMatrix*modelViewMatrix*vec4(position,1.0); }',
        fragmentShader: 'uniform sampler2D map; uniform float uOpacity; uniform float uComb; varying vec2 vUv; void main(){ vec4 c=texture2D(map,vUv); float bands=sin((vUv.y+uComb*.26)*86.0)*.5+.5; float cut=uComb<.001?0.0:smoothstep(-.10,.10,uComb*1.35-vUv.x+(bands-.5)*.12); c.a*=uOpacity*(1.0-cut); gl_FragColor=c; }',
      });
      return { material, usesComb: true };
    } catch (error) {
      combAvailable = false;
      return { material: new THREE.MeshBasicMaterial({ map: texture, transparent: true, opacity: 0, side: THREE.DoubleSide, depthWrite: false, fog: false }), usesComb: false };
    }
  }

  function activateCombFallback() {
    if (!combAvailable) return;
    combAvailable = false;
    studentCards.forEach((card) => {
      if (!card.usesComb) return;
      const replacement = new THREE.MeshBasicMaterial({ map: card.texture, transparent: true, opacity: card.material.uniforms.uOpacity.value, side: THREE.DoubleSide, depthWrite: false, fog: true });
      card.material.dispose(); card.material = replacement; card.mesh.material = replacement; card.usesComb = false;
    });
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
      const cardMaterial = createCardMaterial(texture);
      const mesh = new THREE.Mesh(new THREE.PlaneGeometry(CARD_W, CARD_W * CARD_CANVAS_H / CARD_CANVAS_W), cardMaterial.material); mesh.renderOrder = 2; scene.add(mesh);
      const card = { mesh, material: cardMaterial.material, texture, context, photo: null, d: CARD_D_START - index * gap, index, exiting: false, exitProgress: 0, usesComb: cardMaterial.usesComb };
      if (student.photo) {
        const image = new Image(); image.crossOrigin = 'anonymous';
        image.onload = () => { card.photo = image; drawStudentCard(context, student, image); texture.needsUpdate = true; };
        image.onerror = () => {
          // Photo failed (CORS, 404, network) — render placeholder card immediately
          card.photo = null;
          drawStudentCard(context, student, null);
          texture.needsUpdate = true;
        };
        image.src = student.photo;
      }
      return card;
    });
  }

  function updateStudentCards(dt) {
    const loop = CARD_D_START - CARD_D_END;
    let nearest = -1, nearestDepth = Infinity;
    studentCards.forEach((card) => {
      if (!card.exiting) {
        card.d -= dt * cardSpeed;
        if (card.d <= CARD_D_END) { card.d = CARD_D_END; card.exiting = true; card.exitProgress = 0; }
      } else {
        card.exitProgress += dt / .62;
        if (card.exitProgress >= 1) { card.d = CARD_D_START; card.exiting = false; card.exitProgress = 0; }
      }
      card.mesh.position.set(0, 0, camera.position.z - card.d);

      // Approach opacity: smooth fade in from distant tunnel (d = 20) -> 1.0 full opacity at front screen (d = 2.65)
      const approach = Math.max(0, Math.min(1, (CARD_D_START - card.d) / loop));
      let opacity = 0.08 + approach * 0.92;
      if (card.exiting) {
        opacity = Math.max(0, 1 - card.exitProgress);
      }

      if (card.usesComb) card.material.uniforms.uComb.value = card.exiting ? Math.min(1, card.exitProgress) : 0;
      if (card.usesComb) card.material.uniforms.uOpacity.value = opacity; else card.material.opacity = opacity;

      let scale = .14 + approach * .86;
      if (card.exiting && !card.usesComb) scale *= (1 - card.exitProgress * .3);
      card.mesh.scale.setScalar(scale);

      if (!card.exiting && card.d > 0 && card.d < nearestDepth) { nearestDepth = card.d; nearest = card.index; }
    });

    if (nearest >= 0) {
      [...dotsWrap.children].forEach((dot, index) => dot.classList.toggle('is-current', index === nearest));
      const frontCard = studentCards[nearest];
      if (frontCard?.context) { nameAnimClock += dt; drawStudentCard(frontCard.context, students[nearest], frontCard.photo, nameAnimClock); frontCard.texture.needsUpdate = true; }
    }
  }

  function buildTunnel() {
    scene = new THREE.Scene(); scene.background = new THREE.Color(0x000000); scene.fog = new THREE.Fog(0x000000, Math.min(FOG_FAR * .35, FOG_FAR - .01), FOG_FAR);
    camera = new THREE.PerspectiveCamera(45, 1, 1, 1000); camera.position.set(0, 0, 0);
    renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: false, powerPreference: 'high-performance' }); renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));

    if (renderer.debug) renderer.debug.onShaderError = () => activateCombFallback();
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
    try { renderer.compile(scene, camera); } catch (error) { activateCombFallback(); }
    // Synchronous WebGL link-status guard: Three.js r140+ fires onShaderError
    // asynchronously. On mobile/embedded GPUs shaders can fail silently, leaving
    // all cards invisible. We verify every compiled program NOW before first frame.
    if (combAvailable) {
      try {
        const gl = renderer.getContext();
        const glPrograms = renderer.info?.programs;
        if (gl && glPrograms) {
          for (const prog of glPrograms) {
            if (prog.program && !gl.getProgramParameter(prog.program, gl.LINK_STATUS)) {
              activateCombFallback(); break;
            }
          }
        }
      } catch (_) { /* renderer.info unavailable — rely on onShaderError */ }
    }
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
    cancelAnimationFrame(raf); running = false; paused = false; autoPausedByVisibility = false;
    if (!renderer) return;
    scene.traverse((object) => { if (object.geometry) object.geometry.dispose?.(); if (object.material) { const materials = Array.isArray(object.material) ? object.material : [object.material]; materials.forEach((material) => { material.map?.dispose?.(); material.dispose?.(); }); } });
    renderer.dispose(); renderer = scene = camera = null; segments = []; fading = []; studentCards = []; dotsWrap.replaceChildren();
  }

  function startTunnel() {
    destroyTunnel(); scrollPos = 0; last = 0; imageIndex = 0; colorIndex = 0; paused = false; autoPausedByVisibility = false;
    students.forEach((_, index) => { const dot = document.createElement('span'); dot.classList.toggle('is-current', index === 0); dotsWrap.appendChild(dot); });
    buildTunnel(); running = true; raf = requestAnimationFrame(animate); setOverlayReady(); startMusic(); syncPauseUI();
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
  function closeTunnel() { destroyTunnel(); overlay.classList.remove('is-open', 'is-ready'); overlay.setAttribute('aria-hidden', 'true'); stopMusic(); syncPauseUI(); }

  launch.addEventListener('click', openTunnel);
  closeButton.addEventListener('click', closeTunnel);
  muteButton.addEventListener('click', toggleMute);
  if (pauseButton) {
    pauseButton.addEventListener('click', () => (paused ? resumeTunnel() : pauseTunnel()));
  }

  document.addEventListener('keydown', (event) => {
    if (!overlay.classList.contains('is-open')) return;
    if (event.key === 'Escape') closeTunnel();
    if (event.key === ' ') {
      event.preventDefault();
      paused ? resumeTunnel() : pauseTunnel();
    }
  });

  document.addEventListener('visibilitychange', () => {
    if (!overlay.classList.contains('is-open')) return;
    if (document.hidden) {
      if (!paused) pauseTunnel('visibility');
    } else if (autoPausedByVisibility) {
      resumeTunnel();
    }
  });

  canvas.addEventListener('pointerdown', () => { pressed = true; });
  window.addEventListener('pointerup', () => { pressed = false; });
  canvas.addEventListener('pointerleave', () => { pressed = false; });
  window.addEventListener('pagehide', closeTunnel);
})();
