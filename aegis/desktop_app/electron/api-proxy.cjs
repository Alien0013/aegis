const API_PROXY_ALLOWED_METHODS = new Set(["GET", "POST", "PUT", "PATCH", "DELETE"]);
const API_PROXY_MAX_BODY_BYTES = 1024 * 1024;

function normalizeApiProxyMethod(method = "GET") {
  const value = String(method || "GET").trim().toUpperCase();
  if (!API_PROXY_ALLOWED_METHODS.has(value)) {
    throw new Error(`unsupported API proxy method: ${value || "<missing>"}`);
  }
  return value;
}

function decodePathSegment(segment) {
  try {
    return decodeURIComponent(segment);
  } catch {
    return segment;
  }
}

function normalizeApiProxyPath(requestPath = "") {
  let text = String(requestPath || "").trim();
  if (/^[A-Za-z][A-Za-z0-9+.-]*:/.test(text) || text.startsWith("//")) {
    throw new Error("API proxy path must be relative");
  }
  if (text.includes("\\") || text.includes("#") || /\x00|[\x01-\x1f\x7f]/.test(text)) {
    throw new Error("API proxy path contains invalid characters");
  }
  text = text.replace(/^\/+/, "");

  const queryIndex = text.indexOf("?");
  let pathPart = queryIndex >= 0 ? text.slice(0, queryIndex) : text;
  const queryPart = queryIndex >= 0 ? text.slice(queryIndex) : "";
  if (pathPart === "api") pathPart = "";
  else pathPart = pathPart.replace(/^api\/+/, "");
  const segments = pathPart.split("/").filter(Boolean);
  for (const segment of segments) {
    const decoded = decodePathSegment(segment);
    if (decoded === "." || decoded === ".." || decoded.includes("/") || decoded.includes("\\")) {
      throw new Error("API proxy path must stay under /api");
    }
  }
  return `${segments.join("/")}${queryPart}`;
}

function serializeApiProxyBody(body = null) {
  if (body == null) return null;
  const text = JSON.stringify(body);
  if (typeof text !== "string") {
    throw new Error("API proxy body must be JSON serializable");
  }
  const payload = Buffer.from(text);
  if (payload.length > API_PROXY_MAX_BODY_BYTES) {
    throw new Error(`API proxy body too large: ${payload.length} bytes`);
  }
  return payload;
}

module.exports = {
  API_PROXY_ALLOWED_METHODS,
  API_PROXY_MAX_BODY_BYTES,
  normalizeApiProxyMethod,
  normalizeApiProxyPath,
  serializeApiProxyBody,
};
