---
name: docx
description: Create and edit Microsoft Word .docx documents programmatically (python-docx). Use when asked to produce or modify a Word document.
version: 1.0.0
metadata:
  category: documents
  tags: [docx, word, python-docx, office]
requires:
  env: [python3]
---

## When to Use
- Asked to generate, fill, or edit a `.docx` Word file (reports, letters, templates).
- Programmatic text/table/image insertion or find-and-replace in an existing doc.
- NOT for `.doc` (legacy binary) — convert first; not for PDFs.

## Procedure
1. Ensure dep: `python3 -c "import docx" 2>/dev/null || pip install python-docx` (bash).
2. For edits, confirm the source file exists with read_file or `ls`.
3. Write a script with write_file, then run it with execute_code / bash. Key API:
   - `from docx import Document`
   - New: `doc = Document()`. Edit: `doc = Document("in.docx")`.
   - Headings/text: `doc.add_heading("Title", level=1)`, `doc.add_paragraph("text")`.
   - Runs for formatting: `p = doc.add_paragraph(); r = p.add_run("bold"); r.bold = True`.
   - Tables: `t = doc.add_table(rows=1, cols=3); t.style = "Light Grid Accent 1"`; fill `t.rows[i].cells[j].text = ...`.
   - Images: `doc.add_picture("img.png", width=Inches(4))` (`from docx.shared import Inches, Pt, RGBColor`).
4. Always `doc.save("out.docx")` at the end (saving over the input is fine).
5. For find/replace, iterate `doc.paragraphs` and each paragraph's `.runs` (replacing `paragraph.text` wholesale loses formatting).

## Quick Reference
```python
from docx import Document
from docx.shared import Pt, Inches, RGBColor
doc = Document()                          # or Document("template.docx")
doc.add_heading("Report", 0)
p = doc.add_paragraph("Hello "); p.add_run("world").bold = True
t = doc.add_table(rows=2, cols=2); t.style = "Table Grid"
doc.save("out.docx")
```
Replace across runs (single-run case):
```python
for para in doc.paragraphs:
    for run in para.runs:
        if "{{name}}" in run.text:
            run.text = run.text.replace("{{name}}", "Alice")
```

## Pitfalls
- Text split across multiple runs: a placeholder may span runs, so naive run-level replace misses it — join/clear runs or set `runs[0].text` and blank the rest.
- `paragraph.text = ...` drops all formatting and inline images — avoid for edits.
- Text inside tables/headers/footers is not in `doc.paragraphs`; iterate `table.rows[].cells[].paragraphs` and `section.header/footer` too.
- `add_picture` without a width can overflow the page; pass `Inches(...)`.
- Styles must already exist in the doc (e.g. "Table Grid"); a bad name raises KeyError.

## Verification
- Script exits 0 and `out.docx` exists: `ls -la out.docx`.
- Re-open to confirm content: `python3 -c "from docx import Document; d=Document('out.docx'); print(len(d.paragraphs), [p.text for p in d.paragraphs][:5])"`.
- For edits, diff expected strings are present and target placeholders gone.
