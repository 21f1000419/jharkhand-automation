package com.compitcom.otpreader;

import android.content.Context;
import android.content.SharedPreferences;

final class OtpStore {
    private static final String PREFS = "latest_otp";

    static void save(Context context, String otp, String sender, String message) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit()
                .putString("otp", otp).putString("sender", sender).putString("message", message)
                .apply();
    }

    static Latest load(Context context) {
        SharedPreferences p = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
        String otp = p.getString("otp", null);
        return otp == null ? null : new Latest(otp, p.getString("sender", "unknown"), p.getString("message", ""));
    }

    static void savePairing(Context context, String serverAddress, String token, boolean forwarding) {
        context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).edit()
                .putString("server_address", serverAddress.trim().replaceAll("/+$", ""))
                .putString("pairing_token", token.trim()).putBoolean("forwarding", forwarding).apply();
    }

    static Pairing pairing(Context context) {
        SharedPreferences p = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
        return new Pairing(p.getString("server_address", ""), p.getString("pairing_token", ""),
                p.getBoolean("forwarding", false));
    }

    static final class Latest {
        final String otp, sender, message;
        Latest(String otp, String sender, String message) { this.otp = otp; this.sender = sender; this.message = message; }
    }

    static final class Pairing {
        final String serverAddress, token;
        final boolean forwarding;
        Pairing(String serverAddress, String token, boolean forwarding) {
            this.serverAddress = serverAddress; this.token = token; this.forwarding = forwarding;
        }
    }
}
