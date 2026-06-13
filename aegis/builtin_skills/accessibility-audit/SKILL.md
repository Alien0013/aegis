---
name: accessibility-audit
description: Audit and fix a web UI for accessibility (WCAG) — semantic structure, keyboard operability, contrast, labels, and focus. Use when asked to make a UI accessible or to fix a11y issues.
version: 1.0.0
metadata:
  category: frontend
  tags: [accessibility, a11y, wcag, aria, frontend]
---

## When to Use
A web UI needs to be usable by people relying on keyboards, screen readers, magnification, or high contrast — and to meet WCAG AA. Fix the structure first; ARIA is a patch, not a foundation.

## Procedure
1. **Use the right element first.** A real `<button>`, `<a href>`, `<label>`, `<nav>`, `<main>` gives you focusability, keyboard handling, and semantics for free. A `<div onclick>` gives you none. Prefer native HTML over rebuilding it with ARIA.
2. **Make it keyboard-operable.** Every interactive element must be reachable with Tab and operated with Enter/Space (and Esc to dismiss). Tab order must follow visual order. No keyboard traps. Test the whole flow with the mouse unplugged.
3. **Make focus visible.** Never `outline: none` without a clearly visible replacement. The user must always see where they are.
4. **Name everything.** Every input has an associated `<label>`; every icon-only button has an accessible name (`aria-label`); every meaningful image has `alt`, decorative ones `alt=""`. Screen-reader users navigate by these names.
5. **Check contrast.** Text ≥ 4.5:1 (≥ 3:1 for large text); UI controls/borders ≥ 3:1. Don't rely on color alone to convey meaning (add text/icon/shape).
6. **Wire dynamic state.** Use `aria-expanded`, `aria-selected`, `aria-current`, and `role` where native semantics fall short; announce async updates with a polite live region. Manage focus on route/modal changes (move focus into the dialog, return it on close).
7. **Respect the user.** Honor `prefers-reduced-motion`; don't disable zoom; support 200% text without clipping.

## Quick Reference
```
Landmarks: header / nav / main / aside / footer  (one <main> per page)
Headings:  one <h1>, no skipped levels (h2→h3, not h2→h4)
Forms:     <label for> + input id  ·  group with <fieldset>/<legend>
Dialog:    role="dialog" aria-modal="true", focus-trap, Esc closes, restore focus
Live:      aria-live="polite" for status; "assertive" only for errors
Tools:     axe DevTools · Lighthouse a11y · keyboard-only pass · screen reader (VoiceOver/NVDA)
```

## Pitfalls
- ARIA over native: `role="button"` on a div instead of `<button>` (now you owe keyboard + focus handling, and usually skip it).
- Removing focus outlines for looks, stranding keyboard users.
- Placeholder text used as the only label (disappears on input, low contrast).
- `aria-label` on non-interactive elements, or redundant labels that double-announce.
- Color-only status (red/green) unreadable to colorblind users.
- Auto-focus/scroll stealing, or focus lost to the page top after a modal closes.

## Verification
- Full primary flow completable with **keyboard only**, with focus always visible and in logical order.
- Automated scan (axe / Lighthouse) reports zero critical violations; remaining items reviewed, not ignored.
- Every control has an accessible name; a screen-reader pass of the main flow makes sense read aloud.
- Contrast checked on text and controls; no information conveyed by color alone.
- Layout holds at 200% zoom and with reduced motion enabled.
