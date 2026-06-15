// Chat = the graphical chat app, embedded as the dashboard's Chat tab (the same
// surface the desktop app opens into). Session browsing lives on the Sessions
// page; opening one there deep-links to /chat?id=… and resumes it here. The raw
// xterm/PTY terminal still lives in the dashboard under the Terminal tab.

import { useNavigate, useSearchParams } from "react-router-dom";
import { GraphicalChat } from "./GraphicalChat";

export function ChatGraphical() {
  const [params] = useSearchParams();
  const nav = useNavigate();
  const id = params.get("id") || "";
  return (
    <GraphicalChat
      sessionId={id}
      onSession={(sid) => {
        if (sid && sid !== id) nav(`/chat?id=${encodeURIComponent(sid)}`, { replace: true });
      }}
    />
  );
}
