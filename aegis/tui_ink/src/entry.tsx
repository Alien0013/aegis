/**
 * AEGIS terminal UI — Node/Ink front-end (v2).
 *
 * Full-screen alternate-screen layout: a fixed header, a bottom-anchored scrollable
 * message viewport that renders structured components (user/assistant bubbles, tool cards
 * with status pills, thinking, notices) plus captured ANSI output, a live status bar, and a
 * persistent composer. Talks to the Python gateway (aegis.tui_gateway) over a local
 * WebSocket. Original AEGIS code; only the public Ink/React API is used.
 */

import React, {useEffect, useReducer, useRef, useState} from 'react';
import {Box, Text, render, useApp, useInput, useStdout} from 'ink';
import TextInput from 'ink-text-input';
import WebSocket from 'ws';

// Terminal palettes, keyed by the same names as the dashboard themes so the TUI tracks
// the configured theme (the launcher passes AEGIS_TUI_THEME from display.theme; the env
// var still wins if set directly). Unknown names fall back to the signature aegis-dark.
type Palette = {amber: string; green: string; cyan: string; red: string; muted: string; panel: string; code: string};
const THEMES: Record<string, Palette> = {
  'aegis-dark': {amber: '#d6a15e', green: '#7ecf8f', cyan: '#6fb7d8', red: '#e96e6e', muted: '#8f968f', panel: '#262a31', code: '#cdd6c4'},
  'aegis-light': {amber: '#9a6b1f', green: '#0c8f88', cyan: '#2f6bff', red: '#d83a52', muted: '#6b7280', panel: '#e6e8ec', code: '#16191f'},
  'midnight': {amber: '#a78bfa', green: '#34d399', cyan: '#22d3ee', red: '#fb7185', muted: '#8b8baf', panel: '#1c1c40', code: '#d7d4ff'},
  'ember': {amber: '#f97316', green: '#84cc16', cyan: '#fb923c', red: '#ef4444', muted: '#b08c78', panel: '#321a10', code: '#ffe0c8'},
  'mono': {amber: '#cfcfcf', green: '#bdbdbd', cyan: '#dddddd', red: '#e57373', muted: '#7d7d7d', panel: '#202020', code: '#d6d6d6'},
  'cyberpunk': {amber: '#fcee0a', green: '#00ff9f', cyan: '#00d4ff', red: '#ff003c', muted: '#7a8aa0', panel: '#1a1a2e', code: '#e0e0ff'},
  'rose': {amber: '#e0709a', green: '#86c79b', cyan: '#88b9d8', red: '#e05c6e', muted: '#9a8a90', panel: '#2a1a22', code: '#f3dde6'},
  'nord': {amber: '#ebcb8b', green: '#a3be8c', cyan: '#88c0d0', red: '#bf616a', muted: '#7b88a1', panel: '#3b4252', code: '#d8dee9'},
  'dracula': {amber: '#f1fa8c', green: '#50fa7b', cyan: '#8be9fd', red: '#ff5555', muted: '#8a8fb3', panel: '#343746', code: '#f8f8f2'},
  'gruvbox': {amber: '#fabd2f', green: '#b8bb26', cyan: '#83a598', red: '#fb4934', muted: '#a89984', panel: '#3c3836', code: '#ebdbb2'},
  'solarized': {amber: '#b58900', green: '#859900', cyan: '#2aa198', red: '#dc322f', muted: '#839496', panel: '#073642', code: '#eee8d5'},
  'latte': {amber: '#df8e1d', green: '#40a02b', cyan: '#209fb5', red: '#d20f39', muted: '#8c8fa1', panel: '#e6e9ef', code: '#4c4f69'},
};
const THEME = THEMES[(process.env.AEGIS_TUI_THEME || 'aegis-dark').toLowerCase()] || THEMES['aegis-dark'];
const AMBER = THEME.amber;
const GREEN = THEME.green;
const CYAN = THEME.cyan;
const RED = THEME.red;
const MUTED = THEME.muted;
const PANEL = THEME.panel;

const ANSI_RE = /\x1b\[[0-9;]*m/g;

// Glyphs are gated on the gateway's Unicode detection (AEGIS_ASCII / terminal encoding) and
// kept to reliable single-width BMP symbols — no multi-codepoint emoji, which misalign in
// flex layouts on terminals with weak fonts. ASCII fallbacks keep the UI legible anywhere.
const ICONS_UNI: Record<string, string> = {
  bash: '$', edit_file: '✎', write_file: '✎', apply_patch: '✎', read_file: '▤',
  list_dir: '▤', glob: '▤', search: '⌕', code_search: '⌕', web_search: '↗',
  web_fetch: '↗', web_extract: '↗', http_request: '↗', browser: '◈', memory: '◆',
  skill: '▣', kanban: '▦', cronjob: '◷', schedule_task: '◷', spawn_subagent: '↳',
  todo_write: '☑', generate_image: '▩', vision_analyze: '◉', execute_code: '⚙',
};
const ICONS_ASCII: Record<string, string> = {
  bash: '$', edit_file: '*', write_file: '*', apply_patch: '*', read_file: '-',
  list_dir: '-', glob: '-', search: '?', code_search: '?', web_search: '@',
  web_fetch: '@', web_extract: '@', http_request: '@', browser: '#', memory: 'M',
  skill: 'S', kanban: 'K', cronjob: 'T', schedule_task: 'T', spawn_subagent: '>',
  todo_write: 'x', generate_image: 'I', vision_analyze: 'V', execute_code: 'C',
};
const SPIN_UNI = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏'];
const SPIN_ASCII = ['|', '/', '-', '\\'];

// Unicode capability is detected on the *client* — only the Ink process knows its own TTY
// and locale. Honours the same AEGIS_ASCII / AEGIS_UNICODE escapes as the Python surface.
function detectUnicode(): boolean {
  const env = process.env;
  if (/^(1|true|yes|on)$/i.test(env.AEGIS_ASCII || '')) return false;
  if (env.AEGIS_UNICODE != null) return /^(1|true|yes|on)$/i.test(env.AEGIS_UNICODE);
  if ((env.TERM || '').toLowerCase() === 'dumb') return false;
  const enc = (env.LC_ALL || env.LC_CTYPE || env.LANG || '').toLowerCase();
  if (/utf-?8/.test(enc)) return true;
  return Boolean(process.stdout.isTTY); // modern terminals are UTF-8 by default
}
const CLIENT_UNI = detectUnicode();

function glyphs(uni: boolean) {
  return {
    icons: uni ? ICONS_UNI : ICONS_ASCII,
    iconDefault: uni ? '◇' : '*',
    spinner: uni ? SPIN_UNI : SPIN_ASCII,
    brand: uni ? '◆ AEGIS' : 'AEGIS',
    arrow: uni ? '❯' : '>',
    ok: uni ? '✓' : 'ok',
    bad: uni ? '✗' : 'x',
    barFull: uni ? '█' : '#',
    barEmpty: uni ? '░' : '-',
    sep: uni ? '│' : '|',
    dot: uni ? '·' : '-',
    cursor: uni ? '▋' : '_',
    sub: uni ? '↳' : '>',
    cont: uni ? '↻' : '~',
    down: uni ? '↘' : 'v',
    up: uni ? '⇡' : '^',
    bullet: uni ? '•' : '*',
    quote: uni ? '▏' : '|',
    rule: uni ? '─' : '-',
    check: uni ? '☑' : '[x]',
    uncheck: uni ? '☐' : '[ ]',
  };
}

const CODE = THEME.code;

type Header = {
  brand?: string; model?: string; session_id?: string; session_title?: string;
  ctx_used?: number; ctx_window?: number; ctx_percent?: number;
  input_tokens?: number; output_tokens?: number; cost?: number;
  reasoning?: string; perms?: string; busy?: string; cwd?: string; version?: string;
};

type Msg =
  | {kind: 'user'; text: string}
  | {kind: 'assistant'; text: string; streaming: boolean}
  | {kind: 'thinking'; chars: number; done: boolean}
  | {kind: 'tool'; name: string; preview: string; status: 'running' | 'ok' | 'error'; ms?: number; summary?: string}
  | {kind: 'notice'; text: string; tone: 'info' | 'warn' | 'good'}
  | {kind: 'output'; text: string};

function fmtTokens(n?: number): string {
  if (!n) return '0';
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + 'M';
  if (n >= 1000) return (n / 1000).toFixed(1) + 'k';
  return String(n);
}
function ctxBar(p = 0, full = '█', empty = '░', w = 10): string {
  const v = Math.max(0, Math.min(100, p));
  const f = Math.round((v / 100) * w);
  return full.repeat(f) + empty.repeat(w - f);
}
function visibleWidth(s: string): number {
  return s.replace(ANSI_RE, '').length;
}
function estimateRows(m: Msg, cols: number): number {
  const wrap = (t: string) =>
    t.split('\n').reduce((a, ln) => a + Math.max(1, Math.ceil(visibleWidth(ln) / Math.max(1, cols))), 0);
  switch (m.kind) {
    case 'user': return wrap(m.text);
    case 'assistant': return wrap(m.text || ' ');
    case 'output': return wrap(m.text);
    default: return 1;
  }
}

// --- reducer over the message list -----------------------------------------
type Action =
  | {t: 'event'; e: any}
  | {t: 'output'; text: string}
  | {t: 'user'; text: string};

function lastIndex(ms: Msg[], pred: (m: Msg) => boolean): number {
  for (let i = ms.length - 1; i >= 0; i--) if (pred(ms[i])) return i;
  return -1;
}

function reduce(ms: Msg[], a: Action): Msg[] {
  if (a.t === 'user') return [...ms, {kind: 'user', text: a.text}];
  if (a.t === 'output') {
    const next = [...ms];
    for (const line of a.text.split('\n')) {
      if (line.length) next.push({kind: 'output', text: line});
    }
    return next;
  }
  const e = a.e;
  const next = [...ms];
  const finalizeThinking = () => {
    const ti = lastIndex(next, (m) => m.kind === 'thinking' && !m.done);
    if (ti >= 0) next[ti] = {...(next[ti] as any), done: true};
  };
  switch (e.type) {
    case 'reasoning_delta': {
      const ti = lastIndex(next, (m) => m.kind === 'thinking' && !m.done);
      if (ti >= 0) {
        const cur = next[ti] as Extract<Msg, {kind: 'thinking'}>;
        next[ti] = {...cur, chars: cur.chars + String(e.text || '').length};
      } else {
        next.push({kind: 'thinking', chars: String(e.text || '').length, done: false});
      }
      return next;
    }
    case 'assistant_delta': {
      finalizeThinking();
      const ai = lastIndex(next, (m) => m.kind === 'assistant' && m.streaming);
      if (ai >= 0) {
        const cur = next[ai] as Extract<Msg, {kind: 'assistant'}>;
        next[ai] = {...cur, text: cur.text + String(e.text || '')};
      } else {
        next.push({kind: 'assistant', text: String(e.text || ''), streaming: true});
      }
      return next;
    }
    case 'assistant_message': {
      finalizeThinking();
      const ai = lastIndex(next, (m) => m.kind === 'assistant' && m.streaming);
      if (ai >= 0) {
        next[ai] = {...(next[ai] as any), streaming: false};
      } else if (e.text) {
        next.push({kind: 'assistant', text: String(e.text), streaming: false});
      }
      return next;
    }
    case 'tool_start': {
      finalizeThinking();
      next.push({kind: 'tool', name: String(e.name || 'tool'),
        preview: String(e.preview || e.text || ''), status: 'running'});
      return next;
    }
    case 'tool_result': {
      const ti = lastIndex(next, (m) => m.kind === 'tool' && m.status === 'running' && m.name === e.name);
      const idx = ti >= 0 ? ti : lastIndex(next, (m) => m.kind === 'tool' && m.status === 'running');
      if (idx >= 0) {
        const cur = next[idx] as Extract<Msg, {kind: 'tool'}>;
        next[idx] = {...cur, status: e.is_error ? 'error' : 'ok',
          ms: Number(e.duration_ms || 0), summary: String(e.summary || '')};
      }
      return next;
    }
    case 'subagent_start':
      next.push({kind: 'notice', tone: 'info',
        text: `subagent ${String(e.agent_type || 'agent')} - ${String(e.task || e.prompt || '')}`.trim()});
      return next;
    case 'subagent_done':
      next.push({kind: 'notice', tone: e.status === 'error' ? 'warn' : 'good',
        text: `subagent ${String(e.status || 'done')}`});
      return next;
    case 'terminal_turn_end': {
      finalizeThinking();
      const ai = lastIndex(next, (m) => m.kind === 'assistant' && m.streaming);
      if (ai >= 0) next[ai] = {...(next[ai] as any), streaming: false};
      return next;
    }
    case 'continuation':
    case 'empty_nudge':
    case 'thinking_strip_retry':
    case 'ultracode_continue':
      next.push({kind: 'notice', tone: 'info', text: String(e.type).replace(/_/g, ' ')});
      return next;
    case 'model_downshift':
      next.push({kind: 'notice', tone: 'warn', text: `budget guard switched model to ${String(e.model || '')}`});
      return next;
    case 'budget_warning':
      next.push({kind: 'notice', tone: 'warn', text: `budget: ${String(e.text || e.summary || 'warning')}`});
      return next;
    default:
      return next;
  }
}

// --- components -------------------------------------------------------------
type G = ReturnType<typeof glyphs>;

const ToolCard: React.FC<{m: Extract<Msg, {kind: 'tool'}>; g: G}> = ({m, g}) => {
  const icon = g.icons[m.name] || g.iconDefault;
  const secs = m.ms ? (m.ms / 1000).toFixed(1) + 's' : '';
  const pill =
    m.status === 'running' ? <Text color={AMBER}>{g.spinner[0]}</Text>
      : m.status === 'error' ? <Text color={RED}>{`${g.bad} ${secs}`}</Text>
        : <Text color={GREEN}>{`${g.ok} ${secs}`}</Text>;
  // diff-aware: surface +adds/-dels for edit/write/patch tools from the result summary
  const stat = parseDiffStat(m.summary || m.preview);
  const summary = (m.summary || '').replace(ANSI_RE, '');
  return (
    <Box>
      <Text color={AMBER}>{`  ${icon} `}</Text>
      <Text color={MUTED} bold>{m.name}</Text>
      <Text> </Text>
      <Text color={MUTED}>{m.preview.slice(0, 80)}</Text>
      <Text>  </Text>
      {pill}
      {stat ? (
        <Text>
          <Text>{'  '}</Text>
          {stat.adds ? <Text color={GREEN}>{`+${stat.adds}`}</Text> : null}
          {stat.adds && stat.dels ? <Text> </Text> : null}
          {stat.dels ? <Text color={RED}>{`-${stat.dels}`}</Text> : null}
        </Text>
      ) : null}
      {summary && !stat && m.status !== 'running'
        ? <Text color={MUTED}>{`  ${summary.slice(0, 60)}`}</Text> : null}
    </Box>
  );
};

function parseDiffStat(text?: string): {adds: number; dels: number} | null {
  if (!text) return null;
  const t = text.replace(ANSI_RE, '');
  // matches "(+3 -1)", "+3/-1", "3 insertions, 1 deletion", etc.
  let m = t.match(/\+(\d+)[\s/,]*-(\d+)/) || t.match(/-(\d+)[\s/,]*\+(\d+)/);
  if (m) {
    const a = t.indexOf('+') < t.indexOf('-') ? [m[1], m[2]] : [m[2], m[1]];
    return {adds: parseInt(a[0], 10), dels: parseInt(a[1], 10)};
  }
  const ins = t.match(/(\d+)\s+insertion/);
  const del = t.match(/(\d+)\s+deletion/);
  if (ins || del) return {adds: ins ? +ins[1] : 0, dels: del ? +del[1] : 0};
  return null;
}

// --- terminal markdown ------------------------------------------------------
// Render the model's markdown the way a chat surface would: headings, lists,
// blockquotes, fenced code, rules, and inline **bold** / *italic* / `code` /
// [links]. Kept compact and dependency-free; the streaming text stays plain
// (with a cursor) and re-renders as formatted markdown once the turn settles.
const INLINE_RE =
  /(\*\*([^*]+)\*\*|__([^_]+)__|(?<!\*)\*(?!\s)([^*]+?)\*|(?<![\w_])_(?!\s)([^_]+?)_(?![\w_])|`([^`]+)`|\[([^\]]+)\]\(([^)]+)\))/;

// Lightweight, language-agnostic code highlighting for fenced blocks — the same
// approach as the dashboard, mapped onto terminal colors. Comments dim, strings
// green, numbers amber, keywords cyan.
const HL_RE =
  /(\/\/[^\n]*|#[^\n]*|--[^\n]*|\/\*[\s\S]*?\*\/)|("(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'|`(?:[^`\\]|\\.)*`)|\b(0x[0-9a-fA-F]+|\d[\d_.]*)\b|\b(const|let|var|function|fn|def|class|struct|enum|interface|type|return|yield|if|elif|else|for|while|do|switch|case|break|continue|in|of|import|from|export|package|use|new|delete|await|async|try|except|catch|finally|throw|raise|with|as|public|private|protected|static|final|void|int|float|double|string|str|bool|boolean|true|false|null|nil|none|None|True|False|undefined|self|this|super|lambda|match|select|insert|update|where|and|or|not)\b/g;

function highlightCode(line: string, keyBase: string): React.ReactNode[] {
  const nodes: React.ReactNode[] = [];
  let last = 0;
  let m: RegExpExecArray | null;
  let k = 0;
  HL_RE.lastIndex = 0;
  while ((m = HL_RE.exec(line)) !== null) {
    if (m.index > last) nodes.push(<Text key={`${keyBase}-${k++}`} color={CODE}>{line.slice(last, m.index)}</Text>);
    if (m[1] != null) nodes.push(<Text key={`${keyBase}-${k++}`} color={MUTED} italic>{m[1]}</Text>);
    else if (m[2] != null) nodes.push(<Text key={`${keyBase}-${k++}`} color={GREEN}>{m[2]}</Text>);
    else if (m[3] != null) nodes.push(<Text key={`${keyBase}-${k++}`} color={AMBER}>{m[3]}</Text>);
    else if (m[4] != null) nodes.push(<Text key={`${keyBase}-${k++}`} color={CYAN}>{m[4]}</Text>);
    last = m.index + m[0].length;
  }
  if (last < line.length) nodes.push(<Text key={`${keyBase}-${k++}`} color={CODE}>{line.slice(last)}</Text>);
  if (!nodes.length) nodes.push(<Text key={`${keyBase}-0`} color={CODE}>{line || ' '}</Text>);
  return nodes;
}

function inlineMd(text: string, keyBase: string): React.ReactNode[] {
  const nodes: React.ReactNode[] = [];
  let rest = text;
  let k = 0;
  while (rest) {
    const m = INLINE_RE.exec(rest);
    if (!m || m.index === undefined) { nodes.push(<Text key={`${keyBase}-${k++}`}>{rest}</Text>); break; }
    if (m.index > 0) nodes.push(<Text key={`${keyBase}-${k++}`}>{rest.slice(0, m.index)}</Text>);
    if (m[2] != null || m[3] != null) {
      nodes.push(<Text key={`${keyBase}-${k++}`} bold>{m[2] ?? m[3]}</Text>);
    } else if (m[4] != null || m[5] != null) {
      nodes.push(<Text key={`${keyBase}-${k++}`} italic>{m[4] ?? m[5]}</Text>);
    } else if (m[6] != null) {
      nodes.push(<Text key={`${keyBase}-${k++}`} color={CYAN} backgroundColor={PANEL}>{` ${m[6]} `}</Text>);
    } else if (m[7] != null) {
      nodes.push(<Text key={`${keyBase}-${k++}`} color={CYAN} underline>{m[7]}</Text>);
    }
    rest = rest.slice(m.index + m[0].length);
  }
  return nodes;
}

const Markdown: React.FC<{text: string; g: G}> = ({text, g}) => {
  const lines = text.replace(/\r/g, '').split('\n');
  const out: React.ReactNode[] = [];
  let i = 0;
  let key = 0;
  while (i < lines.length) {
    const line = lines[i];
    const fence = line.match(/^\s*```(\S*)/);
    if (fence) {
      const lang = fence[1];
      const body: string[] = [];
      i++;
      while (i < lines.length && !/^\s*```/.test(lines[i])) { body.push(lines[i]); i++; }
      i++; // closing fence
      out.push(
        <Box key={key++} flexDirection="column" marginLeft={2}>
          {lang ? <Text color={MUTED}>{`${g.dot} ${lang}`}</Text> : null}
          {body.map((b, j) => (
            <Text key={j}><Text color={MUTED}>{`${g.quote} `}</Text>{highlightCode(b, `c${j}`)}</Text>
          ))}
        </Box>,
      );
      continue;
    }
    const h = line.match(/^(#{1,6})\s+(.*)$/);
    if (h) { out.push(<Text key={key++} color={AMBER} bold>{h[2]}</Text>); i++; continue; }
    if (/^\s*([-*_])(\s*\1){2,}\s*$/.test(line)) {
      out.push(<Text key={key++} color={MUTED}>{g.rule.repeat(24)}</Text>); i++; continue;
    }
    const q = line.match(/^>\s?(.*)$/);
    if (q) {
      out.push(<Text key={key++} color={MUTED}>{`${g.quote} `}{inlineMd(q[1], `q${key}`)}</Text>); i++; continue;
    }
    const task = line.match(/^(\s*)[-*+]\s+\[([ xX])\]\s+(.*)$/);
    if (task) {
      const done = task[2].toLowerCase() === 'x';
      out.push(
        <Text key={key++}>{task[1]}<Text color={done ? GREEN : MUTED}>{`${done ? g.check : g.uncheck} `}</Text>{inlineMd(task[3], `t${key}`)}</Text>,
      );
      i++; continue;
    }
    const b = line.match(/^(\s*)[-*+]\s+(.*)$/);
    if (b) {
      out.push(<Text key={key++}>{b[1]}<Text color={AMBER}>{`${g.bullet} `}</Text>{inlineMd(b[2], `b${key}`)}</Text>);
      i++; continue;
    }
    const n = line.match(/^(\s*)(\d+)\.\s+(.*)$/);
    if (n) {
      out.push(<Text key={key++}>{n[1]}<Text color={AMBER}>{`${n[2]}. `}</Text>{inlineMd(n[3], `n${key}`)}</Text>);
      i++; continue;
    }
    if (!line.trim()) { out.push(<Text key={key++}> </Text>); i++; continue; }
    out.push(<Text key={key++}>{inlineMd(line, `p${key}`)}</Text>);
    i++;
  }
  return <Box flexDirection="column">{out}</Box>;
};

const MessageView: React.FC<{m: Msg; g: G}> = ({m, g}) => {
  switch (m.kind) {
    case 'user':
      return <Text color={AMBER} bold>{`${g.arrow} ${m.text}`}</Text>;
    case 'assistant':
      return m.streaming
        ? <Text>{m.text}<Text color={MUTED}>{g.cursor}</Text></Text>
        : <Markdown text={m.text} g={g} />;
    case 'thinking':
      return <Text color={MUTED}>{`  ${g.dot} thinking${m.done ? ' complete' : '…'} (${m.chars} chars)`}</Text>;
    case 'tool':
      return <ToolCard m={m} g={g} />;
    case 'notice': {
      const c = m.tone === 'warn' ? AMBER : m.tone === 'good' ? GREEN : CYAN;
      return <Text color={c}>{`  ${g.dot} ${m.text}`}</Text>;
    }
    case 'output':
      return <Text>{m.text}</Text>;
  }
};

const App: React.FC<{url: string; token: string}> = ({url, token}) => {
  const {exit} = useApp();
  const {stdout} = useStdout();
  const [messages, dispatch] = useReducer(reduce, [] as Msg[]);
  const [header, setHeader] = useState<Header>({});
  const [running, setRunning] = useState(false);
  const [asking, setAsking] = useState<{label: string; secret: boolean} | null>(null);
  const [value, setValue] = useState('');
  const [tick, setTick] = useState(0);
  const [commands, setCommands] = useState<{name: string; summary: string}[]>([]);
  const [scroll, setScroll] = useState(0); // messages hidden below the fold (0 = follow bottom)
  const [size, setSize] = useState({cols: stdout.columns || 80, rows: stdout.rows || 24});
  const [connected, setConnected] = useState(false);
  const [pending, setPending] = useState<string[]>([]); // composed lines awaiting send (multiline)
  const wsRef = useRef<WebSocket | null>(null);
  const histRef = useRef<string[]>([]);      // submitted inputs, oldest→newest
  const histIdxRef = useRef<number>(-1);     // -1 = editing live draft
  const draftRef = useRef<string>('');       // live draft stashed while browsing history

  // alternate screen on mount; restore on exit. Mouse-wheel scroll is opt-in
  // (AEGIS_TUI_MOUSE=1) because mouse tracking disables native text selection/copy-paste.
  useEffect(() => {
    // 1049=alt screen, 25l=hide cursor; 1000/1006=mouse wheel + SGR encoding (opt-in)
    const mouse = /^(1|true|yes|on)$/i.test(process.env.AEGIS_TUI_MOUSE || '');
    stdout.write('\x1b[?1049h\x1b[2J\x1b[H\x1b[?25l' + (mouse ? '\x1b[?1000h\x1b[?1006h' : ''));
    const onResize = () => setSize({cols: stdout.columns || 80, rows: stdout.rows || 24});
    stdout.on('resize', onResize);
    return () => {
      stdout.off('resize', onResize);
      stdout.write((mouse ? '\x1b[?1000l\x1b[?1006l' : '') + '\x1b[?25h\x1b[?1049l');
    };
  }, [stdout]);

  useEffect(() => {
    if (!running) return;
    const id = setInterval(() => setTick((t) => t + 1), 90);
    return () => clearInterval(id);
  }, [running]);

  useEffect(() => {
    const ws = new WebSocket(url);
    wsRef.current = ws;
    ws.on('open', () => {
      setConnected(true);
      ws.send(JSON.stringify({type: 'hello', token}));
    });
    ws.on('message', (data: WebSocket.RawData) => {
      let frame: any;
      try { frame = JSON.parse(data.toString()); } catch { return; }
      switch (frame.type) {
        case 'ready':
          setHeader(frame.header || {});
          if (Array.isArray(frame.commands)) setCommands(frame.commands);
          break;
        case 'output': dispatch({t: 'output', text: String(frame.text || '')}); setScroll(0); break;
        case 'event': dispatch({t: 'event', e: frame.event || {}}); setScroll(0); break;
        case 'status':
          if (frame.header) setHeader(frame.header);
          setRunning(Boolean(frame.running));
          break;
        case 'ask': setAsking({label: String(frame.label || 'answer'), secret: Boolean(frame.secret)}); setRunning(true); break;
        case 'exit': ws.close(); break;
      }
    });
    ws.on('close', () => exit());
    ws.on('error', (err: Error) => dispatch({t: 'output', text: `  gateway error: ${err.message}`}));
    return () => ws.close();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useInput((input, key) => {
    // SGR mouse wheel: ESC [ < 64 ; col ; row M (64=up, 65=down)
    const mouse = input.match(/\[<(\d+);\d+;\d+[Mm]/);
    if (mouse) {
      const btn = parseInt(mouse[1], 10);
      if (btn === 64) setScroll((s) => Math.min(messages.length - 1, s + 3));
      else if (btn === 65) setScroll((s) => Math.max(0, s - 3));
      return;
    }
    if (key.ctrl && input === 'c') {
      if (running && !asking) wsRef.current?.send(JSON.stringify({type: 'interrupt'}));
      else exit();
      return;
    }
    if (key.pageUp) { setScroll((s) => Math.min(messages.length - 1, s + 5)); return; }
    if (key.pageDown) { setScroll((s) => Math.max(0, s - 5)); return; }
    if (key.escape) { setScroll(0); if (pending.length) setPending([]); return; }
    // Up/Down: browse composer history (when not navigating slash completion)
    if ((key.upArrow || key.downArrow) && !running && !asking && histRef.current.length) {
      const hist = histRef.current;
      if (key.upArrow) {
        if (histIdxRef.current === -1) { draftRef.current = value; histIdxRef.current = hist.length - 1; }
        else histIdxRef.current = Math.max(0, histIdxRef.current - 1);
      } else {
        if (histIdxRef.current === -1) return;
        histIdxRef.current = histIdxRef.current + 1;
        if (histIdxRef.current >= hist.length) { histIdxRef.current = -1; setValue(draftRef.current); return; }
      }
      setValue(hist[histIdxRef.current]);
      return;
    }
    if (key.tab && !running && !asking && value.startsWith('/')) {
      const token = value.split(' ')[0];
      const hit = commands.find((c) => c.name.startsWith(token));
      if (hit) setValue(hit.name + ' ');
    }
  });

  const onSubmit = (text: string) => {
    setValue('');
    setScroll(0);
    histIdxRef.current = -1;
    if (asking) {
      wsRef.current?.send(JSON.stringify({type: 'answer', value: text}));
      setAsking(null);
      return;
    }
    if (running) { setPending([]); return; }
    // trailing backslash = newline continuation: keep composing instead of sending
    if (text.endsWith('\\')) {
      setPending((p) => [...p, text.slice(0, -1)]);
      return;
    }
    const full = (pending.length ? pending.join('\n') + '\n' : '') + text;
    setPending([]);
    if (!full.trim()) return;
    histRef.current = [...histRef.current, full].slice(-100);
    dispatch({t: 'user', text: full});
    wsRef.current?.send(JSON.stringify({type: 'input', text: full}));
  };

  const slashMatches = (!running && !asking && value.startsWith('/'))
    ? commands.filter((c) => c.name.startsWith(value.split(' ')[0])).slice(0, 6)
    : [];
  // bottom-anchored viewport: header(1)+status(1)+composer(1) + pending + completion menu
  const chromeRows = 3 + pending.length + (slashMatches.length ? slashMatches.length + 1 : 0);
  const bodyRows = Math.max(3, size.rows - chromeRows);
  const end = Math.max(0, messages.length - scroll);
  let used = 0;
  let start = end;
  for (let i = end - 1; i >= 0; i--) {
    used += estimateRows(messages[i], size.cols);
    if (used > bodyRows) break;
    start = i;
  }
  const visible = messages.slice(start, end);
  // When content fills/overflows the viewport, anchor to the BOTTOM so the newest lines
  // (the streaming answer) stay visible instead of being clipped off-screen at the bottom.
  const anchorBottom = used >= bodyRows;

  const uni = CLIENT_UNI;
  const g = glyphs(uni);
  const spinner = g.spinner[tick % g.spinner.length];
  const ctxText = header.ctx_window
    ? `${ctxBar(header.ctx_percent, g.barFull, g.barEmpty)} ${header.ctx_percent}% (${fmtTokens(header.ctx_used)}/${fmtTokens(header.ctx_window)})`
    : fmtTokens(header.ctx_used);
  const scrolledUp = scroll > 0;
  const sep = g.sep;

  return (
    <Box flexDirection="column" width={size.cols} height={size.rows}>
      {/* header */}
      <Box>
        <Text backgroundColor={PANEL} color={AMBER} bold>{` ${g.brand} `}</Text>
        <Text backgroundColor={PANEL} color={MUTED}>{` ${header.model || '?'} `}</Text>
        <Text backgroundColor={PANEL} color={MUTED}>{`${g.dot} ${(header.session_title || header.session_id || '').slice(0, 24)} `}</Text>
        <Text backgroundColor={PANEL} color={MUTED}>{`${g.dot} v${header.version || ''} `}</Text>
      </Box>

      {/* message viewport — bottom-anchored when full so the newest lines stay visible */}
      <Box flexDirection="column" flexGrow={1} overflow="hidden"
           justifyContent={anchorBottom ? 'flex-end' : 'flex-start'}>
        {visible.map((m, i) => <MessageView key={start + i} m={m} g={g} />)}
      </Box>

      {/* status bar */}
      <Box>
        <Text backgroundColor={PANEL} color={running ? AMBER : MUTED}>
          {running ? ` ${spinner} working… ^C stop ` : ` ready `}
        </Text>
        <Text backgroundColor={PANEL} color={MUTED}>{`${sep} ctx `}</Text>
        <Text backgroundColor={PANEL} color={GREEN}>{ctxText}</Text>
        <Text backgroundColor={PANEL} color={MUTED}>{` ${sep} ${fmtTokens(header.input_tokens)}↑ ${fmtTokens(header.output_tokens)}↓`}</Text>
        {header.cost ? <Text backgroundColor={PANEL} color={MUTED}>{` ${sep} $${(header.cost || 0).toFixed(4)}`}</Text> : null}
        <Text backgroundColor={PANEL} color={MUTED}>{` ${sep} ${header.reasoning || ''} ${sep} ${header.perms || ''} `}</Text>
        {scrolledUp ? <Text backgroundColor={PANEL} color={CYAN}>{`${sep} ${g.up} scrolled (Esc=bottom) `}</Text> : null}
      </Box>

      {/* slash-command completion menu */}
      {slashMatches.length ? (
        <Box flexDirection="column">
          {slashMatches.map((c, i) => (
            <Box key={c.name}>
              <Text color={i === 0 ? AMBER : MUTED} bold={i === 0}>{`  ${c.name}`}</Text>
              <Text color={MUTED}>{`  ${c.summary}`}</Text>
            </Box>
          ))}
          <Text color={MUTED}>{`  ${g.dot} Tab to complete`}</Text>
        </Box>
      ) : null}

      {/* pending multiline lines (Ctrl+J), shown above the composer */}
      {pending.length ? (
        <Box flexDirection="column">
          {pending.map((line, i) => (
            <Box key={i}>
              <Text color={MUTED}>{`   ${g.dot} `}</Text>
              <Text>{line}</Text>
            </Box>
          ))}
        </Box>
      ) : null}

      {/* composer */}
      <Box>
        <Text color={asking?.secret ? CYAN : AMBER} bold>{` ${asking ? asking.label : 'aegis ' + g.arrow} `}</Text>
        <TextInput
          value={value}
          onChange={(v) => setValue(v.replace(/\t/g, ''))}
          onSubmit={onSubmit}
          mask={asking?.secret ? '*' : undefined}
          placeholder={connected ? (running && !asking ? 'working… (^C to stop)' : 'message or /command · ↑ history · \\ + ↵ newline · ⇞ scroll') : 'connecting…'}
        />
      </Box>
    </Box>
  );
};

const url = process.env.AEGIS_TUI_WS || '';
const token = process.env.AEGIS_TUI_TOKEN || '';
if (!url) {
  process.stderr.write('AEGIS_TUI_WS not set\n');
  process.exit(2);
}

render(<App url={url} token={token} />, {exitOnCtrlC: false});
