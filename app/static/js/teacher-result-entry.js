(() => {
  const page = document.getElementById("resultEntryPage");
  if (!page) return;

  const form = document.getElementById("teacherResultForm");
  const cards = [...page.querySelectorAll("[data-student-card]")];
  const inputs = cards.map((card) => card.querySelector("[data-score-input]")).filter(Boolean);
  const maxScore = Number(page.dataset.maxScore);
  const validMaximum = Number.isFinite(maxScore) && maxScore > 0;
  let gradeScales = { exam: [], global: [], fallback: [] };
  try {
    gradeScales = JSON.parse(page.dataset.gradeScales || "{}");
  } catch (_) {
    gradeScales = { exam: [], global: [], fallback: [] };
  }
  const timers = new Map();
  let autosaveQueue = Promise.resolve();
  const statuses = {
    all: document.querySelector('[data-pill-count="all"]'),
    todo: document.querySelector('[data-pill-count="todo"]'),
    flag: document.querySelector('[data-pill-count="flag"]'),
  };
  const statusPanel = document.getElementById("saveStatus");
  const statusTitle = statusPanel?.querySelector("[data-status-title]");
  const statusDetail = statusPanel?.querySelector("[data-status-detail]");
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  let activeFilter = "all";
  let searchValue = "";
  let lastFocus = null;
  let submitInProgress = false;
  let savedSinceLoad = false;
  let reviewHighlightTimer = null;
  let reviewHighlightedCard = null;
  let restoreFiltersOpen = false;

  function setSaveStatus(state, title, detail) {
    if (!statusPanel) return;
    statusPanel.classList.remove("ok", "unsaved", "saving", "error", "ready", "submitted", "reopened");
    statusPanel.classList.add(state);
    if (statusTitle) statusTitle.textContent = title;
    if (statusDetail) statusDetail.textContent = detail;
  }

  function renderMarkAction(card, action) {
    const badge = card.querySelector("[data-mark-action]");
    if (!badge) return;
    const actions = {
      entered: ["fa-circle-plus", "Natiijo cusub la geliyey"],
      edited: ["fa-pen-to-square", "Natiijo waa la beddelay"],
      cleared: ["fa-trash-can", "Natiijo waa la tirtiray"],
      legacy: ["fa-clock-rotate-left", "Taariikh ficil hore lama hayo"],
    };
    const detail = actions[action];
    badge.hidden = !detail;
    badge.className = detail ? `re-mark-action is-${action}` : "re-mark-action";
    badge.dataset.actionType = detail ? action : "";
    if (!detail) return;
    badge.querySelector("i").className = `fa-solid ${detail[0]}`;
    badge.querySelector("[data-action-label]").textContent = detail[1];
  }

  function initDeadlineCountdown() {
    const card = page.querySelector("[data-deadline-card]");
    if (!card?.dataset.deadlineAt) return;

    const deadline = Date.parse(card.dataset.deadlineAt);
    const daysNode = card.querySelector("[data-countdown-days]");
    const hoursNode = card.querySelector("[data-countdown-hours]");
    const minutesNode = card.querySelector("[data-countdown-minutes]");
    const secondsNode = card.querySelector("[data-countdown-seconds]");
    const messageNode = card.querySelector("[data-deadline-message]");
    const liveLabel = card.querySelector("[data-deadline-live-label]");
    const alertIcon = card.querySelector("[data-deadline-alert-icon]");

    if (!Number.isFinite(deadline)) {
      if (messageNode) messageNode.textContent = "Waqtiga lama akhrin karo. Dib u cusboonaysii bogga ama la xiriir maamulka.";
      if (liveLabel) liveLabel.textContent = "HUBI";
      return;
    }

    let intervalId = 0;
    const stop = () => {
      if (intervalId) window.clearInterval(intervalId);
      intervalId = 0;
    };
    const setUnit = (node, value) => {
      if (node) node.textContent = String(value).padStart(2, "0");
    };
    const update = () => {
      const remaining = Math.max(0, deadline - Date.now());
      const totalSeconds = Math.floor(remaining / 1000);
      const days = Math.floor(totalSeconds / 86400);
      const hours = Math.floor((totalSeconds % 86400) / 3600);
      const minutes = Math.floor((totalSeconds % 3600) / 60);
      const seconds = totalSeconds % 60;
      setUnit(daysNode, days);
      setUnit(hoursNode, hours);
      setUnit(minutesNode, minutes);
      setUnit(secondsNode, seconds);

      let state = "normal";
      let message = "Ku gudbi natiijooyinka ka hor waqtiga kama dambaysta ah.";
      let badge = "TOOS";
      let icon = "fa-bell";
      if (remaining <= 0) {
        state = "expired";
        badge = page.dataset.deadlineLocks === "true" ? "DHAMMAADAY" : "DIB LOO FURAY";
        message = page.dataset.deadlineLocks === "true"
          ? "Waqtigu wuu dhammaaday. La xiriir maamulka."
          : "Waqtigu wuu dhammaaday; xaashida dib ayaa loo furay.";
        icon = "fa-circle-exclamation";
      } else if (remaining <= 3600000) {
        state = "critical";
        message = "Wax ka yar 1 saac ayaa hadhay; gudbi hadda.";
        badge = "DEGDEG";
        icon = "fa-triangle-exclamation";
      } else if (remaining <= 86400000) {
        state = "urgent";
        message = "Wax ka yar 24 saac ayaa hadhay.";
        badge = "DIGNIIN";
        icon = "fa-triangle-exclamation";
      } else if (remaining <= 259200000) {
        state = "soon";
        message = "Wax ka yar 3 maalmood ayaa hadhay; sii dhammee.";
        badge = "DIGNIIN";
        icon = "fa-bell";
      }

      if (card.dataset.state !== state) card.dataset.state = state;
      if (messageNode && messageNode.textContent !== message) messageNode.textContent = message;
      if (liveLabel && liveLabel.textContent !== badge) liveLabel.textContent = badge;
      if (alertIcon) alertIcon.className = `fa-solid ${icon}`;

      if (state === "expired") {
        stop();
        if (page.dataset.deadlineLocks === "true" && page.dataset.locked !== "true") {
          page.dataset.locked = "true";
          inputs.forEach((input) => {
            const pendingTimer = timers.get(input);
            if (pendingTimer) window.clearTimeout(pendingTimer);
            timers.delete(input);
            input.disabled = true;
          });
          ["openReview", "confirmSubmit"].forEach((id) => {
            const button = document.getElementById(id);
            if (button) button.disabled = true;
          });
          let lockNote = page.querySelector(".re-lock-note");
          if (!lockNote) {
            lockNote = document.createElement("div");
            lockNote.className = "re-lock-note";
            lockNote.setAttribute("role", "status");
            lockNote.innerHTML = '<i class="fa-solid fa-lock" aria-hidden="true"></i><span>Waqtiga gelintu wuu dhammaaday. Xaashidan hadda waa xiran tahay.</span>';
            page.querySelector(".re-policy")?.after(lockNote);
          }
          setSaveStatus("error", "Waqtigu wuu dhammaaday", "Gelinta natiijooyinka waa la xiray.");
        }
        return true;
      }
      return false;
    };

    const start = () => {
      stop();
      if (!update()) intervalId = window.setInterval(update, 1000);
    };
    document.addEventListener("visibilitychange", () => {
      if (document.hidden) stop();
      else start();
    });
    start();
  }

  function scoreValue(input) {
    const raw = input.value.trim();
    if (!raw) return { empty: true, valid: false, value: null, error: "" };
    const value = Number(raw);
    if (!Number.isFinite(value) || value < 0) {
      return { empty: false, valid: false, value: null, error: "Darajada ma saxna. Geli nambar sax ah.", state: "bad" };
    }
    if (!validMaximum || value > maxScore) {
      return { empty: false, valid: false, value, error: `Darajadu waxay dhaaftay ${validMaximum ? maxScore : "xadka"}. Hubi nambarka.`, state: "bad" };
    }
    return { empty: false, valid: true, value, error: "", state: "done" };
  }

  function normalized(value) {
    return value.trim() === "" ? "" : String(Number(value.replace(",", ".")));
  }

  function gradeFor(percent) {
    for (const bucket of ["exam", "global", "fallback"]) {
      const match = (gradeScales[bucket] || []).find((scale) => (
        percent >= Number(scale.min_score) && percent <= Number(scale.max_score)
      ));
      if (match) return match;
    }
    return null;
  }

  function setGradeStyle(node, scale) {
    for (const name of ["--grade-bg", "--grade-text", "--grade-border"]) node.style.removeProperty(name);
    if (!scale) return;
    node.style.setProperty("--grade-bg", scale.background_color || "transparent");
    node.style.setProperty("--grade-text", scale.text_color || "inherit");
    node.style.setProperty("--grade-border", scale.border_color || "transparent");
  }

  function inspect(card) {
    const input = card.querySelector("[data-score-input]");
    const parsed = scoreValue(input);
    const oldScore = card.dataset.previous === "" ? null : Number(card.dataset.previous);
    if (parsed.valid && oldScore !== null && Number.isFinite(oldScore) && Math.abs(parsed.value - oldScore) >= 25) {
      parsed.state = "warn";
      parsed.error = `Isbeddel weyn: hore ${oldScore}, hadda ${parsed.value}. Hubi.`;
    }
    const saved = normalized(card.dataset.saved || "");
    const current = normalized(input.value);
    parsed.changed = !parsed.empty && current !== saved;
    if (parsed.empty && saved !== "") parsed.changed = true;
    return parsed;
  }

  function renderCard(card, animateGrade = false) {
    const input = card.querySelector("[data-score-input]");
    const grade = card.querySelector("[data-grade]");
    const percentNode = card.querySelector("[data-percent]");
    const progress = card.querySelector("[data-progress]");
    const message = card.querySelector("[data-message]");
    const badge = card.querySelector("[data-state-icon]");
    const result = inspect(card);
    const previousGrade = grade.textContent;
    card.classList.remove("done", "ed", "warn", "bad");
    setGradeStyle(grade, null);
    message.textContent = "";
    message.removeAttribute("data-kind");
    if (result.empty) {
      grade.textContent = "—";
      grade.className = "re-grade";
      percentNode.textContent = "—";
      progress.style.width = "0%";
      progress.style.background = "var(--re-muted)";
      badge.innerHTML = '<i class="fa-solid fa-user" aria-hidden="true"></i>';
      if (result.changed) card.classList.add("ed");
    } else if (!result.valid) {
      card.classList.add("bad");
      grade.textContent = "!";
      grade.className = "re-grade g-f";
      percentNode.textContent = "—";
      progress.style.width = "0%";
      progress.style.background = "var(--re-bad)";
      badge.innerHTML = '<i class="fa-solid fa-xmark" aria-hidden="true"></i>';
      message.innerHTML = '<i class="fa-solid fa-circle-exclamation" aria-hidden="true"></i>';
      message.append(document.createTextNode(result.error));
      message.dataset.kind = "bad";
    } else {
      const percent = Math.min(100, (result.value / maxScore) * 100);
      const configuredGrade = gradeFor(percent);
      grade.textContent = configuredGrade?.grade || "—";
      grade.className = `re-grade${configuredGrade ? " configured" : ""}`;
      setGradeStyle(grade, configuredGrade);
      percentNode.textContent = `${percent.toFixed(2)}%`;
      progress.style.width = `${percent}%`;
      progress.style.background = configuredGrade?.badge_color
        || configuredGrade?.border_color
        || "var(--re-muted)";
      if (result.state === "warn") {
        card.classList.add("warn");
        badge.innerHTML = '<i class="fa-solid fa-triangle-exclamation" aria-hidden="true"></i>';
        message.innerHTML = '<i class="fa-solid fa-triangle-exclamation" aria-hidden="true"></i>';
        message.append(document.createTextNode(result.error));
        message.dataset.kind = "warn";
      } else if (result.changed) {
        card.classList.add("ed");
        badge.innerHTML = '<i class="fa-solid fa-pen" aria-hidden="true"></i>';
      } else {
        card.classList.add("done");
        badge.innerHTML = '<i class="fa-solid fa-check" aria-hidden="true"></i>';
      }
    }
    if (animateGrade && previousGrade !== grade.textContent && !reducedMotion) {
      grade.classList.remove("bump");
      void grade.offsetWidth;
      grade.classList.add("bump");
    }
    input.setAttribute("aria-invalid", result.state === "bad" ? "true" : "false");
    return result;
  }

  function rowRecords() {
    return cards.map((card, index) => ({ card, input: card.querySelector("[data-score-input]"), index, result: inspect(card) }));
  }

  function updateView() {
    const records = rowRecords();
    let entered = 0;
    let flagged = 0;
    let todo = 0;
    let errors = 0;
    let totalPercent = 0;
    const grades = {};
    for (const record of records) {
      const { card } = record;
      renderCard(card, true);
      const current = inspect(card);
      record.result = current;
      if (current.empty) {
        todo += 1;
        flagged += 1;
      } else if (!current.valid || current.state === "warn") {
        flagged += 1;
        if (!current.valid) errors += 1;
      } else {
        entered += 1;
        const percent = (current.value / maxScore) * 100;
        totalPercent += percent;
        const configuredGrade = gradeFor(percent);
        if (configuredGrade?.grade && configuredGrade.grade !== "-") {
          grades[configuredGrade.grade] = (grades[configuredGrade.grade] || 0) + 1;
        }
      }
    }
    const average = entered ? totalPercent / entered : 0;
    const values = {
      total: records.length,
      entered,
      flagged,
      average: `${average.toFixed(2)}%`,
    };
    for (const [key, value] of Object.entries(values)) {
      const node = document.querySelector(`[data-stat="${key}"]`);
      if (node) node.textContent = value;
    }
    if (statuses.all) statuses.all.textContent = records.length;
    if (statuses.todo) statuses.todo.textContent = todo;
    if (statuses.flag) statuses.flag.textContent = records.filter(({ card }) => ["warn", "bad"].some((state) => card.classList.contains(state))).length;
    for (const record of records) {
      const { card, input } = record;
      const matchesSearch = !searchValue || card.dataset.search.includes(searchValue);
      const state = inspect(card);
      const matchesFilter = activeFilter === "all"
        || (activeFilter === "todo" && state.empty)
        || (activeFilter === "flag" && (state.state === "warn" || state.state === "bad"));
      card.hidden = !(matchesSearch && matchesFilter);
      input.setAttribute("aria-label", `Dhibcaha ${card.querySelector(".re-student-info h2").textContent}`);
    }
    const statAverage = document.querySelector('[data-stat="average"]');
    if (statAverage) statAverage.textContent = `${average.toFixed(2)}%`;
    return { records, entered, flagged, todo, errors, average, grades };
  }

  function animateInitialStats() {
    if (reducedMotion) return;
    document.querySelectorAll("[data-stat][data-target]").forEach((node) => {
      const target = Number(node.dataset.target);
      if (!Number.isFinite(target) || target <= 0) return;
      const start = performance.now();
      const duration = 520;
      function tick(now) {
        const ratio = Math.min(1, (now - start) / duration);
        const value = Math.round(target * (1 - Math.pow(1 - ratio, 3)));
        node.textContent = node.dataset.stat === "average" ? `${value}%` : String(value);
        if (ratio < 1) requestAnimationFrame(tick);
      }
      requestAnimationFrame(tick);
    });
  }

  function setFilter(filter) {
    activeFilter = filter;
    document.querySelectorAll("[data-filter]").forEach((button) => {
      const selected = button.dataset.filter === filter;
      button.classList.toggle("active", selected);
      button.setAttribute("aria-pressed", selected ? "true" : "false");
    });
    updateView();
  }

  function setFiltersOpen(open) {
    const card = document.getElementById("resultScopeFilters");
    const toggle = document.getElementById("filterToggle");
    if (!card || !toggle) return;
    const expanded = window.matchMedia("(min-width: 760px)").matches || open;
    card.classList.toggle("open", expanded);
    toggle.setAttribute("aria-expanded", expanded ? "true" : "false");
    const stateLabel = toggle.querySelector("[data-filter-toggle-state]");
    if (stateLabel) stateLabel.textContent = expanded ? "Qari" : "Fur";
  }

  const scopeFilterForm = document.getElementById("resultScopeFilters");
  if (scopeFilterForm) {
    const applyButton = scopeFilterForm.querySelector('button[type="submit"]');
    const scopeSelects = [...scopeFilterForm.querySelectorAll("select")];
    const initialFilterValues = Object.fromEntries(scopeSelects.map(select => [select.name, select.value]));
    const originalApplyButton = applyButton?.innerHTML || "";
    const filterStatePrefix = `teacher-result-entry:${window.location.pathname}`;
    const filterOpenKey = `${filterStatePrefix}:open`;
    const filterScrollKey = `${filterStatePrefix}:scroll`;
    try {
      restoreFiltersOpen = sessionStorage.getItem(filterOpenKey) === "1";
      if (sessionStorage.getItem(filterScrollKey) !== null) {
        window.addEventListener("pageshow", () => {
          const savedScroll = Number(sessionStorage.getItem(filterScrollKey));
          sessionStorage.removeItem(filterScrollKey);
          sessionStorage.removeItem(filterOpenKey);
          if (Number.isFinite(savedScroll)) requestAnimationFrame(() => window.scrollTo(0, savedScroll));
        }, { once: true });
      }
    } catch (_) {
      restoreFiltersOpen = false;
    }
    let applyingFilters = false;
    let allowFilterSubmit = false;

    const savePendingScoresAndApplyFilters = async (changedFilter = null) => {
      if (applyingFilters) return;
      applyingFilters = true;
      const savedScroll = window.scrollY;
      const currentAssignment = scopeFilterForm.querySelector('[name="assignment_id"]');
      if (changedFilter && currentAssignment) currentAssignment.value = "";
      const activeInputs = inputs.filter((input) => !input.disabled);
      activeInputs.forEach((input) => { input.disabled = true; });
      scopeSelects.forEach((select) => { select.disabled = true; });
      scopeFilterForm.setAttribute("aria-busy", "true");
      if (applyButton) {
        applyButton.disabled = true;
        applyButton.innerHTML = '<i class="fa-solid fa-spinner fa-spin" aria-hidden="true"></i> Waa la raadinayaa…';
      }
      setSaveStatus("saving", "Waa la raadinayaa…", "");
      try {
        for (const input of inputs) clearTimer(input);
        await autosaveQueue;
        for (const input of inputs) {
          if (!inspect(input.closest("[data-student-card]")).changed) continue;
          if (!await saveInput(input)) throw new Error("Dhibco sugaya lama keydin. Mar kale isku day ka hor shaandhaynta.");
        }
        scopeSelects.forEach((select) => { select.disabled = false; });
        scopeFilterForm.removeAttribute("aria-busy");
        try {
          sessionStorage.setItem(filterOpenKey, "1");
          sessionStorage.setItem(filterScrollKey, String(savedScroll));
        } catch (_) {}
        allowFilterSubmit = true;
        scopeFilterForm.requestSubmit();
      } catch (error) {
        if (changedFilter) changedFilter.value = initialFilterValues[changedFilter.name] || "";
        scopeSelects.forEach((select) => { select.disabled = false; });
        activeInputs.forEach((input) => { input.disabled = false; });
        scopeFilterForm.removeAttribute("aria-busy");
        if (applyButton) {
          applyButton.disabled = false;
          applyButton.innerHTML = originalApplyButton;
        }
        applyingFilters = false;
        setSaveStatus("error", "Shaandhaynta lama adeegsan", error.message);
      }
    };

    scopeFilterForm.addEventListener("change", (event) => {
      if (event.target.matches("select")) savePendingScoresAndApplyFilters(event.target);
    });
    scopeFilterForm.addEventListener("submit", (event) => {
      if (allowFilterSubmit) {
        allowFilterSubmit = false;
        return;
      }
      event.preventDefault();
      savePendingScoresAndApplyFilters();
    });
  }

  function setUnsavedStatus() {
    const count = cards.reduce((total, card) => total + (inspect(card).changed ? 1 : 0), 0);
    if (count) setSaveStatus("unsaved", "Aan la keydin", `${count} isbeddel`);
    else setIdleStatus();
  }

  function setIdleStatus() {
    const submissionStatus = statusPanel?.dataset.submissionStatus || "draft";
    const hasSavedScores = cards.some((card) => String(card.dataset.saved || "").trim() !== "");
    if (!savedSinceLoad && ["submitted", "approved"].includes(submissionStatus)) {
      setSaveStatus("submitted", "Hore ayaa loo gudbiyey", "");
    } else if (!savedSinceLoad && submissionStatus === "reopened") {
      setSaveStatus("reopened", "Dib ayaa loo furay", "Sixitaan ayaa loo baahan yahay");
    } else if (hasSavedScores) {
      setSaveStatus("ok", "La keydiyey", "");
    } else {
      setSaveStatus("ready", "Diyaar", "");
    }
  }

  function sanitizeScore(input) {
    const old = input.value;
    let next = old.replace(/,/g, ".").replace(/[^0-9.\-]/g, "");
    const negative = next.startsWith("-");
    next = next.replace(/-/g, "");
    const dot = next.indexOf(".");
    if (dot >= 0) {
      const fraction = next.slice(dot + 1).replace(/\./g, "").slice(0, 2);
      next = `${next.slice(0, dot + 1)}${fraction}`;
    }
    if (negative) next = `-${next}`;
    if (next !== old) {
      const cursor = input.selectionStart;
      input.value = next;
      if (cursor !== null) input.setSelectionRange(Math.min(cursor, next.length), Math.min(cursor, next.length));
    }
  }

  function clearTimer(input) {
    const timer = timers.get(input);
    if (timer) window.clearTimeout(timer);
    timers.delete(input);
  }

  function queueAutosave(input, delay) {
    clearTimer(input);
    const card = input.closest("[data-student-card]");
    const result = inspect(card);
    if (!result.changed || (result.state === "bad" && !result.empty) || page.dataset.locked === "true") {
      setUnsavedStatus();
      return;
    }
    const wait = delay ?? Number(page.dataset.autosaveDelay || 1800);
    timers.set(input, window.setTimeout(() => {
      autosaveQueue = autosaveQueue.then(() => saveInput(input));
    }, wait));
  }

  async function saveInput(input) {
    const card = input.closest("[data-student-card]");
    const inspected = inspect(card);
    if (!inspected.changed) return;
    if ((!inspected.empty && !inspected.valid) || page.dataset.locked === "true") {
      setUnsavedStatus();
      return;
    }
    const valueToSave = input.value.trim();
    const version = Number(input.dataset.requestVersion || "0") + 1;
    input.dataset.requestVersion = String(version);
    setSaveStatus("saving", "Waa la keydinayaa…", "U yara kaadi.......");
    try {
      const response = await fetch(page.dataset.autosaveUrl, {
        method: "POST",
        credentials: "same-origin",
        headers: {
          "Content-Type": "application/json",
          Accept: "application/json",
          "X-CSRFToken": form.querySelector('[name="csrf_token"]').value,
        },
        body: JSON.stringify({
          assignment_id: Number(page.dataset.assignmentId),
          student_id: Number(card.dataset.studentId),
          score: valueToSave,
        }),
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || !data.success) throw new Error(data.message || "Kaydintu ma suurtagelin.");
      const saved = data.score == null ? "" : String(data.score);
      card.dataset.saved = saved;
      savedSinceLoad = true;
      card.dataset.hasDraft = data.has_draft ? "true" : "false";
      card.dataset.markStatus = data.mark_status || "draft";
      renderMarkAction(card, data.action_type || "");
      if (Number(input.dataset.requestVersion) === version && input.value.trim() === valueToSave) input.value = saved;
      renderCard(card, true);
      if (input.value.trim() !== saved) {
        setUnsavedStatus();
        queueAutosave(input, 500);
      } else if (saved) {
        setSaveStatus("ok", "La keydiyey", "");
      } else {
        setIdleStatus();
      }
      updateView();
      return true;
    } catch (error) {
      setSaveStatus("error", "Keydintu ma dhammayn", error.message || "Hubi isku-xirka, kadib mar kale taabo dhibcaha.");
      return false;
    }
  }

  function percentFor(result) {
    return result.valid ? `${((result.value / maxScore) * 100).toFixed(2)}%` : "—";
  }

  function makeReviewRow(record, reviewIndex, asFlag = false) {
    const { card, input, result } = record;
    const row = document.createElement("button");
    row.type = "button";
    row.className = "re-review-row";
    if (result.state === "bad") row.classList.add("bad");
    else if (asFlag || result.state === "warn") row.classList.add("warn");
    row.dataset.jumpStudent = card.dataset.studentId;
    const number = document.createElement("span");
    number.className = "re-review-number";
    number.textContent = String(reviewIndex + 1);
    const copy = document.createElement("span");
    copy.className = "re-review-copy";
    const name = document.createElement("b");
    name.textContent = card.querySelector(".re-student-info h2").textContent;
    const avatar = document.createElement("span");
    avatar.className = "re-review-avatar";
    avatar.setAttribute("aria-hidden", "true");
    const initials = name.textContent.trim().split(/\s+/).slice(0, 2).map((part) => part[0] || "").join("").toUpperCase();
    const photoSource = card.querySelector(".re-avatar img");
    if (photoSource?.src) {
      const photo = document.createElement("img");
      photo.src = photoSource.currentSrc || photoSource.src;
      photo.alt = "";
      photo.loading = "lazy";
      photo.onerror = () => {
        avatar.classList.add("fallback");
        avatar.textContent = initials;
      };
      avatar.append(photo);
    } else {
      avatar.classList.add("fallback");
      avatar.textContent = card.querySelector(".re-avatar b")?.textContent.trim() || initials;
    }
    const detail = document.createElement("small");
    const detailText = result.empty ? "Dhibco lama gelin" : (result.error || `ID: ${card.querySelector(".re-student-id").textContent.replace(/^ID:\s*/, "")}`);
    if (asFlag) {
      const detailIcon = document.createElement("i");
      detailIcon.className = `re-review-detail-icon fa-solid ${result.empty ? "fa-circle-exclamation" : result.valid ? "fa-circle-info" : "fa-triangle-exclamation"}`;
      detailIcon.setAttribute("aria-hidden", "true");
      detail.append(detailIcon, document.createTextNode(detailText));
    } else if (!result.empty && !result.error) {
      const idIcon = document.createElement("i");
      idIcon.className = "re-review-id-icon fa-solid fa-id-card";
      idIcon.setAttribute("aria-hidden", "true");
      detail.append(idIcon, document.createTextNode(detailText));
    } else {
      detail.textContent = detailText;
    }
    const value = document.createElement("span");
    value.className = "re-review-value";
    value.textContent = result.valid ? input.value : (result.empty ? "—" : input.value);
    const percent = document.createElement("small");
    percent.textContent = percentFor(result);
    value.append(percent);
    copy.append(name, detail);
    row.append(number, avatar, copy, value);
    if (!asFlag) row.setAttribute("aria-label", `${name.textContent}, ${result.empty ? "dhibco ma leh" : input.value}`);
    return row;
  }

  function renderReview() {
    const summary = updateView();
    const allRows = summary.records;
    const invalidCount = allRows.filter(({ result }) => !result.empty && !result.valid).length;
    const pendingCount = allRows.filter(({ result, card }) => result.valid && card.dataset.locked !== "true" && (card.dataset.hasDraft === "true" || result.changed)).length;
    const enteredNode = document.querySelector("[data-review-entered]");
    const flaggedNode = document.querySelector("[data-review-flagged]");
    const averageNode = document.querySelector("[data-review-average]");
    const block = document.querySelector("[data-submit-block]");
    const partialNote = document.querySelector("[data-partial-note]");
    const flagSection = document.querySelector("[data-flag-section]");
    const flagTitle = document.querySelector("[data-flag-title]");
    const flagCount = document.querySelector("[data-flag-count]");
    const flagList = document.querySelector("[data-flag-list]");
    const allList = document.querySelector("[data-all-list]");
    const allCount = document.querySelector("[data-all-count]");
    const distribution = document.querySelector("[data-grade-distribution]");
    const confirm = document.getElementById("reviewConfirm");
    const submit = document.getElementById("confirmSubmit");
    enteredNode.textContent = `${summary.entered}/${allRows.length}`;
    flaggedNode.textContent = String(summary.flagged);
    averageNode.textContent = `${summary.average.toFixed(2)}%`;
    block.hidden = invalidCount === 0;
    partialNote.hidden = invalidCount > 0 || page.dataset.locked === "true";
    const flagged = allRows.filter(({ result }) => result.empty || !result.valid || result.state === "warn");
    flagSection.hidden = flagged.length === 0;
    flagTitle.textContent = "U baahan in la hubiyo";
    flagCount.textContent = String(flagged.length);
    flagList.replaceChildren(...flagged.map((record, index) => makeReviewRow(record, index, true)));
    allList.replaceChildren(...allRows.map((record, index) => makeReviewRow(record, index)));
    allCount.textContent = `${allRows.length} arday`;
    distribution.replaceChildren();
    const order = [...new Set(["exam", "global", "fallback"].flatMap((bucket) => (
      (gradeScales[bucket] || []).map((scale) => scale.grade).filter(Boolean)
    )))];
    order.filter((grade) => summary.grades[grade]).forEach((grade) => {
      const chip = document.createElement("span");
      chip.className = "re-grade-chip";
      chip.textContent = `${grade} · ${summary.grades[grade]}`;
      distribution.append(chip);
    });
    confirm.checked = false;
    submit.disabled = page.dataset.locked === "true" || invalidCount > 0 || pendingCount === 0;
    submit.dataset.invalidCount = String(invalidCount);
    submit.dataset.pendingCount = String(pendingCount);
  }

  function openModal() {
    const modal = document.getElementById("reviewModal");
    if (!modal) return;
    renderReview();
    lastFocus = document.activeElement;
    modal.classList.add("open");
    modal.setAttribute("aria-hidden", "false");
    document.body.classList.add("re-modal-open");
    modal.querySelector(".re-sheet").focus();
  }

  function closeModal() {
    const modal = document.getElementById("reviewModal");
    if (!modal) return;
    modal.classList.remove("open");
    modal.setAttribute("aria-hidden", "true");
    document.body.classList.remove("re-modal-open");
    if (lastFocus && typeof lastFocus.focus === "function") lastFocus.focus();
  }

  function jumpToStudent(studentId) {
    closeModal();
    searchValue = "";
    const search = document.getElementById("studentSearch");
    if (search) search.value = "";
    setFilter("all");
    const card = cards.find((item) => item.dataset.studentId === studentId);
    const input = card?.querySelector("[data-score-input]");
    if (card && input) {
      if (reviewHighlightTimer) window.clearTimeout(reviewHighlightTimer);
      reviewHighlightedCard?.classList.remove("review-target-highlight");
      reviewHighlightedCard = card;
      card.classList.remove("review-target-highlight");
      void card.offsetWidth;
      card.classList.add("review-target-highlight");
      reviewHighlightTimer = window.setTimeout(() => {
        card.classList.remove("review-target-highlight");
        if (reviewHighlightedCard === card) reviewHighlightedCard = null;
        reviewHighlightTimer = null;
      }, 60_000);
      card.scrollIntoView({ behavior: reducedMotion ? "auto" : "smooth", block: "center" });
      window.setTimeout(() => input.focus({ preventScroll: true }), 180);
    }
  }

  async function submitResults() {
    const confirm = document.getElementById("reviewConfirm");
    const submitButton = document.getElementById("confirmSubmit");
    if (submitInProgress || !confirm.checked || submitButton.disabled) return;
    submitInProgress = true;
    submitButton.disabled = true;
    setSaveStatus("saving", "Waa la gudbinayaa…", "U yara kaadi.......");
    try {
      await autosaveQueue;
      for (const input of inputs) {
        clearTimer(input);
        if (!inspect(input.closest("[data-student-card]")).changed) continue;
        const saved = await saveInput(input);
        if (!saved) throw new Error("Qaar ka mid ah dhibcaha lama keydin. Sax fariinta muuqata oo mar kale isku day.");
      }
      const data = new FormData(form);
      data.set("action", "submit");
      const response = await fetch(form.dataset.submitUrl, {
        method: "POST",
        body: data,
        credentials: "same-origin",
        headers: { Accept: "application/json", "X-Requested-With": "XMLHttpRequest" },
      });
      const result = await response.json().catch(() => ({}));
      if (!response.ok || !result.success) throw new Error(result.message || "Gudbintu ma suurtagelin.");
      document.querySelector(".re-success").classList.add("show");
      document.querySelector(".re-success").setAttribute("aria-hidden", "false");
      setSaveStatus("ok", "Waa la gudbiyey", "Natiijada waa la xareeyey");
      window.setTimeout(() => { window.location.assign(result.redirect_url); }, 1900);
    } catch (error) {
      setSaveStatus("error", "Gudbintu way fashilantay", error.message || "Dib u eeg xogta, kadib mar kale isku day.");
      submitInProgress = false;
      submitButton.disabled = false;
    }
  }

  function nextInput(input) {
    const current = inputs.indexOf(input);
    const next = inputs.slice(current + 1).find((candidate) => !candidate.disabled && !candidate.closest("[data-student-card]").hidden);
    if (next) next.focus({ preventScroll: true });
    else input.blur();
  }

  function dockAboveKeyboard() {
    const dock = document.getElementById("resultDock");
    const viewport = window.visualViewport;
    if (!dock || !viewport) return;
    const keyboardHeight = window.innerHeight - (viewport.height + viewport.offsetTop);
    dock.style.bottom = keyboardHeight > 60 ? `calc(${Math.round(keyboardHeight + 10)}px + env(safe-area-inset-bottom, 0px))` : "";
    if (document.activeElement?.matches("[data-score-input]")) scrollActiveCardIntoView();
  }

  function scrollActiveCardIntoView() {
    const input = document.activeElement?.matches("[data-score-input]") ? document.activeElement : null;
    const card = input?.closest("[data-student-card]");
    const dock = document.getElementById("resultDock");
    const viewport = window.visualViewport;
    if (!card || !dock) return;
    const viewportTop = viewport?.offsetTop || 0;
    const viewportBottom = viewportTop + (viewport?.height || window.innerHeight) - dock.getBoundingClientRect().height - 16;
    const rect = card.getBoundingClientRect();
    if (rect.bottom > viewportBottom) window.scrollBy({ top: rect.bottom - viewportBottom + 10, behavior: "smooth" });
    else if (rect.top < viewportTop + 8) window.scrollBy({ top: rect.top - viewportTop - 8, behavior: "smooth" });
  }

  const filterToggle = document.getElementById("filterToggle");
  filterToggle?.addEventListener("click", () => {
    if (window.matchMedia("(min-width: 760px)").matches) return;
    setFiltersOpen(filterToggle.getAttribute("aria-expanded") !== "true");
  });
  window.matchMedia("(min-width: 760px)").addEventListener?.("change", (event) => setFiltersOpen(event.matches));
  setFiltersOpen(restoreFiltersOpen);

  document.getElementById("studentSearch")?.addEventListener("input", (event) => {
    searchValue = event.target.value.trim().toLocaleLowerCase();
    updateView();
  });
  document.querySelectorAll("[data-filter]").forEach((button) => button.addEventListener("click", () => setFilter(button.dataset.filter)));

  for (const [index, card] of cards.entries()) {
    const input = card.querySelector("[data-score-input]");
    const next = card.querySelector("[data-next]");
    card.classList.add("enter");
    card.style.setProperty("--stagger", `${Math.min(index * 18, 360)}ms`);
    input.addEventListener("input", () => {
      sanitizeScore(input);
      renderCard(card, true);
      updateView();
      setUnsavedStatus();
      queueAutosave(input);
    });
    input.addEventListener("focus", () => {
      cards.forEach((item) => item.classList.toggle("active", item === card));
      page.querySelector(".re-student-list")?.classList.add("has-active");
      page.classList.add("keyboard-active");
      window.setTimeout(scrollActiveCardIntoView, 80);
      window.setTimeout(scrollActiveCardIntoView, 350);
      window.setTimeout(scrollActiveCardIntoView, 600);
    });
    input.addEventListener("blur", () => {
      queueAutosave(input, 500);
      window.setTimeout(() => {
        if (document.activeElement?.matches("[data-score-input]")) return;
        cards.forEach((item) => item.classList.remove("active"));
        page.querySelector(".re-student-list")?.classList.remove("has-active");
        page.classList.remove("keyboard-active");
        const dock = document.getElementById("resultDock");
        if (dock) dock.style.bottom = "";
      }, 130);
    });
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        nextInput(input);
      }
    });
    next?.addEventListener("mousedown", (event) => event.preventDefault());
    next?.addEventListener("click", () => nextInput(input));
  }

  if (window.visualViewport) {
    window.visualViewport.addEventListener("resize", dockAboveKeyboard);
    window.visualViewport.addEventListener("scroll", dockAboveKeyboard);
  }
  window.addEventListener("resize", dockAboveKeyboard);

  const modal = document.getElementById("reviewModal");
  document.getElementById("openReview")?.addEventListener("click", openModal);
  document.getElementById("closeReview")?.addEventListener("click", closeModal);
  document.getElementById("backToEditing")?.addEventListener("click", closeModal);
  modal?.addEventListener("click", (event) => { if (event.target === modal) closeModal(); });
  modal?.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeModal();
    if (event.key !== "Tab") return;
    const focusable = [...modal.querySelectorAll("button:not([disabled]), input:not([disabled]), [tabindex='0']")];
    if (!focusable.length) return;
    if (event.shiftKey && document.activeElement === focusable[0]) {
      event.preventDefault();
      focusable.at(-1).focus();
    } else if (!event.shiftKey && document.activeElement === focusable.at(-1)) {
      event.preventDefault();
      focusable[0].focus();
    }
  });
  modal?.addEventListener("click", (event) => {
    const row = event.target.closest("[data-jump-student]");
    if (row) jumpToStudent(row.dataset.jumpStudent);
  });
  document.getElementById("reviewConfirm")?.addEventListener("change", (event) => {
    const button = document.getElementById("confirmSubmit");
    button.disabled = !event.currentTarget.checked || Number(button.dataset.invalidCount) > 0 || Number(button.dataset.pendingCount) === 0 || page.dataset.locked === "true";
  });
  document.getElementById("confirmSubmit")?.addEventListener("click", submitResults);
  form?.addEventListener("submit", (event) => {
    event.preventDefault();
    openModal();
  });

  setIdleStatus();
  initDeadlineCountdown();
  updateView();
  animateInitialStats();
})();
