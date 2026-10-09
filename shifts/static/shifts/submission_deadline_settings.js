(() => {
  const form = document.getElementById("submission-deadline-form");
  if (!form) return;

  const modeWrapper = form.querySelector("[data-deadline-mode-field]");
  const weekday = form.querySelector('[name="weekly_deadline_weekday"]');
  const monthDay = form.querySelector('[name="monthly_deadline_day"]');
  const preview = document.getElementById("deadline-preview");
  const description = document.getElementById("deadline-preview-description");
  const rules = form.querySelectorAll("[data-deadline-rule]");
  const selectedDate = form.dataset.selectedDate;
  if (!modeWrapper || !weekday || !monthDay) return;

  const utcDate = (year, month, day) => {
    const result = new Date(0);
    result.setUTCFullYear(year, month, day);
    result.setUTCHours(0, 0, 0, 0);
    return result;
  };
  const offsetDays = (date, count) => utcDate(date.getUTCFullYear(), date.getUTCMonth(), date.getUTCDate() + count);
  const formatDate = (date, withWeekday = false) => {
    const text = `${date.getUTCFullYear()}年${date.getUTCMonth() + 1}月${date.getUTCDate()}日`;
    return withWeekday ? `${text}（${"日月火水木金土"[date.getUTCDay()]}）` : text;
  };
  const selectedParts = /^(\d{4})-(\d{2})-(\d{2})$/.exec(selectedDate || "");
  const targetDate = selectedParts ? utcDate(Number(selectedParts[1]), Number(selectedParts[2]) - 1, Number(selectedParts[3])) : null;

  function modeValue() {
    const checked = modeWrapper.querySelector('input[name="mode"]:checked');
    const select = modeWrapper.querySelector('select[name="mode"]');
    return checked ? checked.value : select ? select.value : "monthly";
  }

  function period(mode, next) {
    if (!targetDate) return null;
    if (mode === "weekly") {
      const chosenWeekday = Number(weekday.value);
      if (!Number.isInteger(chosenWeekday) || chosenWeekday < 0 || chosenWeekday > 6 || weekday.value === "") return null;
      const mondayOffset = (targetDate.getUTCDay() + 6) % 7;
      const start = offsetDays(targetDate, -mondayOffset + (next ? 7 : 0));
      return { start, end: offsetDays(start, 6), deadline: offsetDays(start, -7 + chosenWeekday) };
    }
    const day = Number(monthDay.value);
    if (!Number.isInteger(day) || day < 1 || day > 31 || monthDay.value === "") return null;
    const start = utcDate(targetDate.getUTCFullYear(), targetDate.getUTCMonth() + (next ? 1 : 0), 1);
    const end = utcDate(start.getUTCFullYear(), start.getUTCMonth() + 1, 0);
    const previousMonthEnd = utcDate(start.getUTCFullYear(), start.getUTCMonth(), 0);
    const deadline = utcDate(previousMonthEnd.getUTCFullYear(), previousMonthEnd.getUTCMonth(), Math.min(day, previousMonthEnd.getUTCDate()));
    return { start, end, deadline };
  }

  function update(changed = false) {
    const mode = modeValue();
    rules.forEach((rule) => {
      const active = rule.dataset.deadlineRule === mode;
      rule.hidden = !active;
      rule.querySelectorAll("input, select, textarea").forEach((input) => {
        input.disabled = !active;
        input.required = active;
      });
    });
    if (changed && description) description.textContent = "入力中のルールによる締切例です。保存すると適用されます。";
    if (!preview) return;
    ["current", "next"].forEach((key) => {
      const result = period(mode, key === "next");
      const card = preview.querySelector(`[data-deadline-period="${key}"]`);
      if (!card) return;
      if (!result) {
        card.querySelector("[data-period-deadline]").textContent = mode === "weekly" ? "締切曜日を選択してください。" : "締切日を1〜31で入力してください。";
        return;
      }
      card.querySelector("[data-period-start]").textContent = formatDate(result.start);
      card.querySelector("[data-period-end]").textContent = formatDate(result.end);
      card.querySelector("[data-period-deadline]").textContent = `${formatDate(result.deadline, true)} 23:59まで`;
    });
  }

  form.addEventListener("change", () => update(true));
  monthDay.addEventListener("input", () => update(true));
  update();
})();
