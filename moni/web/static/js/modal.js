// moni/web/static/js/modal.js
//
// Unified dialog layer for moni.
// Replaces ad-hoc x-show / setProperty / focus handling for 12 modals.
//
// Exposes:
//   window.moniModal.open(modalElOrFlag, opts)            // by ref or by Alpine state flag
//   window.moniModal.close(modalElOrFlag, opts)
//   window.moniModal.confirm(message, opts) -> Promise    // async confirm — replaces native confirm()
//   window.moniModal.escOn(flag, fn)                      // explicit Escape binding for legacy flows
//
// What it does on every modal:
//   1. Sets role="dialog", aria-modal="true", aria-labelledby pointing at the
//      modal's <h3> (auto-id'd if missing).
//   2. Stores last-focused element; restores it on close.
//   3. Locks body scroll while open (overflow:hidden on <body>).
//   4. Traps Tab/Shift+Tab inside the modal.
//   5. Listens for Escape at the document level — closes the topmost modal.
//   6. Adds a click-on-backdrop close (via @click.self) is already wired in HTML.
//
// Destructive confirm() replaces native confirm() with a real modal so dark/light
// tokens apply and screen-reader announce a label.
//
// v1.0.13 (Sprint 02 — Dialog Certainty): audit fix F5/F13/F14 — see
// docs/frontend-arch-a11y-audit-2026-08-05.md.

(function () {
  'use strict';

  // ── Internal state ────────────────────────────────────────────────────────
  const _openStack = [];     // LIFO of currently-open modal elements
  const _focusStore = new WeakMap();   // el -> previously focused element

  // ── ARIA helpers ──────────────────────────────────────────────────────────
  function _ensureAria(modal) {
    if (!modal.hasAttribute('role')) modal.setAttribute('role', 'dialog');
    if (!modal.hasAttribute('aria-modal')) modal.setAttribute('aria-modal', 'true');

    // Find or create the labelled-by <h3>.
    if (!modal.hasAttribute('aria-labelledby')) {
      const header = modal.querySelector('h3, h2');
      if (header) {
        let id = header.id;
        if (!id) {
          // Build a stable id from the closest x-show flag or a hash of the
          // element's first attribute.
          const flag = (header.closest('[x-show]') || {}).getAttribute?.('x-show') || '';
          id = 'modal-title-' + _slug(flag || String(Math.random()).slice(2, 7));
          header.id = id;
        }
        modal.setAttribute('aria-labelledby', id);
      }
    }
    return modal;
  }

  function _slug(s) {
    return String(s)
      .replace(/[^a-zA-Z0-9_]/g, '-')
      .replace(/-+/g, '-')
      .replace(/^-|-$/g, '')
      .toLowerCase()
      .slice(0, 32) || 'modal';
  }

  // ── Focus management ──────────────────────────────────────────────────────
  function _storeFocus(modal) {
    const active = document.activeElement;
    if (active && active !== document.body && modal.contains(active) === false) {
      _focusStore.set(modal, active);
    }
  }

  function _restoreFocus(modal) {
    const prev = _focusStore.get(modal);
    if (prev && typeof prev.focus === 'function') {
      try { prev.focus(); } catch (e) { /* swallow */ }
    }
    _focusStore.delete(modal);
  }

  function _focusFirst(modal) {
    // Try focusable selectors inside the modal; fall back to the modal itself.
    const sel = 'a[href], button:not([disabled]), input:not([disabled]):not([type=hidden]), '
              + 'textarea:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"]), '
              + 'h1, h2, h3, h4, h5, h6, [role="button"]';
    const targets = modal.querySelectorAll(sel);
    // Skip .modal-close buttons if there are richer targets ahead.
    const first = targets[0];
    if (first && typeof first.focus === 'function') {
      try { first.focus(); } catch (e) { /* swallow */ }
    } else {
      // Last resort: make the modal focusable and focus it.
      modal.setAttribute('tabindex', '-1');
      try { modal.focus(); } catch (e) { /* swallow */ }
    }
  }

  // ── Tab trap ──────────────────────────────────────────────────────────────
  function _getFocusable(modal) {
    const sel = 'a[href], button:not([disabled]), input:not([disabled]):not([type=hidden]), '
              + 'textarea:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])';
    return Array.from(modal.querySelectorAll(sel))
      .filter(el => el.offsetWidth > 0 || el.offsetHeight > 0 || el === document.activeElement);
  }

  function _trapTab(e) {
    if (e.key !== 'Tab') return;
    const top = _openStack[_openStack.length - 1];
    if (!top || !top.contains(e.target)) return;
    const f = _getFocusable(top);
    if (f.length === 0) {
      // Nothing focusable — keep focus on the modal itself.
      e.preventDefault();
      top.setAttribute('tabindex', '-1');
      top.focus();
      return;
    }
    const first = f[0];
    const last = f[f.length - 1];
    if (e.shiftKey && e.target === first) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && e.target === last) {
      e.preventDefault();
      first.focus();
    }
  }

  // ── Escape close ──────────────────────────────────────────────────────────
  function _onKeydown(e) {
    if (e.key === 'Escape' && _openStack.length > 0) {
      e.preventDefault();
      e.stopPropagation();
      const top = _openStack[_openStack.length - 1];
      // Top may have an @click.self="closeX" already; we close via dispatching
      // the click handler by reading its data attribute or setting the visible
      // flag back to false. Without a helper, fire a backdrop click.
      _closeInternal(top, /* restoreFocus */ true);
    }
  }

  function _closeInternal(modal, restoreFocus) {
    if (!modal || !modal.parentNode) return;
    // Toggle x-show state via reading the @click handlers: easiest is to call
    // a .close() method if Alpine assigns one. As a safe fallback, run a
    // click on the backdrop's first close button. If neither, dispatch a
    // click on the backdrop itself (covered by @click.self).
    let closed = false;
    // 1. Try buttons whose @click invokes a known close method.
    const closeBtns = modal.querySelectorAll('.modal-close');
    if (closeBtns.length > 0) {
      // Search inner text for matching handler string was brittle. Instead
      // just click the first close button (visually identical for the user).
      closeBtns[0].click();
      closed = true;
    }
    if (!closed) {
      // Fallback: dispatch a click on the backdrop.
      const ev = new MouseEvent('click', { bubbles: true, cancelable: true });
      modal.dispatchEvent(ev);
    }
    if (restoreFocus) {
      setTimeout(() => _restoreFocus(modal), 0);
    }
  }

  // ── Public API ────────────────────────────────────────────────────────────
  function open(target, opts) {
    const modal = _resolve(target);
    if (!modal) { console.warn('moniModal.open: target not found'); return; }
    if (_openStack.includes(modal)) return;   // already open

    opts = opts || {};
    _ensureAria(modal);
    _storeFocus(modal);
    _openStack.push(modal);

    if (_openStack.length === 1) {
      // First modal — lock body scroll + bind global handlers.
      document.documentElement.classList.add('moni-modal-open');
      document.body.style.overflow = 'hidden';
      document.addEventListener('keydown', _trapTab, true);
      document.addEventListener('keydown', _onKeydown, true);
    } else {
      document.addEventListener('keydown', _trapTab, true);
    }

    // After paint, focus first focusable.
    requestAnimationFrame(() => _focusFirst(modal));
    return modal;
  }

  function close(target, opts) {
    const modal = _resolve(target);
    if (!modal) return;
    _closeInternal(modal, /* restoreFocus */ true);
    // The actual close happens via the click handler chain; we restack.
    const idx = _openStack.indexOf(modal);
    if (idx >= 0) _openStack.splice(idx, 1);
    if (_openStack.length === 0) {
      document.documentElement.classList.remove('moni-modal-open');
      document.body.style.overflow = '';
      document.removeEventListener('keydown', _trapTab, true);
      document.removeEventListener('keydown', _onKeydown, true);
    }
  }

  // ── confirm() replacement ─────────────────────────────────────────────────
  // Build a one-shot modal with a custom message; returns Promise<boolean>.
  let _confirmId = 0;
  function confirm(message, opts) {
    opts = opts || {};
    const okLabel = opts.okLabel || '确认';
    const cancelLabel = opts.cancelLabel || '取消';
    const danger = !!opts.danger;

    return new Promise((resolve) => {
      const wrap = document.createElement('div');
      wrap.className = 'modal-backdrop moni-modal-confirm';
      wrap.setAttribute('role', 'alertdialog');
      wrap.setAttribute('aria-modal', 'true');
      _openStack.push(wrap);
      if (_openStack.length === 1) {
        document.documentElement.classList.add('moni-modal-open');
        document.body.style.overflow = 'hidden';
        document.addEventListener('keydown', _trapTab, true);
        document.addEventListener('keydown', _onKeydown, true);
      } else {
        document.addEventListener('keydown', _trapTab, true);
      }

      const titleId = 'moni-confirm-title-' + (++_confirmId);
      const safeMsg = String(message || '').replace(/[<>&"']/g, c => ({ '<':'&lt;', '>':'&gt;', '&':'&amp;', '"':'&quot;', "'":'&#39;' }[c]));
      wrap.innerHTML = `
        <div class="modal-content" style="max-width:420px;">
          <div class="modal-header">
            <h3 id="${titleId}">${opts.title || '请确认'}</h3>
          </div>
          <div class="modal-body">
            <p>${safeMsg.replace(/\n/g, '<br>')}</p>
          </div>
          <div class="modal-footer">
            <button type="button" class="btn btn-secondary" data-act="cancel">${cancelLabel}</button>
            <button type="button" class="btn ${danger ? 'btn-danger' : 'btn-primary'}" data-act="ok">${okLabel}</button>
          </div>
        </div>
      `;
      wrap.setAttribute('aria-labelledby', titleId);
      document.body.appendChild(wrap);

      function done(result) {
        const idx = _openStack.indexOf(wrap);
        if (idx >= 0) _openStack.splice(idx, 1);
        if (_openStack.length === 0) {
          document.documentElement.classList.remove('moni-modal-open');
          document.body.style.overflow = '';
          document.removeEventListener('keydown', _trapTab, true);
          document.removeEventListener('keydown', _onKeydown, true);
        }
        if (wrap.parentNode) wrap.parentNode.removeChild(wrap);
        resolve(result);
      }

      wrap.addEventListener('click', (e) => {
        if (e.target === wrap) done(false);   // backdrop click = cancel
        const act = e.target && e.target.dataset && e.target.dataset.act;
        if (act === 'ok') done(true);
        if (act === 'cancel') done(false);
      });
      requestAnimationFrame(() => {
        const okBtn = wrap.querySelector('[data-act="ok"]');
        if (okBtn) okBtn.focus();
      });
    });
  }

  function _resolve(target) {
    if (!target) return null;
    if (typeof target === 'string') {
      // Try CSS selector first
      let el = document.querySelector(target);
      if (el) return el;
      // Fallback: look up element by Alpine flag (x-show="<flag>").
      const all = document.querySelectorAll('[x-show]');
      for (const node of all) {
        if (node.getAttribute('x-show').trim() === target.trim()) return node;
      }
      return null;
    }
    return target;
  }

  function escOn(flag, fn) {
    // Bind Escape handler for older modals that lack @keydown.escape. fn() runs on Escape.
    document.addEventListener('keydown', (e) => {
      if (e.key !== 'Escape') return;
      // Find a visible modal whose x-show matches `flag`.
      const node = document.querySelector(`[x-show="${flag}"]`);
      if (!node) return;
      const visible = getComputedStyle(node).display !== 'none';
      if (visible && typeof fn === 'function') fn(e);
    });
  }

  window.moniModal = { open, close, confirm, escOn };
})();
