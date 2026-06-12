import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Build the dashboard into the Python package so it ships as data and is served
// by aegis/dashboard.py at "/" with assets under "/assets/*".
export default defineConfig({
  plugins: [react()],
  base: "/",
  build: {
    outDir: "../aegis/static/web_dist",
    emptyOutDir: true,
    assetsDir: "assets",
  },
  server: {
    proxy: {
      "/api": process.env.VITE_AEGIS_API_TARGET || "http://127.0.0.1:9119",
    },
  },
});
