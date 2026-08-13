package com.compitcom.otpreader;

import android.Manifest;
import android.app.Activity;
import android.content.pm.PackageManager;
import android.database.Cursor;
import android.graphics.Color;
import android.os.Bundle;
import android.provider.Telephony;
import android.view.Gravity;
import android.view.View;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.TextView;

public final class MainActivity extends Activity {
    private TextView result;

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setPadding(48, 64, 48, 48);
        root.setGravity(Gravity.CENTER_HORIZONTAL);

        TextView title = new TextView(this);
        title.setText("OTP Reader");
        title.setTextSize(26);
        title.setTextColor(Color.rgb(20, 65, 120));
        root.addView(title);

        TextView help = new TextView(this);
        help.setText("Grants SMS access only on this device. New OTPs are shown here and written to logcat as OTP_READER.");
        help.setTextSize(16);
        help.setPadding(0, 30, 0, 30);
        root.addView(help);

        Button grant = new Button(this);
        grant.setText("Grant SMS permission");
        grant.setOnClickListener(v -> requestSmsPermission());
        root.addView(grant);

        Button refresh = new Button(this);
        refresh.setText("Refresh latest OTP");
        refresh.setOnClickListener(v -> render());
        root.addView(refresh);

        result = new TextView(this);
        result.setTextSize(20);
        result.setTextIsSelectable(true);
        result.setPadding(0, 42, 0, 0);
        root.addView(result);
        setContentView(root);
        render();
    }

    private void requestSmsPermission() {
        if (checkSelfPermission(Manifest.permission.RECEIVE_SMS) != PackageManager.PERMISSION_GRANTED
                || checkSelfPermission(Manifest.permission.READ_SMS) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[]{Manifest.permission.RECEIVE_SMS, Manifest.permission.READ_SMS}, 10);
        }
    }

    @Override public void onRequestPermissionsResult(int code, String[] permissions, int[] results) {
        super.onRequestPermissionsResult(code, permissions, results);
        importLatestInboxOtp();
        render();
    }

    @Override public void onResume() { super.onResume(); if (result != null) render(); }

    private void render() {
        if (checkSelfPermission(Manifest.permission.RECEIVE_SMS) != PackageManager.PERMISSION_GRANTED) {
            result.setText("SMS permission has not been granted.");
            return;
        }
        importLatestInboxOtp();
        OtpStore.Latest latest = OtpStore.load(this);
        result.setText(latest == null ? "Waiting for an SMS…" :
                "Latest OTP: " + latest.otp + "\n\nFrom: " + latest.sender + "\n\nReceived:\n" + latest.message);
    }

    private void importLatestInboxOtp() {
        if (checkSelfPermission(Manifest.permission.READ_SMS) != PackageManager.PERMISSION_GRANTED) return;
        try (Cursor cursor = getContentResolver().query(
                Telephony.Sms.Inbox.CONTENT_URI,
                new String[]{Telephony.Sms.ADDRESS, Telephony.Sms.BODY},
                null, null, Telephony.Sms.DATE + " DESC")) {
            if (cursor == null) return;
            int count = 0;
            while (cursor.moveToNext() && count++ < 40) {
                String body = cursor.getString(1);
                String otp = SmsReceiver.extract(body == null ? "" : body);
                if (otp != null) {
                    OtpStore.save(this, otp, cursor.getString(0), body);
                    return;
                }
            }
        } catch (SecurityException ignored) {
            // Android denied provider access despite the permission; incoming SMS still works.
        }
    }
}
