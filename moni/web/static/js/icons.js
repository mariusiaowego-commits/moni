// moni inline SVG icons (v1.0.8)
//
// Style: nullframe-inspired (Nothing design language).  Glyph-grid
// geometry, single stroke, no fills (except the active "filled" state
// where the head gets a transparent accent fill).
//
// All icons:
// * 24x24 viewBox (renders crisply at 16/20/24px)
// * stroke-width: 1.5 (matches the rest of moni's UI weight)
// * stroke="currentColor" (inherits the parent's color — set via CSS)
// * Use the export functions and x-html to render.
//
// Exports:
//   pinIcon(filled)  — 4-state pin (outline / filled × on / off)
//
// Future: add other icons here (share, copy, etc.) so we have one place
// for inline SVG.  Each icon ~5-15 lines of SVG.

/**
 * Render the pin icon as an inline SVG string.
 *
 * @param {boolean} filled - true = the article IS pinned (filled head)
 * @returns {string} SVG markup ready for x-html
 */
function pinIcon(filled) {
  // Filled: solid head fill with currentColor.  Outline: empty head.
  // Stroke stays the same in both states so the silhouette is
  // recognisable — only the head fill changes.  This is the "active
  // toggle" idiom: shape stable, fill flips.
  const headFill = filled ? 'fill="currentColor" fill-opacity="0.18"' : 'fill="none"';
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" width="16" height="16"
       fill="none" stroke="currentColor" stroke-width="1.5"
       stroke-linecap="round" stroke-linejoin="round"
       class="icon icon-pin" aria-hidden="true">
  <!-- Push-pin head: trapezoid, top wider than bottom -->
  <path d="M 7 4 H 17 L 15 10 H 9 Z" ${headFill} />
  <!-- Pin stem: short vertical line from head to point -->
  <line x1="12" y1="10" x2="12" y2="18" />
  <!-- Point indicator: small dot at the tip (visual anchor) -->
  <circle cx="12" cy="20" r="0.6" fill="currentColor" stroke="none" />
</svg>`;
}

// Attach to window for Alpine x-data / inline script access
window.pinIcon = pinIcon;
