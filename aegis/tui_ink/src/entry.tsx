/**
 * AEGIS terminal UI — Node/Ink front-end.
 *
 * Connects to the Python WebSocket gateway (aegis.tui_gateway), renders the streamed
 * conversation in the terminal scrollback, and keeps a persistent header + status bar +
 * composer pinned to the bottom. Original AEGIS code; only the public Ink/React API is used.
 */

import React, {useEffect, useMemo, useRef, useState} from 'react';
import {Box, Static, Text, render, useApp, useInput} from 'ink';
import TextInput from 'ink-text-input';
import WebSocket from 'ws';

const AMBER = '#d6a15e';
const GREEN = '#7ecf8f';
const CYAN = '#6fb7d8';
const MUTED = '#8f968f';
const SPINNER = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏'];

type Header = {
  brand?: string;
  model?: string;
  provider?: string;
  session_id?: string;
  session_title?: string;
  ctx_used?: number;
  ctx_window?: number;
  ctx_percent?: number;
  input_tokens?: number;
  output_tokens?: number;
  cost?: number;
  reasoning?: string;
  perms?: string;
  busy?: string;
  cwd?: string;
  version?: string;
};

type Ask = {label: string; secret: boolean};

function fmtTokens(n?: number): string {
  if (!n) return '0';
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(1) + 'M';
  if (n >= 1000) return (n / 1000).toFixed(1) + 'k';
  return String(n);
}

function ctxBar(percent = 0, width = 10): string {
  const p = Math.max(0, Math.min(100, percent));
  const filled = Math.round((p / 100) * width);
  return '█'.repeat(filled) + '░'.repeat(width - filled);
}

const App: React.FC<{url: string; token: string}> = ({url, token}) => {
  const {exit} = useApp();
  const [lines, setLines] = useState<string[]>([]);
  const [partial, setPartial] = useState('');
  const [header, setHeader] = useState<Header>({});
  const [running, setRunning] = useState(false);
  const [asking, setAsking] = useState<Ask | null>(null);
  const [value, setValue] = useState('');
  const [tick, setTick] = useState(0);
  const [connected, setConnected] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const bufRef = useRef('');

  // spinner animation while a turn runs
  useEffect(() => {
    if (!running) return;
    const id = setInterval(() => setTick((t) => t + 1), 90);
    return () => clearInterval(id);
  }, [running]);

  // append streamed output, committing whole lines to the scrollback Static region
  const pushOutput = (text: string) => {
    bufRef.current += text.replace(/\r/g, '');
    const parts = bufRef.current.split('\n');
    bufRef.current = parts.pop() ?? '';
    if (parts.length) setLines((prev) => [...prev, ...parts]);
    setPartial(bufRef.current);
  };

  useEffect(() => {
    const ws = new WebSocket(url);
    wsRef.current = ws;
    ws.on('open', () => {
      setConnected(true);
      ws.send(JSON.stringify({type: 'hello', token}));
    });
    ws.on('message', (data: WebSocket.RawData) => {
      let frame: any;
      try {
        frame = JSON.parse(data.toString());
      } catch {
        return;
      }
      switch (frame.type) {
        case 'ready':
          setHeader(frame.header || {});
          break;
        case 'output':
          pushOutput(String(frame.text || ''));
          break;
        case 'status':
          if (frame.header) setHeader(frame.header);
          setRunning(Boolean(frame.running));
          break;
        case 'ask':
          setAsking({label: String(frame.label || 'answer'), secret: Boolean(frame.secret)});
          setRunning(true);
          break;
        case 'turn_done':
          if (bufRef.current) {
            setLines((prev) => [...prev, bufRef.current]);
            bufRef.current = '';
            setPartial('');
          }
          break;
        case 'exit':
          ws.close();
          break;
      }
    });
    ws.on('close', () => exit());
    ws.on('error', (err: Error) => {
      pushOutput(`  gateway error: ${err.message}\n`);
    });
    return () => ws.close();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useInput((input, key) => {
    if (key.ctrl && input === 'c') {
      if (running && !asking) {
        wsRef.current?.send(JSON.stringify({type: 'interrupt'}));
      } else {
        exit();
      }
    }
  });

  const onSubmit = (text: string) => {
    setValue('');
    if (asking) {
      wsRef.current?.send(JSON.stringify({type: 'answer', value: text}));
      setAsking(null);
      return;
    }
    if (running) return; // busy — ^C to stop
    if (!text.trim()) return;
    wsRef.current?.send(JSON.stringify({type: 'input', text}));
  };

  const spinner = SPINNER[tick % SPINNER.length];
  const ctxText = header.ctx_window
    ? `${ctxBar(header.ctx_percent)} ${header.ctx_percent}% (${fmtTokens(header.ctx_used)}/${fmtTokens(header.ctx_window)})`
    : fmtTokens(header.ctx_used);
  const promptLabel = asking ? asking.label : `aegis ❯`;

  const statusLeft = running
    ? `${spinner} working…  ^C to stop`
    : `◆ AEGIS  ${header.model || '?'}`;

  return (
    <Box flexDirection="column">
      <Static items={lines}>
        {(line, i) => (
          <Text key={i}>{line}</Text>
        )}
      </Static>

      {partial ? <Text>{partial}</Text> : null}

      <Box marginTop={1}>
        <Text backgroundColor={'#262a31'}>
          <Text color={running ? AMBER : MUTED}>{` ${statusLeft} `}</Text>
          <Text color={MUTED}>{'│ ctx '}</Text>
          <Text color={GREEN}>{ctxText}</Text>
          <Text color={MUTED}>{`  │ ${fmtTokens(header.input_tokens)}↑ ${fmtTokens(header.output_tokens)}↓`}</Text>
          {header.cost ? <Text color={MUTED}>{`  │ $${(header.cost || 0).toFixed(4)}`}</Text> : null}
          <Text color={MUTED}>{`  │ ${header.reasoning || ''}  │ ${header.perms || ''}  │ ${header.busy || ''} `}</Text>
        </Text>
      </Box>

      <Box>
        <Text color={asking?.secret ? CYAN : AMBER} bold>
          {` ${promptLabel} `}
        </Text>
        <TextInput
          value={value}
          onChange={setValue}
          onSubmit={onSubmit}
          mask={asking?.secret ? '*' : undefined}
          placeholder={connected ? (running && !asking ? 'working… (^C to stop)' : 'type a message or /command') : 'connecting…'}
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
