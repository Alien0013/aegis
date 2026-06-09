---
name: expense-summary
description: Summarize expenses from a CSV/receipts/statement — totals by category, top spends, and anomalies. Use for "summarize my spending", "categorize these expenses", or a budget check.
version: 1.0.0
metadata:
  category: finance
  tags: [finance, expenses, budget, csv, analysis]
---

## When to Use
The user has transaction data (CSV, bank export, list of receipts) and wants it summarized or categorized. Compute from the data — never estimate numbers.

## Procedure
1. **Load the data.** `read_file` the CSV/export. Identify the columns (date, description, amount, currency). If amounts mix signs, confirm the sign convention (debits vs credits).
2. **Compute with code, not in your head.** Use `execute_code` (pandas or stdlib `csv`) to: total spend, spend by category, top N line items, and month-over-month change if multiple periods exist. Categorize by description keywords; put unknowns in `uncategorized` rather than guessing.
3. **Flag anomalies** — unusually large charges, duplicates (same amount+merchant+day), and any category that jumped vs prior period.
4. **Report:**
   - **Total** (with currency and period).
   - **By category** — sorted desc, with % of total.
   - **Top items** and **flags**.
5. **Offer** to `write_file` a cleaned/categorized CSV.

## Guardrails
- Every number must come from the data via code — show the computation, don't approximate.
- This is summarization, not financial/tax advice; say so if asked for advice.
