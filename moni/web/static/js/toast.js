// moni/web/static/js/toast.js
//
// Toast / non-blocking feedback layer for moni.
//
// Exposes:
//   window.moniToast.success(msg, opts)
//   window.moniToast.error(msg, opts)        // sticky by default
//   window.moniToast.info(msg, opts)
//   window.moniToast.undo(msg, onUndo, opts)  // 8s with action button
//
// opts:
//   { sticky: bool, action: {label, onClick}, duration: ms, copyError: bool }
//
// Replaces 33 native alert() calls. Modal-rendered destructive confirmations
// stay in modal.js — toast is for non-blocking feedback only.
//
// v1.0.13 (Sprint 02 — Dialog Certainty): audit fix F4/F5/F14 — see
// docs/frontend-arch-a11y-audit-2026-08-05.md.

(function () {
  'use strict';

  const TOAST_REGION_ID = 'moni-toast-region';
  const DEFAULT_DURATION = 4000;
  const UNDO_DURATION = 8000;
  const STACK_OFFSET = 12;     // px gap between toasts
  const MAX_VISIBLE = 5;       // stack cap; older get auto-dismissed

  let _seq = 0;

  function ensureRegion() {
    let region = document.getElementById(TOAST_REGION_ID);
    if (region) return region;
    region = document.createElement('div');
    region.id = TOAST_REGION_ID;
    region.setAttribute('role', 'region');
    region.setAttribute('aria-label', '通知');
    region.setAttribute('aria-live', 'polite');
    document.body.appendChild(region);
    return region;
  }

  function createToast(kind, msg, opts) {
    opts = opts || {};
    const region = ensureRegion();

    // Enforce stack cap (dismiss oldest non-sticky if over)
    const visible = region.querySelectorAll('.toast:not(.dismissing)');
    if (visible.length >= MAX_VISIBLE) {
      const oldest = visible[0];
      if (oldest && !oldest.classList.contains('sticky')) {
        dismiss(oldest);
      }
    }

    const el = document.createElement('div');
    el.className = 'toast toast-' + kind + ' show';
    if (opts.sticky) el.classList.add('sticky');

    const id = ++_seq;
    el.dataset.toastId = String(id);

    // Build content
    const msgEl = document.createElement('span');
    msgEl.className = 'toast-msg';
    msgEl.textContent = String(msg || '');
    el.appendChild(msgEl);

    // Optional technical detail: the headline stays human-readable while
    // the raw backend error stays one click away (and copyable).
    if (opts.detail) {
      const det = document.createElement('details');
      det.className = 'toast-detail';
      const sum = document.createElement('summary');
      sum.textContent = '查看原因';
      const pre = document.createElement('pre');
      pre.textContent = String(opts.detail);
      det.appendChild(sum);
      det.appendChild(pre);
      el.appendChild(det);
    }

    // Action button (undo / copy error)
    if (opts.action && opts.action.label) {
      const btn = document.createElement('button');
      btn.className = 'toast-action';
      btn.type = 'button';
      btn.textContent = opts.action.label;
      btn.addEventListener('click', () => {
        try { opts.action.onClick && opts.action.onClick(); } finally { dismiss(el); }
      });
      el.appendChild(btn);
    }

    // Error copy button (sticky error gets a "复制错误" automatically)
    if (kind === 'error' && opts.copyError !== false) {
      const copyBtn = document.createElement('button');
      copyBtn.className = 'toast-action';
      copyBtn.type = 'button';
      copyBtn.textContent = '复制错误';
      copyBtn.addEventListener('click', () => {
        navigator.clipboard && navigator.clipboard.writeText(String(opts.detail || msg || ''))
          .then(() => { copyBtn.textContent = '已复制'; copyBtn.disabled = true; })
          .catch(() => { copyBtn.textContent = '复制失败'; });
      });
      el.appendChild(copyBtn);
    }

    // Close button
    const closeBtn = document.createElement('button');
    closeBtn.className = 'toast-close';
    closeBtn.type = 'button';
    closeBtn.setAttribute('aria-label', '关闭通知');
    closeBtn.textContent = '\u00d7';
    closeBtn.addEventListener('click', () => dismiss(el));
    el.appendChild(closeBtn);

    // a11y
    el.setAttribute('role', kind === 'error' ? 'alert' : 'status');

    region.appendChild(el);

    // Auto-dismiss (unless sticky or has undo action).
    const duration = opts.sticky ? 0
                   : opts.action ? UNDO_DURATION
                   : (opts.duration || DEFAULT_DURATION);
    let timer = 0;
    if (duration > 0) {
      timer = setTimeout(() => dismiss(el), duration);
    }
    el._timer = timer;

    return el;
  }

  function dismiss(el) {
    if (!el || el.classList.contains('dismissing')) return;
    el.classList.add('dismissing');
    el.classList.remove('show');
    if (el._timer) { clearTimeout(el._timer); el._timer = 0; }
    // Allow CSS transition (200ms) before removal.
    setTimeout(() => {
      if (el.parentNode) el.parentNode.removeChild(el);
    }, 260);
  }

  function dismissAll() {
    const region = document.getElementById(TOAST_REGION_ID);
    if (!region) return;
    region.querySelectorAll('.toast').forEach(dismiss);
  }

  // Public API
  const moniToast = {
    success(msg, opts) { return createToast('success', msg, opts); },
    error(msg, opts)   {
      // Error toasts are sticky by default so the user notices.
      return createToast('error', msg, Object.assign({ sticky: true }, opts || {}));
    },
    info(msg, opts)    { return createToast('info', msg, opts); },
    undo(msg, onUndo, opts) {
      return createToast('info', msg, Object.assign({ action: { label: '撤销', onClick: onUndo } }, opts || {}));
    },
    dismiss: dismissAll,
  };

  // Expose globally
  window.moniToast = moniToast;
})();
