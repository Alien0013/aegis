---
name: social-post
description: Draft platform-tuned social media posts (X/LinkedIn/threads) from an idea, link, or announcement — with hooks, length limits, and variants. Use for "write a post about…", "announce X", or a content thread.
version: 1.0.0
metadata:
  category: social-media
  tags: [social, marketing, content, writing, assistant]
---

## When to Use
The user wants social copy for a specific platform. Ask which platform if unstated (the constraints differ a lot).

## Procedure
1. **Clarify** the platform, the goal (awareness / clicks / engagement), and the audience. If there's a link, `web_fetch` it so the post is accurate, not generic.
2. **Respect the medium:**
   - **X/Twitter** — ≤280 chars per post; lead with a hook in the first line; threads = numbered, one idea each.
   - **LinkedIn** — a strong first line (it's the only part shown collapsed), short paragraphs, no hashtag spam (1-3 max), a clear takeaway.
   - **Mastodon/Threads** — conversational, low-hype.
3. **Hook first.** The opening line earns the rest. No "I'm excited to announce" unless it's genuinely an announcement.
4. **Give 2-3 variants** (different angles/hooks) so the user can pick, plus an optional thread expansion.
5. **No fabrication.** Don't invent stats, quotes, or features. Mark anything that needs a real number as `[fill in: …]`.

## Guardrails
- Match the user's voice if you have prior examples; otherwise keep it plain and specific.
- Don't auto-post; draft and let the user choose. Deliver via `send_message` only if asked.
