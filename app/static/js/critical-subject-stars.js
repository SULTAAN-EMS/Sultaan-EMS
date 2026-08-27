(function () {
  "use strict";

  const modal = document.getElementById("criticalStarModal");
  if (!modal) return;
  const subject = modal.querySelector("[data-critical-modal-subject]");
  const score = modal.querySelector("[data-critical-modal-score]");
  const minimum = modal.querySelector("[data-critical-modal-minimum-value]");
  const reason = modal.querySelector("[data-critical-modal-reason]");
  const modalIcon = modal.querySelector("[data-critical-modal-icon]");
  const modalHead = modal.querySelector(".critical-star-modal__head");
  const statObtained = modal.querySelector("[data-critical-modal-obtained]");
  const statMinimum = modal.querySelector("[data-critical-modal-minimum]");
  const whyWrap = modal.querySelector(".critical-star-modal__why-wrap");
  const whyIcon = modal.querySelector(".critical-star-modal__why-icon");
  const closeButton = modal.querySelector("[data-critical-modal-close]");
  let lastTrigger = null;

  function close() {
    modal.hidden = true;
    modal.setAttribute("aria-hidden", "true");
    lastTrigger?.focus();
  }

  function open(trigger) {
    lastTrigger = trigger;
    const subjectName = trigger.dataset.subject || "Maaddo";
    const numericScore = Number(trigger.dataset.score);
    const numericMax = Number(trigger.dataset.maxScore);
    const threshold = Number(trigger.dataset.minimumPercentage);
    const design = trigger.className.match(/critical-star-badge--(emerald|gold|royalblue|magenta)/)?.[1] || "royalblue";
    const theme = {
      emerald: { gradient: "linear-gradient(120deg,#10B981,#065F46)", c1: "#10B981", c2: "#065F46", c3: "#6EE7B7", tint: "#ECFDF5", icon: "#D1FAE5", stroke: "#065F46", iconSvg: '<path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H20v15H6.5A2.5 2.5 0 0 0 4 20.5v-15Z"/><path d="M4 20.5A2.5 2.5 0 0 1 6.5 18H20"/>' },
      gold: { gradient: "linear-gradient(120deg,#CD853F,#6B3E0F)", c1: "#CD853F", c2: "#6B3E0F", c3: "#E8B584", tint: "#F8ECDD", icon: "#F3E3D0", stroke: "#5C3A1E", iconSvg: '<path d="M8 4h8v3a4 4 0 0 1-8 0V4Z"/><path d="M6 5H4v2a3 3 0 0 0 3 3M18 5h2v2a3 3 0 0 1-3 3"/><path d="M10 11v3m4-3v3M9 17h6l1 3H8l1-3Z"/>' },
      royalblue: { gradient: "linear-gradient(120deg,#3B82F6,#1E3A8A)", c1: "#2563EB", c2: "#1E3A8A", c3: "#60A5FA", tint: "#EAF2FF", icon: "#DDEBFF", stroke: "#1E3A8A", iconSvg: '<path d="M12 2 2.5 6.5 12 11l9.5-4.5L12 2Z"/><path d="M5 9v6.5L12 19l7-3.5V9"/><path d="M7.5 13.5 12 16l4.5-2.5"/>' },
      magenta: { gradient: "linear-gradient(120deg,#D946EF,#86198F)", c1: "#D946EF", c2: "#86198F", c3: "#F0ABFC", tint: "#FDF4FF", icon: "#FAE8FF", stroke: "#86198F", iconSvg: '<path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H20v15H6.5A2.5 2.5 0 0 0 4 20.5v-15Z"/><path d="M4 20.5A2.5 2.5 0 0 1 6.5 18H20"/>' }
    }[design] || {
      gradient: "linear-gradient(120deg,#3B82F6,#1E3A8A)", c1: "#2563EB", c2: "#1E3A8A", c3: "#60A5FA", tint: "#EAF2FF", icon: "#DDEBFF", stroke: "#1E3A8A", iconSvg: '<path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H20v15H6.5A2.5 2.5 0 0 0 4 20.5v-15Z"/><path d="M4 20.5A2.5 2.5 0 0 1 6.5 18H20"/>'
    };
    modalHead.style.background = theme.gradient;
    modalIcon.innerHTML = `<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="#fff" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">${theme.iconSvg}</svg>`;
    statObtained.style.setProperty("--critical-modal-stat", theme.gradient);
    statMinimum.style.setProperty("--critical-modal-stat", "linear-gradient(135deg,#FF6B6B,#C81E3C)");
    whyWrap.style.setProperty("--critical-why-c1", theme.c1);
    whyWrap.style.setProperty("--critical-why-c2", theme.c2);
    whyWrap.style.setProperty("--critical-why-c3", theme.c3);
    whyWrap.style.setProperty("--critical-why-tint", theme.tint);
    whyIcon.style.setProperty("--critical-why-icon", theme.icon);
    whyIcon.style.setProperty("--critical-why-stroke", theme.stroke);
    subject.textContent = subjectName;
    score.textContent = Number.isFinite(numericScore)
      ? `${numericScore}`
      : "Lama helin";
    minimum.textContent = Number.isFinite(threshold) ? `${threshold}%` : "-";
    reason.textContent = trigger.dataset.reason || "Maaddadani waxa ay ka mid tahay maadooyinka ay qasab tahay in uu ardeygu ku gudbo.";
    modal.hidden = false;
    modal.setAttribute("aria-hidden", "false");
    closeButton?.focus();
  }

  document.addEventListener("click", (event) => {
    const trigger = event.target.closest("[data-critical-star]");
    if (trigger) open(trigger);
    if (event.target === modal || event.target.closest("[data-critical-modal-close]")) close();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !modal.hidden) close();
  });
}());
