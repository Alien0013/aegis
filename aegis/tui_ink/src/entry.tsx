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

const AMBER = '#d6a15e';
const GREEN = '#7ecf8f';
const CYAN = '#6fb7d8';
const RED = '#e96e6e';
const MUTED = '#8f968f';
const PANEL = '#262a31';

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
  };
}

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
  return (
    <Box>
      <Text color={AMBER}>{`  ${icon} `}</Text>
      <Text color={MUTED} bold>{m.name}</Text>
      <Text> </Text>
      <Text color={MUTED}>{m.preview.slice(0, 80)}</Text>
      <Text>  </Text>
      {pill}
      {m.summary && m.status !== 'running'
        ? <Text color={MUTED}>{`  ${m.summary.replace(ANSI_RE, '').slice(0, 60)}`}</Text> : null}
    </Box>
  );
};

const MessageView: React.FC<{m: Msg; g: G}> = ({m, g}) => {
  switch (m.kind) {
    case 'user':
      return <Text color={AMBER} bold>{`${g.arrow} ${m.text}`}</Text>;
    case 'assistant':
      return <Text>{m.text}{m.streaming ? <Text color={MUTED}>{g.cursor}</Text> : null}</Text>;
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
  const wsRef = useRef<WebSocket | null>(null);

  // alternate screen on mount; restore on exit
  useEffect(() => {
    stdout.write('\x1b[?1049h\x1b[2J\x1b[H\x1b[?25l');
    const onResize = () => setSize({cols: stdout.columns || 80, rows: stdout.rows || 24});
    stdout.on('resize', onResize);
    return () => {
      stdout.off('resize', onResize);
      stdout.write('\x1b[?25h\x1b[?1049l');
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
    if (key.ctrl && input === 'c') {
      if (running && !asking) wsRef.current?.send(JSON.stringify({type: 'interrupt'}));
      else exit();
      return;
    }
    if (key.pageUp) { setScroll((s) => Math.min(messages.length - 1, s + 5)); return; }
    if (key.pageDown) { setScroll((s) => Math.max(0, s - 5)); return; }
    if (key.escape) { setScroll(0); return; }
    if (key.tab && !running && !asking && value.startsWith('/')) {
      const token = value.split(' ')[0];
      const hit = commands.find((c) => c.name.startsWith(token));
      if (hit) setValue(hit.name + ' ');
    }
  });

  const onSubmit = (text: string) => {
    setValue('');
    setScroll(0);
    if (asking) {
      wsRef.current?.send(JSON.stringify({type: 'answer', value: text}));
      setAsking(null);
      return;
    }
    if (running) return;
    if (!text.trim()) return;
    dispatch({t: 'user', text});
    wsRef.current?.send(JSON.stringify({type: 'input', text}));
  };

  // bottom-anchored viewport: walk back from (end - scroll) until we fill the body height
  const bodyRows = Math.max(3, size.rows - 3);
  const end = Math.max(0, messages.length - scroll);
  let used = 0;
  let start = end;
  for (let i = end - 1; i >= 0; i--) {
    used += estimateRows(messages[i], size.cols);
    if (used > bodyRows) break;
    start = i;
  }
  const visible = messages.slice(start, end);

  const uni = CLIENT_UNI;
  const g = glyphs(uni);
  const spinner = g.spinner[tick % g.spinner.length];
  const ctxText = header.ctx_window
    ? `${ctxBar(header.ctx_percent, g.barFull, g.barEmpty)} ${header.ctx_percent}% (${fmtTokens(header.ctx_used)}/${fmtTokens(header.ctx_window)})`
    : fmtTokens(header.ctx_used);
  const scrolledUp = scroll > 0;
  const sep = g.sep;
  const slashMatches = (!running && !asking && value.startsWith('/'))
    ? commands.filter((c) => c.name.startsWith(value.split(' ')[0])).slice(0, 6)
    : [];

  return (
    <Box flexDirection="column" width={size.cols} height={size.rows}>
      {/* header */}
      <Box>
        <Text backgroundColor={PANEL} color={AMBER} bold>{` ${g.brand} `}</Text>
        <Text backgroundColor={PANEL} color={MUTED}>{` ${header.model || '?'} `}</Text>
        <Text backgroundColor={PANEL} color={MUTED}>{`${g.dot} ${(header.session_title || header.session_id || '').slice(0, 24)} `}</Text>
        <Text backgroundColor={PANEL} color={MUTED}>{`${g.dot} v${header.version || ''} `}</Text>
      </Box>

      {/* message viewport */}
      <Box flexDirection="column" flexGrow={1} overflow="hidden">
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

      {/* composer */}
      <Box>
        <Text color={asking?.secret ? CYAN : AMBER} bold>{` ${asking ? asking.label : 'aegis ' + g.arrow} `}</Text>
        <TextInput
          value={value}
          onChange={(v) => setValue(v.replace(/\t/g, ''))}
          onSubmit={onSubmit}
          mask={asking?.secret ? '*' : undefined}
          placeholder={connected ? (running && !asking ? 'working… (^C to stop)' : 'message or /command · PgUp scroll') : 'connecting…'}
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
