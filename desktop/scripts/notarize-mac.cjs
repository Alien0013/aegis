"use strict";

const path = require("node:path");

function truthy(value) {
  return ["1", "true", "yes", "on"].includes(String(value || "").trim().toLowerCase());
}

function notarizeOptionsFromEnv(env = process.env) {
  const appleApiKey = env.APPLE_API_KEY || env.APPLE_API_KEY_PATH || "";
  const appleApiKeyId = env.APPLE_API_KEY_ID || env.APPLE_API_KEYID || "";
  const appleApiIssuer = env.APPLE_API_ISSUER || env.APPLE_API_ISSUER_ID || "";
  if (appleApiKey && appleApiKeyId && appleApiIssuer) {
    return { appleApiKey, appleApiKeyId, appleApiIssuer };
  }

  const appleId = env.APPLE_ID || "";
  const appleIdPassword = env.APPLE_APP_SPECIFIC_PASSWORD || env.APPLE_ID_PASSWORD || "";
  const teamId = env.APPLE_TEAM_ID || env.APPLE_ID_TEAM_ID || "";
  if (appleId && appleIdPassword && teamId) {
    return { appleId, appleIdPassword, teamId };
  }
  return null;
}

function appPathFromContext(context) {
  const appOutDir = context && context.appOutDir;
  const appInfo = context && context.packager && context.packager.appInfo;
  const productName = appInfo && (appInfo.productFilename || appInfo.productName || appInfo.name);
  if (!appOutDir || !productName) return "";
  return path.join(appOutDir, `${productName}.app`);
}

async function notarizeMac(context, options = {}) {
  const env = options.env || process.env;
  const platform = context && context.electronPlatformName;
  if (platform !== "darwin") {
    return { skipped: true, reason: `notarization skipped for ${platform || "unknown platform"}` };
  }
  if (truthy(env.AEGIS_SKIP_MAC_NOTARIZE)) {
    return { skipped: true, reason: "AEGIS_SKIP_MAC_NOTARIZE=1" };
  }

  const notarizeOptions = notarizeOptionsFromEnv(env);
  if (!notarizeOptions) {
    if (truthy(env.AEGIS_ALLOW_UNSIGNED_DESKTOP_RELEASE)) {
      return {
        skipped: true,
        reason: "AEGIS_ALLOW_UNSIGNED_DESKTOP_RELEASE=1 and no Apple notarization credentials are configured",
      };
    }
    throw new Error(
      "macOS notarization needs APPLE_ID + APPLE_APP_SPECIFIC_PASSWORD + APPLE_TEAM_ID "
      + "or APPLE_API_KEY + APPLE_API_KEY_ID + APPLE_API_ISSUER",
    );
  }

  const appPath = appPathFromContext(context);
  if (!appPath) throw new Error("macOS notarization needs Electron Builder appOutDir and appInfo.productFilename");
  const notarize = options.notarize || require("@electron/notarize").notarize;
  await notarize({ appPath, ...notarizeOptions });
  return { skipped: false, appPath };
}

module.exports = notarizeMac;
module.exports.default = notarizeMac;
module.exports.notarizeOptionsFromEnv = notarizeOptionsFromEnv;
module.exports.appPathFromContext = appPathFromContext;
