# Compitcom eStamp Batch Automation

Windows Tkinter application for processing resumable CSV batches through the Jharkhand NGDRS/eGRAS eStamp workflow described in `process.md`. It uses a signed-in dedicated Chrome profile to set up Gemini, then reopens that profile headlessly to read CAPTCHA images. The portal itself opens in the browser selected in the app. There is no OCR web server or FastAPI process.

Version 1 intentionally leaves OTP and payment manual: the application pauses, the user completes the step in Chrome, and then clicks **Resume**.

At the start of a portal session, the application opens the Jharkhand portal home page and clicks its Citizen **Login** link, rather than requesting the login URL directly. When Citizen credentials are supplied, it captures the displayed `#captcha_image` directly for Gemini OCR, fills the CAPTCHA, and clicks **Get OTP**. When an SMS User ID is configured, it polls the SMS server and fills the retrieved OTP; the user then confirms Login. The app allows up to two minutes for the portal's eStamp entry link to appear before treating the login as incomplete.

## Install and run from source

Python 3.11 and Google Chrome are required.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe .\src\app.py
```

On first use, click **Browser profile status...** to open the compact setup dialog:

1. Confirm or browse to `chrome.exe`.
2. Click **Open Profile at Gemini**.
3. Sign in to the Google account that is permitted to use Gemini in that dedicated Chrome profile.
4. Click **Check Profile**.

The browser profile—not Gemini itself—is the one-time setup. When a batch starts, the visible setup browser is
closed and the same profile is reopened in headless mode for Gemini OCR. If the Google session has expired or Gemini
cannot accept input, the batch does not start and the setup dialog opens automatically.

Use **Test CAPTCHA OCR** to check the dedicated Gemini profile without touching the live portal workflow. It opens
`test-gemini-ocr.html`, places its displayed CAPTCHA image on the Windows clipboard, and uses Chrome's normal
**Ctrl+V** paste into Gemini. The page stays open with Gemini's recognized value filled in.

The **CAPTCHA copy** selector controls the live portal workflow. `direct_copy` (the default) captures the rendered
CAPTCHA element without moving the mouse and uploads those exact PNG bytes directly to Gemini. This avoids desktop
clipboard ownership and browser-focus races. The `mouse_cursor` option retains the previous right-click **Copy
image** browser-menu method.

The dedicated profile and non-secret settings are stored under `%LOCALAPPDATA%\Compitcom\eStampAutomation`. Citizen/eGRAS credentials exist only in application memory and are cleared on exit.

## Browser selection

The **Portal browser** picker automatically finds installed Google Chrome, Microsoft Edge, Brave, Opera/Opera GX, Vivaldi, and Yandex Browser. It also always lists **Firefox (managed automation)**. Choose **Browser > Download managed Firefox** once to install the Playwright Firefox build required for Firefox-based automation. The menu item disables itself after a successful download. It is stored in `%LOCALAPPDATA%\Compitcom\eStampAutomation\playwright-browsers`, not beside the executable, so it remains available when the `.exe` is moved to the Desktop or updated. For an unlisted browser or a browser installed on another drive, choose **Custom browser...** and select its `.exe`; an inline selector then appears for Chromium- or Firefox-based. Zen defaults to Firefox-based. Chromium choices run the selected installed browser, while Firefox-based choices run a fresh managed Firefox session. The custom choice is saved. Portal automation launches a separate, visible fresh session without touching saved browser profiles or downloads. Gemini runs headlessly from its dedicated signed-in Chromium profile during a batch. Closing the portal browser stops the automation safely.

## CSV batches

Click **Download CSV Format**, fill and save the downloaded file, then use **Select CSV…** to load it. The
template action never starts or selects a batch by itself. Input columns are:

```text
district,first_party_name,second_party_name,stamp_duty_paid_by,stamp_purpose,pan,mobile,amount,quantity
```

- `quantity` defaults to `1`.
- `second_party_name` defaults to `NIL` and `pan` is optional.
- `mobile` is required and is entered into the portal from each CSV row.
- Select or type the one Article to use for the current batch in the application; it is not a CSV column and is remembered across sessions.
- District must match the visible portal option text.
- Do not keep the CSV open in Excel while automation is running. If it becomes locked, the application pauses rather than losing progress.

The application adds and constantly updates:

```text
status,completed_quantity,processed_quantity,attempt_count,error_count,last_stage,last_error,updated_at,transaction_refs,transaction_details,estamp_files,skipped_quantities
```

Every quantity is tracked separately. Retry stays on the current quantity; Move to Next Quantity records that
quantity as skipped and advances once. The transaction confirmation table is the success boundary, while PDF status
is recorded separately in `transaction_details`.
The app uses the fixed CSV row number internally and does not add a user-facing row identifier. It updates the
selected CSV in place; atomic replacement may create a short-lived temporary file while saving, but no duplicate
CSV is retained.
The destination displayed in the application defaults to the current user's Windows `Downloads` folder and can be
changed with **Browse…**. The app saves validated PDFs with names such as
`eStamp_row-001_unit-001_<reference>.pdf` and records their paths in the CSV; this is explicit rather than relying
on an opaque Chrome-profile download preference.

Each CSV row is validated immediately when the CSV is selected; the progress table and a warning identify rows that
must be corrected, and Start remains disabled until the file is selected again without validation errors. District and
the batch Article are checked against the live portal's `<select>` options before form submission. An unavailable
value is reported with the field name and sample available choices. Required fields, amount, quantity, and
browser-native form validation are also checked before **Proceed to Pay**.

## Activity logs

Every application action, workflow stage, error, and stop event is appended to one local file per day under
`%LOCALAPPDATA%\Compitcom\eStampAutomation\logs`. Use **Activity > Current Session…** for the current session and
**Activity > Open Daily Log Folder** to view the persistent diagnostic files.

## Optional SMS-server OTP auto-fill

Enter the User ID used by the SMS server in **SMS OTP settings**. MacroDroid forwards SMS messages to the shared SMS server, and the application polls that server for matching `main` (NGDRS) and `egrass` OTPs. The default address is `https://sms-server.compitcom.in`; it can be changed in settings if required. An empty or incorrect User ID prevents automatic OTP retrieval, and the browser remains available for manual entry if no OTP arrives.

## Optional payment trigger

Batch setup accepts an optional payment-trigger URL and `GET`/`POST` method. After Pay Now, automation waits until
both **Scan UPI QR** and **Time left to complete the transaction** are visible, then calls the configured URL once.
POST sends an empty request body. The setting is remembered with the other non-secret preferences. Trigger timeouts,
HTTP errors, and invalid URLs are written to the activity log and never stop transaction-result polling.

## Operating modes and controls

- **Assisted errors** asks whether to retry the current quantity or record it as skipped and move to the next quantity.
- **Continuous** records ordinary errors, skips that quantity, and advances once without a prompt.
- **Pause** keeps the current page and stops before the next browser action.
- **Stop** cancels the active unit and preserves it as retryable.
- Closing the selected portal browser stops the entire run. Gemini OCR runs headlessly while a batch is active; closing the application closes both automation browsers.

Continuous mode still pauses for manual OTP and payment. Retrying a failure after payment began can create a duplicate charge; the assisted dialog displays a warning, and the CSV retains the stage/error for review.

## Build the Windows executable

```powershell
.\build_exe.ps1
```

The output is `dist\Compitcom-eStamp-Automation.exe`. Chrome is required for the dedicated Gemini profile; the portal may use any supported detected browser. For managed Firefox, users download the browser from **Browser > Download managed Firefox** after starting the executable. Playwright does not download its own Chromium build.

## Development checks

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m ruff check src tests
.\.venv\Scripts\python.exe -m mypy src tests
$env:PYTHONPATH = 'src'
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

The SMS-server API contract and MacroDroid setup are documented in `sms-reader.md`.
