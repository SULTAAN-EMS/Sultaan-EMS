(() => {
  const yearSelect = document.getElementById('idcYear');
  const examSelect = document.getElementById('idcExam');
  const levelSelect = document.getElementById('idcLevel');
  const classSelect = document.getElementById('idcClass');

  const filterExamOptions = () => {
    if (!yearSelect || !examSelect) return;
    const yearId = yearSelect.value;
    Array.from(examSelect.options).forEach((option) => {
      if (!option.value) return;
      const matchesYear = !yearId || option.dataset.yearId === yearId;
      option.hidden = !matchesYear;
      option.disabled = !matchesYear;
      if (!matchesYear && option.selected) examSelect.value = '';
    });
  };

  if (yearSelect && examSelect) {
    filterExamOptions();
    yearSelect.addEventListener('change', filterExamOptions);
    examSelect.addEventListener('change', () => {
      const option = examSelect.selectedOptions[0];
      if (option?.dataset.yearId && yearSelect.value !== option.dataset.yearId) {
        yearSelect.value = option.dataset.yearId;
        filterExamOptions();
      }
    });
  }

  const filterClassesByLevel = () => {
    if (!levelSelect || !classSelect) return;
    const level = levelSelect.value;
    Array.from(classSelect.options).forEach((option) => {
      if (!option.value) return;
      const matchesLevel = !level || option.dataset.level === level;
      option.hidden = !matchesLevel;
      option.disabled = !matchesLevel;
      if (!matchesLevel && option.selected) classSelect.value = '';
    });
  };

  if (levelSelect && classSelect) {
    filterClassesByLevel();
    levelSelect.addEventListener('change', filterClassesByLevel);
  }

  const validityInput = document.getElementById('idcValidityMonths');
  const validitySummary = document.getElementById('idcValiditySummary');
  if (validityInput) {
    const stepButtons = Array.from(document.querySelectorAll('[data-month-step]'));
    const presetButtons = Array.from(document.querySelectorAll('[data-validity-preset]'));
    const updateValidityControls = () => {
      const value = Number(validityInput.value);
      const valid = Number.isInteger(value) && value >= 1 && value <= 120;
      stepButtons.forEach((button) => {
        const step = Number(button.dataset.monthStep);
        button.disabled = !valid || (step < 0 && value <= 1) || (step > 0 && value >= 120);
      });
      presetButtons.forEach((button) => {
        button.setAttribute('aria-pressed', String(valid && value === Number(button.dataset.validityPreset)));
      });
      if (validitySummary && valid) validitySummary.textContent = `${value} bilood`;
    };

    stepButtons.forEach((button) => button.addEventListener('click', () => {
      const current = Number(validityInput.value) || 1;
      const step = Number(button.dataset.monthStep);
      validityInput.value = String(Math.max(1, Math.min(120, current + step)));
      validityInput.dispatchEvent(new Event('input', { bubbles: true }));
    }));
    presetButtons.forEach((button) => button.addEventListener('click', () => {
      validityInput.value = button.dataset.validityPreset;
      validityInput.dispatchEvent(new Event('input', { bubbles: true }));
    }));
    validityInput.addEventListener('input', updateValidityControls);
    updateValidityControls();
  }

  const form = document.getElementById('idcBulkForm');
  const selectAll = document.getElementById('idcSelectAll');
  const selectedCount = document.getElementById('idcSelectedCount');
  if (!form || !selectAll || !selectedCount) return;

  const checkboxes = Array.from(form.querySelectorAll('input[name="student_ids"]'));
  const updateSelectedCount = () => {
    const selected = checkboxes.filter((checkbox) => checkbox.checked).length;
    selectedCount.textContent = `${selected} la xushay`;
    selectAll.checked = checkboxes.length > 0 && selected === checkboxes.length;
    selectAll.indeterminate = selected > 0 && selected < checkboxes.length;
  };

  selectAll.addEventListener('change', () => {
    checkboxes.forEach((checkbox) => { checkbox.checked = selectAll.checked; });
    updateSelectedCount();
  });
  checkboxes.forEach((checkbox) => checkbox.addEventListener('change', updateSelectedCount));
  updateSelectedCount();
})();
