// moni pin animation (v1.0.8.1)
//
// Lightweight wrapper around GSAP for the inline pin button.  Uses
// event delegation so we don't have to bind handlers per row (Alpine
// re-renders the <template x-for> on every state change).
//
// `pinPop(btnEl, filled)` — called from app.js after a successful
// optimistic toggle.  Pop the button (scale 0.85 → 1.15 → 1.0) and
// briefly add a "just-pinned" / "just-unpinned" class for the
// accent fill animation.

(function () {
  /**
   * Pop a pin button.
   * @param {HTMLElement} btn - the <button class="pin-btn ..."> element
   * @param {boolean} filled - true if the new state is "pinned"
   */
  function pinPop(btn, filled) {
    if (!btn || !window.gsap) return;
    // Stop any in-flight animation on this button to avoid stacking
    // (user clicking fast).
    window.gsap.killTweensOf(btn);
    // Add a transient class for a brief "just-pinned" highlight
    // (CSS handles the colour flash).
    const flashClass = filled ? 'pin-btn--just-pinned' : 'pin-btn--just-unpinned';
    btn.classList.add(flashClass);
    window.gsap.fromTo(
      btn,
      { scale: 0.85 },
      {
        scale: 1,
        duration: 0.35,
        ease: 'back.out(1.7)',
        onComplete: () => {
          // Remove flash class after the colour transition is done
          // (CSS controls the actual transition duration).
          setTimeout(() => btn.classList.remove(flashClass), 350);
        },
      }
    );
  }

  // Expose globally so app.js (and tests) can call it.
  window.pinPop = pinPop;
})();
