const assert = require("node:assert/strict");
const test = require("node:test");

const {
  API_PROXY_MAX_BODY_BYTES,
  normalizeApiProxyMethod,
  normalizeApiProxyPath,
  serializeApiProxyBody,
} = require("./api-proxy.cjs");

test("renderer API proxy normalizes safe API paths", () => {
  assert.equal(normalizeApiProxyPath("/api/status?verbose=1"), "status?verbose=1");
  assert.equal(normalizeApiProxyPath("api/sessions/abc/messages"), "sessions/abc/messages");
  assert.equal(normalizeApiProxyPath("/sessions/abc"), "sessions/abc");
  assert.equal(normalizeApiProxyPath("/api?verbose=1"), "?verbose=1");
  assert.equal(normalizeApiProxyPath(""), "");
});

test("renderer API proxy rejects escaped or absolute paths", () => {
  assert.throws(() => normalizeApiProxyPath("../status"), /under \/api/);
  assert.throws(() => normalizeApiProxyPath("api/%2e%2e/status"), /under \/api/);
  assert.throws(() => normalizeApiProxyPath("api/sessions%2f..%2fstatus"), /under \/api/);
  assert.throws(() => normalizeApiProxyPath("https://example.com/api/status"), /relative/);
  assert.throws(() => normalizeApiProxyPath("//example.com/api/status"), /relative/);
  assert.throws(() => normalizeApiProxyPath("sessions\\abc"), /invalid characters/);
  assert.throws(() => normalizeApiProxyPath("sessions#fragment"), /invalid characters/);
});

test("renderer API proxy limits methods and JSON body size", () => {
  assert.equal(normalizeApiProxyMethod("post"), "POST");
  assert.throws(() => normalizeApiProxyMethod("TRACE"), /unsupported/);
  assert.equal(serializeApiProxyBody({ ok: true }).toString(), '{"ok":true}');
  assert.equal(serializeApiProxyBody(null), null);
  assert.throws(() => serializeApiProxyBody("x".repeat(API_PROXY_MAX_BODY_BYTES)), /too large/);
});
