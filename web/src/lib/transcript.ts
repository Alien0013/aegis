// Turn raw stored session messages into a clean chat transcript: only the
// user/assistant conversation, with all agent scaffolding removed. This is what
// makes the Chat page "just the chat" — no tool dumps, no injected prompts.

export interface RawMessage {
  role: string;
  content: string;
}
export interface CleanTurn {
  role: "user" | "bot";
  text: string;
}

// Wrapper tags the loop injects around tool/volatile content.
const WRAPPERS = [
  /<system-reminder>[\s\S]*?<\/system-reminder>/g,
  /<untrusted_tool_result[^>]*>[\s\S]*?<\/untrusted_tool_result>/g,
  /<persisted-output>[\s\S]*?<\/persisted-output>/g,
  /<retrieved_memory>[\s\S]*?<\/retrieved_memory>/g,
];

// User-role messages the loop injects to steer the model — never shown to a human.
const INJECTED_PREFIXES = [
  "Continue exactly where you left off",
  "You returned an empty reply",
  "You've reached the step limit",
  "[user steering]",
  "[interrupted by user]",
];

export function stripScaffolding(text: string): string {
  let out = text || "";
  for (const re of WRAPPERS) out = out.replace(re, "");
  return out.trim();
}

function isInjected(text: string): boolean {
  const t = (text || "").trim();
  return INJECTED_PREFIXES.some((p) => t.startsWith(p));
}

export function cleanTranscript(messages: RawMessage[]): CleanTurn[] {
  const turns: CleanTurn[] = [];
  for (const m of messages || []) {
    const role = m.role;
    if (role !== "user" && role !== "assistant") continue; // drop system + tool
    if (role === "user" && isInjected(m.content)) continue; // drop steering nudges
    const text = stripScaffolding(m.content);
    if (!text) continue; // drop empty / pure-scaffolding turns
    turns.push({ role: role === "user" ? "user" : "bot", text });
  }
  return turns;
}
