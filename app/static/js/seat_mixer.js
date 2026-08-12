/**
 * Seat Mixer v2 — Main JavaScript
 *
 * Single-page app with 3 screens:
 *   Screen 1: Exam Halls list + create form
 *   Screen 2: Versions list per hall
 *   Screen 3: Builder (per Hall + Version)
 *
 * Uses real data from the database via API calls.
 * Algorithms: Quick Generate, Strict Optimizer (Web Worker), Scatter Students.
 */

(function () {
  'use strict';

  var PALETTE = [
    '#60A5FA', '#F472B6', '#34D399', '#FBBF24', '#A78BFA', '#22D3EE',
    '#FB923C', '#818CF8', '#F87171', '#4ADE80', '#FB7185', '#38BDF8',
  ];
  var CLASS_COLOR_PRESETS = PALETTE.concat(['#2563EB', '#7C3AED', '#0F766E', '#BE123C']);

  // ── Global state ──
  var SM = {
    halls: [],
    levels: [],
    classPalette: PALETTE,
    csrfToken: '',
    schoolName: 'School',

    // Current navigation state
    currentHallId: null,
    currentVersionId: null,

    // Builder state (per hall+version combo)
    store: {}, // key: "hallId::versionId" -> combo object
    studentDirectory: {}, // classId -> [student objects]
    studentHallMap: {}, // studentId -> {hallId, versionId, hallName, versionLabel}
    pendingReplace: null,
    worker: null,
    appearance: {},
    appearanceTimer: null,
  };

  // ── Utility functions ──
  function $(id) { return document.getElementById(id); }

  function escapeHtml(str) {
    if (!str) return '';
    return String(str)
      .replace(/&/g, '&' + 'amp;')
      .replace(/</g, '&' + 'lt;')
      .replace(/>/g, '&' + 'gt;')
      .replace(/"/g, '&' + 'quot;');
  }

  function getInitials(name) {
    if (!name) return '?';
    var parts = name.trim().split(/\s+/);
    if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase();
    return name.slice(0, 2).toUpperCase();
  }

  function photoHtml(student, sizeClass) {
    var fallback = '<div class="sm-avatar-fallback ' + sizeClass + '">' + getInitials(student.full_name || student.name || '?') + '</div>';
    if (student.photo_path) {
      return '<img class="' + sizeClass + '" src="' + escapeHtml(student.photo_path) + '" alt="' + escapeHtml(student.full_name || '') + '" onerror="this.style.display=\'none\';this.nextElementSibling.style.display=\'flex\'">' +
        '<div class="sm-avatar-fallback ' + sizeClass + '" style="display:none;">' + getInitials(student.full_name || student.name || '?') + '</div>';
    }
    return fallback;
  }

  function shuffleArr(arr) {
    for (var i = arr.length - 1; i > 0; i--) {
      var j = Math.floor(Math.random() * (i + 1));
      var tmp = arr[i]; arr[i] = arr[j]; arr[j] = tmp;
    }
    return arr;
  }

  function fmtRange(start, end) {
    if (!start && !end) return 'No time window set';
    var s = start ? new Date(start) : null;
    var e = end ? new Date(end) : null;
    var opts = { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' };
    var sStr = s ? s.toLocaleString('en-GB', opts) : '—';
    var eStr = e ? e.toLocaleString('en-GB', opts) : '—';
    return sStr + ' → ' + eStr;
  }

  function isExpired(hall) {
    if (!hall.end_time && !hall.end) return false;
    var end = hall.end_time || hall.end;
    return new Date(end).getTime() < Date.now();
  }

  function comboKey(hallId, versionId) { return hallId + '::' + versionId; }

  function currentCombo() { return SM.store[comboKey(SM.currentHallId, SM.currentVersionId)]; }

  function currentHall() {
    return SM.halls.find(function (h) { return h.id === SM.currentHallId; });
  }

  function notify(message, type) {
    var region = $('smToastRegion');
    if (!region) return;
    type = type || 'success';
    var icon = type === 'error' ? 'fa-circle-exclamation' : (type === 'info' ? 'fa-circle-info' : 'fa-circle-check');
    var item = document.createElement('div');
    item.className = 'sm-toast ' + type;
    item.innerHTML = '<i class="fa-solid ' + icon + '"></i><span>' + escapeHtml(message) + '</span>';
    region.appendChild(item);
    window.setTimeout(function () { item.remove(); }, 4200);
  }

  function closeActionMenus() {
    document.querySelectorAll('.sm-action-menu').forEach(function (menu) { menu.remove(); });
  }

  function toggleActionMenu(trigger, kind, id) {
    var existing = document.querySelector('.sm-action-menu');
    if (existing) { existing.remove(); return; }
    var menu = document.createElement('div');
    menu.className = 'sm-action-menu';
    menu.dataset.kind = kind;
    menu.dataset.id = id;
    menu.innerHTML = kind === 'hall'
      ? '<button data-action="rename"><i class="fa-solid fa-pen"></i> Rename hall</button>' +
        '<button data-action="edit"><i class="fa-solid fa-sliders"></i> Edit hall</button>' +
        '<button data-action="start"><i class="fa-solid fa-calendar-plus"></i> Change start date</button>' +
        '<button data-action="end"><i class="fa-solid fa-calendar-check"></i> Change end date</button>' +
        '<button class="danger" data-action="delete"><i class="fa-solid fa-trash"></i> Delete hall</button>'
      : '<button data-action="rename"><i class="fa-solid fa-pen"></i> Rename version</button>' +
        '<button data-action="duplicate"><i class="fa-solid fa-copy"></i> Duplicate layout</button>' +
        '<button data-action="restore"><i class="fa-solid fa-rotate-left"></i> Restore / open layout</button>' +
        '<button class="danger" data-action="delete"><i class="fa-solid fa-trash"></i> Delete version</button>';
    document.body.appendChild(menu);
    var r = trigger.getBoundingClientRect();
    menu.style.top = (r.bottom + window.scrollY + 5) + 'px';
    menu.style.left = Math.max(8, r.right + window.scrollX - 190) + 'px';
  }

  function closeManage() {
    $('smManageOverlay').classList.add('hidden');
    $('smManageConfirm').onclick = null;
  }

  function openManage(opts) {
    $('smManageTitle').textContent = opts.title;
    $('smManageCopy').textContent = opts.copy || '';
    $('smManageIcon').className = 'sm-manage-icon' + (opts.danger ? ' danger' : '');
    $('smManageIcon').innerHTML = '<i class="fa-solid ' + (opts.icon || 'fa-gear') + '"></i>';
    $('smManageFields').innerHTML = opts.fields || '';
    $('smManageConfirm').className = 'sm-btn ' + (opts.danger ? 'strict' : 'save');
    $('smManageConfirm').textContent = opts.confirmText || 'Save changes';
    $('smManageConfirm').onclick = function () { opts.onConfirm(); };
    $('smManageOverlay').classList.remove('hidden');
    var first = $('smManageFields').querySelector('input');
    if (first) window.setTimeout(function () { first.focus(); }, 20);
  }

  // ── Build seats from config ──
  function buildSeats(cfg) {
    var seats = [];
    for (var r = 0; r < cfg.rows; r++) {
      for (var t = 0; t < cfg.tablesPerRow; t++) {
        var tableId = 'r' + r + 't' + t;
        var neighborIds = [];
        if (t > 0) neighborIds.push('r' + r + 't' + (t - 1));
        if (t < cfg.tablesPerRow - 1) neighborIds.push('r' + r + 't' + (t + 1));
        if (r > 0) neighborIds.push('r' + (r - 1) + 't' + t);
        if (r < cfg.rows - 1) neighborIds.push('r' + (r + 1) + 't' + t);
        for (var s = 0; s < cfg.seatsPerTable; s++) {
          seats.push({
            id: tableId + 's' + s, row: r, tableIdx: t, seatIdx: s,
            tableId: tableId, neighborIds: neighborIds, assigned: null
          });
        }
      }
    }
    return seats;
  }

  // ── Get or create combo (state for one hall+version) ──
  function getOrCreateCombo(hallId, versionId) {
    var key = comboKey(hallId, versionId);
    if (!SM.store[key]) {
      var cfg = { rows: 3, tablesPerRow: 5, seatsPerTable: 2 };
      SM.store[key] = {
        hallId: hallId, versionId: versionId,
        activeLevels: new Set(),
        selectedClasses: {}, // classId -> {color, uids: Set, collapsed: bool}
        classColorOverrides: {}, // classId -> #RRGGBB, persisted per hall version
        cfg: cfg,
        seats: buildSeats(cfg),
        savedAssignment: null,
        currentSnapshotId: null,
        previewSnapshotId: null,
        dirty: false,
        lastMeta: null,
        loaded: false,
      };
    }
    return SM.store[key];
  }

  // ── Compute metrics ──
  function computeMetrics(seats) {
    var hard = 0, adjacent = 0, near = 0, sameRow = 0, pairs = 0, distanceSum = 0, cost = 0;
    for (var i = 0; i < seats.length; i++) {
      for (var j = i + 1; j < seats.length; j++) {
        var a = seats[i], b = seats[j];
        if (!a.assigned || !b.assigned || a.assigned.classId !== b.assigned.classId) continue;
        var d = Math.abs(a.row - b.row) + Math.abs(a.tableIdx - b.tableIdx) +
          (a.row === b.row && a.tableIdx === b.tableIdx ? Math.abs(a.seatIdx - b.seatIdx) * 0.15 : 0);
        pairs++; distanceSum += d;
        if (d < 0.5) { hard++; cost += 100000; }
        else if (d <= 1.05) { adjacent++; cost += 8500; }
        else if (d <= 2.05) { near++; cost += 1700; }
        else if (d <= 3.05) { cost += 320; if (a.row === b.row) sameRow++; }
        else cost += Math.max(0, 55 - d * 5);
      }
    }
    return {
      hardCount: hard, softSum: adjacent, nearCount: near, sameRowCount: sameRow,
      avgSameClassDistance: pairs ? +(distanceSum / pairs).toFixed(2) : 0,
      integrityScore: Math.max(0, Math.round(100 - hard * 25 - adjacent * 3 - near * 0.25 - sameRow * 0.1)),
      cost: cost
    };
  }

  // ── Get selected students by class ──
  function getSelectedStudentsByClass(combo) {
    var map = {};
    Object.keys(combo.selectedClasses).forEach(function (cid) {
      var roster = SM.studentDirectory[cid] || [];
      var uids = combo.selectedClasses[cid].uids;
      map[cid] = Array.from(uids).map(function (uid) {
        return roster.find(function (s) { return s.id === uid; });
      }).filter(Boolean);
    });
    return map;
  }

  // ── Classes used in the mix ──
  function classesUsed(combo) {
    return Object.keys(combo.selectedClasses).map(function (cid) {
      var level = SM.levels.find(function (l) {
        return l.classes.some(function (c) { return c.id === parseInt(cid); });
      });
      var cls = level ? level.classes.find(function (c) { return c.id === parseInt(cid); }) : null;
      var sel = combo.selectedClasses[cid];
      var seated = combo.seats.find(function (seat) { return seat.assigned && String(seat.assigned.classId) === String(cid); });
      return {
        id: parseInt(cid), name: (seated && seated.assigned.class_name) || (cls ? cls.name : ''),
        color: sel.color, count: sel.uids.size,
        levelId: level ? level.id : null, levelName: level ? level.name : ''
      };
    });
  }

  function classVisual(combo, classId) {
    var level = SM.levels.find(function (l) {
      return l.classes.some(function (c) { return c.id === classId; });
    });
    var cls = level ? level.classes.find(function (c) { return c.id === classId; }) : null;
    return { name: cls ? cls.name : '', color: classColorFor(combo, classId), level: level ? level.name : '' };
  }

  function normalizeClassColor(color) {
    return typeof color === 'string' && /^#[0-9a-f]{6}$/i.test(color) ? color.toUpperCase() : null;
  }

  function defaultClassColor(classId) {
    var palette = SM.classPalette && SM.classPalette.length ? SM.classPalette : PALETTE;
    return palette[parseInt(classId, 10) % palette.length];
  }

  function classColorFor(combo, classId) {
    var cid = String(classId);
    var selected = combo.selectedClasses[cid];
    var override = normalizeClassColor(combo.classColorOverrides && combo.classColorOverrides[cid]);
    return (selected && normalizeClassColor(selected.color)) || override || defaultClassColor(cid);
  }

  function closeClassColorPicker() {
    var picker = document.getElementById('smClassColorPicker');
    if (picker) picker.classList.remove('show');
    SM.classColorPicker = null;
  }

  function selectedClassColorConflict(combo, classId, color) {
    return Object.keys(combo.selectedClasses).some(function (otherId) {
      return otherId !== String(classId) && classColorFor(combo, otherId).toUpperCase() === color;
    });
  }

  function persistClassColor(combo, classId, color, previousOverride, previousSelectedColor) {
    var selectedIds = Object.keys(combo.selectedClasses).map(function (id) { return parseInt(id, 10); });
    fetch('/admin/seat-mixer/api/version/' + SM.currentVersionId + '/class-colors', {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': SM.csrfToken },
      body: JSON.stringify({ class_id: parseInt(classId, 10), color: color, selected_class_ids: selectedIds })
    })
      .then(function (response) { return response.json().then(function (data) { return { ok: response.ok, data: data }; }); })
      .then(function (result) {
        if (result.ok && result.data.success) return;
        throw new Error(result.data.error || 'Could not save this class color');
      })
      .catch(function (error) {
        if (previousOverride) combo.classColorOverrides[String(classId)] = previousOverride;
        else delete combo.classColorOverrides[String(classId)];
        if (combo.selectedClasses[String(classId)]) combo.selectedClasses[String(classId)].color = previousSelectedColor;
        renderClassesArea(combo);
        renderHallFromSeats(combo, combo.lastMeta);
        notify(error.message, 'error');
      });
  }

  function setClassColor(combo, classId, color) {
    color = normalizeClassColor(color);
    if (!color) return;
    if (selectedClassColorConflict(combo, classId, color)) {
      notify('Choose a different color: another selected class already uses this one.', 'info');
      return;
    }
    var cid = String(classId);
    var previousOverride = combo.classColorOverrides[cid] || null;
    var previousSelectedColor = combo.selectedClasses[cid] ? combo.selectedClasses[cid].color : null;
    combo.classColorOverrides[cid] = color;
    if (combo.selectedClasses[cid]) combo.selectedClasses[cid].color = color;
    renderClassesArea(combo);
    renderHallFromSeats(combo, combo.lastMeta);
    closeClassColorPicker();
    persistClassColor(combo, cid, color, previousOverride, previousSelectedColor);
  }

  function openClassColorPicker(dot, combo, classId) {
    var picker = document.getElementById('smClassColorPicker');
    if (!picker) {
      picker = document.createElement('div');
      picker.id = 'smClassColorPicker';
      picker.className = 'sm-class-color-popover';
      picker.setAttribute('role', 'dialog');
      picker.setAttribute('aria-label', 'Choose class color');
      document.body.appendChild(picker);
    }
    var color = classColorFor(combo, classId);
    picker.innerHTML = '<div class="sm-color-picker-title">Class color</div><div class="sm-color-picker-swatches">' +
      CLASS_COLOR_PRESETS.map(function (item) {
        return '<button type="button" class="sm-color-choice' + (item.toUpperCase() === color ? ' active' : '') + '" data-sm-color-choice="' + item + '" style="--choice-color:' + item + ';" aria-label="Use ' + item + '"></button>';
      }).join('') +
      '</div><label class="sm-color-custom"><span>Custom</span><input type="color" data-sm-color-custom value="' + color + '" aria-label="Custom class color"></label>';
    var bounds = dot.getBoundingClientRect();
    var top = Math.min(window.innerHeight - 154, bounds.bottom + 8);
    var left = Math.min(window.innerWidth - 238, Math.max(10, bounds.left));
    picker.style.top = Math.max(10, top) + 'px';
    picker.style.left = left + 'px';
    picker.classList.add('show');
    SM.classColorPicker = { classId: String(classId) };
  }

  function tableDistance(a, b) {
    return Math.abs(a.row - b.row) + Math.abs(a.tableIdx - b.tableIdx);
  }

  function integritySeatOrder(seats) {
    // Fill a broad cross-section of the hall before nearby seats. This lets
    // empty capacity create real distance instead of a front-row cluster.
    var remaining = seats.slice();
    var ordered = [];
    var centerRow = (Math.max.apply(null, seats.map(function (s) { return s.row; })) || 0) / 2;
    var centerTable = (Math.max.apply(null, seats.map(function (s) { return s.tableIdx; })) || 0) / 2;
    remaining.sort(function (a, b) {
      return (Math.abs(b.row - centerRow) + Math.abs(b.tableIdx - centerTable)) -
        (Math.abs(a.row - centerRow) + Math.abs(a.tableIdx - centerTable));
    });
    while (remaining.length) {
      ordered.push(remaining.shift());
      if (!remaining.length) break;
      var last = ordered[ordered.length - 1];
      remaining.sort(function (a, b) { return tableDistance(b, last) - tableDistance(a, last); });
    }
    return ordered;
  }

  function classPenaltyForSeat(seat, classId, placed) {
    var penalty = 0;
    placed.forEach(function (other) {
      if (!other.assigned || other.assigned.classId !== parseInt(classId)) return;
      var distance = tableDistance(seat, other);
      if (distance === 0) penalty += 100000;
      else if (distance === 1) penalty += 6000;
      else if (distance === 2) penalty += 900;
      else if (distance === 3) penalty += 150;
      else penalty += Math.max(0, 25 - distance);
    });
    return penalty;
  }

  // ── Fill seats in a given order ──
  function fillSeatsInOrder(combo, seatOrder) {
    seatOrder.forEach(function (s) { s.assigned = null; });
    var byClass = getSelectedStudentsByClass(combo);
    var pools = {};
    Object.keys(byClass).forEach(function (cid) { pools[cid] = byClass[cid].slice(); });
    var placed = [];

    seatOrder.forEach(function (seat) {
      var candidateClassIds = Object.keys(pools).filter(function (cid) { return pools[cid].length > 0; });
      if (candidateClassIds.length === 0) return;

      shuffleArr(candidateClassIds);
      candidateClassIds.sort(function (a, b) { return pools[b].length - pools[a].length; });

      var chosen = candidateClassIds[0];
      var lowest = Infinity;
      candidateClassIds.forEach(function (cid) {
        // Priority order: class separation dominates, then the largest remaining
        // pool breaks ties so no class is left clustered at the end.
        var score = classPenaltyForSeat(seat, cid, placed) - Math.min(pools[cid].length, 30) * 0.02;
        if (score < lowest) { lowest = score; chosen = cid; }
      });

      var student = pools[chosen].shift();
      seat.assigned = student;
      placed.push(seat);
    });
  }

  function greedyFill(combo) { fillSeatsInOrder(combo, integritySeatOrder(combo.seats)); }

  // ── Scatter: round-robin across rows ──
  function orderSeatsScattered(seats) {
    var byRow = {};
    seats.forEach(function (s) { (byRow[s.row] = byRow[s.row] || []).push(s); });
    var rowKeys = Object.keys(byRow).map(Number).sort(function (a, b) { return a - b; });
    rowKeys.forEach(function (r) {
      byRow[r].sort(function (a, b) { return a.tableIdx - b.tableIdx || a.seatIdx - b.seatIdx; });
    });
    var maxLen = Math.max.apply(null, rowKeys.map(function (r) { return byRow[r].length; }));
    var ordered = [];
    for (var i = 0; i < maxLen; i++) {
      rowKeys.forEach(function (r) { if (byRow[r][i]) ordered.push(byRow[r][i]); });
    }
    return ordered;
  }

  function scatterFill(combo) {
    // ── TRUE SCATTER: Interleave students by class, then place in scattered seats ──
    // This maximizes the distance between same-class students.
    var byClass = getSelectedStudentsByClass(combo);
    var classIds = Object.keys(byClass);

    // Sort classes by size (largest first) for better interleaving
    classIds.sort(function (a, b) { return byClass[b].length - byClass[a].length; });

    // Interleave students: round-robin by class so same-class students
    // are separated by students from other classes in the placement order
    var interleaved = [];
    var pools = {};
    classIds.forEach(function (cid) {
      pools[cid] = byClass[cid].slice();
      // Shuffle each pool for randomness
      shuffleArr(pools[cid]);
    });

    var hasMore = true;
    while (hasMore) {
      hasMore = false;
      classIds.forEach(function (cid) {
        if (pools[cid].length > 0) {
          interleaved.push(pools[cid].shift());
          hasMore = true;
        }
      });
    }

    // Get scattered seat order (round-robin across rows)
    var seatOrder = orderSeatsScattered(combo.seats);

    // Reset all seats
    seatOrder.forEach(function (s) { s.assigned = null; });

    // Place interleaved students in scattered seats
    // Skip seats that would create a hard violation (same class at same table)
    var tableHas = {};
    var studentIdx = 0;
    var placedCount = 0;

    // First pass: place students avoiding hard violations
    for (var i = 0; i < seatOrder.length && studentIdx < interleaved.length; i++) {
      var seat = seatOrder[i];
      var student = interleaved[studentIdx];

      if (!tableHas[seat.tableId]) tableHas[seat.tableId] = new Set();

      // Check if this student's class is already at this table
      if (!tableHas[seat.tableId].has(student.classId)) {
        seat.assigned = student;
        tableHas[seat.tableId].add(student.classId);
        studentIdx++;
        placedCount++;
      }
    }

    // Second pass: place any remaining students in any available seats
    // (even if it creates a hard violation — better than leaving them unplaced)
    for (; studentIdx < interleaved.length; studentIdx++) {
      var student = interleaved[studentIdx];
      for (var j = 0; j < seatOrder.length; j++) {
        var seat = seatOrder[j];
        if (!seat.assigned) {
          if (!tableHas[seat.tableId]) tableHas[seat.tableId] = new Set();
          seat.assigned = student;
          tableHas[seat.tableId].add(student.classId);
          break;
        }
      }
    }
  }

  // ── Strict optimizer (simulated annealing) ──
  function strictOptimize(seats, iterations) {
    var assignedSeats = seats.filter(function (s) { return s.assigned; });
    if (assignedSeats.length < 2) return;

    var T = 4.0;
    var coolRate = Math.pow(0.01 / 4.0, 1 / iterations);

    for (var it = 0; it < iterations; it++) {
      T *= coolRate;
      var i = assignedSeats[Math.floor(Math.random() * assignedSeats.length)];
      var j = assignedSeats[Math.floor(Math.random() * assignedSeats.length)];
      if (i === j || i.assigned.classId === j.assigned.classId) continue;

      var before = computeMetrics(seats);
      var a = i.assigned, b = j.assigned;
      i.assigned = b; j.assigned = a;
      var after = computeMetrics(seats);
      var delta = after.cost - before.cost;

      if (!(delta <= 0 || Math.random() < Math.exp(-delta / Math.max(T, 0.001)))) {
        i.assigned = a; j.assigned = b;
      }
    }
  }

  // ── Swap seat students ──
  function swapSeatStudents(seatA, seatB, newForA, oldFromA) {
    seatA.assigned = newForA;
    if (seatB) seatB.assigned = oldFromA;
  }

  // ── Find seat of a student ──
  function findSeatOf(combo, studentId) {
    return combo.seats.find(function (s) { return s.assigned && s.assigned.id === studentId; }) || null;
  }

  function placementLabel(combo, seatId) {
    var seat = combo.seats.find(function (s) { return s.id === seatId; });
    if (!seat || !seat.assigned) return 'Not Assigned';
    return 'Row ' + (seat.row + 1) + ' · Table ' + (seat.tableIdx + 1);
  }

  // ============================================================
  // SCREEN 1: Exam Halls
  // ============================================================
  function renderHallsScreen() {
    var wrap = $('smHallList');
    if (SM.halls.length === 0) {
      wrap.innerHTML = '<div class="sm-empty-note">No exam halls created yet. Click "+ Create New Exam Hall" to begin.</div>';
      return;
    }
    wrap.innerHTML = SM.halls.map(function (h) {
      var expired = isExpired(h);
      return '<div class="sm-hall-card" data-hall="' + h.id + '">' +
        '<div><div class="hname">' + escapeHtml(h.name) + '</div>' +
        '<div class="hmeta">' + fmtRange(h.start_time, h.end_time) + ' · ' + h.version_count + ' version' + (h.version_count > 1 ? 's' : '') + '</div></div>' +
        '<span class="sm-badge ' + (expired ? 'b-expired' : 'b-active') + '">' + (expired ? 'Expired' : 'Active') + '</span>' +
        '<div class="sm-card-actions"><button class="sm-menu-btn" data-menu-kind="hall" data-menu-id="' + h.id + '" aria-label="Manage hall"><i class="fa-solid fa-ellipsis"></i></button></div>' +
        '</div>';
    }).join('');
  }

  function showScreen(screenId) {
    document.querySelectorAll('.sm-screen').forEach(function (el) { el.classList.remove('active'); });
    $(screenId).classList.add('active');
  }

  // Keep the current workspace addressable. A refresh or bookmark must reopen
  // the exact hall/version instead of silently sending the administrator home.
  function updateWorkspaceLocation(hallId, versionId, snapshotId) {
    var url = new URL(window.location.href);
    ['hall_id', 'version_id', 'snapshot_id'].forEach(function (key) { url.searchParams.delete(key); });
    if (hallId) url.searchParams.set('hall_id', hallId);
    if (versionId) url.searchParams.set('version_id', versionId);
    if (snapshotId) url.searchParams.set('snapshot_id', snapshotId);
    window.history.replaceState({}, '', url.pathname + (url.search ? url.search : '') + url.hash);
  }

  function scrollToLiveMap() {
    window.setTimeout(function () {
      var map = $('smBuilderMapCard');
      if (map) map.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }, 80);
  }

  function showHallsScreen() {
    SM.currentHallId = null;
    SM.currentVersionId = null;
    updateWorkspaceLocation();
    showScreen('smHallsScreen');
    renderHallsScreen();
  }

  // ── Create hall ──
  function createHall() {
    var name = ($('smNewHallName').value || '').trim() || ('Exam Hall ' + (SM.halls.length + 1));
    var start = $('smNewHallStart').value;
    var end = $('smNewHallEnd').value;

    if (start && end && new Date(end) <= new Date(start)) {
      notify('End time must be after start time.', 'error');
      return;
    }

    fetch('/admin/seat-mixer/api/create-hall', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': SM.csrfToken },
      body: JSON.stringify({ name: name, start: start || null, end: end || null })
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.error) { notify(data.error, 'error'); return; }
        SM.halls.push(data.hall);
        $('smNewHallName').value = '';
        $('smNewHallStart').value = '';
        $('smNewHallEnd').value = '';
        $('smCreateBox').classList.remove('show');
        openVersionsScreen(data.hall.id);
      })
      .catch(function (e) { notify('Could not create hall: ' + e.message, 'error'); });
  }

  // ============================================================
  // SCREEN 2: Versions
  // ============================================================
  function openVersionsScreen(hallId) {
    SM.currentHallId = hallId;
    SM.currentVersionId = null;
    updateWorkspaceLocation(hallId);
    showScreen('smVersionsScreen');

    var hall = SM.halls.find(function (h) { return h.id === hallId; });
    if (!hall) return;

    $('smCrumbHallV').textContent = hall.name;
    var expired = isExpired(hall);
    $('smHallStatusBadge').innerHTML = '<span class="sm-badge ' + (expired ? 'b-expired' : 'b-active') + '">' + (expired ? 'Expired' : 'Active') + '</span>';

    // Fetch versions from API
    fetch('/admin/seat-mixer/api/hall/' + hallId + '/versions')
      .then(function (r) { return r.json(); })
      .then(function (data) {
        SM.currentVersions = data.versions || [];
        renderVersionsList(data.versions, hall);
        (data.versions || []).filter(function (version) { return version.is_latest; }).forEach(function (version) {
          var card = $('smVersionList').querySelector('[data-version="' + version.id + '"]');
          var actions = card && card.querySelector('.sm-card-actions');
          if (actions && !actions.querySelector('.sm-latest-badge')) {
            var badge = document.createElement('span');
            badge.className = 'sm-latest-badge';
            badge.textContent = 'Latest Saved';
            actions.insertBefore(badge, actions.firstChild);
          }
        });
      })
      .catch(function (e) { console.error('Failed to load versions:', e); });
  }

  function renderVersionsList(versions, hall) {
    var wrap = $('smVersionList');
    if (versions.length === 0) {
      wrap.innerHTML = '<div class="sm-empty-note">No versions yet. Click "+ New Version" to create one.</div>';
      return;
    }
    wrap.innerHTML = versions.map(function (v) {
      var modified = v.updated_at ? new Date(v.updated_at).toLocaleDateString() : '';
      return '<div class="sm-version-card" data-version="' + v.id + '">' +
        '<div><div class="vname">' + escapeHtml(v.label) + '</div>' +
        '<div class="vmeta">' + (v.has_saved ? '✅ Saved arrangement' : '○ No saved arrangement') + '</div></div>' +
        '<div class="sm-card-actions"><span class="sm-link-btn" style="text-decoration:none;">Open →</span><button class="sm-menu-btn" data-menu-kind="version" data-menu-id="' + v.id + '" aria-label="Manage version"><i class="fa-solid fa-ellipsis"></i></button></div>' +
        '</div>';
    }).join('');
  }

  function addVersion() {
    fetch('/admin/seat-mixer/api/hall/' + SM.currentHallId + '/add-version', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': SM.csrfToken }
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.error) { notify(data.error, 'error'); return; }
        var hall = SM.halls.find(function (h) { return h.id === SM.currentHallId; });
        if (hall) hall.version_count++;
        openVersionsScreen(SM.currentHallId);
      })
      .catch(function (e) { notify('Could not add version: ' + e.message, 'error'); });
  }

  function requestJson(url, method, body) {
    return fetch(url, {
      method: method,
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': SM.csrfToken },
      body: body ? JSON.stringify(body) : undefined
    }).then(function (response) {
      return response.json().then(function (payload) {
        if (!response.ok || payload.error) throw new Error(payload.error || 'The request could not be completed.');
        return payload;
      });
    });
  }

  function manageHallAction(hallId, action) {
    var hall = SM.halls.find(function (item) { return item.id === hallId; });
    if (!hall) return;
    if (action === 'delete') {
      openManage({
        title: 'Delete ' + hall.name + '?', icon: 'fa-triangle-exclamation', danger: true,
        copy: 'This permanently removes the hall, every saved layout version, and its seat assignments. This cannot be undone.',
        confirmText: 'Delete hall',
        onConfirm: function () {
          requestJson('/admin/seat-mixer/api/hall/' + hallId, 'DELETE', { confirm: true })
            .then(function () { SM.halls = SM.halls.filter(function (item) { return item.id !== hallId; }); closeManage(); showHallsScreen(); notify('Hall deleted successfully.'); })
            .catch(function (err) { notify(err.message, 'error'); });
        }
      });
      return;
    }
    var fields = '';
    if (action === 'rename') fields = '<div class="sm-field"><label>Hall name</label><input id="smManageName" type="text" value="' + escapeHtml(hall.name) + '"></div>';
    else if (action === 'edit') fields = '<div class="sm-manage-fields two"><div class="sm-field"><label>Hall name</label><input id="smManageName" type="text" value="' + escapeHtml(hall.name) + '"></div><div class="sm-field"><label>Start</label><input id="smManageStart" type="datetime-local" value="' + (hall.start_time || '') + '"></div><div class="sm-field"><label>End</label><input id="smManageEnd" type="datetime-local" value="' + (hall.end_time || '') + '"></div></div>';
    else fields = '<div class="sm-field"><label>' + (action === 'start' ? 'Exam start' : 'Exam end') + '</label><input id="smManageDate" type="datetime-local" value="' + ((action === 'start' ? hall.start_time : hall.end_time) || '') + '"></div>';
    openManage({
      title: action === 'rename' ? 'Rename hall' : (action === 'edit' ? 'Edit hall details' : 'Update exam ' + action + ' date'),
      icon: action === 'edit' ? 'fa-sliders' : 'fa-building-columns', copy: 'Changes are saved immediately and applied to every version of this hall.', fields: fields,
      onConfirm: function () {
        var body = {};
        if ($('smManageName')) body.name = $('smManageName').value;
        if ($('smManageStart')) body.start = $('smManageStart').value || null;
        if ($('smManageEnd')) body.end = $('smManageEnd').value || null;
        if ($('smManageDate')) body[action] = $('smManageDate').value || null;
        requestJson('/admin/seat-mixer/api/hall/' + hallId, 'PATCH', body)
          .then(function (data) { Object.assign(hall, data.hall); closeManage(); renderHallsScreen(); notify('Hall details updated.'); })
          .catch(function (err) { notify(err.message, 'error'); });
      }
    });
  }

  function manageVersionAction(versionId, action) {
    var version = (SM.currentVersions || []).find(function (item) { return item.id === versionId; });
    if (action === 'restore') {
      openBuilder(SM.currentHallId, versionId);
      return;
    }
    if (action === 'duplicate') {
      requestJson('/admin/seat-mixer/api/version/' + versionId + '/duplicate', 'POST')
        .then(function (data) { openVersionsScreen(SM.currentHallId); notify('Created ' + data.version.label + '.'); })
        .catch(function (err) { notify(err.message, 'error'); });
      return;
    }
    if (action === 'delete') {
      openManage({
        title: 'Delete this version?', icon: 'fa-triangle-exclamation', danger: true,
        copy: 'The saved layout and every seat assignment in this version will be permanently removed.', confirmText: 'Delete version',
        onConfirm: function () {
          requestJson('/admin/seat-mixer/api/version/' + versionId, 'DELETE', { confirm: true })
            .then(function () { closeManage(); openVersionsScreen(SM.currentHallId); notify('Saved layout version deleted.'); })
            .catch(function (err) { notify(err.message, 'error'); });
        }
      });
      return;
    }
    openManage({
      title: 'Rename layout version', icon: 'fa-layer-group', copy: 'Use a meaningful name so this saved arrangement is easy to restore later.',
      fields: '<div class="sm-field"><label>Version name</label><input id="smManageVersion" type="text" value="' + escapeHtml(version ? version.label : '') + '"></div>',
      onConfirm: function () {
        requestJson('/admin/seat-mixer/api/version/' + versionId, 'PATCH', { label: $('smManageVersion').value })
          .then(function () { closeManage(); openVersionsScreen(SM.currentHallId); notify('Layout version renamed.'); })
          .catch(function (err) { notify(err.message, 'error'); });
      }
    });
  }

  // ============================================================
  // SCREEN 3: Builder
  // ============================================================
  function openBuilder(hallId, versionId, previewSnapshotId, focusMap) {
    SM.currentHallId = hallId;
    SM.currentVersionId = versionId;
    updateWorkspaceLocation(hallId, versionId, previewSnapshotId);
    var combo = getOrCreateCombo(hallId, versionId);

    showScreen('smBuilderScreen');

    // Fetch version data (saved assignments + config)
    var builderUrl = '/admin/seat-mixer/api/version/' + versionId + '/data' +
      (previewSnapshotId ? '?snapshot_id=' + encodeURIComponent(previewSnapshotId) : '');
    fetch(builderUrl)
      .then(function (r) { return r.json(); })
      .then(function (data) {
        combo.cfg = data.config;
        combo.classColorOverrides = (data.config && data.config.classColors) || {};
        combo.seats = buildSeats(combo.cfg);
        combo.currentSnapshotId = data.snapshot_id || null;
        combo.previewSnapshotId = data.is_preview ? data.snapshot_id : null;

        // Rebuild from persistent data instead of retaining an earlier browser
        // session's temporary selection when a version is reopened or previewed.
        combo.selectedClasses = {};
        combo.activeLevels = new Set();

        // Load saved assignment
        if ((data.saved_data && data.saved_data.length > 0) || Object.keys(data.selected_students || {}).length > 0) {
          combo.savedAssignment = (data.saved_data || []).map(function (entry) {
            return Object.assign({}, entry, {
              seatId: 'r' + entry.row + 't' + entry.table + 's' + entry.seat,
              uid: entry.student_id,
            });
          });
          // Apply saved assignment to seats
          combo.savedAssignment.forEach(function (entry) {
            if (!entry.uid) return;
            var seat = combo.seats.find(function (s) { return s.id === entry.seatId; });
            if (seat) {
              seat.assigned = {
                id: entry.student_id,
                student_code: entry.student_code,
                full_name: entry.full_name,
                first_name: entry.first_name,
                classId: entry.class_id,
                class_name: entry.class_name,
                level: entry.level,
                gender: entry.gender,
                class_color: entry.class_color,
                photo_path: entry.photo_path,
                hall_assignment_count: entry.hall_assignment_count || 0,
                hall_frequency: entry.hall_frequency || entry.hall_assignment_count || 0,
              };
            }
          });

          // ── CRITICAL FIX: Populate selectedClasses from saved data ──
          // This restores class colors, legend, and data linking.
          var classIds = [];
          var seatedStudentIds = {};
          var selectedStudentIds = data.selected_students || {};
          Object.keys(selectedStudentIds).forEach(function (cid) {
            if (classIds.indexOf(String(cid)) === -1) classIds.push(String(cid));
            seatedStudentIds[String(cid)] = new Set(selectedStudentIds[cid] || []);
          });
          combo.savedAssignment.forEach(function (entry) {
            if (entry.uid && entry.class_id) {
              var cid = String(entry.class_id);
              if (classIds.indexOf(cid) === -1) classIds.push(cid);
              if (!seatedStudentIds[cid]) seatedStudentIds[cid] = new Set();
              seatedStudentIds[cid].add(entry.student_id);
            }
          });

          // Restore saved overrides, otherwise keep the deterministic palette.
          classIds.forEach(function (cid, idx) {
            combo.selectedClasses[cid] = {
              color: classColorFor(combo, cid),
              uids: new Set(seatedStudentIds[cid]),
              collapsed: true
            };
          });

          // Activate levels that contain these classes
          classIds.forEach(function (cid) {
            var level = SM.levels.find(function (l) {
              return l.classes.some(function (c) { return c.id === parseInt(cid); });
            });
            if (level) combo.activeLevels.add(level.id);
          });

          combo.dirty = false;

          // Load student rosters for these classes, then render
          var loadPromises = classIds.map(function (cid) {
            return loadStudentsForClass(parseInt(cid));
          });
          Promise.all(loadPromises).then(function () {
            // Update studentHallMap for seated students
            var hall = currentHall();
            var hallName = hall ? hall.name : '';
            var versionLabel = data.version_label || '';
            classIds.forEach(function (cid) {
              var roster = SM.studentDirectory[cid] || [];
              roster.forEach(function (st) {
                if (seatedStudentIds[cid].has(st.id)) {
                  SM.studentHallMap[st.id] = {
                    hallId: SM.currentHallId,
                    versionId: SM.currentVersionId,
                    hallName: hallName,
                    versionLabel: versionLabel
                  };
                }
              });
            });
            combo.loaded = true;
            showBuilder(combo, data);
            if (focusMap) scrollToLiveMap();
          }).catch(function (e) {
            console.error('Failed to load rosters:', e);
            combo.loaded = true;
            showBuilder(combo, data);
            if (focusMap) scrollToLiveMap();
          });
        } else {
          // An unsaved version always opens as a genuinely blank selection.
          // Do not retain a prior temporary class selection from browser state.
          combo.savedAssignment = null;
          combo.currentSnapshotId = null;
          combo.previewSnapshotId = null;
          combo.selectedClasses = {};
          combo.activeLevels = new Set();
          combo.seats = buildSeats(combo.cfg);
          combo.dirty = false;
          combo.loaded = true;
          showBuilder(combo, data);
          if (focusMap) scrollToLiveMap();
        }
      })
      .catch(function (e) { console.error('Failed to load version data:', e); });
  }

  function showBuilder(combo, versionData) {
    $('smPrintView').classList.remove('show');
    $('smBuilderMapCard').style.display = 'block';

    var hall = SM.halls.find(function (h) { return h.id === SM.currentHallId; });
    $('smCrumbHallB').textContent = hall ? hall.name : '';
    $('smCrumbVersionB').textContent = versionData.version_label || '';
    var expired = versionData.is_expired;
    $('smHallStatusBadge2').innerHTML = '<span class="sm-badge ' + (expired ? 'b-expired' : 'b-active') + '">' + (expired ? 'Expired' : 'Active') + '</span>';

    $('smRows').value = combo.cfg.rows;
    $('smTablesPerRow').value = combo.cfg.tablesPerRow;
    $('smSeatsPerTable').value = combo.cfg.seatsPerTable;
    updateCfgHint(combo.cfg);

    renderLevels(combo);
    renderClassesArea(combo);

    combo.lastMeta = combo.savedAssignment ? (versionData.last_meta || 'Loaded (saved)') : null;
    renderHallFromSeats(combo, combo.lastMeta);

    $('smExplain').innerHTML = combo.savedAssignment
      ? 'A previously saved arrangement was loaded — <b>not re-shuffled</b>.'
      : 'No arrangement yet. Click <b>"⚡ Quick generate"</b>, <b>"🧠 Strict optimizer"</b>, or <b>"🔀 Scatter Students"</b>.';

    updateSaveBadge(combo);

    // History is durable data; load it every time the builder opens, including
    // immediately after a browser refresh.
    loadSaveHistory();

    // Disable action buttons if expired
    ['smGenerateBtn', 'smOptimizeBtn', 'smScatterBtn', 'smSaveBtn'].forEach(function (id) {
      $(id).disabled = expired;
    });
  }

  function updateCfgHint(cfg) {
    var tables = cfg.tablesPerRow * cfg.rows;
    var seats = tables * cfg.seatsPerTable;
    $('smHallCfgHint').textContent = cfg.rows + ' rows × ' + cfg.tablesPerRow + ' tables × ' + cfg.seatsPerTable + ' seats = ' + tables + ' tables, ' + seats + ' chairs.';
  }

  function historyTime(isoValue) {
    if (!isoValue) return 'Saved just now';
    var date = new Date(isoValue);
    if (isNaN(date.getTime())) return 'Saved layout';
    return date.toLocaleString(undefined, {
      day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit'
    });
  }

  function renderSaveHistory(history) {
    var combo = currentCombo();
    var list = $('smSaveHistory');
    if (!combo || !history || history.length === 0) {
      list.innerHTML = '<div class="sm-history-empty">No saved revisions yet. Every save creates a protected layout revision here.</div>';
      $('smHistoryCount').textContent = '0 / 10';
      $('smHistoryReturn').classList.add('hidden');
      return;
    }
    $('smHistoryCount').textContent = history.length + ' / 10';
    list.innerHTML = history.map(function (item) {
      var current = item.is_current;
      var previewing = combo.previewSnapshotId === item.id;
      return '<div class="sm-history-item ' + (current ? 'current' : '') + (previewing ? ' previewing' : '') + '">' +
        '<span class="sm-history-dot"></span><span class="sm-history-rail"></span>' +
        '<div class="sm-history-info"><div class="sm-history-title">' + escapeHtml(historyTime(item.created_at)) +
        (current ? '<span class="sm-history-current-badge">Currently loaded</span>' : '') +
        (previewing ? '<span class="sm-history-current-badge">Previewing</span>' : '') +
        '</div><div class="sm-history-stats">' +
        'Integrity <strong>' + item.integrity_score + '/100</strong> · <span class="sm-history-near">' + item.near_adjacency_count + ' near-adjacency</span> · ' + item.placed_count + ' placed' +
        '</div></div><div class="sm-history-actions">' +
        '<button class="sm-history-action" type="button" data-history-preview="' + item.id + '"><i class="fa-solid fa-eye"></i> Preview</button>' +
        (current ? '' : '<button class="sm-history-action restore" type="button" data-history-restore="' + item.id + '"><i class="fa-solid fa-rotate-left"></i> Restore</button>') +
        '<button class="sm-history-action delete" type="button" data-history-delete="' + item.id + '" aria-label="Delete this saved revision"><i class="fa-solid fa-trash"></i> Delete</button>' +
        '</div></div>';
    }).join('');
    $('smHistoryReturn').classList.toggle('hidden', !combo.previewSnapshotId);
  }

  function loadSaveHistory() {
    if (!SM.currentVersionId) return;
    fetch('/admin/seat-mixer/api/version/' + SM.currentVersionId + '/history')
      .then(function (response) { return response.json(); })
      .then(function (data) { renderSaveHistory(data.history || []); })
      .catch(function (error) { console.error('Failed to load save history:', error); });
  }

  function previewSaveHistory(snapshotId) {
    openBuilder(SM.currentHallId, SM.currentVersionId, snapshotId, true);
  }

  function restoreSaveHistory(snapshotId) {
    openManage({
      title: 'Restore this saved layout?',
      icon: 'fa-clock-rotate-left',
      copy: 'This will make the selected revision the active seating layout. Its history entry remains available.',
      confirmText: 'Restore layout',
      onConfirm: function () {
        requestJson('/admin/seat-mixer/api/version/' + SM.currentVersionId + '/history/' + snapshotId + '/restore', 'POST', {})
          .then(function () {
            closeManage();
            notify('Saved layout restored successfully.');
            openBuilder(SM.currentHallId, SM.currentVersionId);
          })
          .catch(function (error) { notify(error.message, 'error'); });
      }
    });
  }

  function deleteSaveHistory(snapshotId) {
    openManage({
      title: 'Delete this saved revision?',
      icon: 'fa-trash',
      danger: true,
      copy: 'This snapshot will be permanently removed from this version history. The current layout remains available unless this is the revision currently loaded.',
      confirmText: 'Delete revision',
      onConfirm: function () {
        requestJson('/admin/seat-mixer/api/version/' + SM.currentVersionId + '/history/' + snapshotId, 'DELETE', { confirm: true })
          .then(function (data) {
            closeManage();
            notify('Saved revision deleted.');
            openBuilder(SM.currentHallId, SM.currentVersionId, data.current_snapshot_id || null);
          })
          .catch(function (error) { notify(error.message, 'error'); });
      }
    });
  }

  function applyCfgFromInputs(combo) {
    var rows = parseInt($('smRows').value) || 1;
    var tablesPerRow = parseInt($('smTablesPerRow').value) || 1;
    var seatsPerTable = parseInt($('smSeatsPerTable').value) || 1;
    var changed = rows !== combo.cfg.rows || tablesPerRow !== combo.cfg.tablesPerRow || seatsPerTable !== combo.cfg.seatsPerTable;
    combo.cfg = { rows: rows, tablesPerRow: tablesPerRow, seatsPerTable: seatsPerTable };
    if (changed) { combo.seats = buildSeats(combo.cfg); }
    updateCfgHint(combo.cfg);
  }

  // ── Render levels ──
  function renderLevels(combo) {
    $('smLevelRow').innerHTML = SM.levels.map(function (l) {
      return '<div class="sm-level-chip ' + (combo.activeLevels.has(l.id) ? 'active' : '') + '" data-level="' + l.id + '">' + escapeHtml(l.name) + '</div>';
    }).join('');
  }

  // ── Render classes area ──
  function renderClassesArea(combo) {
    var wrap = $('smClassesArea');
    if (combo.activeLevels.size === 0) {
      wrap.innerHTML = '<p class="sm-no-classes-note">Select one or more levels to see their classes.</p>';
      return;
    }

    var html = '';
    SM.levels.filter(function (l) { return combo.activeLevels.has(l.id); }).forEach(function (l) {
      html += '<div class="sm-level-block"><div class="sm-level-block-title">' + escapeHtml(l.name) + ' — Classes</div><div class="sm-class-chip-row">';
      l.classes.forEach(function (c) {
        var cid = String(c.id);
        var sel = combo.selectedClasses[cid];
        var color = sel ? sel.color : (combo.classColorOverrides[cid] || '#CBD5E1');
        var countBadge = sel ? ' · ' + sel.uids.size : '';
        var chevron = sel ? (sel.collapsed ? ' ▸' : ' ▾') : '';
        var removeBtn = sel ? '<span class="sm-chip-remove" data-remove="' + cid + '" title="Remove from mix">✕</span>' : '';
        html += '<div class="sm-class-chip ' + (sel ? 'on' : '') + '" data-class="' + cid + '">' +
          '<span class="dot" style="background:' + color + ';" data-colordot="' + cid + '"></span>' +
          escapeHtml(c.name) + countBadge + chevron + removeBtn + '</div>';
      });
      html += '</div>';
      // Render roster panels for expanded classes
      l.classes.filter(function (c) {
        return combo.selectedClasses[String(c.id)] && !combo.selectedClasses[String(c.id)].collapsed;
      }).forEach(function (c) {
        html += renderRosterPanel(combo, c);
      });
      html += '</div>';
    });
    wrap.innerHTML = html;
  }

  // ── Render roster panel ──
  function renderRosterPanel(combo, cls) {
    var cid = String(cls.id);
    var sel = combo.selectedClasses[cid];
    var classColor = classColorFor(combo, cid);
    if (!sel) return '';

    // Check if we need to load students for this class
    var roster = SM.studentDirectory[cid] || [];
    if (roster.length === 0) {
      // Load students asynchronously
      loadStudentsForClass(cls.id);
      return '<div class="sm-roster-panel" id="roster-' + cid + '">' +
        '<div class="sm-roster-head"><span class="rtitle">' + escapeHtml(cls.name) + ' roster</span>' +
        '<div class="sm-roster-quick"><span class="rcount">Loading...</span></div></div></div>';
    }

    var hall = currentHall();
    var rows = roster.map(function (st) {
      var checked = sel.uids.has(st.id);
      var elsewhere = SM.studentHallMap[st.id];
      var isSelfCombo = elsewhere && elsewhere.hallId === SM.currentHallId && elsewhere.versionId === SM.currentVersionId;
      var statusHtml;
      if (checked) statusHtml = '<span class="rstatus selected" style="--roster-class-color:' + escapeHtml(classColor) + '">Selected</span>';
      else if (elsewhere && !isSelfCombo) statusHtml = '<span class="rstatus taken">' + escapeHtml(elsewhere.hallName) + ' · ' + escapeHtml(elsewhere.versionLabel) + '</span>';
      else statusHtml = '<span class="rstatus">Available</span>';

      return '<div class="sm-roster-row" data-uid="' + st.id + '" data-class="' + cid + '">' +
        '<input type="checkbox" ' + (checked ? 'checked' : '') + '>' +
        photoHtml(st, '') +
        '<span class="rname" title="' + escapeHtml(st.full_name) + '">' + escapeHtml(st.first_name || st.full_name) + '<small class="rmeta"><i class="fa-solid fa-circle" style="color:' + escapeHtml(classColor) + '"></i> ' + escapeHtml(st.class_name || '') + ' · ' + escapeHtml(st.gender || 'Not recorded') + '</small></span>' +
        '<span class="rid"><i class="fa-solid fa-clock-rotate-left"></i> ' + (st.hall_frequency || st.hall_assignment_count || 0) + ' time' + ((st.hall_frequency || st.hall_assignment_count || 0) === 1 ? '' : 's') + '</span>' +
        statusHtml + '</div>';
    }).join('');

    return '<div class="sm-roster-panel">' +
      '<div class="sm-roster-head"><span class="rtitle">' + escapeHtml(cls.name) + ' roster</span>' +
      '<div class="sm-roster-quick">' +
      '<button data-act="all" data-class="' + cid + '">Select all</button>' +
      '<button data-act="clear" data-class="' + cid + '">Clear</button>' +
      '<span class="rcount">' + sel.uids.size + ' selected</span>' +
      '</div></div>' +
      '<div class="sm-roster-list">' + rows + '</div></div>';
  }

  // ── Load students for a class from API ──
  function loadStudentsForClass(classId) {
    var cid = String(classId);
    if (SM.studentDirectory[cid] && SM.studentDirectory[cid].length > 0) return Promise.resolve();

    var params = new URLSearchParams();
    params.append('class_ids', classId);
    params.append('hall_id', SM.currentHallId || '');

    return fetch('/admin/seat-mixer/api/students?' + params.toString())
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.students) {
          // API uses snake_case while the live seating engine uses classId.
          // Normalize once at the boundary so every optimizer and card reads
          // the same real class identity instead of displaying "?".
          SM.studentDirectory[cid] = data.students.map(function (student) {
            student.classId = student.class_id;
            student.class_name = student.class_name || '';
            student.hall_frequency = student.hall_frequency || student.hall_assignment_count || 0;
            return student;
          });
          // Update student hall map
          data.students.forEach(function (st) {
            if (st.elsewhere) {
              SM.studentHallMap[st.id] = {
                hallId: st.elsewhere.hall_id,
                versionId: st.elsewhere.version_id,
                hallName: st.elsewhere.hall_name,
                versionLabel: st.elsewhere.version_label
              };
            }
          });
          // Re-render classes area to show the loaded roster
          var combo = currentCombo();
          if (combo) renderClassesArea(combo);
        }
      })
      .catch(function (e) { console.error('Failed to load students:', e); });
  }

  // ── Compute repeat count: how many times each student appears in the seating ──
  function computeRepeatCounts(seats) {
    var counts = {};
    for (var i = 0; i < seats.length; i++) {
      var s = seats[i];
      if (!s.assigned) continue;
      var sid = s.assigned.id;
      counts[sid] = (counts[sid] || 0) + 1;
    }
    return counts;
  }

  // ── Render hall from seats ──
  function renderHallFromSeats(combo, meta) {
    var seats = combo.seats;
    var metrics = computeMetrics(seats);
    var used = classesUsed(combo);
    var totalStudents = used.reduce(function (a, c) { return a + c.count; }, 0);
    var totalSeats = seats.length;
    var filled = seats.filter(function (s) { return s.assigned; }).length;
    var unassigned = Math.max(0, totalStudents - filled);

    // Compute repeat counts for each student
    var repeatCounts = computeRepeatCounts(seats);
    var totalDuplicates = 0;
    Object.keys(repeatCounts).forEach(function (sid) {
      if (repeatCounts[sid] > 1) totalDuplicates += (repeatCounts[sid] - 1);
    });

    var pills = [];
    pills.push('<span class="sm-pill neutral">🪑 ' + totalSeats + ' seats total</span>');
    pills.push('<span class="sm-pill neutral">👥 ' + used.length + ' classes · ' + totalStudents + ' students</span>');
    pills.push('<span class="sm-pill ' + (filled < totalStudents ? 'bad' : 'ok') + '">' + filled + '/' + totalStudents + ' placed</span>');
    pills.push('<span class="sm-pill ' + (unassigned === 0 ? 'ok' : 'warn') + '">' + (unassigned === 0 ? '✓ 0 unassigned' : '⚠ ' + unassigned + ' unassigned') + '</span>');
    pills.push('<span class="sm-pill ' + (metrics.hardCount === 0 ? 'ok' : 'bad') + '">' + (metrics.hardCount === 0 ? '✓ 0 hard violation' : '⚠ ' + metrics.hardCount + ' hard violation') + '</span>');
    pills.push('<span class="sm-pill ' + (metrics.softSum === 0 ? 'ok' : 'warn') + '">' + (metrics.softSum === 0 ? '✓ 0 near-adjacency' : '⚠ ' + metrics.softSum + ' near-adjacency') + '</span>');
    if (totalDuplicates > 0) pills.push('<span class="sm-pill bad">⚠ ' + totalDuplicates + ' duplicate placement</span>');
    pills.push('<span class="sm-pill neutral">Integrity ' + metrics.integrityScore + '/100</span>');
    pills.push('<span class="sm-pill neutral">Avg class distance ' + metrics.avgSameClassDistance + '</span>');
    if (meta) pills.push('<span class="sm-pill neutral">' + escapeHtml(meta) + '</span>');
    $('smSummary').innerHTML = pills.join('');

    // Render hall map
    var html = '';
    for (var r = 0; r < combo.cfg.rows; r++) {
      html += '<div class="sm-col"><div class="sm-col-label">Row ' + (r + 1) + '</div>';
      for (var t = 0; t < combo.cfg.tablesPerRow; t++) {
        var tableSeats = seats.filter(function (s) { return s.row === r && s.tableIdx === t; });
        var counts = {};
        tableSeats.forEach(function (s) { if (s.assigned) counts[s.assigned.classId] = (counts[s.assigned.classId] || 0) + 1; });
        var hasViolation = Object.values(counts).some(function (n) { return n > 1; });
        var cols = tableSeats.length <= 2 ? tableSeats.length : 2;
        html += '<div class="sm-table-unit ' + (hasViolation ? 'violation' : '') + '" style="grid-template-columns:repeat(' + cols + ',92px);">';
        tableSeats.forEach(function (s, idx) {
          if (s.assigned) {
            var cv = classVisual(combo, s.assigned.classId);
            var repeatCount = repeatCounts[s.assigned.id] || 1;
            var repeatBadge = repeatCount > 1 ? ' ×' + repeatCount : '';
            html += '<div class="sm-chair assigned" data-seat="' + s.id + '" style="--seat-class-color:' + cv.color + '; background:linear-gradient(155deg, ' + cv.color + ', ' + cv.color + 'CC); animation-delay:' + (idx * 35) + 'ms;" title="' + escapeHtml(cv.name) + ' — ' + escapeHtml(s.assigned.full_name) + ' (placed ' + repeatCount + 'x)">' +
              photoHtml(s.assigned, 'photo') +
              '<span class="sname">' + escapeHtml(s.assigned.first_name || (s.assigned.full_name || '').split(' ')[0]) + '</span>' +
              '<span class="code"><i class="fa-solid fa-user-group"></i> ' + (s.assigned.hall_assignment_count || 0) + ' halls' + repeatBadge + '</span>' +
              '</div>';
          } else {
            html += '<div class="sm-chair empty" data-seat="' + s.id + '"><span class="sname">Empty</span></div>';
          }
        });
        html += '</div>';
      }
      html += '</div>';
    }
    $('smHall').innerHTML = html;
    // Keep the existing card shell, but make its content reflect the actual
    // hall context.  Versions are drafts; frequency comes from exam history.
    seats.forEach(function (seat) {
      var card = $('smHall').querySelector('[data-seat="' + seat.id + '"]');
      if (!card || !seat.assigned) return;
      // Saved layouts carry the canonical class label. Do not depend on an
      // optional client-side level cache, which was the source of the "?".
      var visual = classVisual(combo, seat.assigned.classId);
      var className = seat.assigned.class_name || visual.name;
      var frequency = seat.assigned.hall_frequency || seat.assigned.hall_assignment_count || 0;
      card.draggable = true;
      var classLine = document.createElement('span');
      classLine.className = 'seat-meta';
      classLine.textContent = className;
      var code = card.querySelector('.code');
      if (code) {
        code.innerHTML = '<i class="fa-solid fa-clock-rotate-left"></i> ' + frequency + ' Time' + (frequency === 1 ? '' : 's');
        card.insertBefore(classLine, code);
      }
    });

    // Render legend
    $('smLegend').innerHTML = used.map(function (c) {
      return '<div class="sm-legend-item"><div class="swatch" style="background:' + c.color + '"></div>' + escapeHtml(c.name) + ' (' + c.count + ')</div>';
    }).join('');
  }

  // ── Update save badge ──
  function updateSaveBadge(combo) {
    var el = $('smSaveBadge');
    if (combo.savedAssignment && !combo.dirty) {
      el.textContent = '✅ Saved'; el.classList.remove('unsaved');
    } else {
      el.textContent = '● Unsaved'; el.classList.add('unsaved');
    }
  }

  // ── Modal: open / close / replace ──
  function openModal(seatId) {
    var combo = currentCombo();
    var seat = combo.seats.find(function (s) { return s.id === seatId; });
    if (!seat || !seat.assigned) return;
    var cur = seat.assigned;
    var cv = classVisual(combo, cur.classId);

    $('smReplaceClassTitle').textContent = cv.name || 'this class';
    $('smCurSeatInfo').innerHTML =
      '<span class="sm-replace-avatar sm-current-avatar" style="--replace-class-color:' + escapeHtml(cv.color) + '">' + photoHtml(cur, '') + '</span>' +
      '<div class="ci-copy"><div class="ci-name">' + escapeHtml(cur.full_name) + '</div>' +
      '<div class="ci-meta">' + escapeHtml(cv.name) + ' · ' + escapeHtml(cur.level || '') + ' · ' + escapeHtml(cur.gender || 'Not recorded') + '</div></div>' +
      '<div class="ci-position"><span>Position</span><strong>Row ' + (seat.row + 1) + '</strong><em>Table ' + (seat.tableIdx + 1) + ' · Seat ' + (seat.seatIdx + 1) + '</em></div>';

    // Fetch class students from API
    var params = new URLSearchParams();
    params.append('class_id', cur.classId);
    params.append('version_id', SM.currentVersionId);
    params.append('hall_id', SM.currentHallId);
    params.append('current_student_id', cur.id);

    fetch('/admin/seat-mixer/api/class-students?' + params.toString())
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.students) {
          // FIX (3.1): Update positions based on in-memory arrangement
          // so classmates seated in the current (unsaved) view are shown
          var candidates = data.students.filter(function (st) {
            return st.is_current || !!findSeatOf(combo, st.id);
          });
          $('smCandidateList').innerHTML = candidates.map(function (st) {
            var inMemorySeat = findSeatOf(combo, st.id);
            var placement;
            if (inMemorySeat) {
              placement = 'R' + (inMemorySeat.row + 1) + ' T' + (inMemorySeat.tableIdx + 1) + ' S' + (inMemorySeat.seatIdx + 1);
            } else {
              placement = st.position.label;
            }
            return '<div class="sm-cand-row ' + (st.is_current ? 'self' : '') + '" data-uid="' + st.id + '" data-seatid="' + seatId + '"' + (st.is_current ? ' aria-disabled="true"' : '') + '>' +
              '<span class="sm-replace-avatar" style="--replace-class-color:' + escapeHtml(cv.color) + '">' + photoHtml(st, '') + '</span>' +
              '<div class="cn-copy"><div class="cn-name">' + escapeHtml(st.full_name) + '</div>' +
              '<div class="cn-meta">' + escapeHtml(st.gender || 'Not recorded') + ' · ' + (st.hall_assignment_count || 0) + ' hall assignment' + ((st.hall_assignment_count || 0) === 1 ? '' : 's') + '</div></div>' +
              (st.is_current
                ? '<span class="cn-current"><i class="fa-solid fa-location-dot"></i> currently seated here</span>'
                : '<span class="cn-place">' + escapeHtml(placement) + '</span><span class="cn-swap-arrow"><i class="fa-solid fa-arrow-right"></i></span>') +
              '</div>';
          }).join('') || '<div class="sm-empty-note">No other classmate is assigned in this hall layout yet.</div>';
        }
      })
      .catch(function (e) { console.error('Failed to load class students:', e); });

    $('smHardWarn').classList.remove('show');
    SM.pendingReplace = null;
    $('smModalOverlay').classList.remove('hidden');
  }

  function closeModal() {
    $('smModalOverlay').classList.add('hidden');
    $('smHardWarn').classList.remove('show');
    SM.pendingReplace = null;
  }

  function attemptReplace(targetUid, seatId) {
    var combo = currentCombo();
    var seat = combo.seats.find(function (s) { return s.id === seatId; });
    var cur = seat.assigned;
    if (!cur || parseInt(targetUid) === cur.id) { closeModal(); return; }

    // Find target student in the directory
    var cid = String(cur.classId);
    var roster = SM.studentDirectory[cid] || [];
    var target = roster.find(function (s) { return s.id === parseInt(targetUid); });
    if (!target) return;

    var targetSeat = findSeatOf(combo, parseInt(targetUid));

    var before = computeMetrics(combo.seats);
    swapSeatStudents(seat, targetSeat, target, cur);
    var after = computeMetrics(combo.seats);

    if (after.hardCount > before.hardCount) {
      swapSeatStudents(seat, targetSeat, cur, target);
      SM.pendingReplace = { seat: seat, targetSeat: targetSeat, target: target, cur: cur };
      $('smHardWarn').classList.add('show');
      return;
    }

    combo.dirty = true;
    renderHallFromSeats(combo, combo.lastMeta);
    updateSaveBadge(combo);
    closeModal();
  }

  function confirmHardViolation() {
    if (!SM.pendingReplace) return;
    var p = SM.pendingReplace;
    swapSeatStudents(p.seat, p.targetSeat, p.target, p.cur);
    var combo = currentCombo();
    combo.dirty = true;
    $('smHardWarn').classList.remove('show');
    renderHallFromSeats(combo, combo.lastMeta);
    updateSaveBadge(combo);
    SM.pendingReplace = null;
    closeModal();
  }

  function moveOrSwapSeats(fromId, toId) {
    var combo = currentCombo();
    if (!combo || fromId === toId) return;
    var from = combo.seats.find(function (seat) { return seat.id === fromId; });
    var to = combo.seats.find(function (seat) { return seat.id === toId; });
    if (!from || !to || !from.assigned) return;
    var moving = from.assigned;
    from.assigned = to.assigned || null;
    to.assigned = moving;
    combo.dirty = true;
    combo.lastMeta = to.assigned && from.assigned ? 'Manual swap' : 'Manual move';
    renderHallFromSeats(combo, combo.lastMeta);
    updateSaveBadge(combo);
    loadSaveHistory();
    notify(from.assigned ? 'Students swapped and integrity recalculated.' : 'Student moved and integrity recalculated.', 'info');
  }

  function optimizerPayload(combo) {
    var students = [];
    Object.keys(combo.selectedClasses).forEach(function (cid) {
      var roster = SM.studentDirectory[cid] || [];
      combo.selectedClasses[cid].uids.forEach(function (uid) {
        var student = roster.find(function (item) { return item.id === uid; });
        if (student) students.push(Object.assign({}, student, { classId: parseInt(cid, 10) }));
      });
    });
    return {
      seats: combo.seats.map(function (seat) {
        return Object.assign({}, seat, { assigned: seat.assigned ? Object.assign({}, seat.assigned) : null });
      }),
      students: students,
    };
  }

  function applyWorkerSeats(combo, workerSeats, metrics, label) {
    workerSeats.forEach(function (workerSeat) {
      var seat = combo.seats.find(function (item) { return item.id === workerSeat.id; });
      if (!seat) return;
      if (!workerSeat.assigned) { seat.assigned = null; return; }
      var roster = SM.studentDirectory[String(workerSeat.assigned.classId)] || [];
      seat.assigned = roster.find(function (student) { return student.id === workerSeat.assigned.id; }) || workerSeat.assigned;
    });
    combo.lastMetrics = metrics || null;
    combo.dirty = true;
    combo.lastMeta = label;
    renderHallFromSeats(combo, label);
    updateSaveBadge(combo);
  }

  function runOptimizer(mode, button, label, explanation) {
    var combo = currentCombo();
    applyCfgFromInputs(combo);
    var total = Object.keys(combo.selectedClasses).reduce(function (count, cid) { return count + combo.selectedClasses[cid].uids.size; }, 0);
    if (!total) { notify('Select at least one class and student first.', 'info'); return; }
    if (SM.optimizerRun) return;
    var oldLabel = button.innerHTML;
    var runId = (SM.optimizerRunId || 0) + 1;
    SM.optimizerRunId = runId;
    SM.optimizerRun = { id: runId, button: button, oldLabel: oldLabel };
    button.disabled = true;
    button.innerHTML = '<span class="sm-spin"></span>Optimizing…';
    function finish() {
      if (!SM.optimizerRun || SM.optimizerRun.id !== runId) return;
      button.disabled = false;
      button.innerHTML = oldLabel;
      SM.optimizerRun = null;
    }
    if (!SM.worker) {
      if (mode === 'scatter') scatterFill(combo);
      else greedyFill(combo);
      if (mode === 'strict') {
        // Browser worker support is expected, but a retrying fallback keeps
        // Strict meaningful if an older browser cannot create a worker.
        var best = combo.seats.map(function (seat) { return seat.assigned; });
        var bestMetrics = computeMetrics(combo.seats);
        for (var attempt = 0; attempt < 8; attempt++) {
          greedyFill(combo);
          strictOptimize(combo.seats, 2200);
          var candidate = computeMetrics(combo.seats);
          if (candidate.cost < bestMetrics.cost) {
            best = combo.seats.map(function (seat) { return seat.assigned; });
            bestMetrics = candidate;
          }
        }
        combo.seats.forEach(function (seat, index) { seat.assigned = best[index] || null; });
      }
      combo.dirty = true; combo.lastMeta = label;
      renderHallFromSeats(combo, label); updateSaveBadge(combo);
      $('smExplain').innerHTML = explanation;
      finish();
      return;
    }
    var payload = optimizerPayload(combo);
    SM.worker.onmessage = function (event) {
      if (event.data.type !== mode + '_done' || event.data.runId !== runId) return;
      applyWorkerSeats(combo, event.data.seats, event.data.metrics, label);
      var detail = event.data.optimisation;
      $('smExplain').innerHTML = detail
        ? '<b>Strict optimizer:</b> Compared ' + detail.attempts + ' full candidate layouts in ' + (detail.durationMs / 1000).toFixed(1) + ' seconds and kept the strongest integrity result.'
        : explanation;
      finish();
    };
    SM.worker.onerror = function () {
      if (!SM.optimizerRun || SM.optimizerRun.id !== runId) return;
      notify('The optimizer could not complete. Please run it again.', 'error');
      finish();
    };
    SM.worker.postMessage({ type: mode, runId: runId, seats: payload.seats, students: payload.students, iterations: mode === 'strict' ? 12000 : 0 });
  }

  // ── Save arrangement ──
  function saveArrangement() {
    persistArrangement(currentCombo());
  }

  function persistArrangement(combo) {

    var assignments = [];
    var selectedStudents = {};
    combo.seats.forEach(function (s) {
      if (s.assigned) {
        assignments.push({ student_id: s.assigned.id, row: s.row, table: s.tableIdx, seat: s.seatIdx });
      }
    });
    Object.keys(combo.selectedClasses).forEach(function (classId) {
      selectedStudents[classId] = Array.from(combo.selectedClasses[classId].uids);
    });

    fetch('/admin/seat-mixer/api/save', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': SM.csrfToken },
      body: JSON.stringify({
        version_id: SM.currentVersionId,
        assignments: assignments,
        config: combo.cfg,
        selected_students: selectedStudents,
        last_meta: combo.lastMeta || 'Saved layout'
      })
    })
      .then(function (r) { return r.json(); })
      .then(function (data) {
        if (data.success) {
          combo.savedAssignment = combo.seats.map(function (s) {
            return { seatId: s.id, uid: s.assigned ? s.assigned.id : null };
          });
          combo.currentSnapshotId = data.snapshot_id || null;
          combo.previewSnapshotId = null;
          combo.dirty = false;
          updateSaveBadge(combo);
          renderSaveHistory(data.history || []);
          notify('Seat arrangement saved successfully (' + data.count + ' seats).');
        } else {
          notify('Could not save: ' + (data.error || 'Unknown error'), 'error');
        }
      })
      .catch(function (e) { notify('Could not save: ' + e.message, 'error'); });
  }

  // ── Print / Export ──
  function openPrint() {
    var combo = currentCombo();
    if (!combo.savedAssignment) {
      notify('Save the arrangement before printing. The print view only uses persisted seating data.', 'info');
      return;
    }
    $('smPrintOverlay').classList.remove('hidden');
  }

  function applyAppearance(appearance) {
    SM.appearance = Object.assign({}, SM.appearance, appearance || {});
    var style = $('smAppearanceLayoutStyle') || document.createElement('style');
    style.id = 'smAppearanceLayoutStyle';
    style.textContent = '.sm-wrapper{' +
      '--sm-seat-size:' + (SM.appearance.seatSize || 92) + 'px;' +
      '--sm-seat-gap:' + (SM.appearance.seatGap || 8) + 'px;' +
      '--sm-photo-size:' + (SM.appearance.photoSize || 42) + 'px;' +
      '--sm-seat-radius:' + (SM.appearance.cornerRadius || 16) + 'px;' +
      '--sm-font-size:' + (SM.appearance.fontSize || 12) + 'px;' +
      '--sm-font-weight:' + (SM.appearance.fontWeight || 700) + ';' +
      '--sm-seat-border:' + (SM.appearance.studentBorderWidth || 1) + 'px;' +
      '--sm-photo-radius:' + (SM.appearance.photoShape === 'square' ? '10px' : '50%') + ';' +
      '--sm-font-family:' + (SM.appearance.fontFamily || 'Inter') + ',sans-serif;' +
      '}' +
      '.sm-hall{transform:scale(' + ((SM.appearance.mapZoom || 100) / 100) + ');transform-origin:top left;}' +
      '.sm-chair{box-shadow:' + (SM.appearance.shadow === 'none' ? 'none' : (SM.appearance.shadow === 'strong' ? '0 16px 28px -13px rgba(0,0,0,.75)' : '0 8px 18px -12px rgba(0,0,0,.6)')) + ';}';
    if (!style.parentNode) document.head.appendChild(style);
    var active = currentCombo();
    if (active && $('smHall')) renderHallFromSeats(active, active.lastMeta);
  }

  function appearanceFields() {
    var a = SM.appearance;
    var field = function (key, label, type, options) {
      var value = a[key] === undefined ? '' : a[key];
      if (type === 'select') return '<label class="sm-field"><span>' + label + '</span><select data-sm-setting="' + key + '">' + options.map(function (item) { return '<option value="' + item[0] + '"' + (String(value) === String(item[0]) ? ' selected' : '') + '>' + item[1] + '</option>'; }).join('') + '</select></label>';
      return '<label class="sm-field"><span>' + label + '</span><input data-sm-setting="' + key + '" type="' + type + '" value="' + escapeHtml(value) + '"></label>';
    };
    return field('seatSize', 'Seat size', 'number') + field('seatGap', 'Seat spacing', 'number') + field('mapZoom', 'Map zoom (%)', 'number') + field('photoSize', 'Student image size', 'number') +
      field('photoShape', 'Student image shape', 'select', [['circle','Circle'],['square','Rounded square']]) + field('studentBorderWidth', 'Student border width', 'number') +
      field('fontFamily', 'Font family', 'select', [['Inter','Inter'],['Space Grotesk','Space Grotesk'],['Arial','Arial'],['Georgia','Georgia']]) + field('fontSize', 'Font size', 'number') + field('fontWeight', 'Font weight', 'select', [[500,'Medium'],[600,'Semibold'],[700,'Bold'],[800,'Extra bold']]) +
      field('cornerRadius', 'Corner radius', 'number') + field('cardPadding', 'Card padding', 'number') +
      field('shadow', 'Card shadow', 'select', [['none','None'],['medium','Medium'],['strong','Strong']]);
  }

  function openAppearance() {
    $('smAppearanceFields').innerHTML = appearanceFields();
    $('smAppearanceOverlay').classList.remove('hidden');
  }

  function persistAppearance() {
    $('smAppearanceStatus').textContent = 'Saving…';
    fetch('/admin/seat-mixer/api/appearance', {
      method: 'PATCH', headers: {'Content-Type':'application/json','X-CSRFToken':SM.csrfToken}, body: JSON.stringify(SM.appearance)
    }).then(function (response) { return response.json(); }).then(function (data) {
      if (data.success) { applyAppearance(data.appearance); $('smAppearanceStatus').textContent = 'Saved'; }
      else $('smAppearanceStatus').textContent = 'Could not save';
    }).catch(function () { $('smAppearanceStatus').textContent = 'Could not save'; });
  }

  // ============================================================
  // Event handlers
  // ============================================================
  function initEvents() {
    // Screen 1: Halls
    $('smHallList').addEventListener('click', function (e) {
      var menu = e.target.closest('[data-menu-kind]');
      if (menu) {
        e.stopPropagation();
        toggleActionMenu(menu, menu.dataset.menuKind, parseInt(menu.dataset.menuId));
        return;
      }
      var card = e.target.closest('.sm-hall-card');
      if (!card) return;
      openVersionsScreen(parseInt(card.dataset.hall));
    });

    $('smShowCreateBtn').addEventListener('click', function () {
      $('smCreateBox').classList.toggle('show');
    });

    $('smConfirmCreateBtn').addEventListener('click', createHall);

    // Screen 2: Versions
    $('smVersionList').addEventListener('click', function (e) {
      var menu = e.target.closest('[data-menu-kind]');
      if (menu) {
        e.stopPropagation();
        toggleActionMenu(menu, menu.dataset.menuKind, parseInt(menu.dataset.menuId));
        return;
      }
      var card = e.target.closest('.sm-version-card');
      if (!card) return;
      openBuilder(SM.currentHallId, parseInt(card.dataset.version));
    });

    $('smAddVersionBtn').addEventListener('click', addVersion);

    $('smBackToHallsBtn').addEventListener('click', showHallsScreen);

    // Screen 3: Builder
    $('smBackToVersionsBtn').addEventListener('click', function () {
      openVersionsScreen(SM.currentHallId);
    });

    // Hall config inputs
    ['smRows', 'smTablesPerRow', 'smSeatsPerTable'].forEach(function (id) {
      $(id).addEventListener('input', function () {
        var combo = currentCombo();
        if (combo) { applyCfgFromInputs(combo); renderHallFromSeats(combo, combo.lastMeta); }
      });
    });

    // Level chips
    $('smLevelRow').addEventListener('click', function (e) {
      var chip = e.target.closest('.sm-level-chip');
      if (!chip) return;
      var combo = currentCombo();
      var lid = parseInt(chip.dataset.level);
      if (combo.activeLevels.has(lid)) combo.activeLevels.delete(lid);
      else combo.activeLevels.add(lid);
      renderLevels(combo);
      renderClassesArea(combo);
    });

    // Classes area (delegated clicks)
    $('smClassesArea').addEventListener('click', function (e) {
      var combo = currentCombo();
      if (!combo) return;

      // Remove class from mix
      var removeEl = e.target.closest('[data-remove]');
      if (removeEl) {
        var cid = removeEl.dataset.remove;
        var sel = combo.selectedClasses[cid];
        if (sel) {
          sel.uids.forEach(function (uid) {
            delete SM.studentHallMap[uid];
            var seat = combo.seats.find(function (s) { return s.assigned && s.assigned.id === uid; });
            if (seat) seat.assigned = null;
          });
          delete combo.selectedClasses[cid];
        }
        renderClassesArea(combo);
        renderHallFromSeats(combo, combo.lastMeta);
        return;
      }

      // Color dot click
      var dot = e.target.closest('[data-colordot]');
      if (dot) {
        var cid = dot.dataset.colordot;
        openClassColorPicker(dot, combo, cid);
        return;
      }

      // Quick action buttons
      var quickBtn = e.target.closest('[data-act]');
      if (quickBtn) {
        var cid = quickBtn.dataset.class;
        var sel = combo.selectedClasses[cid];
        var hall = currentHall();
        if (quickBtn.dataset.act === 'all') {
          var roster = SM.studentDirectory[cid] || [];
          roster.forEach(function (st) {
            sel.uids.add(st.id);
            SM.studentHallMap[st.id] = {
              hallId: SM.currentHallId, versionId: SM.currentVersionId,
              hallName: hall ? hall.name : '', versionLabel: $('smCrumbVersionB').textContent
            };
          });
        } else {
          sel.uids.forEach(function (uid) {
            delete SM.studentHallMap[uid];
            var seat = combo.seats.find(function (s) { return s.assigned && s.assigned.id === uid; });
            if (seat) seat.assigned = null;
          });
          sel.uids.clear();
        }
        renderClassesArea(combo);
        renderHallFromSeats(combo, combo.lastMeta);
        return;
      }

      // Class chip click
      var chip = e.target.closest('.sm-class-chip');
      if (chip) {
        var cid = chip.dataset.class;
        if (combo.selectedClasses[cid]) {
          // Already in mix: toggle collapse/expand
          // FIX (4.1): Collapse all other rosters so only one panel is open
          Object.keys(combo.selectedClasses).forEach(function (otherCid) {
            if (otherCid !== cid) combo.selectedClasses[otherCid].collapsed = true;
          });
          combo.selectedClasses[cid].collapsed = !combo.selectedClasses[cid].collapsed;
        } else {
          // Add to mix — collapse all others first
          Object.keys(combo.selectedClasses).forEach(function (otherCid) {
            combo.selectedClasses[otherCid].collapsed = true;
          });
          if (selectedClassColorConflict(combo, cid, classColorFor(combo, cid))) {
            notify('This class has a color already used in the current mix. Choose a different swatch first.', 'info');
            return;
          }
          combo.selectedClasses[cid] = {
            color: classColorFor(combo, cid),
            uids: new Set(),
            collapsed: false
          };
        }
        renderClassesArea(combo);
        renderHallFromSeats(combo, combo.lastMeta);
      }
    });
    // The picker lives at document level so it can float above the compact
    // chip row without changing that row's existing layout.
    document.addEventListener('click', function (e) {
      var choice = e.target.closest('[data-sm-color-choice]');
      if (choice && SM.classColorPicker) {
        var combo = currentCombo();
        if (combo) setClassColor(combo, SM.classColorPicker.classId, choice.dataset.smColorChoice);
        return;
      }
      if (!e.target.closest('#smClassColorPicker') && !e.target.closest('[data-colordot]')) closeClassColorPicker();
    });
    document.addEventListener('change', function (e) {
      if (!e.target.matches('[data-sm-color-custom]') || !SM.classColorPicker) return;
      var combo = currentCombo();
      if (combo) setClassColor(combo, SM.classColorPicker.classId, e.target.value);
    });

    // Checkbox changes in roster
    $('smClassesArea').addEventListener('change', function (e) {
      if (e.target.type !== 'checkbox') return;
      var row = e.target.closest('.sm-roster-row');
      var uid = parseInt(row.dataset.uid);
      var cid = row.dataset.class;
      var combo = currentCombo();
      var sel = combo.selectedClasses[cid];
      var hall = currentHall();

      if (e.target.checked) {
        sel.uids.add(uid);
        SM.studentHallMap[uid] = {
          hallId: SM.currentHallId, versionId: SM.currentVersionId,
          hallName: hall ? hall.name : '', versionLabel: $('smCrumbVersionB').textContent
        };
      } else {
        sel.uids.delete(uid);
        delete SM.studentHallMap[uid];
        var seat = combo.seats.find(function (s) { return s.assigned && s.assigned.id === uid; });
        if (seat) seat.assigned = null;
      }
      renderClassesArea(combo);
      renderHallFromSeats(combo, combo.lastMeta);
    });

    // Action buttons
    $('smGenerateBtn').addEventListener('click', function () {
      var combo = currentCombo();
      applyCfgFromInputs(combo);
      var totalSelected = Object.keys(combo.selectedClasses).reduce(function (acc, cid) {
        return acc + combo.selectedClasses[cid].uids.size;
      }, 0);
      if (totalSelected === 0) { notify('Select at least one class and student first.', 'info'); return; }
      greedyFill(combo);
      combo.dirty = true;
      combo.lastMeta = 'Quick (greedy)';
      renderHallFromSeats(combo, combo.lastMeta);
      $('smExplain').innerHTML = '<b>Quick generate:</b> Built a distance-aware first layout that prioritizes separating every class across the hall. Run <b>"🧠 Strict optimizer"</b> for iterative refinement.';
      updateSaveBadge(combo);
    });

    $('smScatterBtn').addEventListener('click', function () {
      var combo = currentCombo();
      applyCfgFromInputs(combo);
      var totalSelected = Object.keys(combo.selectedClasses).reduce(function (acc, cid) {
        return acc + combo.selectedClasses[cid].uids.size;
      }, 0);
      if (totalSelected === 0) { notify('Select at least one class and student first.', 'info'); return; }
      scatterFill(combo);
      combo.dirty = true;
      combo.lastMeta = 'Scattered (spread across all rows)';
      renderHallFromSeats(combo, combo.lastMeta);
      $('smExplain').innerHTML = '<b>Scatter Students:</b> Students were spread across <b>all rows</b> using round-robin ordering, so a small group is distributed across every row rather than clustered at the front.';
      updateSaveBadge(combo);
    });

    $('smSaveBtn').addEventListener('click', saveArrangement);
    $('smSaveHistory').addEventListener('click', function (e) {
      var preview = e.target.closest('[data-history-preview]');
      var restore = e.target.closest('[data-history-restore]');
      var remove = e.target.closest('[data-history-delete]');
      if (preview) previewSaveHistory(parseInt(preview.dataset.historyPreview, 10));
      if (restore) restoreSaveHistory(parseInt(restore.dataset.historyRestore, 10));
      if (remove) deleteSaveHistory(parseInt(remove.dataset.historyDelete, 10));
    });
    $('smHistoryReturn').addEventListener('click', function () {
      openBuilder(SM.currentHallId, SM.currentVersionId);
    });
    $('smPrintBtn').addEventListener('click', openPrint);
    $('smAppearanceBtn').addEventListener('click', openAppearance);
    $('smPrintClose').addEventListener('click', function () { $('smPrintOverlay').classList.add('hidden'); });
    $('smPrintCancel').addEventListener('click', function () { $('smPrintOverlay').classList.add('hidden'); });
    $('smPrintConfirm').addEventListener('click', function () {
      var orientation = document.querySelector('input[name="smPrintOrientation"]:checked').value;
      $('smPrintOverlay').classList.add('hidden');
      window.open('/admin/seat-mixer/print?version_id=' + SM.currentVersionId + '&orientation=' + orientation, '_blank');
    });
    $('smPrintOverlay').addEventListener('click', function (e) { if (e.target === $('smPrintOverlay')) $('smPrintOverlay').classList.add('hidden'); });
    $('smAppearanceClose').addEventListener('click', function () { $('smAppearanceOverlay').classList.add('hidden'); });
    $('smAppearanceOverlay').addEventListener('click', function (e) { if (e.target === $('smAppearanceOverlay')) $('smAppearanceOverlay').classList.add('hidden'); });
    $('smAppearanceFields').addEventListener('input', function (e) {
      var key = e.target.dataset.smSetting;
      if (!key) return;
      SM.appearance[key] = e.target.type === 'number' ? Number(e.target.value) : e.target.value;
      applyAppearance(SM.appearance);
      window.clearTimeout(SM.appearanceTimer);
      SM.appearanceTimer = window.setTimeout(persistAppearance, 350);
    });
    $('smAppearanceFields').addEventListener('change', function (e) {
      var key = e.target.dataset.smSetting;
      if (!key) return;
      SM.appearance[key] = e.target.type === 'number' ? Number(e.target.value) : e.target.value;
      applyAppearance(SM.appearance);
      window.clearTimeout(SM.appearanceTimer);
      SM.appearanceTimer = window.setTimeout(persistAppearance, 100);
    });

    // Hall map click (open modal)
    $('smHall').addEventListener('click', function (e) {
      var el = e.target.closest('.sm-chair.assigned');
      if (!el) return;
      openModal(el.dataset.seat);
    });
    $('smHall').addEventListener('dragstart', function (e) {
      var chair = e.target.closest('.sm-chair.assigned');
      if (!chair) return;
      e.dataTransfer.effectAllowed = 'move';
      e.dataTransfer.setData('text/plain', chair.dataset.seat);
      chair.classList.add('dragging');
    });
    $('smHall').addEventListener('dragover', function (e) {
      var chair = e.target.closest('.sm-chair');
      if (!chair) return;
      e.preventDefault();
      chair.classList.add('drop-target');
    });
    $('smHall').addEventListener('dragleave', function (e) {
      var chair = e.target.closest('.sm-chair');
      if (chair) chair.classList.remove('drop-target');
    });
    $('smHall').addEventListener('dragend', function () {
      document.querySelectorAll('.sm-chair.dragging,.sm-chair.drop-target').forEach(function (item) {
        item.classList.remove('dragging', 'drop-target');
      });
    });
    $('smHall').addEventListener('drop', function (e) {
      var chair = e.target.closest('.sm-chair');
      if (!chair) return;
      e.preventDefault();
      moveOrSwapSeats(e.dataTransfer.getData('text/plain'), chair.dataset.seat);
    });

    // Candidate list click
    $('smCandidateList').addEventListener('click', function (e) {
      var row = e.target.closest('.sm-cand-row');
      if (!row) return;
      attemptReplace(row.dataset.uid, row.dataset.seatid);
    });

    // Modal close
    $('smModalClose').addEventListener('click', closeModal);
    $('smModalOverlay').addEventListener('click', function (e) {
      if (e.target.id === 'smModalOverlay') closeModal();
    });
    $('smWarnConfirm').addEventListener('click', confirmHardViolation);
    $('smWarnCancel').addEventListener('click', function () {
      $('smHardWarn').classList.remove('show');
      SM.pendingReplace = null;
    });

    // Management menus are attached to body because they float outside cards.
    document.addEventListener('click', function (e) {
      var item = e.target.closest('.sm-action-menu [data-action]');
      if (item) {
        var menu = item.closest('.sm-action-menu');
        var kind = menu.dataset.kind;
        var id = parseInt(menu.dataset.id);
        var action = item.dataset.action;
        closeActionMenus();
        if (kind === 'hall') manageHallAction(id, action);
        else manageVersionAction(id, action);
        return;
      }
      if (!e.target.closest('.sm-action-menu') && !e.target.closest('[data-menu-kind]')) closeActionMenus();
    });
    $('smManageClose').addEventListener('click', closeManage);
    $('smManageCancel').addEventListener('click', closeManage);
    $('smManageOverlay').addEventListener('click', function (e) { if (e.target === $('smManageOverlay')) closeManage(); });
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') { closeActionMenus(); closeManage(); closeClassColorPicker(); }
    });

    // Capture phase keeps the established controls and layout intact while all
    // three buttons now share the same integrity objective in the worker.
    $('smGenerateBtn').addEventListener('click', function (e) {
      e.stopImmediatePropagation();
      runOptimizer('quick', this, 'Quick integrity layout', '<b>Quick generate:</b> Built a hall-wide, distance-aware layout using the same integrity score as Strict Optimizer.');
    }, true);
    $('smOptimizeBtn').addEventListener('click', function (e) {
      e.stopImmediatePropagation();
      runOptimizer('strict', this, 'Strict convergence complete', '<b>Strict optimizer:</b> Ran multi-start improvement until no meaningful integrity gain remained.');
    }, true);
    $('smScatterBtn').addEventListener('click', function (e) {
      e.stopImmediatePropagation();
      runOptimizer('scatter', this, 'Maximum scatter layout', '<b>Scatter Students:</b> Occupied seats were chosen across the full hall before closer seats, preserving empty-space separation.');
    }, true);
  }

  // ============================================================
  // Init
  // ============================================================
  window.SM_init = function (opts) {
    SM.halls = opts.halls || [];
    SM.levels = opts.levels || [];
    SM.classPalette = opts.classPalette || PALETTE;
    SM.csrfToken = opts.csrfToken || '';
    SM.schoolName = opts.schoolName || 'School';

    try {
      SM.worker = new Worker('/static/js/seat_mixer_worker.js');
    } catch (e) {
      console.warn('Web Worker not available:', e);
      SM.worker = null;
    }

    initEvents();
    fetch('/admin/seat-mixer/api/appearance')
      .then(function (response) { return response.json(); })
      .then(function (data) { if (data.appearance) applyAppearance(data.appearance); })
      .catch(function () { applyAppearance({}); });
    // Restore the exact workspace after refresh/navigation if it still exists.
    var locationParams = new URLSearchParams(window.location.search);
    var savedHallId = parseInt(locationParams.get('hall_id'), 10);
    var savedVersionId = parseInt(locationParams.get('version_id'), 10);
    var savedSnapshotId = parseInt(locationParams.get('snapshot_id'), 10);
    if (savedHallId && savedVersionId && SM.halls.some(function (hall) { return hall.id === savedHallId; })) {
      openBuilder(savedHallId, savedVersionId, savedSnapshotId || null);
    } else if (savedHallId && SM.halls.some(function (hall) { return hall.id === savedHallId; })) {
      openVersionsScreen(savedHallId);
    } else {
      showHallsScreen();
    }
  };
})();
