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
    if (!expanded) return;
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
