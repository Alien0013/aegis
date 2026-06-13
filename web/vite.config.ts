import { fileURLToPath, URL } from "node:url";
import { defineConfig, type Plugin } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// Build the dashboard into the Python package so it ships as data and is served
// by aegis/dashboard.py at "/" with assets under "/assets/*".
const BACKEND = process.env.VITE_AEGIS_API_TARGET || "http://127.0.0.1:9119";

function aegisDevToken(): Plugin {
  const TOKEN_RE = /window\.__AEGIS_SESSION_TOKEN__\s*=\s*"([^"]+)"/;
  return {
    name: "aegis:dev-session-token",
    apply: "serve",
    async transformIndexHtml() {
      try {
        const res = await fetch(BACKEND, { headers: { accept: "text/html" } });
        const html = await res.text();
        const match = html.match(TOKEN_RE);
        if (!match) return;
        return [{
          tag: "script",
          injectTo: "head",
          children:
            `window.__AEGIS_SESSION_TOKEN__="${match[1]}";`,
        }];
      } catch {
        return;
      }
    },
  };
}

export default defineConfig({
  plugins: [react(), tailwindcss(), aegisDevToken()],
  resolve: { alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) } },
  base: "/",
  publicDir: false,
  build: {
    outDir: "../aegis/static/web_dist",
    emptyOutDir: true,
    assetsDir: "assets",
  },
  server: {
    proxy: {
      "/api": {
        target: BACKEND,
        ws: true,
      },
      "/dashboard-plugins": BACKEND,
    },
  },
});
