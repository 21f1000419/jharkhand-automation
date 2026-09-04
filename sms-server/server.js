"use strict";

// In-memory SMS/OTP bridge for MacroDroid and the local Python app.
// Uses only Node's built-in modules, so no npm install is required.

const http = require("http");
const { URL } = require("url");

const PORT = Number(process.env.PORT || 8787);
const MAX_BODY_BYTES = 64 * 1024;
const OTP_TTL_MS = Number(process.env.OTP_TTL_MS || 5 * 60 * 1000);

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
      const timestamp = new Date().toISOString();
      console.log(`[${timestamp}] Incoming request body (${request.method} ${request.url}): ${body || "<empty>"}`);
      if (!body) return resolve({});
      try {
        resolve(JSON.parse(body));
      } catch (err) {
        console.error(`[${timestamp}] JSON parse error for body:`, body);
        reject(new Error("Request body must be valid JSON."));
      }
    });
    request.on("error", reject);
  });
}

function cleanText(value) {
  return typeof value === "string" ? value.trim() : "";
}

function cleanReferenceNumber(value) {
  const referenceNumber = cleanText(value).toUpperCase();
  return /^[A-Z0-9-]+$/.test(referenceNumber) ? referenceNumber : "";
}

function findOtp(content) {
  // Confirmed Jharnibandhan/NGDRS login message format:
  // "Your OTP to NGDRS Login :- 36367408"
  const mainMatch = content.match(/\byour\s+otp\s+to\s+ngdrs\s+login\s*[:-]+\s*(\d{4,8})\b/i);
  if (mainMatch) return { type: "main", otp: mainMatch[1] };

  // Confirmed KUBER JH e-GRAS message format:
  // "KUBER JH- OTP for One Time User Validation IN JEGRAS IS A09AFD ...
  //  KUBER JH-OTP REFERENCE IS 2127753 Valid for 2 minutes"
  const egrassOtpMatch = content.match(
    /\bkuber\s+jh\s*-\s*otp\s+for\s+one\s+time\s+user\s+validation\s+in\s+jegras\s+is\s+([a-z0-9]{6})\b/i,
  );
  const egrassReferenceMatch = content.match(
    /\bjh\s*-\s*otp\s+reference\s+is\s+([a-z0-9-]+)\b/i,
  );
  if (egrassOtpMatch && egrassReferenceMatch) {
    return {
      type: "egrass",
      otp: egrassOtpMatch[1].toUpperCase(),
      referenceNumber: egrassReferenceMatch[1].toUpperCase(),
    };
  }

  return null;
}

function mainKey(userId) {
  return `main:${userId}`;
}

function egrassKey(referenceNumber) {
  return `egrass:${referenceNumber}`;
}

function removeExpiredOtps(pendingOtps) {
  const cutoff = Date.now() - OTP_TTL_MS;
  for (const [key, entry] of pendingOtps) {
    if (entry.receivedAtMs < cutoff) pendingOtps.delete(key);
  }
}

function readNotBefore(url, response) {
  const value = url.searchParams.get("notBefore");
  const timestamp = value ? Date.parse(value) : null;
  if (value && Number.isNaN(timestamp)) {
    sendJson(response, 400, { error: "notBefore must be a valid ISO-8601 timestamp." });
    return { valid: false, timestamp: null };
  }
  return { valid: true, timestamp };
}

function sendOtp(response, entry) {
  const payload = {
    found: true,
    type: entry.type,
    otp: entry.otp,
    sender: entry.sender,
    receivedAt: entry.receivedAt,
  };
  if (entry.type === "main") payload.userId = entry.userId;
  if (entry.type === "egrass") payload.referenceNumber = entry.referenceNumber;
  return sendJson(response, 200, payload);
}

function lookupClient(request) {
  return request.socket.remoteAddress || "unknown";
}

function logMainOtpLookup(request, userId, rawNotBefore, notBeforeTimestamp, entry, result) {
  const timestamp = new Date().toISOString();
  const notBefore =
    notBeforeTimestamp === null ? "<not set>" : new Date(notBeforeTimestamp).toISOString();
  const entryDetails = entry
    ? `stored OTP: ${entry.otp}, receivedAt: ${entry.receivedAt}, ` +
      `receivedAtMs: ${entry.receivedAtMs}, deltaMs: ${
        notBeforeTimestamp === null ? "n/a" : entry.receivedAtMs - notBeforeTimestamp
      }`
    : "stored OTP: <none>";
  console.log(
    `[${timestamp}] Main OTP lookup - User ID: ${userId}, Client: ${lookupClient(request)}, ` +
      `notBefore raw: ${rawNotBefore || "<not set>"}, notBefore parsed: ${notBefore}, ` +
      `${entryDetails}, Result: ${result}`,
  );
}

async function deleteMatchingOtp(request, response, pendingOtps, key) {
  const body = await getJsonBody(request);
  const otp = cleanText(body.otp).toUpperCase();
  if (!otp) {
    return sendJson(response, 400, { error: "OTP value is required to delete an OTP." });
  }

  removeExpiredOtps(pendingOtps);
  const currentOtp = pendingOtps.get(key);
  if (currentOtp && otp !== currentOtp.otp) {
    return sendJson(response, 409, {
      deleted: false,
      reason: "A different OTP is pending for this key.",
    });
  }

  return sendJson(response, 200, { deleted: pendingOtps.delete(key) });
}

function createRequestHandler(pendingOtps = new Map()) {
  return async function handleRequest(request, response) {
    const url = new URL(request.url, `http://${request.headers.host || "localhost"}`);
    const pathParts = url.pathname.split("/").filter(Boolean);

    if (request.method === "GET" && (url.pathname === "/" || url.pathname === "/health")) {
      removeExpiredOtps(pendingOtps);
      return sendJson(response, 200, {
        ok: true,
        service: "sms-otp-server",
        pendingOtpCount: pendingOtps.size,
      });
    }

    // Existing MacroDroid endpoint. The user ID is used only for main/NGDRS OTPs.
    // e-GRAS entries are keyed solely by the reference number parsed from the SMS.
    if (
      request.method === "POST" &&
      pathParts.length === 4 &&
      pathParts[0] === "api" &&
      pathParts[1] === "users" &&
      pathParts[3] === "sms"
    ) {
      const userId = decodeURIComponent(pathParts[2]).trim();
      if (!userId) return sendJson(response, 400, { error: "userId is required in the path." });

      const body = await getJsonBody(request);
      const content = cleanText(body.content);
      const sender = cleanText(body.sender);
      const receivedAt = new Date().toISOString();

      if (!content) {
        console.log(`[${receivedAt}] Incoming SMS rejected - missing content. Body: ${JSON.stringify(body)}`);
        return sendJson(response, 400, { error: "content is required." });
      }

      console.log(`[${receivedAt}] Incoming SMS - Sender: "${sender || "N/A"}", Content: "${content}"`);
      const recognisedOtp = findOtp(content);
      if (!recognisedOtp) {
        console.log(`[${receivedAt}] Unrecognised SMS - Sender: "${sender || "N/A"}" (no complete OTP pattern)`);
        return sendJson(response, 202, {
          stored: false,
          reason: "SMS content is not a recognised OTP pattern.",
        });
      }

      removeExpiredOtps(pendingOtps);
      const entry = {
        ...recognisedOtp,
        sender: sender || "",
        content,
        metadata: body.metadata && typeof body.metadata === "object" ? body.metadata : {},
        receivedAt,
        receivedAtMs: Date.now(),
      };

      if (entry.type === "main") {
        entry.userId = userId;
        pendingOtps.set(mainKey(userId), entry);
        console.log(`[${receivedAt}] Stored main OTP - User ID: ${userId}, Sender: "${sender || "N/A"}"`);
      } else {
        pendingOtps.set(egrassKey(entry.referenceNumber), entry);
        console.log(`[${receivedAt}] Stored e-GRAS OTP - Reference: ${entry.referenceNumber}, Sender: "${sender || "N/A"}"`);
      }

      return sendJson(response, 201, {
        stored: true,
        type: entry.type,
        referenceNumber: entry.referenceNumber,
        receivedAt,
      });
    }

    // GET /api/users/:userId/otps/main
    if (
      request.method === "GET" &&
      pathParts.length === 5 &&
      pathParts[0] === "api" &&
      pathParts[1] === "users" &&
      pathParts[3] === "otps"
    ) {
      const userId = decodeURIComponent(pathParts[2]).trim();
      const type = decodeURIComponent(pathParts[4]).trim().toLowerCase();
      if (!userId || type !== "main") {
        return sendJson(response, 400, {
          error: "This route accepts only the main OTP type. Fetch e-GRAS by reference number.",
        });
      }

      removeExpiredOtps(pendingOtps);
      const rawNotBefore = url.searchParams.get("notBefore");
      const notBefore = readNotBefore(url, response);
      if (!notBefore.valid) return;
      const entry = pendingOtps.get(mainKey(userId));
      if (!entry) {
        logMainOtpLookup(request, userId, rawNotBefore, notBefore.timestamp, null, "not found");
        return sendJson(response, 404, { found: false });
      }
      if (notBefore.timestamp !== null && entry.receivedAtMs < notBefore.timestamp) {
        logMainOtpLookup(request, userId, rawNotBefore, notBefore.timestamp, entry, "rejected as stale");
        return sendJson(response, 404, { found: false });
      }
      logMainOtpLookup(request, userId, rawNotBefore, notBefore.timestamp, entry, "found");
      return sendOtp(response, entry);
    }

    // GET /api/egrass/otps/:referenceNumber
    if (
      request.method === "GET" &&
      pathParts.length === 4 &&
      pathParts[0] === "api" &&
      pathParts[1] === "egrass" &&
      pathParts[2] === "otps"
    ) {
      const referenceNumber = cleanReferenceNumber(decodeURIComponent(pathParts[3]));
      if (!referenceNumber) {
        return sendJson(response, 400, { error: "A valid e-GRAS reference number is required." });
      }

      removeExpiredOtps(pendingOtps);
      const entry = pendingOtps.get(egrassKey(referenceNumber));
      if (!entry) return sendJson(response, 404, { found: false });
      return sendOtp(response, entry);
    }

    // DELETE /api/users/:userId/otps/main
    if (
      request.method === "DELETE" &&
      pathParts.length === 5 &&
      pathParts[0] === "api" &&
      pathParts[1] === "users" &&
      pathParts[3] === "otps"
    ) {
      const userId = decodeURIComponent(pathParts[2]).trim();
      const type = decodeURIComponent(pathParts[4]).trim().toLowerCase();
      if (!userId || type !== "main") {
        return sendJson(response, 400, {
          error: "This route accepts only the main OTP type. Delete e-GRAS by reference number.",
        });
      }
      return deleteMatchingOtp(request, response, pendingOtps, mainKey(userId));
    }

    // DELETE /api/egrass/otps/:referenceNumber
    if (
      request.method === "DELETE" &&
      pathParts.length === 4 &&
      pathParts[0] === "api" &&
      pathParts[1] === "egrass" &&
      pathParts[2] === "otps"
    ) {
      const referenceNumber = cleanReferenceNumber(decodeURIComponent(pathParts[3]));
      if (!referenceNumber) {
        return sendJson(response, 400, { error: "A valid e-GRAS reference number is required." });
      }
      return deleteMatchingOtp(request, response, pendingOtps, egrassKey(referenceNumber));
    }

    return sendJson(response, 404, { error: "Route not found." });
  };
}

function createSmsOtpServer(pendingOtps = new Map()) {
  const handleRequest = createRequestHandler(pendingOtps);
  return http.createServer((request, response) => {
    handleRequest(request, response).catch((error) => {
      const status =
        error.message === "Request body must be valid JSON." ||
        error.message === "Request body is too large."
          ? 400
          : 500;
      sendJson(response, status, { error: error.message || "Internal server error." });
    });
  });
}

if (require.main === module) {
  const server = createSmsOtpServer();
  server.listen(PORT, "0.0.0.0", () => {
    console.log(`SMS OTP server listening on http://0.0.0.0:${PORT}`);
    console.log("MacroDroid endpoint: POST /api/users/:userId/sms");
    console.log("The user ID routes main/NGDRS OTPs only. e-GRAS ignores it and uses the reference parsed from the SMS.");
    console.log("Main endpoints: GET and DELETE /api/users/:userId/otps/main");
    console.log("e-GRAS endpoints: GET and DELETE /api/egrass/otps/:referenceNumber");
    console.log("SECURITY: Authentication is not enabled. Use only on a trusted private network.");
  });
}

module.exports = {
  createSmsOtpServer,
  findOtp,
};
