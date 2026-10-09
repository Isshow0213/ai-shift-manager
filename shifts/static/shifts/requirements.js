(() => {
  const form = document.getElementById('bulk-form');
  if (!form) return;
  const invalidatePreview = () => {
    const preview = document.getElementById('bulk-preview');
    if (preview) {
      preview.hidden = true;
      preview.querySelectorAll('button[name="action"][value="apply"]').forEach(button => { button.disabled = true; });
    }
  };
  const updateCategories = () => {
    const selected = new Set([...form.querySelectorAll('input[name="categories"]:checked')].map(input => input.value));
    form.querySelectorAll('.bulk-category').forEach(section => {
      section.hidden = !selected.has(section.dataset.category);
      section.querySelectorAll('input, select, textarea').forEach(input => { input.disabled = section.hidden; });
    });
  };
  form.addEventListener('input', invalidatePreview);
  form.addEventListener('change', () => { invalidatePreview(); updateCategories(); });
  form.addEventListener('click', event => {
    const add = event.target.closest('.add-time-slot');
    const remove = event.target.closest('.remove-time-slot');
    if (!add && !remove) return;
    const section = event.target.closest('.bulk-category');
    const list = section.querySelector('.time-slot-list');
    const total = section.querySelector('input[name$="-TOTAL_FORMS"]');
    if (add) {
      const count = Number(total.value);
      if (count >= 24) return;
      list.insertAdjacentHTML('beforeend', section.querySelector('template').innerHTML.replaceAll('__prefix__', String(count)));
      total.value = count + 1;
    } else {
      const row = remove.closest('.time-slot-row');
      if (list.children.length === 1) {
        row.querySelectorAll('input').forEach(input => { input.value = ''; });
      } else {
        row.remove();
        [...list.children].forEach((remaining, index) => {
          remaining.querySelectorAll('input').forEach(input => {
            input.name = input.name.replace(/-\d+-/, `-${index}-`);
            input.id = input.id.replace(/-\d+-/, `-${index}-`);
          });
          remaining.querySelectorAll('label').forEach(label => { label.htmlFor = label.htmlFor.replace(/-\d+-/, `-${index}-`); });
        });
        total.value = list.children.length;
      }
    }
    invalidatePreview();
  });
  updateCategories();
  const preview = document.getElementById('bulk-preview');
  if (preview && !preview.hidden) {
    requestAnimationFrame(() => {
      preview.focus({ preventScroll: true });
      const reducedMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
      preview.scrollIntoView({ behavior: reducedMotion ? 'auto' : 'smooth', block: 'start' });
    });
  }
})();
