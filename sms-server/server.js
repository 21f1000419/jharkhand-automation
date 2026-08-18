"use strict";

// Tiny in-memory SMS/OTP bridge for MacroDroid and the local Python app.
// Uses only Node's built-in modules: no npm install is required.

const http = require("http");
const { URL } = require("url");

const PORT = Number(process.env.PORT || 8787);
const MAX_BODY_BYTES = 64 * 1024;
const OTP_TTL_MS = Number(process.env.OTP_TTL_MS || 5 * 60 * 1000);

// One pending OTP per (userId, type). A newly received OTP replaces the old one.
const pendingOtps = new Map();

function sendJson(response, statusCode, payload) {
  response.writeHead(statusCode, { "Content-Type": "application/json; charset=utf-8" });
  response.end(JSON.stringify(payload));
}

function getJsonBody(request) {
  return new Promise((resolve, reject) => {
    let body = "";
    let size = 0;

    request.on("data", (chunk) => {
      size += chunk.length;
      if (size > MAX_BODY_BYTES) {
        reject(new Error("Request body is too large."));
        request.destroy();
        return;
      }
      body += chunk;
    });
    request.on("end", () => {
      if (!body) return resolve({});
      try {
        resolve(JSON.parse(body));
      } catch {
        reject(new Error("Request body must be valid JSON."));
      }
    });
    request.on("error", reject);
  });
}

function cleanText(value) {
  return typeof value === "string" ? value.trim() : "";
}

function findOtp(content) {
  // Confirmed Jharnibandhan/NGDRS login message format:
  // "Your OTP to NGDRS Login :- 36367408"
  const mainMatch = content.match(/\byour\s+otp\s+to\s+ngdrs\s+login\s*[:-]+\s*(\d{4,8})\b/i);
  if (mainMatch) return { type: "main", otp: mainMatch[1] };

  // Confirmed KUBER JH e-GRAS format (normally sender AX-MKUBER-S):
  // "KUBER JH- OTP for One Time User Validation IN JEGRAS IS A09AFD ...
  //  JH-OTP REFERENCE IS 2127753 Valid for 2 minutes"
  // Capture only the six-character value after "JEGRAS IS". This deliberately
  // cannot select the date/time or the later JH-OTP reference number.
  const egrassMatch = content.match(
    /\bkuber\s+jh\s*-\s*otp\s+for\s+one\s+time\s+user\s+validation\s+in\s+jegras\s+is\s+([a-z0-9]{6})\b/i,
  );
  if (egrassMatch) return { type: "egrass", otp: egrassMatch[1].toUpperCase() };

  return null;
}

function keyFor(userId, type) {
  return `${userId}:${type}`;
}

function removeExpiredOtps() {
  const cutoff = Date.now() - OTP_TTL_MS;
  for (const [key, entry] of pendingOtps) {
    if (entry.receivedAtMs < cutoff) pendingOtps.delete(key);
  }
}

async function handleRequest(request, response) {
  const url = new URL(request.url, `http://${request.headers.host || "localhost"}`);
  const pathParts = url.pathname.split("/").filter(Boolean);

  if (request.method === "GET" && (url.pathname === "/" || url.pathname === "/health")) {
    return sendJson(response, 200, {
      ok: true,
      service: "sms-otp-server",
      pendingOtpCount: pendingOtps.size,
    });
  }

  // POST /api/users/:userId/sms
  if (request.method === "POST" && pathParts.length === 4 && pathParts[0] === "api" && pathParts[1] === "users" && pathParts[3] === "sms") {
    const userId = decodeURIComponent(pathParts[2]).trim();
    if (!userId) return sendJson(response, 400, { error: "userId is required in the path." });

    const body = await getJsonBody(request);
    const content = cleanText(body.content);
    const sender = cleanText(body.sender);
    if (!content || !sender) {
      return sendJson(response, 400, { error: "content and sender are required." });
    }

    const recognisedOtp = findOtp(content);
    if (!recognisedOtp) {
      return sendJson(response, 202, {
        stored: false,
        reason: "SMS content is not a recognised OTP pattern.",
      });
    }

    removeExpiredOtps();
    const receivedAt = new Date().toISOString();
    const entry = {
      userId,
      type: recognisedOtp.type,
      otp: recognisedOtp.otp,
      sender,
      content,
      metadata: body.metadata && typeof body.metadata === "object" ? body.metadata : {},
      receivedAt,
      receivedAtMs: Date.now(),
    };
    pendingOtps.set(keyFor(userId, entry.type), entry);

    console.log(`[${receivedAt}] Received OTP - User ID: ${userId}, Sender: ${sender}, Decoded Type: ${entry.type}`);

    return sendJson(response, 201, {
      stored: true,
      type: entry.type,
      receivedAt,
      // The receiver doesn't need the OTP echoed back in its response.
    });
  }

  // GET /api/users/:userId/otps/:type  (polling endpoint)
  if (request.method === "GET" && pathParts.length === 5 && pathParts[0] === "api" && pathParts[1] === "users" && pathParts[3] === "otps") {
    const userId = decodeURIComponent(pathParts[2]).trim();
    const type = decodeURIComponent(pathParts[4]).trim().toLowerCase();
    if (!userId || !["main", "egrass"].includes(type)) {
      return sendJson(response, 400, { error: "Use a userId and OTP type: main or egrass." });
    }

    removeExpiredOtps();
    const notBeforeValue = url.searchParams.get("notBefore");
    const notBeforeMs = notBeforeValue ? Date.parse(notBeforeValue) : null;
    if (notBeforeValue && Number.isNaN(notBeforeMs)) {
      return sendJson(response, 400, { error: "notBefore must be a valid ISO-8601 timestamp." });
    }
    const entry = pendingOtps.get(keyFor(userId, type));
    if (!entry || (notBeforeMs !== null && entry.receivedAtMs < notBeforeMs)) {
      return sendJson(response, 404, { found: false });
    }

    return sendJson(response, 200, {
      found: true,
      userId: entry.userId,
      type: entry.type,
      otp: entry.otp,
      sender: entry.sender,
      receivedAt: entry.receivedAt,
    });
  }

  // DELETE /api/users/:userId/otps/:type  (mark used/remove after successful use)
  if (request.method === "DELETE" && pathParts.length === 5 && pathParts[0] === "api" && pathParts[1] === "users" && pathParts[3] === "otps") {
    const userId = decodeURIComponent(pathParts[2]).trim();
    const type = decodeURIComponent(pathParts[4]).trim().toLowerCase();
    const body = await getJsonBody(request);
    const otp = cleanText(body.otp);
    if (!otp) {
      return sendJson(response, 400, { error: "OTP value is required to delete an OTP." });
    }
    const key = keyFor(userId, type);
    const currentOtp = pendingOtps.get(key);

    // The client submits the OTP it used. If it no longer matches, a newer
    // OTP is pending and is left untouched. This happens in one DELETE call.
    if (currentOtp && otp !== currentOtp.otp) {
      return sendJson(response, 409, { deleted: false, reason: "A newer OTP is pending." });
    }

    const deleted = pendingOtps.delete(key);
    return sendJson(response, 200, { deleted });
  }

  return sendJson(response, 404, { error: "Route not found." });
}

const server = http.createServer((request, response) => {
  handleRequest(request, response).catch((error) => {
    const status = error.message === "Request body must be valid JSON." || error.message === "Request body is too large." ? 400 : 500;
    sendJson(response, status, { error: error.message || "Internal server error." });
  });
});

server.listen(PORT, "0.0.0.0", () => {
  console.log(`SMS OTP server listening on http://0.0.0.0:${PORT}`);
  console.log(`MacroDroid endpoint: POST /api/users/:userId/sms`);
  console.log("OTP types: main = Jharnibandhan / NGDRS numeric login OTP; egrass = KUBER JH / JEGRAS six-character OTP.");
  console.log(`Python endpoints: GET and DELETE /api/users/:userId/otps/:type (use main or egrass)`);
  console.log("SECURITY: Authentication is not enabled yet. Use only on a trusted private network; add API-key authentication before wider use.");
});
