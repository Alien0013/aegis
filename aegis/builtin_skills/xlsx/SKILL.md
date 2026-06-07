---
name: xlsx
description: Create and edit Excel .xlsx spreadsheets with formulas, formatting, and charts (openpyxl). Use for spreadsheet generation or data export.
version: 1.0.0
requires:
  env: [python3]
metadata:
  category: spreadsheets
  tags: [excel, xlsx, openpyxl, export]
---

## When to Use
- Generate a new `.xlsx` from data (lists, dicts, query results).
- Edit an existing workbook: add sheets, formulas, formatting, or charts.
- Export tabular data when CSV is insufficient (multi-sheet, styled, formula-driven).

## Procedure
1. Ensure dep: `python3 -c "import openpyxl"` (bash); if missing, `pip install openpyxl`.
2. For pure DataFrame dumps, prefer `pandas.to_excel` (still needs openpyxl).
3. Write a script via `write_file`, then run with `execute_code`/bash. Key steps:
   - New: `wb = openpyxl.Workbook(); ws = wb.active; ws.title = "Data"`.
   - Edit: `wb = openpyxl.load_workbook("f.xlsx")` (add `data_only=True` to read computed values).
   - Append rows: `ws.append([col1, col2, ...])` or set `ws["A1"] = val` / `ws.cell(row, column, val)`.
   - Formulas: assign the string, e.g. `ws["C2"] = "=A2*B2"` (no spaces in cell refs).
   - Always `wb.save("out.xlsx")` at the end.
4. Read back with `data_only=True` to verify, or open in a viewer.

## Quick Reference
```python
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment
from openpyxl.chart import BarChart, Reference

wb = openpyxl.Workbook(); ws = wb.active; ws.title = "Sales"
ws.append(["Item", "Qty", "Price", "Total"])
for c in ws[1]:                                   # header style
    c.font = Font(bold=True); c.fill = PatternFill("solid", fgColor="DDDDDD")
ws.append(["Widget", 3, 9.99]); ws["D2"] = "=B2*C2"
ws.column_dimensions["A"].width = 18
ws["C2"].number_format = "$#,##0.00"

chart = BarChart()
chart.add_data(Reference(ws, min_col=4, min_row=1, max_row=ws.max_row), titles_from_data=True)
chart.set_categories(Reference(ws, min_col=1, min_row=2, max_row=ws.max_row))
ws.add_chart(chart, "F2")
wb.save("sales.xlsx")
```

## Pitfalls
- `load_workbook` default keeps formula strings; use `data_only=True` to get cached values (only present if Excel/LibreOffice last saved it — openpyxl does not compute).
- Rows/cols are 1-indexed; `ws.cell(0, ...)` raises.
- Saving overwrites without warning and drops unsupported features (macros need `keep_vba=True` + `.xlsm`).
- Numbers passed as strings become text cells — keep them numeric for formulas/sums.
- Charts/images are lost on round-trip load+save in some openpyxl versions; regenerate if editing chart-heavy files.

## Verification
- `python3 -c "import openpyxl; wb=openpyxl.load_workbook('out.xlsx', data_only=True); ws=wb.active; print(ws.max_row, ws.max_column, ws['A1'].value)"`.
- Confirm file exists and is non-trivial: `ls -la out.xlsx` (bash).
