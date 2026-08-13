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

    static final class Latest {
        final String otp, sender, message;
        Latest(String otp, String sender, String message) { this.otp = otp; this.sender = sender; this.message = message; }
    }
}
