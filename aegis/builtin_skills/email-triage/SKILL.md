---
name: email-triage
description: Triage an inbox or a batch of emails — classify by urgency/action, summarize each, and draft replies. Use when asked to "go through my email", clear an inbox, or respond to messages.
version: 1.0.0
metadata:
  category: productivity
  tags: [email, inbox, triage, communication, assistant]
---

## When to Use
The user wants help processing email: triaging an inbox, summarizing a thread, deciding what needs a reply, or drafting responses. Works on email fed in as text/files, or live via the gateway's email channel.

## Procedure
1. **Gather.** Read the emails (`read_file` for exported `.eml`/`.txt`/`.mbox`, or the message body provided). For a live inbox, ask for an export or the relevant messages — don't assume access you don't have.
2. **Classify each** into exactly one bucket: `urgent` (needs action today), `reply` (needs a response, not urgent), `fyi` (read-only), `archive` (no action). Note the sender, the ask, and any deadline.
3. **Summarize the batch** as a short table-free list: `• [bucket] sender — one-line ask (deadline)`. Lead with `urgent`.
4. **Draft replies** for `urgent` and `reply` items. Match the sender's tone; be concise; state the decision/answer up front; include a clear next step. Never invent facts, commitments, or dates — if you need info to answer, flag `[NEEDS: …]` instead of guessing.
5. **Confirm before sending.** Present drafts for approval. Only send/deliver (e.g. via the email channel or `send_message`) after the user approves, or if they explicitly said "send".

## Output
A triage summary (buckets, counts) followed by ready-to-send drafts, each headed with `To / Subject`. Flag anything you couldn't answer with `[NEEDS: …]`.

## Guardrails
- Never fabricate a commitment, price, or date on the user's behalf.
- Don't auto-send unless told to; default to draft-and-confirm.
