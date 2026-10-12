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
