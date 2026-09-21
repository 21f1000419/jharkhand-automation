# Compitcom eStamp Batch Automation

Windows Tkinter application for processing a resumable CSV batch in parallel through the Jharkhand NGDRS/eGRAS eStamp workflow described in `process.md`. One global run configuration supplies the batch, article, browser, download, OCR, trigger, and mode settings. Each ID keeps only its credentials, SMS user ID, browser count, enabled state, and browser profile. The application uses a signed-in dedicated Chrome profile to set up Gemini, then reopens that profile headlessly to read CAPTCHA images. There is no OCR web server or FastAPI process.

Payment approval remains manual. The application captures each UPI QR code in its built-in carousel, continues polling the transaction, and removes the QR after five minutes or when the matching stamp is ready to download.

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

The dedicated Gemini profile and non-secret settings are stored under `%LOCALAPPDATA%\Compitcom\eStampAutomation`. Saved Citizen/eGRAS credentials use Windows Credential Manager and are isolated by ID.

## Shared workspace and IDs

The compact controls at the top apply to every ID. The application locks these fields while automation is active so all workers continue using one consistent batch configuration. Existing configurations are migrated automatically: the former ID 1 values become the global settings, while every ID keeps its own credentials, SMS user ID, browser count, enabled state, and profile.

The center is split between the shared batch table and a payment QR carousel. The bottom ID strip shows each numbered Citizen ID, its state, and its current stage. Clicking any ID opens one settings dialog with a native tab for every ID. Each colored tab uses the same `number. Citizen ID` label. Password fields remain visible while editing. Active IDs are read-only until stopped. Use **+ Add ID** to create and select another tab, and remove a non-default ID from its tab.

**Start All** starts every valid enabled ID. **Start ID** lists each idle ID with its Citizen username, and **Stop All** stops all active IDs. An ID started later joins the live shared batch and claims the next available quantity. Every worker across every ID shares one claim queue and CSV state, so a row and quantity can only be assigned once even when transactions finish out of order.

The **Browsers** count in each ID's settings selects 1 to 20 parallel workers for that ID. Citizen login is serialized per ID, but each browser starts working as soon as its login is ready. Each worker gets its own persistent browser profile. When an ID has multiple browsers, the status dock displays one panel per worker. Stopping a panel stops only that worker; the other workers continue.

## Browser selection

The **Portal browser** picker automatically finds installed Google Chrome, Microsoft Edge, Brave, Opera/Opera GX, Vivaldi, and Yandex Browser. It also always lists **Firefox (managed automation)**. Use the top-level **Download managed Firefox** menu item once to install the Playwright Firefox build required for Firefox-based automation. The item changes to **Managed Firefox downloaded** after a successful download. It is stored in `%LOCALAPPDATA%\Compitcom\eStampAutomation\playwright-browsers`, not beside the executable, so it remains available when the `.exe` is moved to the Desktop or updated. For an unlisted browser or a browser installed on another drive, choose **Custom browser...** and select its `.exe`; an inline selector then appears for Chromium- or Firefox-based. Zen defaults to Firefox-based. Every browser worker uses its own persistent profile; custom-browser profiles are further separated by browser name. Gemini runs headlessly from its dedicated signed-in Chromium profile during a batch. Closing one portal browser stops that ID's worker group.

## CSV batches

Click **Download CSV Format**, fill and save the downloaded file, then use the global **Select...** button to load it. The
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
status,completed_quantity,processed_quantity,completed_units,processed_units,attempt_count,error_count,last_stage,last_error,updated_at,transaction_refs,transaction_details,estamp_files,skipped_quantities
```

Every quantity is tracked separately. Retry stays on the current quantity; Move to Next Quantity records that
quantity as skipped and advances once. The transaction confirmation table is the success boundary, while PDF status
is recorded separately in `transaction_details`.
The app uses the fixed CSV row number internally and does not add a user-facing row identifier. It updates the
selected CSV in place; atomic replacement may create a short-lived temporary file while saving, but no duplicate
CSV is retained.
The destination displayed in the application defaults to the current user's Windows `Downloads` folder and can be
changed with **Browse…**. The app saves validated PDFs with names such as
`eStamp_<reference>.pdf` and records their paths in the CSV; this is explicit rather than relying
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

Enter the Citizen User ID used by the SMS server in **SMS OTP settings**. The ID applies only to the `main` NGDRS OTP. For eGRAS, the application reads the OTP reference number from the website and fetches the OTP stored under that reference, so parallel eGRAS sessions do not share or overwrite an OTP slot. The default server address is `https://sms-server.compitcom.in`; it can be changed in settings. An empty or incorrect Citizen User ID disables only automatic Citizen OTP retrieval. The browser remains available for manual entry if no OTP arrives.

## Payment QR carousel and optional trigger

The payment-trigger URL and `GET`/`POST` method are global. Payment workers proceed independently without a FIFO payment queue. When the SBIePay page displays the UPI payment block, the application extracts the visible base64 QR image to a temporary local PNG and appends it to the in-app carousel. The portal browser is not brought to the foreground.

The newest QR is selected automatically. Use **Previous** and **Next** to move between active QR codes; the panel shows the ID, CSV row, quantity, and remaining lifetime. A QR is removed after five minutes or as soon as the matching download is ready. Temporary QR files are also cleared when the application starts and closes. If configured, the payment trigger is called once after the QR has been captured successfully; POST sends an empty request body. Trigger timeouts, HTTP errors, and invalid URLs are written to the activity log and do not stop transaction-result polling.

## Operating modes and controls

- **Assisted errors** asks whether to retry the current quantity or record it as skipped and move to the next quantity.
- **Continuous** records ordinary errors, skips that quantity, and advances once without a prompt.
- **Stop All** cancels active units and preserves them as retryable.
- Closing a portal browser stops that worker. Gemini OCR runs headlessly while batches are active; closing the application stops all runs and closes their automation browsers.

The shared table shows row and quantity progress plus the ID currently assigned to each item. The ID strip and status dock show running, stopped, and error states. Actionable failures provide **Retry** and **Move to next** choices without blocking other workers.

Continuous mode still waits for manual OTP and payment when automation cannot complete them. Retrying a failure after payment began can create a duplicate charge; the assisted dialog displays a warning, and the CSV retains the stage/error for review.

If the page reports that the PNB gateway is temporarily suspended after SBIePay selection, the application stops waiting for UPI controls and records a retryable gateway error. Assisted mode offers Retry, Continue, or Move to next. Continuous mode records the failure and advances according to its normal error policy.

## Build the Windows package

```powershell
.\build_exe.ps1
```

The output is the complete `dist\jharni` folder. Keep the folder intact when moving it to another Windows system and start `Compitcom-eStamp-Automation.exe` inside it. The package includes the PaddleOCR model, PaddlePaddle, PaddleX, EasyOCR, Torch, OpenCV, and the Playwright runtime used by the application. Chrome is still required for the dedicated Gemini profile; the portal may use any supported detected browser. For managed Firefox, users use the top-level **Download managed Firefox** menu item after starting the executable. Playwright does not download its own Chromium build.

### Build a downloadable package with GitHub Actions

The `Build Windows package` workflow runs on `windows-latest`. It installs the pinned dependencies, builds the onedir package with `estamp_automation.spec`, checks the bundled OCR and browser files, runs a frozen-runtime smoke test, and creates `jharni.zip`. The ZIP extracts to a top-level `jharni` folder. It also publishes `Compitcom-eStamp-Automation.exe` as a separate release asset for updating an existing installation.

It runs automatically when an existing GitHub release is published. The ZIP, EXE, and SHA-256 checksum are attached to that release. You can also run it from **Actions > Build Windows package > Run workflow** and enter an existing release tag to attach the new files there. The workflow does not retain Actions artifacts, so a manual run without a release tag only builds and validates the package.

For an existing installation, replace only the EXE when the update changes Python application logic or the UI and does not change dependencies, native DLLs, the PyInstaller spec, or bundled assets. Download the complete ZIP whenever any of those packaged files change.

## Development checks

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m ruff check src tests
.\.venv\Scripts\python.exe -m mypy src tests
$env:PYTHONPATH = 'src'
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

The SMS-server API contract and MacroDroid setup are documented in `sms-reader.md`.
