(() => {
  function initialise() {
    const form = document.querySelector('[data-shift-order-form]');
    if (!form) return;

    const body = document.getElementById(form.dataset.orderTarget || '');
    const fields = form.querySelector('[data-order-fields]');
    const status = form.querySelector('[data-order-status]');
    const save = form.querySelector('[data-order-save]');
    const cancel = form.querySelector('[data-order-cancel]');
    if (!body || !fields || !status || !save || !cancel) return;

    const getRows = () => Array.from(body.querySelectorAll('tr[data-shift-user-id]'));
    const originalRows = getRows();
    const originalIds = originalRows.map((row) => row.dataset.shiftUserId);
    if (originalRows.length < 2) return;

    originalRows.forEach((row) => {
      const controls = row.querySelector('[data-order-controls]');
      if (controls) controls.hidden = false;
    });

    function updateOrder(message) {
      const rows = getRows();
      const changed = rows.some((row, index) => row.dataset.shiftUserId !== originalIds[index]);
      fields.replaceChildren(...rows.map((row, index) => {
        const input = document.createElement('input');
        input.type = 'hidden';
        input.name = 'user_ids';
        input.value = row.dataset.shiftUserId;

        const up = row.querySelector('[data-order-direction="up"]');
        const down = row.querySelector('[data-order-direction="down"]');
        if (up) up.disabled = index === 0;
        if (down) down.disabled = index === rows.length - 1;
        return input;
      }));
      save.disabled = !changed;
      cancel.disabled = !changed;
      form.classList.toggle('is-unsaved', changed);
      status.textContent = `${message || ''}${changed ? '表示順の変更は未保存です。「表示順を保存」を押してください。' : '保存済みの表示順です。'}`;
      return rows;
    }

    body.addEventListener('click', (event) => {
      const button = event.target.closest('[data-order-direction]');
      if (!button || button.disabled || !body.contains(button)) return;

      const row = button.closest('tr[data-shift-user-id]');
      if (!row) return;
      const rows = getRows();
      const index = rows.indexOf(row);
      const direction = button.dataset.orderDirection;
      if (direction === 'up' && index > 0) {
        body.insertBefore(row, rows[index - 1]);
      } else if (direction === 'down' && index < rows.length - 1) {
        body.insertBefore(rows[index + 1], row);
      } else {
        return;
      }

      const position = getRows().indexOf(row) + 1;
      updateOrder(`${row.dataset.shiftUserName}を${rows.length}人中${position}番目に移動しました。`);
      // Moving a DOM node can remove keyboard focus; keep it on this person's controls.
      const focusButton = button.disabled
        ? row.querySelector(`[data-order-direction="${direction === 'up' ? 'down' : 'up'}"]`)
        : button;
      if (focusButton) focusButton.focus({ preventScroll: true });
    });

    cancel.addEventListener('click', () => {
      originalRows.forEach((row) => body.appendChild(row));
      updateOrder('変更を取り消しました。');
      const firstControl = originalRows[0].querySelector('[data-order-direction="down"]');
      if (firstControl) firstControl.focus({ preventScroll: true });
    });

    form.addEventListener('submit', (event) => {
      if (save.disabled) event.preventDefault();
    });

    updateOrder();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initialise, { once: true });
  } else {
    initialise();
  }
})();
