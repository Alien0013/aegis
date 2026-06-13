import type { ReactElement } from "react";
const P = (d: string) => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"
       strokeLinecap="round" strokeLinejoin="round" dangerouslySetInnerHTML={{ __html: d }} />
);
export const ICONS: Record<string, ReactElement> = {
  overview: P('<rect x="3" y="3" width="7" height="9" rx="1.5"/><rect x="14" y="3" width="7" height="5" rx="1.5"/><rect x="14" y="12" width="7" height="9" rx="1.5"/><rect x="3" y="16" width="7" height="5" rx="1.5"/>'),
  chat: P('<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>'),
  sessions: P('<path d="M3 5h18M3 12h18M3 19h18"/>'),
  models: P('<circle cx="12" cy="12" r="3"/><path d="M12 2v4M12 18v4M2 12h4M18 12h4"/>'),
  channels: P('<path d="M4 4h16v12H5.2L4 17.2z"/>'),
  skills: P('<path d="m12 2 3 7 7 .5-5.5 4.5L18 21l-6-4-6 4 1.5-7L2 9.5 9 9z"/>'),
  memory: P('<rect x="4" y="4" width="16" height="16" rx="2"/><path d="M9 9h6v6H9z"/>'),
  kanban: P('<rect x="3" y="4" width="5" height="16" rx="1.5"/><rect x="9.5" y="4" width="5" height="16" rx="1.5"/><rect x="16" y="4" width="5" height="16" rx="1.5"/>'),
  cron: P('<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>'),
  tools: P('<path d="M14 7a4 4 0 0 0-5 5l-6 6 2 2 6-6a4 4 0 0 0 5-5l-2 2-2-2 2-2z"/>'),
  agents: P('<rect x="5" y="8" width="14" height="11" rx="2"/><path d="M12 8V4M9 4h6"/><circle cx="9" cy="13" r="1"/><circle cx="15" cy="13" r="1"/>'),
  config: P('<circle cx="12" cy="12" r="3"/><path d="M19 12a7 7 0 0 0-.1-1l2-1.5-2-3.5-2.4 1a7 7 0 0 0-1.7-1L14.5 2h-5l-.3 2.6a7 7 0 0 0-1.7 1l-2.4-1-2 3.5L2.6 11a7 7 0 0 0 0 2l-2 1.5 2 3.5 2.4-1a7 7 0 0 0 1.7 1L9.5 22h5l.3-2.6a7 7 0 0 0 1.7-1l2.4 1 2-3.5-2-1.5q.1-.5.1-1z"/>'),
  logs: P('<path d="M4 4h16v16H4z"/><path d="M8 9h8M8 13h8M8 17h5"/>'),
  system: P('<rect x="3" y="4" width="18" height="12" rx="2"/><path d="M8 20h8M12 16v4"/>'),
  menu: P('<path d="M4 6h16M4 12h16M4 18h16"/>'),
  close: P('<path d="M6 6l12 12M18 6L6 18"/>'),
  search: P('<circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/>'),
  plus: P('<path d="M12 5v14M5 12h14"/>'),
  trash: P('<path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13"/>'),
  play: P('<path d="M7 4v16l13-8z"/>'),
  refresh: P('<path d="M21 12a9 9 0 1 1-3-6.7L21 8M21 4v4h-4"/>'),
  check: P('<path d="M5 12l5 5L20 6"/>'),
  key: P('<circle cx="8" cy="15" r="5"/><path d="M12 12l9-9M17 3l3 3M14 6l3 3"/>'),
  bolt: P('<path d="M13 2 4 14h7l-1 8 9-12h-7z"/>'),
  link: P('<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7L11 6.5"/><path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7L13 17.5"/>'),
  rocket: P('<path d="M5 15c-1.5 1.5-2 5-2 5s3.5-.5 5-2M9 11a8 8 0 0 1 11-7 8 8 0 0 1-7 11l-2 2-4-4z"/><circle cx="15" cy="9" r="1.2"/>'),
};
export const Icon = ({ n }: { n: string }) => ICONS[n] || ICONS.overview;
