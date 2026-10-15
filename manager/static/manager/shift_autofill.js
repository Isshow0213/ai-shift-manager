(() => {
  function initialise() {
    document.querySelectorAll('form[data-availability-source]').forEach(initialiseForm);
  }

  function initialiseForm(form) {

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

    const membership = form.querySelector('[name="membership"], [name$="-membership"]');
    const workDate = form.querySelector('[name="work_date"], [name$="-work_date"]');
    const startTime = form.querySelector('[name="start_time"], [name$="-start_time"]');
    const endTime = form.querySelector('[name="end_time"], [name$="-end_time"]');
    const slot = form.querySelector('[data-availability-slot]');
    const slotField = form.querySelector('[data-availability-slot-field]');
    const summary = form.querySelector('[data-availability-summary]');
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
        availability
        && timePattern.test(availability.start_time)
        && timePattern.test(availability.end_time)
        && availability.start_time < availability.end_time
      )).map((availability, index) => ({ ...availability, slotKey: String(index) })) : [];
      const allDay = Array.isArray(data.all_day_membership_ids)
        && data.all_day_membership_ids.map(String).includes(membership.value);
      if (summary) {
        summary.textContent = allDay ? 'この日は全時間帯で出勤可能です。' : '';
      }

      slot.replaceChildren();
      slotField.hidden = currentSlots.length <= 1;
      if (currentSlots.length > 1) {
        const matched = currentSlots.find((availability) => (
          availability.start_time === startTime.value.slice(0, 5)
          && availability.end_time === endTime.value.slice(0, 5)
        ));
        if (!shouldFill && !matched) appendOption('', '希望時間帯を選択');
        currentSlots.forEach((availability) => {
          appendOption(availability.slotKey, `${availability.start_time}〜${availability.end_time}`);
        });
        slot.value = shouldFill ? currentSlots[0].slotKey : (matched ? matched.slotKey : '');
      }
      if (shouldFill) {
        const fullDay = allDay && timePattern.test(data.all_day_start_time)
          && timePattern.test(data.all_day_end_time) ? {
            start_time: data.all_day_start_time,
            end_time: data.all_day_end_time,
          } : null;
        fillAvailability(allDay ? fullDay : currentSlots[0]);
      }
    }

    membership.addEventListener('change', () => updateSlots(true));
    slot.addEventListener('change', () => {
      const availability = currentSlots.find((item) => item.slotKey === slot.value);
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
