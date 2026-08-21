import csv
import os
import queue
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from zoneinfo import ZoneInfo

try:
    from .browser_detection import detect_supported_browsers
    from .config import ConfigStore
    from .credential_store import WindowsCredentialStore
    from .models import BrowserEngine, Credentials
except ImportError:
    from browser_detection import detect_supported_browsers
    from config import ConfigStore
    from credential_store import WindowsCredentialStore
    from models import BrowserEngine, Credentials

try:
    from playwright.sync_api import TimeoutError as PWTimeoutError
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None
    PWTimeoutError = Exception


BASE_URL = "https://www.shcilestamp.com/OnlineStamping/OlnEsi"
BROWSER_TIME_ZONE = "Asia/Kolkata"
BROWSER_TIME_HOUR = 14

STATES = [
    ("Select State", "0"),
    ("ANDAMAN AND NICOBAR", "AN"),
    ("ARUNACHAL PRADESH", "AR"),
    ("ASSAM", "AS"),
    ("CHANDIGARH", "CH"),
    ("CHATTISGARH", "CG"),
    ("DELHI", "DL"),
    ("GUJARAT", "GJ"),
    ("HIMACHAL PRADESH", "HP"),
    ("JAMMU AND KASHMIR", "JK"),
    ("KARNATAKA", "KA"),
    ("MANIPUR", "MN"),
    ("MEGHALAYA", "ML"),
    ("ODISHA", "OD"),
    ("PONDICHERRY", "PY"),
    ("PUNJAB", "PB"),
    ("TRIPURA", "TR"),
    ("UNION TERRITORY OF LADAKH", "LA"),
    ("UTTAR PRADESH", "UP"),
    ("UTTARAKHAND", "UK"),
]

ARTICLES = [
    ("5 - Agreement or Memorandum of an agreement", "CH-RG-5"),
    ("4 - Affidavit", "CH-RG-4"),
    ("48 - Power of Attorney", "CH-RG-47"),
    ("34 - Indemnity Bond", "CH-RG-33"),
    ("40 - Mortgage Deed", "CH-RG-39"),
]

CSV_COLUMNS = [
    "Description",
    "ConsiderationPrice",
    "Party1Name",
    "Party2Name",
    "StampPaidBy",
    "StampAmount",
]

SAMPLE_ROW = [
    "SALE OF FLAT AT SAMPLE ADDRESS",
    "1500000",
    "RAM KUMAR SHARMA",
    "SITA DEVI",
    "RAM KUMAR SHARMA",
    "500",
]


# ----------------------------------------------------------------------------
# CSV helpers
# ----------------------------------------------------------------------------


def read_csv_rows(path):
    """Read and validate CSV rows. Returns (rows, errors)."""
    rows = []
    errors = []
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        missing_cols = [c for c in CSV_COLUMNS if c not in (reader.fieldnames or [])]
        if missing_cols:
            errors.append("CSV is missing required column(s): " + ", ".join(missing_cols))
            return rows, errors

        for i, row in enumerate(reader, start=2):  # row 1 is header
            desc = (row.get("Description") or "").strip()
            price = (row.get("ConsiderationPrice") or "0").strip() or "0"
            p1 = (row.get("Party1Name") or "").strip()
            p2 = (row.get("Party2Name") or "").strip()
            paid_by = (row.get("StampPaidBy") or "").strip()
            amt = (row.get("StampAmount") or "0").strip() or "0"

            if not desc or not p1 or not p2 or not paid_by or not amt:
                errors.append(f"Row {i}: one or more required fields are empty.")
                continue

            if paid_by.upper() not in (p1.upper(), p2.upper()):
                errors.append(
                    f"Row {i}: 'StampPaidBy' ({paid_by!r}) must exactly match "
                    f"Party1Name ({p1!r}) or Party2Name ({p2!r})."
                )
                continue

            rows.append(
                {
                    "description": desc,
                    "consideration_price": price,
                    "party1": p1,
                    "party2": p2,
                    "paid_by": paid_by,
                    "stamp_amount": amt,
                }
            )
    return rows, errors


def write_csv_template(path):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(CSV_COLUMNS)
        writer.writerow(SAMPLE_ROW)


def parse_browser_time(time_str, timezone_name=BROWSER_TIME_ZONE):
    """Parse time_str into a localized datetime in timezone_name.

    Returns None if time_str is empty/whitespace.
    Raises ValueError if the string cannot be parsed into a valid time/datetime.
    """
    text = (time_str or "").strip()
    if not text:
        return None

    tz = ZoneInfo(timezone_name)
    now_in_tz = datetime.now(tz)
    normalized = " ".join(text.split()).upper()

    time_formats = [
        "%H:%M",
        "%H:%M:%S",
        "%I:%M %p",
        "%I:%M:%S %p",
        "%I %p",
        "%I:%M%p",
        "%I%p",
    ]

    datetime_formats = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d %I:%M:%S %p",
        "%Y-%m-%d %I:%M %p",
        "%Y-%m-%d %I:%M%p",
        "%d-%m-%Y %H:%M:%S",
        "%d-%m-%Y %H:%M",
        "%d-%m-%Y %I:%M:%S %p",
        "%d-%m-%Y %I:%M %p",
        "%d-%m-%Y %I:%M%p",
        "%d/%m/%Y %H:%M:%S",
        "%d/%m/%Y %H:%M",
        "%d/%m/%Y %I:%M:%S %p",
        "%d/%m/%Y %I:%M %p",
        "%d/%m/%Y %I:%M%p",
    ]

    for fmt in datetime_formats:
        try:
            dt = datetime.strptime(normalized, fmt)
            return dt.replace(tzinfo=tz)
        except ValueError:
            pass

    for fmt in time_formats:
        try:
            t = datetime.strptime(normalized, fmt).time()
            return now_in_tz.replace(
                hour=t.hour,
                minute=t.minute,
                second=t.second,
                microsecond=0,
            )
        except ValueError:
            pass

    raise ValueError(
        f"Invalid time format '{time_str}'.\n"
        "Please enter time like '14:00', '02:00 PM', or leave blank for system time."
    )


def browser_time_override(target):
    """Return the given target datetime and a script that exposes it to page JavaScript."""
    target_ms = int(target.timestamp() * 1000)
    script = f"""
(() => {{
  const NativeDate = globalThis.Date;
  const fixedNow = {target_ms};

  const fixedDate = () => new NativeDate(fixedNow);
  const BrowserDate = new Proxy(NativeDate, {{
    apply() {{
      return fixedDate().toString();
    }},
    construct(target, args, newTarget) {{
      return Reflect.construct(
        target,
        args.length === 0 ? [fixedNow] : args,
        newTarget
      );
    }}
  }});

  BrowserDate.now = () => fixedNow;
  globalThis.Date = BrowserDate;

  // Intl's no-argument formatting methods can read the engine clock directly,
  // so make their implicit "now" use the same fixed timestamp as Date.now().
  const formatDescriptor = Object.getOwnPropertyDescriptor(
    Intl.DateTimeFormat.prototype,
    "format"
  );
  const nativeFormatGetter = formatDescriptor.get;
  Object.defineProperty(Intl.DateTimeFormat.prototype, "format", {{
    configurable: formatDescriptor.configurable,
    get() {{
      const nativeFormat = nativeFormatGetter.call(this);
      return (...args) => nativeFormat(...(args.length ? args : [fixedDate()]));
    }}
  }});

  const nativeFormatToParts = Intl.DateTimeFormat.prototype.formatToParts;
  Intl.DateTimeFormat.prototype.formatToParts = function (...args) {{
    return nativeFormatToParts.call(this, ...(args.length ? args : [fixedDate()]));
  }};
}})();
"""
    return target, script


# ----------------------------------------------------------------------------
# Playwright automation
# ----------------------------------------------------------------------------


class AutomationCancelled(Exception):
    pass


class EStampAutomation:
    """Drives the browser through the e-stamping workflow."""

    def __init__(
        self,
        user_id,
        password,
        state_code,
        article_value,
        rows,
        browser_executable,
        browser_time,
        log_fn,
        status_fn,
        stop_event,
    ):
        self.user_id = user_id
        self.password = password
        self.state_code = state_code
        self.article_value = article_value
        self.rows = rows
        self.browser_executable = Path(browser_executable)
        self.browser_time = browser_time
        self.log_fn = log_fn
        self.status_fn = status_fn
        self.current_record = None
        self.log = self._log
        self.stop_event = stop_event

    def _log(self, message):
        self.log_fn(message, self.current_record)

    def _check_stop(self):
        if self.stop_event.is_set():
            raise AutomationCancelled()

    def run(self):
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=False,
                executable_path=str(self.browser_executable),
            )
            context = browser.new_context(timezone_id=BROWSER_TIME_ZONE)
            if self.browser_time is not None:
                _, browser_time_script = browser_time_override(self.browser_time)
                context.add_init_script(script=browser_time_script)
                self._log(
                    f"Browser time override enabled: "
                    f"{self.browser_time.strftime('%Y-%m-%d %I:%M %p')} IST"
                )
            else:
                self._log("Browser time override: Disabled (using system time)")
            page = context.new_page()

            # Auto-accept every alert/confirm/prompt dialog the site raises,
            # for the entire lifetime of the page. No manual clicking needed.
            page.on(
                "dialog",
                lambda dialog: (self._log(f"[dialog] auto-accepting: {dialog.message}"), dialog.accept()),
            )

            self._log("Opening SHCIL e-Stamping portal...")
            page.goto(BASE_URL, wait_until="domcontentloaded")

            pay_stamp_open = self._login(page)

            total = len(self.rows)
            for idx, row in enumerate(self.rows, start=1):
                self._check_stop()
                self.current_record = idx
                self.status_fn(idx, "running", "Processing record...")
                self._log(f"--- Processing record {idx}/{total}: {row['description'][:40]}...")
                self._process_record(page, row, pay_stamp_open=pay_stamp_open)
                pay_stamp_open = False
                self.status_fn(idx, "completed", "Certificate printed.")
                self._log(f"--- Record {idx}/{total} complete.")

            self.current_record = None
            self._log("All records processed. Leaving browser window open for review.")
            # Intentionally do not auto-close the browser/context so the user
            # can inspect the final state / last certificate.

    # -- steps -------------------------------------------------------------

    def _login(self, page):
        self.log("Waiting for SHCIL login form...")
        login_scope = self._portal_scope(page, "#sUID", timeout=120_000)

        self.log("Entering User ID...")
        login_scope.locator("#sUID").fill(self.user_id)
        login_scope.locator("#bLogin").click()

        self.log("Waiting for password field...")
        login_scope = self._portal_scope(page, "#sPass", timeout=120_000)
        login_scope.locator("#sPass").fill(self.password)

        self.log("Password entered. If a CAPTCHA challenge appears, please solve it manually.")
        self.log("Polling continuously for 'Pay Stamp Duty'...")
        self._wait_for_pay_stamp_duty_link(page, timeout=180_000)
        self._check_stop()
        self.log("'Pay Stamp Duty' is available. Clicking it now...")
        self._click_pay_stamp_duty(page)
        self.log("Login successful. Pay Stamp Duty form opened.")
        return True

    def _portal_scope(self, page, selector, state="visible", timeout=120_000):
        """Use the top page first, with a lazy iframe fallback for older portal layouts."""
        top_locator = page.locator(selector)
        try:
            top_locator.wait_for(state=state, timeout=min(timeout, 3_000))
            return page
        except PWTimeoutError:
            frame_locator = page.frame_locator("iframe[name='loginFrame']")
            frame_locator.locator(selector).wait_for(state=state, timeout=timeout)
            return frame_locator

    def _product_scope(self, page):
        """Return the nested product frame that contains the post-login portal."""
        return page.frame_locator("iframe[name='loginFrame']").frame_locator("iframe[name='prodPage']")

    def _action_scope(self, page):
        """Return the action frame containing the eStamp form controls."""
        return self._product_scope(page).frame_locator("iframe[name='actionFrame']")

    def _wait_for_pay_stamp_duty_link(self, page, timeout=120_000):
        self._product_scope(page).locator("a.button_ecf:has-text('Pay Stamp Duty')").wait_for(
            state="visible", timeout=timeout
        )

    def _click_pay_stamp_duty(self, page):
        button = self._product_scope(page).locator("a.button_ecf:has-text('Pay Stamp Duty')")
        button.wait_for(state="visible", timeout=30_000)
        button.scroll_into_view_if_needed()
        button.click()

    def _process_record(self, page, row, pay_stamp_open=False):
        # Step 4: click "Pay Stamp Duty" unless the post-login poll already opened it.
        if not pay_stamp_open:
            self._check_stop()
            self.log("Polling for 'Pay Stamp Duty' and clicking it...")
        self._wait_for_pay_stamp_duty_link(page)
        self._click_pay_stamp_duty(page)
        portal_scope = self._action_scope(page)

        # Step 5: select state. The portal refreshes its hidden options here.
        self.log(f"Selecting state {self.state_code}...")
        portal_scope.locator("#iSttCd").wait_for(state="visible", timeout=60_000)
        portal_scope.locator("#iSttCd").select_option(value=self.state_code)

        # Set the self-print limit after StateSelect() has finished, because
        # that portal callback overwrites the hidden field with its default.
        portal_scope.locator("#iEsiSelfPrintLimit").wait_for(state="attached", timeout=60_000)
        portal_scope.locator("#iEsiSelfPrintLimit").evaluate("el => { el.value = '999999'; }")

        # Step 6: select article type
        self.log(f"Selecting article {self.article_value}...")
        portal_scope.locator(f"input.cArt[value='{self.article_value}']").click()

        # Step 7: proceed
        self.log("Clicking Proceed...")
        portal_scope.locator("#btn_sub[value='Proceed']").click()

        # Step 9: fill the record's fields
        self._check_stop()
        portal_scope.locator("#iPropDesc").wait_for(state="visible", timeout=60_000)
        self.log("Filling description, price, and party details...")
        portal_scope.locator("#iPropDesc").fill(row["description"])
        portal_scope.locator("#iConPrice").fill(row["consideration_price"])
        portal_scope.locator("#iParty1Nm").fill(row["party1"])
        portal_scope.locator("#iParty2Nm").fill(row["party2"])
        portal_scope.locator("#iStampPdBy").fill(row["paid_by"])
        portal_scope.locator("#iStampAmt").fill(row["stamp_amount"])
        # Press Tab to trigger the onblur validation - any resulting alert()
        # is auto-accepted by the global dialog handler registered above.
        portal_scope.locator("#iStampAmt").press("Tab")
        page.wait_for_timeout(500)

        # Select RAZORPAY as payment mode
        self.log("Selecting RAZORPAY payment mode...")
        portal_scope.locator("#iPmtMode").wait_for(state="visible", timeout=60_000)
        portal_scope.locator("#iPmtMode").select_option(value="RAZORPAY")

        # Press Save
        self._check_stop()
        self.log("Clicking Save...")
        portal_scope.locator("input#btn_sub[value='Save']").click()
        page.wait_for_timeout(500)

        # Press Confirm
        self.log("Clicking Confirm...")
        portal_scope.locator("#btnConfirm").wait_for(state="visible", timeout=60_000)
        portal_scope.locator("#btnConfirm").click()
        page.wait_for_timeout(500)

        # Accept terms & conditions checkbox
        self.log("Checking 'I accept Terms and Conditions'...")
        portal_scope.locator("#iChk").wait_for(state="visible", timeout=60_000)
        portal_scope.locator("#iChk").check()

        # At this point the actual Razorpay payment (bank login / UPI / OTP)
        # normally has to happen. The script does not attempt this - it just
        # waits for the certificate button, giving the user time to complete
        # payment manually in this same browser window.
        self.log(
            "Waiting for payment to complete and 'Print eStamp Certificate' "
            "button to appear. Complete the Razorpay payment manually if prompted..."
        )
        portal_scope.locator("#btnPrintCert").wait_for(state="visible", timeout=900_000)

        self.log("Clicking 'Print eStamp Certificate'...")
        portal_scope.locator("#btnPrintCert").click()
        page.wait_for_timeout(500)

        # Wait for the final Print button and click it
        self.log("Waiting for 'Print' button...")
        page.locator("#printBtn").wait_for(state="visible", timeout=180_000)
        page.locator("#printBtn").click()

        self.log("Certificate printed. Waiting to return to 'Pay Stamp Duty' for the next record...")
        self._wait_for_pay_stamp_duty_link(page, timeout=180_000)


# ----------------------------------------------------------------------------
# Tkinter GUI
# ----------------------------------------------------------------------------


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("SHCIL e-Stamping Automation")
        self.geometry("1100x750")
        self.resizable(True, True)

        self.config_store = ConfigStore()
        self.config = self.config_store.load()
        self.credential_store = WindowsCredentialStore()
        try:
            saved_credentials = self.credential_store.load()
        except (OSError, RuntimeError, ValueError):
            saved_credentials = None

        self.csv_path = tk.StringVar(value=self.config.last_csv_path)
        self.state_var = tk.StringVar(value=self.config.last_state or STATES[0][1])
        self.article_var = tk.StringVar(value=self.config.last_article or ARTICLES[0][1])
        self.time_var = tk.StringVar(value=self.config.last_time)
        self.uid_var = tk.StringVar(value=saved_credentials.citizen_username if saved_credentials else "")
        self.pwd_var = tk.StringVar(value=saved_credentials.citizen_password if saved_credentials else "")
        self.credentials_saved = saved_credentials is not None

        self.log_queue = queue.Queue()
        self.stop_event = threading.Event()
        self.worker_thread = None
        self.status_var = tk.StringVar(value="Ready")
        self.browser_path = self._find_browser()

        self._build_ui()
        self.after(150, self._drain_log_queue)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # -- UI construction -----------------------------------------------

    def _build_ui(self):
        pad = {"padx": 8, "pady": 4}

        creds = ttk.LabelFrame(self, text="Login")
        creds.pack(fill="x", **pad)

        ttk.Label(creds, text="User ID:").grid(row=0, column=0, sticky="w", **pad)
        ttk.Entry(creds, textvariable=self.uid_var, width=40).grid(row=0, column=1, sticky="w", **pad)

        ttk.Label(creds, text="Password:").grid(row=1, column=0, sticky="w", **pad)
        ttk.Entry(creds, textvariable=self.pwd_var, show="*", width=40).grid(
            row=1, column=1, sticky="w", **pad
        )
        credential_controls = ttk.Frame(creds)
        credential_controls.grid(row=2, column=1, sticky="w", **pad)
        ttk.Button(credential_controls, text="Save login securely", command=self._save_credentials).pack(
            side="left"
        )
        self.clear_credentials_btn = ttk.Button(
            credential_controls, text="Clear saved login", command=self._clear_credentials
        )
        if self.credentials_saved:
            self.clear_credentials_btn.pack(side="left", padx=(8, 0))
        self.credentials_status_var = tk.StringVar(
            value="Saved securely in Windows Credential Manager." if self.credentials_saved else ""
        )
        ttk.Label(creds, textvariable=self.credentials_status_var, foreground="#555555").grid(
            row=3, column=1, sticky="w", **pad
        )

        opts = ttk.LabelFrame(self, text="Stamp Settings (applied to every record)")
        opts.pack(fill="x", **pad)

        ttk.Label(opts, text="State:").grid(row=0, column=0, sticky="w", **pad)
        state_combo = ttk.Combobox(
            opts,
            state="readonly",
            width=37,
            values=[f"{label} ({code})" for label, code in STATES if code != "0"],
        )
        state_combo.grid(row=0, column=1, sticky="w", **pad)
        state_codes = [code for _label, code in STATES if code != "0"]
        selected_state = self.state_var.get() if self.state_var.get() in state_codes else state_codes[0]
        state_combo.current(state_codes.index(selected_state))
        self.state_var.set(selected_state)
        state_combo.bind(
            "<<ComboboxSelected>>",
            lambda e: self._state_selected(state_combo, state_codes),
        )

        ttk.Label(opts, text="Article Type:").grid(row=1, column=0, sticky="nw", **pad)
        art_frame = ttk.Frame(opts)
        art_frame.grid(row=1, column=1, sticky="w", **pad)
        article_values = [value for _label, value in ARTICLES]
        if self.article_var.get() not in article_values:
            self.article_var.set(article_values[0])
        for i, (label, value) in enumerate(ARTICLES):
            ttk.Radiobutton(art_frame, text=label, variable=self.article_var, value=value).grid(
                row=i, column=0, sticky="w"
            )

        ttk.Label(opts, text="Browser Time (IST):").grid(row=2, column=0, sticky="w", **pad)
        time_frame = ttk.Frame(opts)
        time_frame.grid(row=2, column=1, sticky="w", **pad)
        ttk.Entry(time_frame, textvariable=self.time_var, width=18).pack(side="left")
        ttk.Button(time_frame, text="Set 2:00 PM", command=lambda: self.time_var.set("14:00")).pack(
            side="left", padx=(6, 0)
        )
        ttk.Button(time_frame, text="Clear (System Time)", command=lambda: self.time_var.set("")).pack(
            side="left", padx=(6, 0)
        )
        ttk.Label(
            opts,
            text="Leave blank to use actual system time, or set custom time (e.g. 14:00, 02:00 PM, 2026-08-21 14:00).",
            foreground="#555555",
        ).grid(row=3, column=1, sticky="w", **pad)

        csvf = ttk.LabelFrame(self, text="Records CSV")
        csvf.pack(fill="x", **pad)

        ttk.Entry(csvf, textvariable=self.csv_path, width=55).grid(row=0, column=0, sticky="w", **pad)
        ttk.Button(csvf, text="Browse...", command=self._browse_csv).grid(row=0, column=1, **pad)
        ttk.Button(csvf, text="Download CSV Template", command=self._download_template).grid(
            row=0, column=2, **pad
        )

        controls = ttk.Frame(self)
        controls.pack(fill="x", **pad)
        self.start_btn = ttk.Button(controls, text="Start Automation", command=self._start)
        self.start_btn.pack(side="left", **pad)
        self.stop_btn = ttk.Button(controls, text="Stop", command=self._stop, state="disabled")
        self.stop_btn.pack(side="left", **pad)
        ttk.Label(controls, textvariable=self.status_var).pack(side="left", padx=12)

        records = ttk.LabelFrame(self, text="Records")
        records.pack(fill="both", expand=True, **pad)
        records.rowconfigure(0, weight=1)
        records.columnconfigure(0, weight=1)
        columns = (
            "row",
            "description",
            "consideration",
            "party1",
            "party2",
            "paid_by",
            "amount",
            "status",
            "details",
        )
        self.records_table = ttk.Treeview(records, columns=columns, show="headings")
        self.records_table.tag_configure("running", background="#dbeafe")
        self.records_table.tag_configure("completed", background="#dcfce7")
        self.records_table.tag_configure("error", background="#fee2e2")
        headings = {
            "row": "CSV row",
            "description": "Description",
            "consideration": "Consideration",
            "party1": "Party 1",
            "party2": "Party 2",
            "paid_by": "Paid by",
            "amount": "Stamp amount",
            "status": "Status",
            "details": "Latest detail",
        }
        widths = {
            "row": 65,
            "description": 220,
            "consideration": 110,
            "party1": 150,
            "party2": 150,
            "paid_by": 130,
            "amount": 100,
            "status": 100,
            "details": 260,
        }
        for column in columns:
            self.records_table.heading(column, text=headings[column])
            self.records_table.column(
                column,
                width=widths[column],
                stretch=column in {"description", "party1", "party2", "details"},
            )
        records_scrollbar = ttk.Scrollbar(records, orient="vertical", command=self.records_table.yview)
        self.records_table.configure(yscrollcommand=records_scrollbar.set)
        self.records_table.grid(row=0, column=0, sticky="nsew")
        records_scrollbar.grid(row=0, column=1, sticky="ns")

    # -- actions ---------------------------------------------------------

    def _find_browser(self):
        saved_paths = (
            self.config.last_browser_path,
            self.config.chrome_executable,
        )
        for saved_path in saved_paths:
            candidate = Path(saved_path)
            if candidate.is_file():
                return candidate
        for browser in detect_supported_browsers():
            if browser.engine == BrowserEngine.CHROMIUM and browser.executable.is_file():
                return browser.executable
        return None

    def _state_selected(self, state_combo, state_codes):
        self.state_var.set(state_codes[state_combo.current()])
        self._save_settings()

    def _save_settings(self):
        self.config.last_state = self.state_var.get()
        self.config.last_article = self.article_var.get()
        self.config.last_csv_path = self.csv_path.get().strip()
        self.config.last_time = self.time_var.get().strip()
        if self.browser_path is not None:
            self.config.last_browser_path = str(self.browser_path)
        self.config_store.save(self.config)

    def _save_credentials(self):
        user_id = self.uid_var.get().strip()
        password = self.pwd_var.get()
        if not user_id or not password:
            messagebox.showwarning("Missing login", "Enter both User ID and Password first.")
            return False
        try:
            self.credential_store.save(Credentials(citizen_username=user_id, citizen_password=password))
        except (OSError, RuntimeError, ValueError) as error:
            messagebox.showerror("Save login", str(error))
            return False
        self.credentials_saved = True
        self.credentials_status_var.set("Saved securely in Windows Credential Manager.")
        if not self.clear_credentials_btn.winfo_manager():
            self.clear_credentials_btn.pack(side="left", padx=(8, 0))
        return True

    def _clear_credentials(self):
        if not messagebox.askyesno(
            "Clear saved login",
            "Remove the saved Chandigarh User ID and Password from Windows Credential Manager?",
        ):
            return
        try:
            self.credential_store.clear()
        except (OSError, RuntimeError) as error:
            messagebox.showerror("Clear saved login", str(error))
            return
        self.credentials_saved = False
        self.uid_var.set("")
        self.pwd_var.set("")
        self.credentials_status_var.set("Saved login cleared.")
        self.clear_credentials_btn.pack_forget()

    def _browse_csv(self):
        path = filedialog.askopenfilename(
            title="Select records CSV", filetypes=[("CSV files", "*.csv"), ("All files", "*.*")]
        )
        if path:
            self.csv_path.set(path)
            rows, errors = read_csv_rows(path)
            if errors:
                messagebox.showerror(
                    "CSV validation failed",
                    "Please fix the following issues in your CSV and try again:\n\n"
                    + "\n".join(errors[:20])
                    + ("\n\n(more errors not shown)" if len(errors) > 20 else ""),
                )
            else:
                self._render_records(rows)
            self._save_settings()

    def _download_template(self):
        path = filedialog.asksaveasfilename(
            title="Save CSV template as",
            defaultextension=".csv",
            initialfile="estamp_records_template.csv",
            filetypes=[("CSV files", "*.csv")],
        )
        if not path:
            return
        try:
            write_csv_template(path)
            messagebox.showinfo("Template saved", f"CSV template saved to:\n{path}")
        except OSError as e:
            messagebox.showerror("Error", f"Could not save template:\n{e}")

    def _log(self, message, record_index=None):
        self.log_queue.put(("log", record_index, message))

    def _record_status(self, record_index, status, detail):
        self.log_queue.put(("status", record_index, status, detail))

    def _render_records(self, rows):
        for item in self.records_table.get_children():
            self.records_table.delete(item)
        for index, row in enumerate(rows, start=1):
            self.records_table.insert(
                "",
                "end",
                iid=str(index),
                values=(
                    index,
                    row["description"],
                    row["consideration_price"],
                    row["party1"],
                    row["party2"],
                    row["paid_by"],
                    row["stamp_amount"],
                    "Pending",
                    "",
                ),
            )

    def _update_record_row(self, record_index, status, detail):
        item_id = str(record_index)
        if not self.records_table.exists(item_id):
            return
        values = list(self.records_table.item(item_id, "values"))
        values[7] = status.title()
        values[8] = detail
        self.records_table.item(item_id, values=values, tags=(status,))

    def _drain_log_queue(self):
        try:
            while True:
                event = self.log_queue.get_nowait()
                if event[0] == "status":
                    _, record_index, status, detail = event
                    self._update_record_row(record_index, status, detail)
                    self.status_var.set(f"Record {record_index}: {detail}")
                else:
                    _, record_index, message = event
                    if record_index is not None:
                        item_id = str(record_index)
                        if self.records_table.exists(item_id):
                            values = list(self.records_table.item(item_id, "values"))
                            values[8] = message
                            self.records_table.item(item_id, values=values)
                    self.status_var.set(message)
        except queue.Empty:
            pass
        self.after(150, self._drain_log_queue)

    def _start(self):
        if sync_playwright is None:
            messagebox.showerror(
                "Playwright not installed",
                "Playwright is not installed in this environment.\n\nRun:\n    pip install playwright",
            )
            return

        uid = self.uid_var.get().strip()
        pwd = self.pwd_var.get()
        csv_path = self.csv_path.get().strip()
        time_str = self.time_var.get().strip()

        if not uid or not pwd:
            messagebox.showerror("Missing info", "Please enter User ID and Password.")
            return
        if self.state_var.get() == "0":
            messagebox.showerror("Missing info", "Please select a State.")
            return
        if not csv_path or not os.path.isfile(csv_path):
            messagebox.showerror("Missing CSV", "Please select a valid records CSV file.")
            return

        try:
            browser_time = parse_browser_time(time_str)
        except ValueError as err:
            messagebox.showerror("Invalid Time", str(err))
            return

        rows, errors = read_csv_rows(csv_path)
        if errors:
            messagebox.showerror(
                "CSV validation failed",
                "Please fix the following issues in your CSV and try again:\n\n"
                + "\n".join(errors[:20])
                + ("\n\n(more errors not shown)" if len(errors) > 20 else ""),
            )
            return
        if not rows:
            messagebox.showerror("Empty CSV", "No valid records found in the CSV.")
            return

        self.browser_path = self._find_browser()
        if self.browser_path is None:
            messagebox.showerror(
                "Browser not found",
                "No installed Chromium-based browser was found. Install Google Chrome, "
                "Microsoft Edge, Brave, Opera, Vivaldi, or another supported browser, "
                "then try again.",
            )
            return

        if not messagebox.askyesno(
            "Confirm",
            f"About to process {len(rows)} record(s). This will submit real forms and "
            "may involve real payments on the SHCIL portal. Continue?",
        ):
            return

        if not self._save_credentials():
            return
        self._save_settings()
        self._render_records(rows)

        self.stop_event.clear()
        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")

        automation = EStampAutomation(
            user_id=uid,
            password=pwd,
            state_code=self.state_var.get(),
            article_value=self.article_var.get(),
            rows=rows,
            browser_executable=self.browser_path,
            browser_time=browser_time,
            log_fn=self._log,
            status_fn=self._record_status,
            stop_event=self.stop_event,
        )

        def worker():
            try:
                automation.run()
                self._log("Automation finished.")
            except AutomationCancelled:
                if automation.current_record is not None:
                    self._record_status(automation.current_record, "stopped", "Stopped by user.")
                self._log("Automation stopped by user.")
            except PWTimeoutError as e:
                if automation.current_record is not None:
                    self._record_status(automation.current_record, "error", str(e))
                self._log(f"Timed out waiting for a page element: {e}")
            except Exception as e:
                if automation.current_record is not None:
                    self._record_status(automation.current_record, "error", str(e))
                self._log(f"Error: {e}")
            finally:
                self.after(0, self._run_finished)

        self.worker_thread = threading.Thread(target=worker, daemon=True)
        self.worker_thread.start()

    def _stop(self):
        self.stop_event.set()
        self._log("Stop requested - will halt before the next record starts.")

    def _run_finished(self):
        self.start_btn.configure(state="normal")
        self.stop_btn.configure(state="disabled")

    def _on_close(self):
        self._save_settings()
        if self.worker_thread is not None and self.worker_thread.is_alive():
            self.stop_event.set()
        self.destroy()


if __name__ == "__main__":
    app = App()
    app.mainloop()
