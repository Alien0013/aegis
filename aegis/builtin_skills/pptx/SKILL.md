---
name: pptx
description: Create PowerPoint .pptx presentations with slides, layouts, and visuals (python-pptx). Use when asked to build a slide deck.
version: 1.0.0
metadata:
  category: documents
  tags: [pptx, powerpoint, slides, python-pptx]
requires:
  bins: [python3]
  env: []
---

## When to Use
When asked to generate, build, or edit a PowerPoint (.pptx) slide deck — title slides, bullets, tables, images, charts, or custom layouts.

## Procedure
1. Ensure the lib is installed: `python3 -c "import pptx" || pip install python-pptx`.
2. Plan slides first: list each slide's title + content/visual in a short outline before coding.
3. Write a build script with write_file (e.g. `build_deck.py`) using python-pptx (see Quick Reference).
4. Pick layouts by index from `prs.slide_layouts` (0=title, 1=title+content, 5=title only, 6=blank). Don't guess names.
5. Add content via placeholders (`slide.placeholders[idx]`) or absolute-positioned text boxes/shapes using `Inches()`/`Pt()`.
6. Run it with bash: `python3 build_deck.py`. Save with `prs.save("out.pptx")`.
7. Verify the file exists and opens (see Verification). Iterate with edit_file if layout is off.

## Quick Reference
```python
from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor
prs = Presentation()                          # or Presentation("template.pptx")
s = prs.slides.add_slide(prs.slide_layouts[0])
s.shapes.title.text = "My Deck"
s.placeholders[1].text = "Subtitle"
# bullets
body = prs.slides.add_slide(prs.slide_layouts[1]).placeholders[1].text_frame
body.text = "First"; p = body.add_paragraph(); p.text = "Second"; p.level = 1
# image / textbox
s.shapes.add_picture("img.png", Inches(1), Inches(1), width=Inches(4))
tb = s.shapes.add_textbox(Inches(1), Inches(1), Inches(4), Inches(1)).text_frame
tb.text = "hi"; tb.paragraphs[0].runs[0].font.size = Pt(24)
prs.save("out.pptx")
```
Slide size: `prs.slide_width = Inches(13.333)` for 16:9.

## Pitfalls
- `placeholders[1]` index varies by layout; iterate `for ph in slide.placeholders: print(ph.placeholder_format.idx, ph.name)` to find the right one.
- Title-only/blank layouts have no body placeholder — add a textbox instead.
- Colors need `RGBColor(0x1F,0x4E,0x79)`, not hex strings.
- Native pptx charts need `from pptx.chart.data import CategoryChartData`; for complex charts render a PNG (matplotlib) and add_picture.
- Overwriting an existing deck: open it with `Presentation(path)`, don't start fresh.

## Verification
- `python3 -c "from pptx import Presentation; p=Presentation('out.pptx'); print(len(p.slides.__iter__.__self__._sldIdLst), 'slides')"` runs without error.
- Confirm file size > 0 and slide count matches the outline.
