"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");

const { createSmsOtpServer, findOtp } = require("./server");

const EGRASS_MESSAGE = (otp, referenceNumber) =>
  `KUBER JH- OTP for One Time User Validation IN JEGRAS IS ${otp} ` +
  `Time:18/08/2026 08:35:22 AM KUBER JH-OTP REFERENCE IS ${referenceNumber} Valid for 2 minutes`;

async function request(baseUrl, path, options = {}) {
  const response = await fetch(`${baseUrl}${path}`, options);
  return { status: response.status, body: await response.json() };
}

test("findOtp requires and extracts the e-GRAS reference number", () => {
  assert.deepEqual(findOtp(EGRASS_MESSAGE("A09AFD", "2127753")), {
    type: "egrass",
    otp: "A09AFD",
    referenceNumber: "2127753",
  });
  assert.equal(
    findOtp("KUBER JH- OTP for One Time User Validation IN JEGRAS IS A09AFD"),
    null,
  );
});

test("e-GRAS OTPs coexist and are fetched only by reference number", async (context) => {
  const server = createSmsOtpServer();
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  context.after(() => new Promise((resolve) => server.close(resolve)));
  const address = server.address();
  assert.ok(address && typeof address === "object");
  const baseUrl = `http://127.0.0.1:${address.port}`;

  for (const [userId, otp, referenceNumber] of [
    ["citizen-one", "A09AFD", "2127753"],
    ["citizen-two", "B18BGE", "2163352"],
  ]) {
    const result = await request(baseUrl, `/api/users/${userId}/sms`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        sender: "AX-MKUBER-S",
        content: EGRASS_MESSAGE(otp, referenceNumber),
      }),
    });
    assert.equal(result.status, 201);
  }

  const first = await request(baseUrl, "/api/egrass/otps/2127753");
  const second = await request(baseUrl, "/api/egrass/otps/2163352");
  assert.equal(first.status, 200);
  assert.equal(first.body.otp, "A09AFD");
  assert.equal(first.body.referenceNumber, "2127753");
  assert.equal(second.status, 200);
  assert.equal(second.body.otp, "B18BGE");
  assert.equal(second.body.referenceNumber, "2163352");
  assert.equal("userId" in first.body, false);

  const oldUserRoute = await request(baseUrl, "/api/users/citizen-one/otps/egrass");
  assert.equal(oldUserRoute.status, 400);

  const mainStored = await request(baseUrl, "/api/users/citizen-one/sms", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      sender: "VM-NGDRS",
      content: "Your OTP to NGDRS Login :- 36367408",
    }),
  });
  assert.equal(mainStored.status, 201);
  const main = await request(
    baseUrl,
    "/api/users/citizen-one/otps/main?notBefore=2026-08-18T10:00:00.000Z",
  );
  assert.equal(main.status, 200);
  assert.equal(main.body.otp, "36367408");
  assert.equal(main.body.userId, "citizen-one");

  const deleted = await request(baseUrl, "/api/egrass/otps/2127753", {
    method: "DELETE",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ otp: "A09AFD" }),
  });
  assert.deepEqual(deleted, { status: 200, body: { deleted: true } });

  const stillPending = await request(baseUrl, "/api/egrass/otps/2163352");
  assert.equal(stillPending.status, 200);
  assert.equal(stillPending.body.otp, "B18BGE");
});
