"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");

const {
  adoptServedDashboardToken,
  dashboardIndexUrl,
  extractInjectedDashboardToken,
  fetchPublicText,
  isForeignBackendToken,
  resolveServedDashboardToken,
} = require("./dashboard-token.cjs");

test("extractInjectedDashboardToken reads the JSON-encoded AEGIS session token", () => {
  const html = '<script>window.__AEGIS_SESSION_TOKEN__="served-token";</script>';
  assert.equal(extractInjectedDashboardToken(html), "served-token");
});

test("extractInjectedDashboardToken handles escaped token strings and malformed input", () => {
  const html = '<script>window.__AEGIS_SESSION_TOKEN__="served\\\\token\\"quoted";</script>';
  assert.equal(extractInjectedDashboardToken(html), 'served\\token"quoted');
  assert.equal(extractInjectedDashboardToken("<html></html>"), null);
  assert.equal(extractInjectedDashboardToken("<script>window.__AEGIS_SESSION_TOKEN__={bad}</script>"), null);
});

test("dashboardIndexUrl preserves remote dashboard path prefixes", () => {
  assert.equal(dashboardIndexUrl("http://127.0.0.1:9120"), "http://127.0.0.1:9120/");
  assert.equal(dashboardIndexUrl("https://agent.example/aegis/"), "https://agent.example/aegis/");
});

test("resolveServedDashboardToken adopts the served token and logs token drift", async () => {
  const logs = [];
  const token = await resolveServedDashboardToken("http://127.0.0.1:9120", "spawn-token", {
    fetchText: async (url) => {
      assert.equal(url, "http://127.0.0.1:9120/");
      return '<script>window.__AEGIS_SESSION_TOKEN__="served-token";</script>';
    },
    rememberLog: (line) => logs.push(line),
  });

  assert.equal(token, "served-token");
  assert.equal(logs.length, 1);
  assert.match(logs[0], /served a different AEGIS session token/);
});

test("resolveServedDashboardToken falls back when no token is injected", async () => {
  const token = await resolveServedDashboardToken("http://127.0.0.1:9120", "spawn-token", {
    fetchText: async () => "<html></html>",
    rememberLog: () => {
      throw new Error("should not log without token drift");
    },
  });

  assert.equal(token, "spawn-token");
});

test("fetchPublicText rejects unsupported backend URL protocols", async () => {
  await assert.rejects(() => fetchPublicText("file:///tmp/index.html"), /Unsupported AEGIS backend URL protocol/);
});

test("isForeignBackendToken only flags a mismatched token from a dead child", () => {
  assert.equal(isForeignBackendToken({ servedToken: "other", spawnToken: "mine", childAlive: false }), true);
  assert.equal(isForeignBackendToken({ servedToken: "other", spawnToken: "mine", childAlive: true }), false);
  assert.equal(isForeignBackendToken({ servedToken: "mine", spawnToken: "mine", childAlive: false }), false);
  assert.equal(isForeignBackendToken({ servedToken: "", spawnToken: "mine", childAlive: false }), false);
});

test("adoptServedDashboardToken refuses a foreign dashboard token after backend exit", async () => {
  await assert.rejects(
    () => adoptServedDashboardToken("http://127.0.0.1:9120", "spawn-token", {
      childAlive: () => false,
      fetchText: async () => '<script>window.__AEGIS_SESSION_TOKEN__="squatter-token";</script>',
      label: "AEGIS backend",
    }),
    /AEGIS backend exited.*process we did not spawn/,
  );
});

test("adoptServedDashboardToken falls back to the spawned token when token fetch fails", async () => {
  const logs = [];
  const token = await adoptServedDashboardToken("http://127.0.0.1:9120", "spawn-token", {
    childAlive: () => true,
    fetchText: async () => {
      throw new Error("boom");
    },
    rememberLog: (line) => logs.push(line),
  });

  assert.equal(token, "spawn-token");
  assert.equal(logs.length, 1);
  assert.match(logs[0], /could not read served AEGIS session token/);
});
