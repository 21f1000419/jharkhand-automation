# Local SMS OTP server

A dependency-free Node.js API for forwarding SMS messages from MacroDroid to the automation app. OTPs are held in memory and expire after five minutes by default. Restarting the server clears them.

## Start the server

```powershell
cd sms-server
node server.js
```

The server listens on port `8787`. These environment variables change the port and expiry:

```powershell
$env:PORT = 9000
$env:OTP_TTL_MS = 300000
node server.js
```

Check it with `GET /health`:

```json
{ "ok": true, "service": "sms-otp-server", "pendingOtpCount": 0 }
```

## Forward SMS messages

The existing MacroDroid endpoint remains:

```text
POST /api/users/USER_ID/sms
Content-Type: application/json
```

```json
{
  "sender": "[SMS sender variable]",
  "content": "[SMS message body variable]",
  "metadata": {
    "receivedOnPhone": "[timestamp variable]"
  }
}
```

`USER_ID` routes only the Citizen/NGDRS OTP. The server ignores it for eGRAS and extracts the eGRAS reference number from the SMS body.

## OTP recognition and storage

- Citizen/NGDRS messages match `Your OTP to NGDRS Login :- 36367408`. One pending Citizen OTP is stored per user ID.
- eGRAS messages must contain both `JEGRAS IS A09AFD` and `JH-OTP REFERENCE IS 2127753`. The server stores `A09AFD` under reference `2127753`.
- Each eGRAS reference has its own entry. Receiving an OTP for another reference does not replace or delete any existing eGRAS OTP.
- Receiving another OTP for the same reference replaces that reference's value, which is needed for resends.
- Unknown or incomplete messages return `202` and are not stored.

## Fetch OTPs

Citizen OTPs still use the configured SMS user ID and freshness timestamp:

```text
GET /api/users/USER_ID/otps/main?notBefore=2026-08-18T10:00:00.000Z
```

eGRAS has no user ID or OTP type in its lookup route:

```text
GET /api/egrass/otps/2127753
```

A missing OTP returns `404` with `{ "found": false }`. A matching eGRAS OTP returns:

```json
{
  "found": true,
  "type": "egrass",
  "otp": "A09AFD",
  "referenceNumber": "2127753",
  "sender": "AX-MKUBER-S",
  "receivedAt": "2026-08-18T10:00:05.000Z"
}
```

## Delete used OTPs

Delete an OTP only after the website accepts it. Include the used value so delayed cleanup cannot remove a replacement OTP.

```text
DELETE /api/users/USER_ID/otps/main
Content-Type: application/json

{ "otp": "36367408" }
```

```text
DELETE /api/egrass/otps/2127753
Content-Type: application/json

{ "otp": "A09AFD" }
```

The response is `{ "deleted": true }` when the entry existed. A different pending OTP for the same key returns `409` and remains stored.

## Security

This server has no authentication. Keep it on a trusted private network. Add authentication, HTTPS, and network restrictions before exposing it more widely.
