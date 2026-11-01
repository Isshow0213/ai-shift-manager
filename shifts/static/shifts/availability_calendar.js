(() => {
  const calendar = document.getElementById('availability-calendar');
  if (!calendar) return;

  const dialog = document.getElementById('availability-calendar-editor');
  const shouldOpen = dialog && dialog.dataset.open === 'true';
  const startsAtCalendar = window.location.hash === '#availability-calendar' || shouldOpen;

  function alignCalendar() {
    calendar.scrollIntoView({ behavior: 'auto', block: 'start' });
  }

  // Keep every existing section in place; only the viewport starts at the calendar.
  if (startsAtCalendar) alignCalendar();
  if (!dialog || typeof dialog.showModal !== 'function') return;

  const form = dialog.querySelector('form');
  const selectedDay = calendar.querySelector('.calendar-day[aria-current="date"]');
  const submitButton = form.querySelector('[type="submit"]');

  dialog.querySelectorAll('[data-calendar-editor-close]').forEach(button => {
    button.hidden = false;
    button.addEventListener('click', () => dialog.close());
  });
  const cancel = dialog.querySelector('[data-calendar-editor-cancel]');
  if (cancel) cancel.addEventListener('click', event => {
    event.preventDefault();
    dialog.close();
  });

  dialog.addEventListener('close', () => {
    const url = new URL(window.location.href);
    url.searchParams.delete('edit');
    window.history.replaceState(window.history.state, '', url);
    if (selectedDay) selectedDay.focus({ preventScroll: true });
  });
  form.addEventListener('submit', () => {
    if (submitButton) submitButton.disabled = true;
  });

  if (shouldOpen) {
    dialog.showModal();
    const focusTarget = form.querySelector('[aria-invalid="true"]')
      || form.querySelector('input[name="start_time"]:not(:disabled)')
      || form.querySelector('input[name="day_status"]:checked')
      || cancel;
    if (focusTarget) focusTarget.focus({ preventScroll: true });
  }

  window.addEventListener('pageshow', event => {
    if (event.persisted && startsAtCalendar && !dialog.open) alignCalendar();
    if (submitButton) submitButton.disabled = false;
  });
})();
