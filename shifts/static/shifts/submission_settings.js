(() => {
  const form = document.querySelector('[data-default-time-settings]');
  if (!form) return;
  const fields = form.querySelector('[data-default-time-fields]');
  if (!fields) return;
  const choices = Array.from(form.querySelectorAll('input[name="submission_mode"]'));
  function updateVisibility(keepErrors) {
    const dayOff = choices.some(choice => choice.checked && choice.value === 'day_off');
    fields.hidden = !dayOff && !(keepErrors && fields.dataset.hasErrors === 'true');
  }
  choices.forEach(choice => choice.addEventListener('change', () => updateVisibility(false)));
  // Keep values when switching modes, so the saved basic time is not lost.
  updateVisibility(true);
})();
