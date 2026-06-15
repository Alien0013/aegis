// Custom titlebar for the frameless desktop window. Renders nothing in a browser
// tab (the OS chrome stays). Inside the app it provides the drag region, the
// current-view label, a ⌘K command trigger, and — on Linux/Windows where we hide
// the OS frame — minimize / maximize / close controls wired to the main process.

import { useEffect, useState } from "react";
import { useLocation } from "react-router-dom";
import { desktop, isDesktop, isMac } from "../lib/desktop";
import { NAV_ITEMS } from "../lib/nav";
import { openCommandPalette } from "./CommandPalette";
import { Icon } from "./icons";
import { Mark } from "./Mark";

function viewLabel(pathname: string): string {
  if (pathname.startsWith("/app")) return "Chat";
  const hit = NAV_ITEMS.find(
    (i) => i.path === pathname || (i.path !== "/" && pathname.startsWith(i.path)),
  );
  return hit?.label ?? "AEGIS";
}

function WindowControls() {
  const [max, setMax] = useState(false);
  useEffect(() => {
    desktop?.isMaximized().then(setMax).catch(() => {});
    return desktop?.onMaximizeChange(setMax);
  }, []);
  const btn = "app-no-drag flex h-full w-11 items-center justify-center text-dim transition-colors";
  return (
    <div className="flex h-full">
      <button className={`${btn} hover:bg-surface-2 hover:text-text`} title="Minimize" onClick={() => desktop?.minimize()}>
        <Icon name="winMin" size={15} />
      </button>
      <button className={`${btn} hover:bg-surface-2 hover:text-text`} title={max ? "Restore" : "Maximize"} onClick={() => desktop?.maximizeToggle()}>
        <Icon name={max ? "winRestore" : "winMax"} size={13} />
      </button>
      <button className={`${btn} hover:bg-danger hover:text-white`} title="Close" onClick={() => desktop?.close()}>
        <Icon name="x" size={15} />
      </button>
    </div>
  );
}

export function TitleBar() {
  const loc = useLocation();
  if (!isDesktop) return null;

  return (
    <header className="app-drag flex h-9 shrink-0 select-none items-center border-b border-border bg-surface/70 backdrop-blur">
      {isMac ? (
        <div className="w-[72px] shrink-0" />
      ) : (
        <div className="flex shrink-0 items-center gap-1.5 pl-3 pr-2">
          <Mark size={15} />
          <span className="text-[12px] font-semibold tracking-wide text-text">AEGIS</span>
          <span className="ml-1 text-[12px] text-faint">·</span>
          <span className="text-[12px] text-dim">{viewLabel(loc.pathname)}</span>
        </div>
      )}

      <div className="flex flex-1 justify-center px-2">
        <button
          onClick={openCommandPalette}
          className="app-no-drag group flex h-6 items-center gap-2 rounded-full border border-border bg-surface-2/70 pl-2.5 pr-1.5 text-[12px] text-faint transition-colors hover:border-border-2 hover:text-dim"
          title="Command palette"
        >
          <Icon name="search" size={12} />
          <span className="hidden sm:inline">Search or run a command</span>
          <kbd className="rounded border border-border bg-surface px-1 py-px font-mono text-[10px] text-faint">
            {isMac ? "⌘K" : "Ctrl K"}
          </kbd>
        </button>
      </div>

      {isMac ? <div className="w-[72px] shrink-0" /> : <WindowControls />}
    </header>
  );
}
