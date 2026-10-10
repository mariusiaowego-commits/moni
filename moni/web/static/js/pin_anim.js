// moni pin animation (v1.0.8.1)
//
// Lightweight wrapper around GSAP for the inline pin button.  Uses
// event delegation so we don't have to bind handlers per row (Alpine
// re-renders the <template x-for> on any state change).
//
// `pinPop(btnEl, filled)` — called from app.js after a successful
// optimistic toggle.  Pop the button (scale 0.85 → 1.15 → 1.0) and
// briefly add a "just-pinned" / "just-unpinned" class for the
// accent fill animation.
//
// v1.0.44 (sprint-24 render hygiene):
//   1. The flash class is added BEFORE the GSAP check, so a slow/absent
//      CDN no longer swallows the CSS colour feedback along with the
//      scale pop.  (AC-6)
//   2. The GSAP `fromTo` is skipped when the user prefers reduced motion.
//      A CSS media query cannot reach a GSAP tween, so the decision has
//      to be made here in JS.  (AC-5)
//   3. The flash lifetime is ONE clock, started when the class is added.
//      Previously the GSAP path waited for the 350 ms tween *and then*
//      started a second 350 ms timer, so the colour flash was visible for
//      ~700 ms with GSAP and 350 ms without it.  (P1-2)

(function () {
  const REDUCED_MOTION_QUERY = '(prefers-reduced-motion: reduce)';

  // The visible colour flash is painted by the CSS animation on
  // .pin-btn--just-pinned / --just-unpinned. This constant has to equal
  // that duration (0.7s in main.css) so the class is removed exactly when
  // the paint finishes, on every path. dad pinned it to 700ms (2026-10-10).
  const FLASH_MS = 700;

  function prefersReducedMotion() {
    try {
      return !!(window.matchMedia
        && window.matchMedia(REDUCED_MOTION_QUERY).matches);
    } catch (_) {
      return false;
    }
  }

  /**
   * Pop a pin button.
   * @param {HTMLElement} btn - the <button class="pin-btn ..."> element
   * @param {boolean} filled - true if the new state is "pinned"
   */
  function pinPop(btn, filled) {
    if (!btn) return;
    // Add a transient class for a brief "just-pinned" highlight.  This runs
    // before any GSAP check on purpose: the colour flash is plain CSS and
    // must survive a GSAP that hasn't loaded yet.  (v1.0.44)
    const flashClass = filled ? 'pin-btn--just-pinned' : 'pin-btn--just-unpinned';
    btn.classList.add(flashClass);

    // The removal clock starts HERE, on every path, so the flash lasts the
    // same 350 ms whether GSAP animates it, reduced-motion skips the tween,
    // or GSAP never loaded.  (P1-2)
    setTimeout(() => btn.classList.remove(flashClass), FLASH_MS);

    // Reduced motion: no scale pop, but keep the CSS colour flash so the
    // action still has a visible result.  (v1.0.44)
    if (prefersReducedMotion() || !window.gsap) return;

    // Stop any in-flight animation on this button to avoid stacking
    // (user clicking fast).
    window.gsap.killTweensOf(btn);
    window.gsap.fromTo(
      btn,
      { scale: 0.85 },
      {
        scale: 1,
        duration: FLASH_MS / 1000,
        ease: 'back.out(1.7)',
      }
    );
  }

  // Expose globally so app.js (and tests) can call it.
  window.pinPop = pinPop;
})();
