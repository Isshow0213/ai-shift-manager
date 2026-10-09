(() => {
  const form = document.getElementById("availability-form");
  if (!form) return;

  const startInput = form.querySelector('[name="start_time"]');
  const endInput = form.querySelector('[name="end_time"]');
  const submitButton = form.querySelector('[type="submit"]');
  const status = form.querySelector("#quickfill-status");
  if (!startInput || !endInput) return;

  form.querySelectorAll("[data-availability-quickfill]").forEach((button) => {
    button.addEventListener("click", () => {
      const start = button.dataset.startTime;
      const end = button.dataset.endTime;
      const timePattern = /^([01]\d|2[0-3]):[0-5]\d$/;
      if (!timePattern.test(start) || !timePattern.test(end) || start >= end) return;

      startInput.value = start;
      endInput.value = end;
      [startInput, endInput].forEach((input) => {
        input.dispatchEvent(new Event("input", { bubbles: true }));
        input.dispatchEvent(new Event("change", { bubbles: true }));
      });
      if (status) {
        status.textContent = `${start}〜${end}を入力しました。内容を確認して提出してください。`;
      }

      if (button.hasAttribute("data-full-day") && submitButton) {
        submitButton.focus({ preventScroll: true });
        const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
        submitButton.scrollIntoView({ behavior: reduceMotion ? "auto" : "smooth", block: "center" });
      }
    });
  });
})();
