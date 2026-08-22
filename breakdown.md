Here is the complete analysis of the Daily Self-Print Quota Counter Mechanism, detailing how it is initialized, checked,
incremented on the backend, scoped, and interacted with or manipulated.
──────

### Architecture of the Daily Self-Print Quota Mechanism

    sequenceDiagram
        autonumber
        actor User as Client / Browser
        participant Portal as OlnEsiCrtSubm.jsp
        participant CertOpt as OlnEsiGetCertOption.jsp
        participant SPCnt as OlnEsiGetSPCnt.jsp
        participant ServerDB as SHCIL Database

        User->>Portal: Selects State (e.g. Chandigarh "CH")
        Portal->>CertOpt: AJAX GET OlnEsiGetCertOption.jsp?iUsr=compitcom&uSttCd=CH
        CertOpt-->>Portal: Pipe-delimited string (opt[5] = Daily Limit)
        Portal->>Portal: Sets $("#iEsiSelfPrintLimit").val(opt[5])

        User->>Portal: Clicks "Proceed" (#btn_sub)
        Portal->>Portal: Triggers checkStateselect()
        Portal->>SPCnt: AJAX GET OlnEsiGetSPCnt.jsp?iUsr=compitcom&uSttCd=CH&lType=RG
        SPCnt->>ServerDB: Query daily SP transactions count for user+state today
        ServerDB-->>SPCnt: Current count integer (vSPSubmCnt)
        SPCnt-->>Portal: Returns count (e.g. "0", "1", "2")

        alt vSPSubmCnt >= iEsiSelfPrintLimit
            Portal-->>User: Alert: "Please Note, Only X Self Printing is allowed for a day" (BLOCKED)
        else vSPSubmCnt < iEsiSelfPrintLimit
            Portal->>User: Form validation passes -> POST to OlnEsiSubmission.jsp
        end

──────

## 1. Where the Limit Ceiling is Retrieved & Set (iEsiSelfPrintLimit)

• File: index.html:234-247
• Trigger: Invoked whenever the user selects or changes a State dropdown (#iSttCd) via StateSelect().
• API Request:
var milliseconds = new Date().getTime();
$.ajax({
        url: "OlnEsiGetCertOption.jsp?iUsr=compitcom&uSttCd=" + vSttCd,
        data: {'time': milliseconds.toString()},
        async: false
    }).done(function (result) {
        var opt = $.trim(result).split("|");
$("#iMultiOpt").val(opt[0]);
        $("#iCashEnb").val(opt[1]);
$("#iSroPrintEnb").val(opt[2]);
        $("#iEsiCurEnb").val(opt[3]);
$("#iEsiSelfPrintEnb").val(opt[4]);     // 'Y' / 'N'
        $("#iEsiSelfPrintLimit").val(opt[5]); // Configured Daily Limit (e.g., 5, 10)
$("#iEsiSelfPrintNote").html(opt[6]); // State limit disclaimer text
});

• HTML Field: <input type="hidden" name="iEsiSelfPrintLimit" id="iEsiSelfPrintLimit" value="-"> (index.html:1636).
──────

## 2. Where the Current Daily Counter is Queried & Evaluated (vSPSubmCnt)

• File: index.html:170-188 (and 010/assets/001-OlnEsiCrtSubm.jsp:L187-L197)
• Trigger: Invoked when the user clicks the Proceed button (#btn_sub) inside the jQuery validation submitHandler →
checkStateselect().
• API Request & Quota Check:
else if ($("#iCollOpt").val() === "SELF")
    {
        var vSPSubmCnt = "0";
        var milliseconds = new Date().getTime();
        $.ajax({
url: "OlnEsiGetSPCnt.jsp?iUsr=compitcom&uSttCd=" + sttList + "&lType=RG",
data: {'time': milliseconds.toString()},
async: false
}).done(function (result) {
vSPSubmCnt = $.trim(result);
});

        if (parseInt(vSPSubmCnt) >= parseInt($("#iEsiSelfPrintLimit").val()))
        {
            alert("Please Note, Only " + $("#iEsiSelfPrintLimit").val() + " Self Printing of e-Stamping Certificate is allowed for

a day.");
$("#iSttCd").focus();
            return "N"; // Blocks submission
        }
        else
        {
            $("#iCollOpt").val("SELF");
return "Y"; // Allows form to submit
}
}

──────

## 3. What Increases / Increments the Daily Counter on the Server

The server database increments vSPSubmCnt when a transaction completes the Self-Print lifecycle:

1. Transaction Creation (sOlnSubmission):
   • POST to /OnlineStamping/sOlnSubmission (index.html:1244) with iCollOpt="SELF", iEsiSPAcntCd="ch-self", generating a
   transaction reference with prefix SP (SPCH380210826131500).
2. Payment Settlement (sOlnPGRPGetStatus):
   • Razorpay webhook / gateway redirect marks the SPCH... transaction as paid
   (030-20260821-131536-www.shcilestamp.com-main-sOlnPGRPGetStatus).
3. Certificate Generation (SelfPrintServlet):
   • Servlet SelfPrintServlet?rDoAction=printEstamp (035-20260821-131537-www.shcilestamp.com-main-SelfPrintServlet) issues the
   certificate number (e.g. IN-CH63775751111282Y).
4. Print Confirmation Callback (sOlnEsiGetPrintStatus):
   • Form redirectToOnlinePaymentSystem POSTs the encrypted esiRespMessage to /OnlineStamping/sOlnEsiGetPrintStatus
   (index.html:125), setting final status 100 - Print success
   (038-20260821-131630-www.shcilestamp.com-main-sOlnEsiGetPrintStatus).
   • Result: The server records this completed Self-Print for (iUsr, uSttCd, current_date), causing future calls to
   OlnEsiGetSPCnt.jsp on that date to return vSPSubmCnt + 1.

──────

## 4. Reset & Scope Behavior (How it is Deducted / Reset / Bypassed)

• Daily Server Reset: The counter is strictly a calendar-day metric. It automatically resets to 0 at server midnight 00:00:00.
• Per-State Isolation: uSttCd is passed in the query string (OlnEsiGetSPCnt.jsp?...uSttCd=CH...). Quota counts for Chandigarh
(CH) do not consume quota for Delhi (DL) or other states.
• Delivery Mode Bypass: The check only triggers when $("#iCollOpt").val() === "SELF". If the user selects SRO (#iOptSRO), Courier
Home Delivery (#iOptCUR), Branch (#iOptBRN), or ACC (#iOptACC), the checkStateselect() branch skips OlnEsiGetSPCnt.jsp entirely.
──────

## 5. Client-Side Counter Override (in Automation Codebase)

In app.py:443-448, the automation script manipulates this exact limit field on the DOM immediately after StateSelect() completes:

    # Set the self-print limit after StateSelect() has finished, because
    # that portal callback overwrites the hidden field with its default.
    self._find_in_any_frame(
        page, "#iEsiSelfPrintLimit", state="attached", timeout=60_000
    ).evaluate("el => { el.value = '999999'; }")

Effect: Overwriting the hidden input value from the default quota (e.g. 5) to '999999' prevents the client-side JavaScript
condition parseInt(vSPSubmCnt) >= parseInt($("#iEsiSelfPrintLimit").val()) from ever triggering, allowing the browser to submit
the form without being blocked by client validation.
