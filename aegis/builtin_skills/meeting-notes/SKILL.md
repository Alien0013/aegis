---
name: meeting-notes
description: Turn a meeting transcript or recording into clean notes — summary, decisions, and assigned action items with owners and dates. Use for "summarize this meeting", "what did we decide", or processing a transcript.
version: 1.0.0
metadata:
  category: productivity
  tags: [meetings, notes, summary, action-items, assistant]
---

## When to Use
The user has a transcript, recording, or raw notes from a meeting and wants structured output. If given audio, transcribe it first (`transcribe` tool) — otherwise `read_file` the transcript.

## Procedure
1. **Ingest.** Transcribe audio or read the transcript. If it's long, process it in order; don't drop the tail.
2. **Extract, don't paraphrase loosely.** Pull out: key topics, **decisions made** (quote or tightly paraphrase), **action items** (who / what / by when), open questions, and notable risks.
3. **Attribute owners.** For each action item, name the owner if stated; mark `owner: unassigned` rather than guessing. Same for dates — `due: unstated` if not given.
4. **Write the notes** in this shape:
   - **Summary** — 2-4 sentences.
   - **Decisions** — bullet list.
   - **Action items** — `• [owner] task — due <date|unstated>`.
   - **Open questions** — bullet list.
5. **Save/deliver.** Offer to `write_file` the notes (e.g. `notes/YYYY-MM-DD-<topic>.md`) and, if asked, `send_message` them to a channel.

## Guardrails
- Never invent a decision, owner, or deadline that wasn't in the source. Mark gaps explicitly.
- Keep quotes accurate; flag anything ambiguous rather than smoothing it over.
