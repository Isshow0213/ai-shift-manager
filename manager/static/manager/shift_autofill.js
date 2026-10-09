(() => {
  function initialise() {
    const form = document.querySelector('form.manual-shift-form');
    if (!form) return;

    const source = document.getElementById(form.dataset.availabilitySource || '');
    if (!source) return;

    let data;
    try {
      data = JSON.parse(source.textContent);
    } catch {
      return;
    }
    if (!data || typeof data.date !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(data.date)) return;
    if (!data.memberships || typeof data.memberships !== 'object' || Array.isArray(data.memberships)) return;
    const parsedDate = new Date(`${data.date}T00:00:00Z`);
    if (!Number.isFinite(parsedDate.getTime()) || parsedDate.toISOString().slice(0, 10) !== data.date) return;

    const membership = form.querySelector('[name="membership"]');
    const workDate = form.querySelector('[name="work_date"]');
    const startTime = form.querySelector('[name="start_time"]');
    const endTime = form.querySelector('[name="end_time"]');
    const slot = document.getElementById('manual-availability-slot');
    const slotField = document.getElementById('manual-availability-field');
    if (!membership || !workDate || !startTime || !endTime || !slot || !slotField) return;

    const timePattern = /^(?:[01]\d|2[0-3]):[0-5]\d$/;
    let currentSlots = [];

    function appendOption(value, label) {
      const option = document.createElement('option');
      option.value = value;
      option.textContent = label;
      slot.appendChild(option);
    }

    function fillAvailability(availability) {
      workDate.value = data.date;
      startTime.value = availability ? availability.start_time : '';
      endTime.value = availability ? availability.end_time : '';
    }

    function updateSlots(shouldFill) {
      const availabilities = data.memberships[membership.value];
      currentSlots = Array.isArray(availabilities) ? availabilities.filter((availability) => (
        availability && /^\d+$/.test(String(availability.id))
        && timePattern.test(availability.start_time)
        && timePattern.test(availability.end_time)
      )) : [];

      slot.replaceChildren();
      slotField.hidden = currentSlots.length <= 1;
      if (currentSlots.length > 1) {
        const matched = currentSlots.find((availability) => (
          availability.start_time === startTime.value.slice(0, 5)
          && availability.end_time === endTime.value.slice(0, 5)
        ));
        if (!shouldFill && !matched) appendOption('', '希望時間帯を選択');
        currentSlots.forEach((availability) => {
          appendOption(String(availability.id), `${availability.start_time}〜${availability.end_time}`);
        });
        slot.value = shouldFill ? String(currentSlots[0].id) : (matched ? String(matched.id) : '');
      }
      if (shouldFill) fillAvailability(currentSlots[0]);
    }

    membership.addEventListener('change', () => updateSlots(true));
    slot.addEventListener('change', () => {
      const availability = currentSlots.find((item) => String(item.id) === slot.value);
      if (availability) fillAvailability(availability);
    });

    // Failed POSTs retain the user's inputs until they choose a staff member or slot.
    updateSlots(form.dataset.isBound === 'false');
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initialise, { once: true });
  } else {
    initialise();
  }
})();
