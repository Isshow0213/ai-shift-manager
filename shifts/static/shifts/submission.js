(() => {
  const form = document.getElementById('availability-form');
  if (!form) return;

  const choices = [...form.querySelectorAll('input[name="day_status"]')];
  if (!choices.length) return;
  const timeSections = [...form.querySelectorAll('[data-submission-times]')];
  const times = [...form.querySelectorAll('input[name="start_time"], input[name="end_time"]')];
  function updateTimeFields() {
    const timeLimited = choices.some((choice) => choice.checked && choice.value === 'available');
    timeSections.forEach((section) => { section.hidden = !timeLimited; });
    times.forEach((input) => {
      input.required = timeLimited;
      input.disabled = !timeLimited;
    });
  }
  choices.forEach((choice) => choice.addEventListener('change', updateTimeFields));
  updateTimeFields();
})();
