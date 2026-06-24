---
name: frontend-design
description: Build clean, accessible, responsive UI components with good visual hierarchy and modern CSS. Use when asked to design or build a frontend/UI.
version: 1.0.0
metadata:
  category: frontend
  tags: [ui, css, accessibility, responsive]
---

## When to Use
When asked to design or build a frontend, UI component, page, or layout — especially when visual quality, accessibility, and responsiveness matter.

## Procedure
1. Clarify intent: target framework (vanilla/React/Vue), light/dark mode, brand colors. If unstated, default to vanilla HTML/CSS, system fonts, light mode. Read existing files (read_file) to match the project's stack/style before writing anything.
2. Establish design tokens FIRST: define CSS custom properties for color, spacing scale (4/8px base), font sizes (modular scale ~1.25x), radius, shadow. Put them in `:root`.
3. Structure semantically: use `<header><nav><main><section><button>` etc. — not `<div>` soup. One `<h1>` per page; headings nest in order.
4. Layout with Flexbox/Grid. Use `clamp()` for fluid type/spacing; mobile-first `min-width` media queries only where layout truly breaks.
5. Apply visual hierarchy: limit to 2 fonts, 1 accent color, 2-3 weights. Generous whitespace. Align to the spacing scale.
6. Add accessibility: visible `:focus-visible` ring, `aria-label` on icon buttons, alt text, 4.5:1 contrast, `prefers-reduced-motion` guard on animations.
7. Add subtle polish: 150-200ms transitions on hover/focus, soft shadows, hover states. No gratuitous animation.
8. Write files with write_file/edit_file. Verify in a browser or with execute_code (e.g. a headless render/screenshot) where possible.

## Quick Reference
```css
:root{
  --space:8px; --radius:10px; --accent:#4f46e5;
  --fg:#1a1a2e; --bg:#fff; --muted:#6b7280;
  --shadow:0 1px 3px rgba(0,0,0,.1),0 1px 2px rgba(0,0,0,.06);
  color-scheme: light dark;
}
h1{font-size:clamp(1.75rem,4vw,3rem);line-height:1.1}
.btn{padding:calc(var(--space)*1.5) calc(var(--space)*3);
  border-radius:var(--radius);background:var(--accent);color:#fff;
  border:0;transition:filter .15s,transform .15s}
.btn:hover{filter:brightness(1.08)} .btn:active{transform:translateY(1px)}
:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
@media(prefers-reduced-motion:reduce){*{transition:none!important}}
```
Layout: `display:grid;gap:var(--space);grid-template-columns:repeat(auto-fit,minmax(min(280px,100%),1fr))`

## Pitfalls
- Fixed `px` widths that overflow on mobile — use `max-width` + `%`/`fr`/`minmax`.
- Removing focus outlines without a replacement (breaks keyboard nav).
- Low contrast gray-on-white text; verify 4.5:1.
- Too many fonts/colors/shadows — restraint reads as "designed".
- Hardcoded colors instead of tokens; blocks theming/dark mode.

## Verification
- Resize to 360px and 1440px: no horizontal scroll, no overlap.
- Tab through: every interactive element shows a focus ring, order is logical.
- Run an a11y/contrast check (axe or Lighthouse) and confirm valid semantic HTML.

## Learned Notes
- When building a standalone product/landing page via a subagent or temporary artifact, require a concrete verification pass before reporting success: check the output file exists, expected H1/product name is present, semantic landmarks exist (`nav`, `main`, multiple `section`s), any product/process diagram renders or is present, keyboard focus styling (`:focus-visible`) exists, and `prefers-reduced-motion` guards animations. Report the artifact path and these verification facts instead of only describing the design.
