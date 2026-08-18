# Local SMS OTP server

A dependency-free Node.js API for MacroDroid to forward incoming SMS messages to the local automation application. OTPs live only in memory: restarting the server clears them.

## Run

```powershell
cd sms-server
node server.js
```

It listens on port `8787`. To change this or the five-minute OTP expiry:

```powershell
$env:PORT = 8788
$env:OTP_TTL_MS = 300000
node server.js
```

Open `http://localhost:8787/` to verify the server is running. It returns JSON like:

```json
{ "ok": true, "service": "sms-otp-server", "pendingOtpCount": 0 }
```

The phone and the computer must be able to reach each other. Use the computer's LAN IP in MacroDroid, for example `http://192.168.1.20:8787`.

## MacroDroid HTTP request

Create an SMS-received macro and send an HTTP `POST` request to:

```
http://COMPUTER_LAN_IP:8787/api/users/USER_ID/sms
```

Set the body type to JSON and send the actual MacroDroid variables for the sender and message content:

```json
{
  "sender": "[SMS sender variable]",
  "content": "[SMS message body variable]",
  "metadata": {
    "receivedOnPhone": "[optional timestamp variable]"
  }
}
```

`USER_ID` must exactly match the user ID entered in the Python application. `metadata` is optional.

## OTP recognition and storage

- `main` (Jharnibandhan): a content match for `Your OTP to NGDRS Login :- 36367408` stores the 4-8 digit value immediately after `:-` as the `main` OTP.
- `egrass` (KUBER JH, usually sender `AX-MKUBER-S`): a content match for `KUBER JH- OTP for One Time User Validation IN JEGRAS IS A09AFD` stores exactly the six alphanumeric characters after `JEGRAS IS`. The later `JH-OTP REFERENCE IS 2127753` is never stored as an OTP.
- Other numeric values, including dates, times, GRNs, and reference numbers, are ignored.
- Only one pending OTP exists for each `(userId, type)`. A new matching SMS replaces the old value automatically.
- OTPs expire after five minutes by default. They are removed after a successful use by the Python app.

Unknown SMS messages are accepted but not stored (`202` response), so one MacroDroid rule can forward all SMS messages safely.

## Python API contract

Poll every 1-2 seconds while the browser is waiting for an OTP:

```
GET /api/users/USER_ID/otps/main
GET /api/users/USER_ID/otps/egrass
```

To exclude OTPs that arrived before the website requested a new one, add an ISO-8601 UTC `notBefore` query parameter:

```
GET /api/users/USER_ID/otps/main?notBefore=2026-08-18T10:00:00.000Z
```

The desktop app records this timestamp in UTC immediately before requesting an OTP. UTC avoids timezone differences between the desktop and server.

A waiting OTP returns `404` with `{ "found": false }`. A received OTP returns:

```json
{
  "found": true,
  "userId": "USER_ID",
  "type": "main",
  "otp": "36367408",
  "sender": "VM-NGDRS",
  "receivedAt": "2026-08-18T10:00:00.000Z"
}
```

After the OTP was actually accepted by the target website, remove it. Send the OTP value used in the body so a delayed cleanup cannot delete a newer replacement OTP. This is one DELETE request; no pre-delete lookup is needed.

```
DELETE /api/users/USER_ID/otps/main
```

```json
{ "otp": "36367408" }
```

Use the same route with `egrass` for e-GRAS. The delete response is `{ "deleted": true }` when an entry was removed. It returns `409` if a newer OTP is already pending.

## Quick manual check

```powershell
Invoke-RestMethod -Method Post -Uri 'http://localhost:8787/api/users/demo/sms' -ContentType 'application/json' -Body '{"sender":"VM-NGDRS","content":"Your OTP to NGDRS Login :- 36367408"}'
Invoke-RestMethod 'http://localhost:8787/api/users/demo/otps/main'
Invoke-RestMethod -Method Delete -Uri 'http://localhost:8787/api/users/demo/otps/main' -ContentType 'application/json' -Body '{"otp":"36367408"}'
```
