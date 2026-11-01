(() => {
  const monthForm = document.querySelector('.overview-month-form');

  if (monthForm) {
    monthForm.addEventListener('change', (event) => {
      if (['year', 'month'].includes(event.target.name) && monthForm.checkValidity()) {
        monthForm.requestSubmit();
      }
    });
  }

  const board = document.querySelector('[data-overview-board]');
  if (!board) return;

  const controls = board.querySelector('[data-overview-controls]');
  const table = board.querySelector('.shift-overview-table');
  const scroll = board.querySelector('.overview-scroll');
  const zoomOut = board.querySelector('[data-overview-zoom-out]');
  const zoomIn = board.querySelector('[data-overview-zoom-in]');
  const zoomReset = board.querySelector('[data-overview-zoom-reset]');
  const zoomValue = board.querySelector('[data-overview-zoom-value]');
  const fullscreen = board.querySelector('[data-overview-fullscreen]');
  if (!controls || !table || !scroll || !zoomOut || !zoomIn || !zoomReset || !zoomValue || !fullscreen) return;

  let zoom = 100;
  let expanded = false;
  let inertElements = [];
  let pagePosition = { x: 0, y: 0 };

  function setZoom(value) {
    const previousZoom = zoom;
    const position = { left: scroll.scrollLeft, top: scroll.scrollTop };
    zoom = Math.max(50, Math.min(200, value));
    // CSS zoom changes layout as well as rendering, so sticky cells and the
    // scrollable area follow the actual size of the table in either mode.
    table.style.zoom = String(zoom / 100);
    table.style.minWidth = `${10000 / zoom}%`;
    scroll.scrollLeft = position.left * zoom / previousZoom;
    scroll.scrollTop = position.top * zoom / previousZoom;
    zoomValue.value = `${zoom}%`;
    zoomOut.disabled = zoom === 50;
    zoomIn.disabled = zoom === 200;
    zoomReset.disabled = zoom === 100;
  }

  zoomOut.addEventListener('click', () => setZoom(zoom - 10));
  zoomIn.addEventListener('click', () => setZoom(zoom + 10));
  zoomReset.addEventListener('click', () => setZoom(100));

  function setExpanded(value) {
    if (expanded === value) return;
    if (value) pagePosition = { x: window.scrollX, y: window.scrollY };
    expanded = value;
    board.classList.toggle('is-fullscreen', value);
    document.documentElement.classList.toggle('overview-fullscreen-open', value);
    fullscreen.textContent = value ? '全画面を終了' : '全画面で表示';
    fullscreen.setAttribute('aria-pressed', String(value));

    if (value) {
      board.setAttribute('role', 'dialog');
      board.setAttribute('aria-modal', 'true');
      // Keep the rest of the page out of keyboard/screen-reader navigation,
      // including when native fullscreen is unavailable or rejected.
      let current = board;
      while (current.parentElement) {
        for (const sibling of current.parentElement.children) {
          if (sibling !== current && !sibling.inert) {
            sibling.inert = true;
            inertElements.push(sibling);
          }
        }
        current = current.parentElement;
        if (current === document.body) break;
      }
    } else {
      board.removeAttribute('role');
      board.removeAttribute('aria-modal');
      inertElements.forEach((element) => { element.inert = false; });
      inertElements = [];
      window.scrollTo(pagePosition.x, pagePosition.y);
    }
    fullscreen.focus({ preventScroll: true });
  }

  async function exitFullscreen() {
    if (document.fullscreenElement === board) {
      try {
        await document.exitFullscreen();
      } catch {
        // Keep the exit control available if the browser refuses to exit.
        return;
      }
    }
    setExpanded(false);
  }

  fullscreen.addEventListener('click', async () => {
    if (expanded) {
      await exitFullscreen();
      return;
    }

    setExpanded(true);
    if (board.requestFullscreen && document.fullscreenEnabled) {
      fullscreen.disabled = true;
      try {
        await board.requestFullscreen();
        // Escape may have closed the expanded view while entry was pending.
        if (!expanded && document.fullscreenElement === board) {
          setExpanded(true);
          await exitFullscreen();
        }
      } catch {
        // The fixed, viewport-sized board also works on mobile browsers and
        // in embedded pages where the Fullscreen API cannot be used.
      } finally {
        fullscreen.disabled = false;
        fullscreen.focus({ preventScroll: true });
      }
    }
  });

  document.addEventListener('fullscreenchange', () => {
    if (expanded && document.fullscreenElement !== board) setExpanded(false);
  });

  board.addEventListener('keydown', (event) => {
    if (!expanded || board.querySelector('#overview-editor[open]')) return;
    if (event.key === 'Escape') {
      event.preventDefault();
      exitFullscreen();
    } else if (event.key === 'Tab') {
      const focusable = Array.from(board.querySelectorAll('a[href], button, input, select, textarea, [tabindex]'))
        .filter((element) => !element.disabled && element.tabIndex >= 0 && element.getClientRects().length);
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    }
  });

  setZoom(100);
  controls.hidden = false;
})();

(() => {
  const dialog = document.querySelector('#overview-editor');
  if (!dialog || typeof dialog.showModal !== 'function') return;

  const form = dialog.querySelector('[data-overview-editor-form]');
  const title = dialog.querySelector('#overview-editor-title');
  const save = dialog.querySelector('[data-overview-save]');
  const membership = form.elements.membership;
  let returnFocus = null;

  function normalizeDate(value) {
    // HTML date controls require at least four digits for the year.
    const parts = value.split('-');
    parts[0] = parts[0].padStart(4, '0');
    return parts.join('-');
  }

  function showEditor(trigger) {
    returnFocus = trigger;
    title.textContent = form.elements.shift_id.value ? 'シフトを編集' : 'シフトを作成';
    save.disabled = false;
    dialog.showModal();
    const invalid = form.querySelector('[aria-invalid="true"]');
    (invalid || membership).focus();
  }

  function openCell(cell, trigger, shift) {
    const memberId = (shift && shift.dataset.membership) || cell.dataset.membership;
    if (!memberId || (!shift && cell.dataset.canCreate !== 'true')) return false;

    // Do not carry invalid values, confirmation, or errors into a different shift.
    form.reset();
    form.querySelectorAll('[data-overview-errors]').forEach(element => element.remove());
    form.querySelectorAll('[aria-invalid]').forEach(element => element.removeAttribute('aria-invalid'));
    membership.querySelectorAll('[data-overview-temporary-option]').forEach(option => option.remove());
    if (!Array.from(membership.options).some(option => option.value === memberId)) {
      if (!shift) return false;
      const option = new Option(cell.dataset.person, memberId);
      option.dataset.overviewTemporaryOption = '';
      membership.add(option);
    }
    form.elements.shift_id.value = shift ? shift.dataset.id : '';
    membership.value = memberId;
    form.elements.work_date.value = normalizeDate(cell.dataset.date);
    form.elements.start_time.value = shift ? shift.dataset.start : '';
    form.elements.end_time.value = shift ? shift.dataset.end : '';
    form.elements.display_color.value = shift ? shift.dataset.color : '';
    form.elements.note.value = shift ? shift.dataset.note : '';
    form.elements.confirm_outside.checked = false;
    showEditor(trigger);
    return true;
  }

  document.querySelectorAll('[data-overview-cell]').forEach(cell => {
    cell.querySelectorAll('[data-overview-add]').forEach(button => { button.hidden = false; });
    cell.addEventListener('click', event => {
      if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      const target = event.target.closest('[data-overview-edit], [data-overview-add], .overview-no-shift');
      // Clicking the cell itself edits the first shift; + always starts a new one.
      const shift = target && target.matches('[data-overview-add], .overview-no-shift')
        ? null : (target || cell.querySelector('[data-overview-edit]'));
      const trigger = target || cell.querySelector('[data-overview-edit], .overview-no-shift, [data-overview-add]');
      if (openCell(cell, trigger, shift)) event.preventDefault();
    });
  });

  dialog.querySelectorAll('[data-overview-close]').forEach(button => {
    button.hidden = false;
    button.addEventListener('click', () => dialog.close());
  });
  // Let the native dialog own Escape and focus trapping, including in full screen.
  dialog.addEventListener('keydown', event => { event.stopPropagation(); });
  dialog.addEventListener('close', () => {
    if (returnFocus && returnFocus.isConnected) returnFocus.focus({ preventScroll: true });
  });
  form.addEventListener('submit', () => { save.disabled = true; });
  if (dialog.dataset.open === 'true') showEditor(null);
})();
