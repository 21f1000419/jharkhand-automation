# Compitcom eStamp Batch Automation

Windows Tkinter application for processing multiple resumable CSV batches in parallel through the Jharkhand NGDRS/eGRAS eStamp workflow described in `process.md`. Each ID tab owns its settings, credentials, CSV, SMS configuration, browser profile, and automation process. The application uses a signed-in dedicated Chrome profile to set up Gemini, then reopens that profile headlessly to read CAPTCHA images. The portals open in the browsers selected in their tabs. There is no OCR web server or FastAPI process.

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

The dedicated Gemini profile and non-secret tab settings are stored under `%LOCALAPPDATA%\Compitcom\eStampAutomation`. Saved Citizen/eGRAS credentials use Windows Credential Manager and are isolated by ID tab.

## ID tabs and parallel runs

**ID 1** is the permanent default tab. Use **Add ID** to create more independently configured tabs and **Remove ID** to remove the selected non-default tab. Removing a tab keeps its browser profile and saved credentials. The next added tab reuses the first available ID and reconnects to the data associated with that ID. Its run settings start blank. **Copy all from ID 1** copies ID 1's current run settings and credentials while preserving the target ID and its separate browser profile.

Each tab has a different color marker and can use its own CSV, Article, browser, OCR choice, SMS settings, payment trigger, download folder, and operating mode. **Disable ID** keeps that tab and its settings but excludes it from **Start All** and grays its tab marker; **Enable ID** includes it again. Its Start, Pause, Resume, and Stop controls affect only that run. **Start All** starts every valid enabled tab and reports tabs that still need configuration; **Stop All** stops every active run. The same CSV cannot be active in two tabs at once.

The **Browsers** count selects 1 to 20 parallel browser workers for that ID. Workers share one quantity queue and one CSV state, so rows and quantities are claimed once even when transactions finish out of order. Citizen login is one-by-one per ID, but each browser starts working right after its own login without waiting for the other browsers to finish logging in. Each worker gets its own persistent browser profile. When there is more than one browser, the existing ID name is kept and B3.1, B3.2, B3.3 is appended with a separator too (for example ID 3 shows ID 3 | B3.1, ID 3 | B3.2), and the status dock shows one panel per browser instead of wrapping them into one. Stopping one browser panel stops only that browser; the remaining browsers continue working.

## Browser selection

The **Portal browser** picker automatically finds installed Google Chrome, Microsoft Edge, Brave, Opera/Opera GX, Vivaldi, and Yandex Browser. It also always lists **Firefox (managed automation)**. Use the top-level **Download managed Firefox** menu item once to install the Playwright Firefox build required for Firefox-based automation. The item changes to **Managed Firefox downloaded** after a successful download. It is stored in `%LOCALAPPDATA%\Compitcom\eStampAutomation\playwright-browsers`, not beside the executable, so it remains available when the `.exe` is moved to the Desktop or updated. For an unlisted browser or a browser installed on another drive, choose **Custom browser...** and select its `.exe`; an inline selector then appears for Chromium- or Firefox-based. Zen defaults to Firefox-based. Every browser worker uses its own persistent profile; custom-browser profiles are further separated by browser name. Gemini runs headlessly from its dedicated signed-in Chromium profile during a batch. Closing one portal browser stops that ID's worker group.

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

Enter the Citizen User ID used by the SMS server in **SMS OTP settings**. The ID applies only to the `main` NGDRS OTP. For eGRAS, the application reads the OTP reference number from the website and fetches the OTP stored under that reference, so parallel eGRAS sessions do not share or overwrite an OTP slot. The default server address is `https://sms-server.compitcom.in`; it can be changed in settings. An empty or incorrect Citizen User ID disables only automatic Citizen OTP retrieval. The browser remains available for manual entry if no OTP arrives.

## Optional payment trigger and payment queue

Each ID accepts an optional payment-trigger URL and `GET`/`POST` method. Before clicking **Pay Now**, parallel runs enter a process-wide FIFO queue. Only the run at the front proceeds to its QR code. After both **Scan UPI QR** and **Time left to complete the transaction** appear, the application brings that run's native browser window to the foreground and verifies it is active. Only then does it call the configured URL once; POST sends an empty request body.

That run retains the queue until the transaction result and download link appear. The next queued browser is then allowed to proceed and come to the foreground. Foreground activation retries until it succeeds or the run is stopped. Trigger timeouts, HTTP errors, and invalid URLs are written to the activity log and do not stop transaction-result polling.

## Operating modes and controls

- **Assisted errors** asks whether to retry the current quantity or record it as skipped and move to the next quantity.
- **Continuous** records ordinary errors, skips that quantity, and advances once without a prompt.
- **Pause** keeps the current page and stops before the next browser action.
- **Stop** cancels the active unit and preserves it as retryable.
- Closing a portal browser stops only its ID. Gemini OCR runs headlessly while batches are active; closing the application stops all runs and closes their automation browsers.

Each tab shows its current state, row/quantity progress, and payment-queue position. Actionable row failures provide **Retry** and **Move to next** choices for that tab without blocking the other runs.

Continuous mode still pauses for manual OTP and payment. Retrying a failure after payment began can create a duplicate charge; the assisted dialog displays a warning, and the CSV retains the stage/error for review.

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
