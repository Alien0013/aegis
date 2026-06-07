---
name: summarize
description: Summarize long documents, codebases, or transcripts into faithful, structured summaries at the requested length. Use when asked to summarize or TL;DR.
version: 1.0.0
metadata:
  category: writing
  tags: [summary, documents, transcripts, codebase]
---

## When to Use
When the user asks to summarize, condense, TL;DR, or extract key points from a long document, codebase, transcript, paper, or thread — at a specific length or level of detail.

## Procedure
1. Clarify the target if ambiguous: length (one-line / paragraph / N bullets), audience, and focus. If unstated, default to a 3-5 bullet structured summary.
2. Ingest the source fully before writing:
   - Files: `read_file` (chunk large files; read all parts, never sample).
   - Codebase: `bash` `wc -l`, `ls -R`, `git log --oneline -20`; read entry points, configs, and module headers.
   - URL/web: `web_search` / WebFetch to retrieve, or read provided transcript files.
3. Map structure: identify sections, themes, decisions, action items, or modules. Note the document's own hierarchy — mirror it.
4. Extract only claims present in the source. Tag uncertain items; never invent specifics (numbers, names, dates).
5. Draft at the requested length. Lead with the single most important takeaway, then supporting points.
6. Tighten: remove hedging and repetition; keep load-bearing nouns/numbers verbatim from source.

## Quick Reference
- Line/word count: `wc -lw file`
- Repo shape: `git ls-files | sed 's#/.*##' | sort -u`
- Recent changes: `git log --oneline -20`
- Long file in chunks: `read_file` with offset/limit
- Default structure: **TL;DR** (1 line) → **Key points** (bullets) → **Details/Action items** (if asked)

## Pitfalls
- Summarizing from a partial read — read the whole source first.
- Hallucinating figures, names, or conclusions not in the text.
- Editorializing or adding opinions; stay faithful and neutral.
- Ignoring the requested length (too long is a failure too).
- Flattening structure — preserve the source's sections/ordering when meaningful.
- Dropping caveats, dissent, or open questions that change meaning.

## Verification
- Every claim traces to a specific source passage (spot-check 2-3).
- Length/format matches what was requested.
- No invented specifics: numbers, names, dates appear in the source.
- A reader of the summary alone would not be misled about the source's main point.
