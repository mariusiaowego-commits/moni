// moni inline SVG icons (v1.0.8.3)
//
// Style: nullframe-inspired (Nothing design language).  Glyph-grid
// geometry, single colour, no gradients / shadows / 3D.
//
// Pin icon was generated via image gen (Fal gpt-image-2) — see
// _demo_pin_4candidates.html.  User picked "B" (round/trapezoid
// head + thick stem + tip dot) for the pinned state, and the
// unpin state keeps the v1.0.8 outline (cleaner contrast).
//
// All icons:
// * 24x24 viewBox (renders crisply at 17px and up)
// * stroke-width: 1.6 (tuned for 17px legibility)
// * currentColor (inherits parent colour via CSS)
//
// Exports:
//   pinIcon(filled)  — outlined (unpinned) / solid (pinned)

/**
 * Render the pin icon as an inline SVG string.
 *
 * @param {boolean} filled - true = the article IS pinned (solid silhouette)
 * @returns {string} SVG markup ready for x-html
 */
function pinIcon(filled) {
  if (filled) {
    // Pinned: SOLID silhouette (moni 紫藤萝, 圆头+粗针+底点).
    // Image-gen source: moni/web/static/_pin_b.png (1024x1024 master,
    // user-approved candidate B).  Inline SVG version is sharp at
    // any size — no aliasing at 17px.
    return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="17" height="17"
       fill="currentColor" stroke="none" class="icon icon-pin"
       aria-hidden="true">
  <!-- Head: rounded-top trapezoid (wider top, narrower bottom) -->
  <path d="M 5 3 H 19 L 16 12 H 8 Z" />
  <!-- Stem: thick vertical bar -->
  <rect x="11" y="12" width="2" height="9" rx="0.5" />
  <!-- Tip dot -->
  <circle cx="12" cy="22" r="1.4" />
</svg>`;
  }
  // Unpinned: clean outline (no fill) — unchanged from v1.0.8.
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="17" height="17"
       fill="none" stroke="currentColor" stroke-width="1.6"
       stroke-linecap="round" stroke-linejoin="round"
       class="icon icon-pin" aria-hidden="true">
  <!-- Head outline -->
  <path d="M 7 4 H 17 L 15 10 H 9 Z" />
  <!-- Stem -->
  <line x1="12" y1="10" x2="12" y2="18" />
  <!-- Tip dot -->
  <circle cx="12" cy="20" r="0.6" fill="currentColor" stroke="none" />
</svg>`;
}

// Attach to window for Alpine x-data / inline script access
window.pinIcon = pinIcon;
