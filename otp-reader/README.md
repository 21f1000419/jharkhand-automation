# OTP Reader (Android)

Minimal Android app for a device you own. It requests `RECEIVE_SMS` and `READ_SMS`, extracts a 4–8 digit OTP from a newly received SMS (and scans the latest 40 inbox SMS messages when opened), shows it in the app, and logs it to Android logcat.

## Build and install

Use Android Studio's **Open** command on this directory, then choose **Build > Build APK(s)**. Install its debug APK with:

```powershell
adb install -r app\build\outputs\apk\debug\app-debug.apk
adb shell am start -n com.compitcom.otpreader/.MainActivity
```

Tap **Grant SMS permission** and approve the Android prompt.

## Read an arriving OTP over ADB

```powershell
adb logcat -c
adb logcat -s OTP_READER:I *:S
```

Example output: `OTP=123456 FROM=VM-EXAMPLE`.

The latest stored value can also be read from a debug build:

```powershell
adb shell run-as com.compitcom.otpreader cat shared_prefs/latest_otp.xml
```

Do not send OTPs to a server or commit terminal logs containing them.
