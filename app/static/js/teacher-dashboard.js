(function () {
  function parseDataset(canvas, key) {
    try {
      return JSON.parse(canvas.dataset[key] || "[]");
    } catch (error) {
      return [];
    }
  }

  function chartTypeForId(id) {
    if (id.includes("Pie") || id === "dashboardPassChart") return "doughnut";
    if (id.includes("Bar") || id === "comparisonChart" || id.startsWith("subject")) return "bar";
    if (id.includes("Area")) return "line";
    return "line";
  }

  function buildChart(canvas) {
    if (!window.Chart) return;
    const labels = parseDataset(canvas, "labels");
    const values = parseDataset(canvas, "values");
    if (!labels.length) return;

    const type = chartTypeForId(canvas.id);
    const palette = ["#002060", "#007bff", "#16a34a", "#f59e0b", "#7c3aed", "#0ea5e9", "#ef4444"];
    const dataset = {
      label: "Performance",
      data: values,
      borderColor: "#002060",
      backgroundColor: type === "doughnut" || type === "pie"
        ? palette
        : type === "bar"
          ? "rgba(0, 123, 255, 0.72)"
          : "rgba(0, 123, 255, 0.18)",
      borderWidth: 2,
      tension: 0.35,
      fill: canvas.id.includes("Area"),
      borderRadius: type === "bar" ? 8 : 0,
    };

    new Chart(canvas, {
      type,
      data: { labels, datasets: [dataset] },
      options: {
        responsive: true,
        maintainAspectRatio: true,
        plugins: {
          legend: {
            display: type === "doughnut" || type === "pie",
            position: "bottom",
          },
        },
        scales: type === "doughnut" || type === "pie"
          ? {}
          : {
              y: {
                beginAtZero: true,
                suggestedMax: 100,
              },
            },
      },
    });
  }

  function animateCounters() {
    document.querySelectorAll(".td-counter[data-target]").forEach((node) => {
      const target = parseFloat(node.dataset.target);
      if (Number.isNaN(target)) return;
      const suffix = node.textContent.includes("%") ? "%" : "";
      const duration = 900;
      const start = performance.now();

      function tick(now) {
        const progress = Math.min((now - start) / duration, 1);
        const value = target * progress;
        node.textContent = suffix ? `${value.toFixed(1)}%` : Math.round(value).toString();
        if (progress < 1) requestAnimationFrame(tick);
      }

      requestAnimationFrame(tick);
    });
  }

  function initThemeToggle() {
    const toggle = document.getElementById("themeToggle");
    const body = document.body;
    if (!toggle || !body) return;

    if (body.classList.contains("teacher-results-body")) {
      let storedTheme = "light";
      try {
        storedTheme = localStorage.getItem("teacher-result-entry-theme") === "dark" ? "dark" : "light";
      } catch (error) {
        storedTheme = "light";
      }
      const applyTheme = (theme) => {
        const isDark = theme === "dark";
        body.classList.toggle("theme-dark", isDark);
        body.classList.toggle("theme-light", !isDark);
        document.documentElement.dataset.theme = isDark ? "dark" : "light";
        toggle.innerHTML = isDark
          ? '<i class="fa-solid fa-sun" aria-hidden="true"></i>'
          : '<i class="fa-regular fa-moon" aria-hidden="true"></i>';
        toggle.setAttribute("aria-label", isDark ? "U beddel muuqaalka iftiinka" : "U beddel muuqaalka madow");
      };
      applyTheme(storedTheme);
      toggle.addEventListener("click", () => {
        const nextTheme = body.classList.contains("theme-dark") ? "light" : "dark";
        applyTheme(nextTheme);
        try {
          localStorage.setItem("teacher-result-entry-theme", nextTheme);
        } catch (error) {
          // Keep the selected theme for this page even if storage is unavailable.
        }
      });
      return;
    }

    toggle.addEventListener("click", () => {
      const isDark = body.classList.contains("theme-dark");
      body.classList.toggle("theme-dark", !isDark);
      body.classList.toggle("theme-light", isDark);
      toggle.innerHTML = isDark
        ? '<i class="fa-solid fa-moon"></i>'
        : '<i class="fa-solid fa-sun"></i>';
    });
  }

  document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll("canvas[data-labels][data-values]").forEach(buildChart);
    animateCounters();
    initThemeToggle();
  });
})();
