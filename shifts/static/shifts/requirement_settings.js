(() => {
  const form = document.getElementById('requirement-settings-form');
  if (!form) return;

  const modeInputs = [...form.querySelectorAll('[name="schedule_mode"]')];
  const modeSections = [...form.querySelectorAll('[data-requirement-mode]')];
  const submitLabel = form.querySelector('[data-requirement-submit-label]');
  const feedback = document.getElementById('requirement-preset-feedback');
  const selectedMode = () => {
    const selected = modeInputs.find(input => input.type !== 'radio' || input.checked);
    return selected?.value === 'month' ? 'month' : 'date';
  };
  const bulkLink = document.querySelector('[data-requirement-bulk-link]');
  const bulkHref = bulkLink?.getAttribute('href');
  const updateBulkMonth = () => {
    if (!bulkLink) return;
    const fieldName = selectedMode() === 'month' ? 'target_month' : 'work_date';
    const match = form.elements.namedItem(fieldName)?.value.match(/^(\d{4})-(\d{2})(?:-\d{2})?$/);
    const url = new URL(bulkHref, window.location.href);
    if (match && Number(match[1]) >= 1949 && Number(match[1]) <= 2099 && Number(match[2]) >= 1 && Number(match[2]) <= 12) {
      url.searchParams.set('year', String(Number(match[1])));
      url.searchParams.set('month', String(Number(match[2])));
    }
    bulkLink.setAttribute('href', url.pathname + url.search + url.hash);
  };
  const updateMode = () => {
    const mode = selectedMode();
    modeSections.forEach(section => {
      section.hidden = section.dataset.requirementMode !== mode;
      section.querySelectorAll('input, select, textarea').forEach(input => {
        input.disabled = section.hidden;
        input.required = !section.hidden;
      });
    });
    if (submitLabel) submitLabel.textContent = mode === 'month' ? 'この月の該当日に保存' : 'この日に保存';
    updateBulkMonth();
  };
  modeInputs.forEach(input => input.addEventListener('change', updateMode));
  ['target_month', 'work_date'].forEach(name => {
    const input = form.elements.namedItem(name);
    input?.addEventListener('input', updateBulkMonth);
    input?.addEventListener('change', updateBulkMonth);
  });

  document.querySelectorAll('[data-requirement-preset]').forEach(button => {
    button.addEventListener('click', () => {
      const values = {
        start_time: button.dataset.startTime,
        end_time: button.dataset.endTime,
        required_staff_count: button.dataset.requiredStaffCount,
        memo: button.dataset.memo || '',
      };
      Object.entries(values).forEach(([name, value]) => {
        const input = form.elements.namedItem(name);
        if (!input || value === undefined) return;
        input.value = value;
        input.dispatchEvent(new Event('input', { bubbles: true }));
        input.dispatchEvent(new Event('change', { bubbles: true }));
      });
      if (feedback) feedback.textContent = `${values.start_time}〜${values.end_time}の時間帯を入力しました。内容を確認して保存してください。`;
      const reducedMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
      form.scrollIntoView({ behavior: reducedMotion ? 'auto' : 'smooth', block: 'start' });
      form.elements.namedItem('start_time')?.focus({ preventScroll: true });
    });
  });
  updateMode();
})();
