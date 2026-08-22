package com.compitcom.otpreader;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.os.Bundle;
import android.telephony.SmsMessage;
import android.util.Log;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

public final class SmsReceiver extends BroadcastReceiver {
    private static final String TAG = "OTP_READER";
    private static final Pattern LABELED_OTP = Pattern.compile("(?i)(?:otp|one[- ]?time (?:password|code)|verification code|code)\\D{0,32}(?<!\\d)(\\d{4,8})(?!\\d)");
    private static final Pattern ANY_CODE = Pattern.compile("(?<!\\d)(\\d{4,8})(?!\\d)");

    @Override public void onReceive(Context context, Intent intent) {
        Bundle extras = intent.getExtras();
        if (extras == null) return;
        Object[] pdus = (Object[]) extras.get("pdus");
        if (pdus == null) return;
        String format = extras.getString("format");
        StringBuilder text = new StringBuilder();
        String sender = "unknown";
        for (Object pdu : pdus) {
            SmsMessage sms = SmsMessage.createFromPdu((byte[]) pdu, format);
            if (sms != null) {
                sender = sms.getOriginatingAddress();
                text.append(sms.getMessageBody());
            }
        }
        String message = text.toString();
        String otp = extract(message);
        if (otp == null) return;
        OtpStore.save(context, otp, sender, message);
        OtpForwarder.forward(context, otp);
        Log.i(TAG, "OTP received from " + sender);
    }

    static String extract(String message) {
        Matcher match = LABELED_OTP.matcher(message);
        if (match.find()) return match.group(1);
        match = ANY_CODE.matcher(message);
        return match.find() ? match.group(1) : null;
    }
}
