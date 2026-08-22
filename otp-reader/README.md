# Compitcom OTP Reader (Android)

Android companion for a phone you control. It extracts a 4–8 digit OTP from newly received SMS messages and can send only that code directly to the desktop application over the same Wi-Fi. It never sends the SMS body or sender to a cloud service.

## Pair with the desktop application

1. Open the desktop app and choose **OTP phone...**.
2. On the Android app, grant SMS permission.
3. Enter the displayed **Server address** and **Pairing token**.
4. Turn on **Forward new OTPs to desktop**, then tap **Save pairing**.
5. In the desktop app, enable **Auto-fill eGRAS OTP from paired phone** before starting the batch.

The Android app confirms its pairing to the desktop whenever you save it. If no paired phone is connected, or the phone does not send an OTP, the workflow uses the normal manual OTP checkpoint. Even after auto-fill, CAPTCHA and the portal’s final submit action remain manual.

The token changes whenever the desktop application restarts. Keep both devices on the same private Wi-Fi; do not expose the desktop listener to the internet.

## Build and install

```powershell
cd otp-reader
.\gradlew.bat assembleDebug
adb install -r app\build\outputs\apk\debug\app-debug.apk
```

The project copies the debug APK to `..\dist\otp-reader-debug.apk` after a successful debug build.
