const assert = require("node:assert/strict");
const test = require("node:test");

const notarizeMac = require("./notarize-mac.cjs");
const {
  appPathFromContext,
  notarizeOptionsFromEnv,
} = require("./notarize-mac.cjs");

function context(platform = "darwin") {
  return {
    electronPlatformName: platform,
    appOutDir: "/tmp/aegis-dist/mac",
    packager: { appInfo: { productFilename: "AEGIS" } },
  };
}

test("notarize options support Apple ID and App Store Connect API key credentials", () => {
  assert.deepEqual(
    notarizeOptionsFromEnv({
      APPLE_ID: "dev@example.com",
      APPLE_APP_SPECIFIC_PASSWORD: "app-pass",
      APPLE_TEAM_ID: "TEAM123",
    }),
    { appleId: "dev@example.com", appleIdPassword: "app-pass", teamId: "TEAM123" },
  );
  assert.deepEqual(
    notarizeOptionsFromEnv({
      APPLE_API_KEY: "/tmp/AuthKey_123.p8",
      APPLE_API_KEY_ID: "KEY123",
      APPLE_API_ISSUER: "issuer-uuid",
    }),
    { appleApiKey: "/tmp/AuthKey_123.p8", appleApiKeyId: "KEY123", appleApiIssuer: "issuer-uuid" },
  );
  assert.equal(notarizeOptionsFromEnv({ APPLE_ID: "dev@example.com" }), null);
});

test("notarize hook skips non-mac platforms and explicit unsigned releases", async () => {
  assert.deepEqual(
    await notarizeMac(context("linux"), { env: {}, notarize: () => { throw new Error("not used"); } }),
    { skipped: true, reason: "notarization skipped for linux" },
  );
  assert.deepEqual(
    await notarizeMac(context("darwin"), {
      env: { AEGIS_ALLOW_UNSIGNED_DESKTOP_RELEASE: "1" },
      notarize: () => { throw new Error("not used"); },
    }),
    {
      skipped: true,
      reason: "AEGIS_ALLOW_UNSIGNED_DESKTOP_RELEASE=1 and no Apple notarization credentials are configured",
    },
  );
});

test("notarize hook invokes electron notarize with app bundle and credentials", async () => {
  const calls = [];
  const result = await notarizeMac(context("darwin"), {
    env: {
      APPLE_ID: "dev@example.com",
      APPLE_APP_SPECIFIC_PASSWORD: "app-pass",
      APPLE_TEAM_ID: "TEAM123",
    },
    notarize: async (options) => { calls.push(options); },
  });

  assert.deepEqual(result, { skipped: false, appPath: "/tmp/aegis-dist/mac/AEGIS.app" });
  assert.deepEqual(calls, [{
    appPath: "/tmp/aegis-dist/mac/AEGIS.app",
    appleId: "dev@example.com",
    appleIdPassword: "app-pass",
    teamId: "TEAM123",
  }]);
  assert.equal(appPathFromContext(context("darwin")), "/tmp/aegis-dist/mac/AEGIS.app");
});

test("notarize hook fails closed on mac releases without credentials", async () => {
  await assert.rejects(
    () => notarizeMac(context("darwin"), { env: {}, notarize: () => { throw new Error("not used"); } }),
    /macOS notarization needs APPLE_ID/,
  );
});
