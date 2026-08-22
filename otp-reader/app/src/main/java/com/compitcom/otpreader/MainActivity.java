package com.compitcom.otpreader;

import android.Manifest;
import android.app.Activity;
import android.content.pm.PackageManager;
import android.database.Cursor;
import android.graphics.Color;
import android.os.Bundle;
import android.provider.Telephony;
import android.view.Gravity;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.Switch;
import android.widget.TextView;

public final class MainActivity extends Activity {
    private TextView result;
    private EditText serverAddress, pairingToken;
    private Switch forwarding;

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setPadding(48, 64, 48, 48);
        root.setGravity(Gravity.CENTER_HORIZONTAL);

        TextView title = new TextView(this);
        title.setText("Compitcom OTP Reader"); title.setTextSize(26); title.setTextColor(Color.rgb(20, 65, 120));
        root.addView(title);
        TextView help = new TextView(this);
        help.setText("Pair this phone with the desktop app on the same Wi-Fi. Only the extracted OTP is sent directly to your computer; SMS text is never forwarded.");
        help.setTextSize(16); help.setPadding(0, 22, 0, 18); root.addView(help);

        Button grant = new Button(this); grant.setText("Grant SMS permission");
        grant.setOnClickListener(v -> requestSmsPermission()); root.addView(grant);

        serverAddress = field("Desktop server address", root);
        pairingToken = field("Pairing token", root);
        forwarding = new Switch(this); forwarding.setText("Forward new OTPs to desktop"); root.addView(forwarding);
        Button save = new Button(this); save.setText("Save pairing"); save.setOnClickListener(v -> savePairing()); root.addView(save);
        Button refresh = new Button(this); refresh.setText("Refresh latest OTP"); refresh.setOnClickListener(v -> render()); root.addView(refresh);

        result = new TextView(this); result.setTextSize(18); result.setTextIsSelectable(true); result.setPadding(0, 26, 0, 0);
        root.addView(result); setContentView(root);
        loadPairing(); render();
    }

    private EditText field(String hint, LinearLayout root) {
        EditText field = new EditText(this); field.setHint(hint); field.setSingleLine(true); root.addView(field); return field;
    }

    private void loadPairing() {
        OtpStore.Pairing pairing = OtpStore.pairing(this);
        serverAddress.setText(pairing.serverAddress); pairingToken.setText(pairing.token); forwarding.setChecked(pairing.forwarding);
    }

    private void savePairing() {
        OtpStore.savePairing(this, serverAddress.getText().toString(), pairingToken.getText().toString(), forwarding.isChecked());
        OtpForwarder.pair(this);
        render();
    }

    private void requestSmsPermission() {
        if (checkSelfPermission(Manifest.permission.RECEIVE_SMS) != PackageManager.PERMISSION_GRANTED
                || checkSelfPermission(Manifest.permission.READ_SMS) != PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[]{Manifest.permission.RECEIVE_SMS, Manifest.permission.READ_SMS}, 10);
        }
    }

    @Override public void onRequestPermissionsResult(int code, String[] permissions, int[] results) {
        super.onRequestPermissionsResult(code, permissions, results); importLatestInboxOtp(); render();
    }

    @Override public void onResume() { super.onResume(); if (result != null) render(); }

    private void render() {
        if (checkSelfPermission(Manifest.permission.RECEIVE_SMS) != PackageManager.PERMISSION_GRANTED) {
            result.setText("SMS permission has not been granted."); return;
        }
        importLatestInboxOtp(); OtpStore.Latest latest = OtpStore.load(this); OtpStore.Pairing pairing = OtpStore.pairing(this);
        String pairingState = pairing.forwarding ? "Forwarding: enabled" : "Forwarding: disabled";
        result.setText(pairingState + "\n\n" + (latest == null ? "Waiting for a new OTP…" : "Latest OTP: " + latest.otp + "\nFrom: " + latest.sender));
    }

    private void importLatestInboxOtp() {
        if (checkSelfPermission(Manifest.permission.READ_SMS) != PackageManager.PERMISSION_GRANTED) return;
        try (Cursor cursor = getContentResolver().query(Telephony.Sms.Inbox.CONTENT_URI,
                new String[]{Telephony.Sms.ADDRESS, Telephony.Sms.BODY}, null, null, Telephony.Sms.DATE + " DESC")) {
            if (cursor == null) return;
            int count = 0;
            while (cursor.moveToNext() && count++ < 40) {
                String body = cursor.getString(1); String otp = SmsReceiver.extract(body == null ? "" : body);
                if (otp != null) { OtpStore.save(this, otp, cursor.getString(0), body); return; }
            }
        } catch (SecurityException ignored) { }
    }
}
