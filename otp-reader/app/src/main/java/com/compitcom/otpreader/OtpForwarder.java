package com.compitcom.otpreader;

import android.content.Context;
import android.util.Log;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;

final class OtpForwarder {
    private static final String TAG = "OTP_READER";

    static void forward(Context context, String otp) {
        OtpStore.Pairing pairing = OtpStore.pairing(context);
        if (!pairing.forwarding || pairing.serverAddress.isEmpty() || pairing.token.isEmpty()) return;
        new Thread(() -> post(pairing, otp), "otp-forwarder").start();
    }

    static void pair(Context context) {
        OtpStore.Pairing pairing = OtpStore.pairing(context);
        if (!pairing.forwarding || pairing.serverAddress.isEmpty() || pairing.token.isEmpty()) return;
        new Thread(() -> post(pairing, null), "otp-pairing").start();
    }

    private static void post(OtpStore.Pairing pairing, String otp) {
        HttpURLConnection connection = null;
        try {
            String endpoint = otp == null ? "/pair" : "/otp";
            connection = (HttpURLConnection) new URL(pairing.serverAddress + endpoint).openConnection();
            connection.setRequestMethod("POST");
            connection.setConnectTimeout(5000);
            connection.setReadTimeout(5000);
            connection.setDoOutput(true);
            connection.setRequestProperty("Content-Type", "application/json");
            connection.setRequestProperty("X-Compitcom-Token", pairing.token);
            byte[] body = (otp == null ? "{}" : "{\"otp\":\"" + otp + "\"}").getBytes(StandardCharsets.UTF_8);
            try (OutputStream output = connection.getOutputStream()) { output.write(body); }
            if (connection.getResponseCode() != 204) Log.w(TAG, "OTP forwarding was rejected.");
        } catch (Exception error) {
            Log.w(TAG, "OTP forwarding failed: " + error.getClass().getSimpleName());
        } finally {
            if (connection != null) connection.disconnect();
        }
    }

    private OtpForwarder() {}
}
