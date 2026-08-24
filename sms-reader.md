# SMS reader API

The `sms-server` bridge receives messages from MacroDroid and lets the Python automation poll for matching OTPs. It uses Node.js built-ins and keeps OTPs only in memory.

## Start and check the server

```powershell
cd sms-server
node server.js
```

```text
GET /health
```

```json
{ "ok": true, "pendingOtpCount": 0 }
```

## Forward an SMS from MacroDroid

```text
POST /api/users/:userId/sms
Content-Type: application/json
```

```json
{
  "sender": "AX-MKUBER-S",
  "content": "KUBER JH- OTP for One Time User Validation IN JEGRAS IS A09AFD Time:18/08/2026 08:35:22 AM KUBER JH-OTP REFERENCE IS 2127753 Valid for 2 minutes",
  "metadata": {
    "receivedOnPhone": "2026-08-18T08:35:22+05:30"
  }
}
```

The path user ID is used only when the message is a Citizen/NGDRS OTP. eGRAS storage ignores it and uses the reference number in the SMS.

## Poll a Citizen OTP

```text
GET /api/users/:userId/otps/main?notBefore=:serverUtcTimestamp
```

The app records the UTC timestamp immediately before polling. OTPs older than `notBefore` are not returned.

## Poll an eGRAS OTP

The automation reads text such as `OTP Reference number : 2163352` from `#divOtpMsg` on the eGRAS page. It then requests only that reference:

```text
GET /api/egrass/otps/2163352
```

The eGRAS route has no user ID. Multiple references can remain pending at the same time, so parallel browser sessions cannot overwrite one another's OTPs.

If no OTP is waiting, either poll route returns `404`:

```json
{ "found": false }
```

A matching eGRAS response is:

```json
{
  "found": true,
  "type": "egrass",
  "otp": "A09AFD",
  "referenceNumber": "2163352",
  "sender": "AX-MKUBER-S",
  "receivedAt": "2026-08-18T10:00:05.000Z"
}
```

The app polls for up to 90 seconds for Citizen OTPs and 100 seconds for eGRAS. Checks happen every second for the first 10 seconds, every two seconds until 40 seconds, then every three seconds.

## Remove an accepted OTP

The app deletes an OTP only after the target website accepts it. It sends the used OTP value so a delayed cleanup cannot delete a replacement.

```text
DELETE /api/users/:userId/otps/main
Content-Type: application/json

{ "otp": "36367408" }
```

```text
DELETE /api/egrass/otps/2163352
Content-Type: application/json

{ "otp": "A09AFD" }
```

The server returns `409` and keeps the stored entry if the submitted OTP differs from the current OTP for that user or reference.

## Recognition and expiry

- Citizen/NGDRS OTPs come only from `Your OTP to NGDRS Login :- 36367408` and contain four to eight digits.
- eGRAS OTPs come from the six-character value after `JEGRAS IS`.
- An eGRAS message is stored only when it also contains `JH-OTP REFERENCE IS ...`.
- Each eGRAS reference has an independent entry. A resend for the same reference updates only that entry.
- Entries expire after five minutes by default. Set `OTP_TTL_MS` to change this.

## Security

The server currently has no authentication. Use it only on a trusted private network. Add authentication, HTTPS, and network restrictions before wider exposure.
