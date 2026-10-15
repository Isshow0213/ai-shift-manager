(() => {
  function initialise() {
    document.querySelectorAll('form[data-shift-availability-confirm], form[data-outside-shift-form]').forEach((form) => {
      const confirmation = form.querySelector('[name="confirm_outside_availability"], [name$="-confirm_outside_availability"]');
      if (!confirmation) return;

      const membership = form.querySelector('[name="membership"], [name$="-membership"]');
      const startTime = form.querySelector('[name="start_time"], [name$="-start_time"]');
      const endTime = form.querySelector('[name="end_time"], [name$="-end_time"]');
      const source = document.getElementById(form.dataset.availabilitySource || 'manual-shift-availabilities');
      let data;
      try {
        data = source ? JSON.parse(source.textContent) : null;
      } catch {
        data = null;
      }

      const timePattern = /^(?:[01]\d|2[0-3]):[0-5]\d$/;
      function availableSlots() {
        if (!data || !data.memberships || !membership) return null;
        const slots = data.memberships[membership.value];
        if (!Array.isArray(slots)) return null;
        return slots.filter((slot) => slot && timePattern.test(slot.start_time)
          && timePattern.test(slot.end_time) && slot.start_time < slot.end_time)
          .sort((a, b) => a.start_time.localeCompare(b.start_time) || a.end_time.localeCompare(b.end_time));
      }

      function coversTime(slots, start, end) {
        let coveredUntil = start;
        for (const slot of slots) {
          if (slot.end_time <= coveredUntil) continue;
          if (slot.start_time > coveredUntil) return false;
          coveredUntil = slot.end_time;
          if (coveredUntil >= end) return true;
        }
        return false;
      }

      let submitting = false;
      confirmation.value = 'False';

      form.addEventListener('submit', (event) => {
        if (submitting) {
          event.preventDefault();
          return;
        }

        confirmation.value = 'False';
        if (!form.checkValidity()) {
          event.preventDefault();
          form.reportValidity();
          return;
        }

        const slots = availableSlots();
        const start = startTime ? startTime.value.slice(0, 5) : '';
        const end = endTime ? endTime.value.slice(0, 5) : '';
        const allDay = data && Array.isArray(data.all_day_membership_ids) && membership
          && data.all_day_membership_ids.map(String).includes(membership.value);
        const outside = !allDay && (!slots || !timePattern.test(start) || !timePattern.test(end)
          || !coversTime(slots, start, end));
        if (outside) {
          const name = membership && membership.selectedOptions[0] ? membership.selectedOptions[0].textContent.trim() : '選択したスタッフ';
          const desired = slots && slots.length ? slots.map((slot) => `${slot.start_time}〜${slot.end_time}`).join('、') : '確認できません';
          const warning = `希望時間外のシフトです\n\n${name}さんの希望時間：${desired}\n登録する時間：${start}〜${end}\n\n本人への確認はお済みですか？\n確認済みの場合は「OK」で登録してください。`;
          if (!window.confirm(warning)) {
            event.preventDefault();
            return;
          }
          confirmation.value = 'True';
        }
        submitting = true;
      });

      window.addEventListener('pageshow', () => {
        submitting = false;
        confirmation.value = 'False';
      });
    });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initialise, { once: true });
  } else {
    initialise();
  }
})();
