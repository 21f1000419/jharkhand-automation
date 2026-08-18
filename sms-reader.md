# SMS reader API

This document describes the local SMS-to-OTP bridge in [`sms-server`](sms-server). MacroDroid forwards SMS messages from a phone to the server, then the Python automation polls the server for an OTP matching its entered user ID.

The server is intentionally lightweight: Node.js built-ins only, no database, and no npm dependencies. OTPs are kept only in memory, so restarting the server clears them.

## Start the server

```powershell
cd sms-server
node server.js
```

Default address: `http://COMPUTER_LAN_IP:8787`.

The phone must be on a network that can reach the computer. If Windows Firewall asks, allow Node.js on the private network only.

## API

### Health check

```http
GET /health
```

Example response:

```json
{ "ok": true, "pendingOtpCount": 0 }
```

### Forward an SMS from MacroDroid

```http
POST /api/users/:userId/sms
Content-Type: application/json
```

`userId` must exactly match the ID entered in the Python application.

```json
{
  "sender": "AX-MKUBER-S",
  "content": "KUBER JH- OTP for One Time User Validation IN JEGRAS IS A09AFD Time:18/08/2026 08:35:22 AM KUBER JH-OTP REFERENCE IS 2127753 Valid for 2 minutes",
  "metadata": {
    "receivedOnPhone": "optional MacroDroid timestamp"
  }
}
```

`metadata` is optional. Unrecognised messages are accepted with `202` but are not stored.

### Poll an OTP from the Python application

```http
GET /api/users/:userId/otps/:type
```

Allowed OTP types are `main` and `egrass`.

```http
GET /api/users/my-user/otps/main
GET /api/users/my-user/otps/egrass
```

If no OTP is waiting, the response is `404`:

```json
{ "found": false }
```

If an OTP is waiting, the response is `200`:

```json
{
  "found": true,
  "userId": "my-user",
  "type": "egrass",
  "otp": "A09AFD",
  "sender": "AX-MKUBER-S",
  "receivedAt": "2026-08-18T10:00:00.000Z"
}
```

Poll every 1-2 seconds while the target website is waiting for the OTP. Stop polling once received or when the automation timeout is reached.

### Mark the OTP used and remove it

Only delete the OTP after the target website has accepted it.

```http
DELETE /api/users/:userId/otps/:type
```

Example:

```http
DELETE /api/users/my-user/otps/egrass
```

Response:

```json
{ "deleted": true }
```

## OTP recognition and replacement rules

- `main` / Jharnibandhan: the numeric OTP is extracted only from `Your OTP to NGDRS Login :- 36367408`. The captured OTP is 4-8 digits.
- `egrass` / KUBER JH: the six-character alphanumeric OTP is extracted only from `KUBER JH- OTP for One Time User Validation IN JEGRAS IS A09AFD`.
- For e-GRAS, the `JH-OTP REFERENCE IS 2127753` value is a reference number, not an OTP, and is never used.
- The original SMS sender is stored with the OTP for traceability. The known e-GRAS sender is normally `AX-MKUBER-S`.
- Only one pending OTP exists for each `(userId, type)`. A newer matching SMS replaces an older one automatically.
- OTPs expire after five minutes by default (`OTP_TTL_MS` can change this). Used OTPs should be deleted through the API immediately after successful use.

## Future authentication

Authentication is deliberately not implemented for the initial 2-5-user local-network setup. Before exposing this server outside a trusted private network, add authentication to every SMS submission and OTP read/delete request—for example, a shared API key in an `Authorization` header or a per-device token. HTTPS and IP/network restrictions should also be considered before any wider use.
