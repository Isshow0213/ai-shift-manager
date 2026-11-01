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
  const slotField = form.querySelector('[data-overview-wish-slot-field]');
  const slot = form.querySelector('[data-overview-wish-slot]');
  const confirmation = form.querySelector('[data-overview-confirmation]');
  const wishSource = document.getElementById('overview-wish-times');
  const defaultSource = document.getElementById('overview-default-times');
  let wishTimes = {};
  let defaultTimes = {};
  let currentSlots = [];
  let returnFocus = null;

  if (wishSource) {
    try { wishTimes = JSON.parse(wishSource.textContent) || {}; } catch { /* Keep manual entry available. */ }
  }
  if (defaultSource) {
    try { defaultTimes = JSON.parse(defaultSource.textContent) || {}; } catch { /* Keep manual entry available. */ }
  }

  function clearConfirmation() {
    if (form.elements.confirm_outside) form.elements.confirm_outside.checked = false;
    if (form.elements.outside_confirmation_token) form.elements.outside_confirmation_token.value = '';
    if (confirmation) confirmation.hidden = true;
    save.textContent = '保存する';
    save.disabled = false;
    if (dialog.open) title.textContent = form.elements.shift_id.value ? 'シフトを編集' : 'シフトを作成';
  }

  function fillSlot(value) {
    if (!value) return;
    form.elements.start_time.value = value.start_time;
    form.elements.end_time.value = value.end_time;
  }

  function updateWishSlots(shouldFill) {
    const memberWishes = wishTimes[membership.value];
    const wishes = memberWishes && memberWishes[form.elements.work_date.value];
    const timePattern = /^(?:[01]\d|2[0-3]):[0-5]\d$/;
    currentSlots = Array.isArray(wishes) ? wishes.filter(wish => (
      wish && timePattern.test(wish.start_time) && timePattern.test(wish.end_time)
      && wish.start_time < wish.end_time
    )) : [];
    if (slot && slotField) {
      slot.replaceChildren();
      slotField.hidden = currentSlots.length <= 1;
      const matchedIndex = currentSlots.findIndex(wish => (
        wish.start_time === form.elements.start_time.value.slice(0, 5)
        && wish.end_time === form.elements.end_time.value.slice(0, 5)
      ));
      if (!shouldFill && matchedIndex === -1) slot.add(new Option('希望時間帯を選択', ''));
      currentSlots.forEach((wish, index) => {
        slot.add(new Option(`${wish.start_time}〜${wish.end_time}`, String(index)));
      });
      slot.value = shouldFill && currentSlots.length ? '0' : (matchedIndex < 0 ? '' : String(matchedIndex));
    }
    if (shouldFill) {
      const memberDefaults = defaultTimes[membership.value];
      const personalDefault = memberDefaults && memberDefaults[form.elements.work_date.value];
      const validDefault = personalDefault
        && timePattern.test(personalDefault.start_time) && timePattern.test(personalDefault.end_time)
        && personalDefault.start_time < personalDefault.end_time ? personalDefault : null;
      fillSlot(currentSlots[0] || validDefault);
    }
  }

  function normalizeDate(value) {
    // HTML date controls require at least four digits for the year.
    const parts = value.split('-');
    parts[0] = parts[0].padStart(4, '0');
    return parts.join('-');
  }

  function showEditor(trigger) {
    returnFocus = trigger;
    const isConfirming = confirmation && !confirmation.hidden;
    title.textContent = isConfirming ? '希望外のシフトを確認' : (form.elements.shift_id.value ? 'シフトを編集' : 'シフトを作成');
    save.textContent = isConfirming ? '確認して保存する' : '保存する';
    save.disabled = false;
    dialog.showModal();
    const invalid = form.querySelector('[aria-invalid="true"]');
    (invalid || (isConfirming && form.elements.confirm_outside) || membership).focus();
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
    clearConfirmation();
    updateWishSlots(!shift);
    showEditor(trigger);
    return true;
  }

  document.querySelectorAll('[data-overview-cell]').forEach(cell => {
    cell.addEventListener('click', event => {
      if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      const target = event.target.closest('[data-overview-edit], [data-overview-create]');
      // Clicking an occupied cell edits its first shift; empty or wish-only cells create one.
      const shift = target && target.matches('[data-overview-create]')
        ? null : (target || cell.querySelector('[data-overview-edit]'));
      const trigger = target || cell.querySelector('[data-overview-edit], [data-overview-create]');
      if (openCell(cell, trigger, shift)) event.preventDefault();
    });
  });

  [membership, form.elements.work_date].forEach(field => {
    field.addEventListener('change', () => {
      clearConfirmation();
      updateWishSlots(true);
    });
  });
  [membership, form.elements.work_date, form.elements.start_time, form.elements.end_time].forEach(field => {
    field.addEventListener('input', clearConfirmation);
  });
  if (slot) slot.addEventListener('change', () => {
    clearConfirmation();
    if (slot.value !== '') fillSlot(currentSlots[Number(slot.value)]);
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
  updateWishSlots(false);
  if (dialog.dataset.open === 'true') showEditor(null);
})();
