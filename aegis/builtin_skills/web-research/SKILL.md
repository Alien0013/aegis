---
name: web-research
description: Research a topic on the web and produce a concise, cited summary. Use when the user asks a question that needs current information or sources.
version: 1.0.0
metadata:
  category: research
  tags: [web, research, citations]
---

## When to Use
Use this skill when a question depends on current facts, comparisons, or anything
outside your training data, and the user wants sources.

## Procedure
1. Decompose the question into 3–6 focused sub-queries.
2. Call `web_search` for each sub-query.
3. Call `web_fetch` on the 2–4 most relevant URLs to read the actual content.
4. Cross-check claims across at least two independent sources before stating them.
5. Note disagreements explicitly rather than averaging them away.

## Output format
- A short answer first (2–4 sentences).
- Then key findings as bullets, each with the source URL in parentheses.
- End with a "Sources" list of the URLs you actually used.

## Pitfalls
- Do not state a fact found in only one low-quality source as settled.
- Prefer primary sources and official docs over aggregator blogs.

## Verification
Every non-obvious claim in the summary must trace to a URL you fetched.
