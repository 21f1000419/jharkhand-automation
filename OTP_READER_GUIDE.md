# OTP Reader: build and ADB reference

The ready-to-install debug APK is always exported here after a build:

```text
dist\otp-reader-debug.apk
```

The app is for your own Android device. It requests SMS permission, looks for a 4–8 digit OTP in recent inbox messages and in future incoming SMS messages, displays the latest one, and writes a single line to logcat. Keep its output private: an OTP can be used to sign in as you.

## 1. Prepare the phone and ADB

On the phone, enable **Developer options** and **USB debugging**, connect it by USB, then accept the RSA debugging prompt on the phone.

```powershell
adb start-server
adb devices
```

`adb devices` must show one device with the status `device`. If it says `unauthorized`, unlock the phone and accept the prompt. If it is empty, reconnect the USB cable and ensure its USB mode permits data transfer.

## 2. Build the APK

From the workspace root:

```powershell
$env:JAVA_HOME = 'E:\Android Studio\jbr'
& 'C:\Users\vikas\.gradle\wrapper\dists\gradle-8.14.3-all\10utluxaxniiv4wxiphsi49nj\gradle-8.14.3\bin\gradle.bat' --no-daemon -p .\otp-reader assembleDebug
```

This produces both `otp-reader\app\build\outputs\apk\debug\app-debug.apk` and `dist\otp-reader-debug.apk`.

## 3. Install and enable the app

```powershell
adb install -r .\dist\otp-reader-debug.apk
adb shell am start -n com.compitcom.otpreader/.MainActivity
```

In the opened app, tap **Grant SMS permission** and approve both prompts. Alternatively, when the phone is connected over ADB, grant them directly:

```powershell
adb shell pm grant com.compitcom.otpreader android.permission.RECEIVE_SMS
adb shell pm grant com.compitcom.otpreader android.permission.READ_SMS
```

## 4. Receive an OTP and read it via ADB

Start listening before requesting/sending the OTP:

```powershell
adb logcat -c
adb logcat -s OTP_READER:I *:S
```

When a real SMS arrives, the app extracts the code and prints a line such as:

```text
OTP=123456 FROM=VM-EXAMPLE
```

Leave that command running while you wait for a message. Press `Ctrl+C` to stop it.

To print the most recently stored result at any time (including an OTP found in the recent inbox when the app opens):

```powershell
adb shell run-as com.compitcom.otpreader cat shared_prefs/latest_otp.xml
```

To show only already-captured log lines without waiting:

```powershell
adb logcat -d -s OTP_READER:I *:S
```

## Notes

- The app receives future SMS after permission is granted; it does not need to remain open.
- SMS messages without a 4–8 digit number are ignored. If a message contains several matching numbers, the app prefers a number near `OTP`, `code`, or `verification code`.
- This sideloaded utility is not designed for Google Play distribution, where SMS permissions are restricted.
