---
name: data-analysis
description: Analyze CSV/JSON/tabular data with pandas: load, clean, aggregate, correlate, and summarize findings. Use when asked to analyze a data file or produce stats/insights.
version: 1.0.0
metadata:
  category: data
  tags: [pandas, csv, stats, eda]
requires:
  env: [python3]
---

## When to Use
When the user points at a CSV/JSON/Parquet/Excel file (or pasted tabular data) and wants stats, aggregations, correlations, trends, or a written summary of insights.

## Procedure
1. Inspect first with bash before loading: `head -5 file.csv`, `wc -l file.csv` to gauge size and delimiter. Use read_file for small files only.
2. Use execute_code (Python) for everything else. Load: `pd.read_csv(path)` (or `read_json`/`read_parquet`/`read_excel`). For >1GB, pass `usecols=`/`dtype=`/`chunksize=`.
3. Profile: print `df.shape`, `df.dtypes`, `df.head()`, `df.describe(include='all')`, `df.isna().sum()`, and `df.nunique()`.
4. Clean explicitly and state each choice: dedupe (`drop_duplicates`), handle NaN (drop vs fill — never silently), fix dtypes (`pd.to_datetime`, `astype`), strip/normalize strings.
5. Aggregate: `df.groupby(keys).agg({...})`; pivot with `pd.pivot_table`. Sort and round results for readability.
6. Correlate: `df.select_dtypes('number').corr()`; flag |r|>0.7 pairs. Mention correlation != causation.
7. Summarize findings in prose: top drivers, outliers, missing-data caveats, and 2-4 concrete insights with the numbers that back them.
8. Save derived outputs with write_file if requested (`df.to_csv(..., index=False)`).

## Quick Reference
```python
import pandas as pd
df = pd.read_csv("data.csv")
df.info(); df.describe(include="all")
df.isna().mean().sort_values(ascending=False)      # % missing per col
df.groupby("cat")["val"].agg(["count","mean","sum"])
df.select_dtypes("number").corr()
```

## Pitfalls
- Don't read huge files into the chat context; profile via bash/execute_code instead.
- Watch encoding/delimiter: try `encoding="latin-1"`, `sep=";"` on parse errors.
- Numeric columns parsed as object usually hide commas, `$`, or `%` — strip then `pd.to_numeric(..., errors="coerce")`.
- Mean is misleading with skew/outliers; report median + quantiles too.
- `groupby` silently drops NaN keys (`dropna=False` to keep).
- Never infer causation from correlation in the summary.

## Verification
- Row count after cleaning is explained (matches original minus stated drops).
- No unexpected NaN in reported aggregates; counts per group sum to total.
- Spot-check one aggregate by hand against a filtered slice.
- Every stated insight cites a number reproducible from the printed output.
