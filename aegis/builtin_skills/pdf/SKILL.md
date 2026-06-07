---
name: pdf
description: Extract text and data from a PDF file, or fill/split/merge PDFs. Use when the user references a .pdf file.
version: 1.0.0
metadata:
  category: documents
  tags: [pdf, extract, documents]
---

## When to Use
Any task that involves reading or manipulating a PDF.

## Procedure
1. For text extraction, prefer (in order): `pdftotext file.pdf -` (poppler),
   then `python -c "import pypdf; ..."`, then the `bash` tool with whatever is installed.
2. For structured extraction (tables/forms), extract page-by-page and reason over the text.
3. For split/merge, use `pdftk` or `pypdf` via the `bash`/`execute_code` tool.

## Quick Reference
- All text: `pdftotext input.pdf out.txt`
- One page: `pdftotext -f 3 -l 3 input.pdf -`
- Page count: `pdfinfo input.pdf | grep Pages`

## Pitfalls
- Scanned PDFs have no text layer — say so and offer OCR (`ocrmypdf`) if available.

## Verification
Confirm the extracted text matches what the user asked for before summarizing.
