(() => {
  function initialise() {
    document.querySelectorAll('form[data-outside-shift-form]').forEach((form) => {
      const confirmation = form.querySelector('[name$="-confirm_outside_availability"]');
      if (!confirmation) return;

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

        if (!window.confirm('希望外のシフトですがよろしいですか？')) {
          event.preventDefault();
          return;
        }

        confirmation.value = 'True';
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
