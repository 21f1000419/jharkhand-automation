import csv
import os
import queue
import threading
import time
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
    from .reference_recorder import ReferenceRecorder
except ImportError:
    from browser_detection import detect_supported_browsers
    from config import ConfigStore
    from credential_store import WindowsCredentialStore
    from models import BrowserEngine, Credentials
    from reference_recorder import ReferenceRecorder

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

COLLECTION_MODES = [
    ("Self Printing", "SELF"),
    ("Sub Registrar Office", "SRO"),
    ("Home Delivery / Courier", "CUR"),
    ("Nearest StockHolding Branch", "BRN"),
    ("Authorized Collection Center", "ACC"),
]


def find_in_any_frame(
    page,
    selector,
    *,
    state="visible",
    timeout=30_000,
    stop_event=None,
    pause_event=None,
    log_fn=None,
):
    """Return the first matching locator from the page or any live iframe.

    The SHCIL portal replaces its nested frames without changing the browser
    URL.  ``page.frames`` contains the main frame and every descendant frame,
    so this lookup does not rely on a fixed ``loginFrame > prodPage >
    actionFrame`` hierarchy.

    ``state='attached'`` is useful for controls that are intentionally hidden
    and driven by a styled label, such as the article radio buttons.
    """
    deadline = time.monotonic() + (timeout / 1_000)
    last_error = None

    while time.monotonic() < deadline:
        if stop_event is not None and stop_event.is_set():
            raise AutomationCancelled()
        if pause_event is not None and pause_event.is_set():
            pause_start = time.monotonic()
            if log_fn:
                log_fn("Automation paused. Click Resume to continue.")
            while pause_event.is_set():
                if stop_event is not None and stop_event.is_set():
                    raise AutomationCancelled()
                page.wait_for_timeout(200)
            deadline += time.monotonic() - pause_start
            if log_fn:
                log_fn("Automation resumed.")

        # Take a fresh snapshot each time: selecting State can recreate one or
        # more portal iframes while this function is waiting.
        for frame in page.frames:
            try:
                matches = frame.locator(selector)
                for index in range(matches.count()):
                    candidate = matches.nth(index)
                    if state == "attached" or candidate.is_visible():
                        return candidate
            except Exception as exc:  # Frame may be navigating or detached.
                last_error = exc

        page.wait_for_timeout(150)

    detail = f" Last frame error: {last_error}" if last_error else ""
    raise PWTimeoutError(
        f"Timed out waiting for {selector!r} in the page or any iframe "
        f"(required state: {state}).{detail}"
    )


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
        collection_mode,
        sro_location,
        courier_address,
        log_fn,
        status_fn,
        stop_event,
        pause_event=None,
        capture_references=False,
    ):
        self.user_id = user_id
        self.password = password
        self.state_code = state_code
        self.article_value = article_value
        self.rows = rows
        self.browser_executable = Path(browser_executable)
        self.browser_time = browser_time
        self.collection_mode = collection_mode
        self.sro_location = sro_location
        self.courier_address = courier_address
        self.log_fn = log_fn
        self.status_fn = status_fn
        self.current_record = None
        self.log = self._log
        self.stop_event = stop_event
        self.pause_event = pause_event
        self.capture_references = capture_references

    def _log(self, message):
        self.log_fn(message, self.current_record)

    def _check_stop(self):
        if self.stop_event.is_set():
            raise AutomationCancelled()
        if self.pause_event is not None and self.pause_event.is_set():
            self._log("Automation paused. Click Resume to continue.")
            while self.pause_event.is_set():
                if self.stop_event.is_set():
                    raise AutomationCancelled()
                time.sleep(0.2)
            self._log("Automation resumed.")

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
            if self.capture_references:
                recorder = ReferenceRecorder(Path(__file__).resolve().parent / "refs" / "captures", self._log)
                page.on("framenavigated", recorder.record)

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
                completion = (
                    "Certificate printed."
                    if self.collection_mode == "SELF"
                    else "Payment and collection request completed."
                )
                self.status_fn(idx, "completed", completion)
                self._log(f"--- Record {idx}/{total} complete.")

            self.current_record = None
            self._log("All records processed. Leaving browser window open for review.")
            # Intentionally do not auto-close the browser/context so the user
            # can inspect the final state / last certificate.

    # -- steps -------------------------------------------------------------

    def _login(self, page):
        self.log("Waiting for SHCIL login form...")

        self.log("Entering User ID...")
        self._find_in_any_frame(page, "#sUID", timeout=120_000).fill(self.user_id)
        self.log("Submitting User ID (the portal runs Google reCAPTCHA before the password screen)...")
        self._find_in_any_frame(page, "#bLogin", timeout=120_000).click()

        self.log(
            "Waiting for password field. Complete the Google reCAPTCHA manually if the portal presents one."
        )
        password_field = self._find_in_any_frame(page, "#sPass", timeout=180_000)
        password_field.fill(self.password)
        password_field.press("Tab")

        self.log("Password entered. If a CAPTCHA challenge appears, please solve it manually.")
        self.log("Polling continuously for 'Pay Stamp Duty'...")
        self._wait_for_pay_stamp_duty_link(page, timeout=180_000)
        self._check_stop()
        self.log("'Pay Stamp Duty' is available. Clicking it now...")
        self._click_pay_stamp_duty(page)
        self.log("Login successful. Pay Stamp Duty form opened.")
        return True

    def _find_in_any_frame(self, page, selector, *, state="visible", timeout=30_000):
        """Portal-wide locator that tolerates the site's changing iframe tree."""
        return find_in_any_frame(
            page,
            selector,
            state=state,
            timeout=timeout,
            stop_event=self.stop_event,
            pause_event=self.pause_event,
            log_fn=self._log,
        )

    def _wait_for_pay_stamp_duty_link(self, page, timeout=120_000):
        self._find_in_any_frame(
            page, "a.button_ecf:has-text('Pay Stamp Duty')", timeout=timeout
        )

    def _click_pay_stamp_duty(self, page):
        button = self._find_in_any_frame(page, "a.button_ecf:has-text('Pay Stamp Duty')")
        button.scroll_into_view_if_needed()
        button.click()

    def _process_record(self, page, row, pay_stamp_open=False):
        # Step 4: click "Pay Stamp Duty" unless the post-login poll already opened it.
        if not pay_stamp_open:
            self._check_stop()
            self.log("Polling for 'Pay Stamp Duty' and clicking it...")
        self._wait_for_pay_stamp_duty_link(page)
        self._click_pay_stamp_duty(page)

        # Step 5: select state. The portal refreshes its hidden options here.
        self._check_stop()
        self.log(f"Selecting state {self.state_code}...")
        self._find_in_any_frame(page, "#iSttCd", timeout=60_000).select_option(value=self.state_code)

        # Set the self-print limit after StateSelect() has finished, because
        # that portal callback overwrites the hidden field with its default.
        self._find_in_any_frame(
            page, "#iEsiSelfPrintLimit", state="attached", timeout=60_000
        ).evaluate("el => { el.value = '999999'; }")

        # StateSelect determines which collection methods the portal permits.
        # Use its normal radio/change handling rather than manipulating a hidden
        # value, so state-specific availability and self-print limits apply.
        self._select_collection_mode(page)

        # Step 6: select article type
        self._check_stop()
        self.log(f"Selecting article {self.article_value}...")
        # The portal hides the real radio input and displays a styled control.
        # Force-check the attached input so the native change handler still
        # runs, without depending on the particular iframe or CSS layout.
        self._find_in_any_frame(
            page, f"input.cArt[value='{self.article_value}']", state="attached", timeout=60_000
        ).check(force=True)

        # Step 7: proceed
        self._check_stop()
        self.log("Clicking Proceed...")
        self._find_in_any_frame(page, "#btn_sub[value='Proceed']", timeout=60_000).click()

        # Step 9: fill the record's fields
        self._check_stop()
        self.log("Filling description, price, and party details...")
        self._find_in_any_frame(page, "#iPropDesc", timeout=60_000).fill(row["description"])
        self._find_in_any_frame(page, "#iConPrice", timeout=60_000).fill(row["consideration_price"])
        self._find_in_any_frame(page, "#iParty1Nm", timeout=60_000).fill(row["party1"])
        self._find_in_any_frame(page, "#iParty2Nm", timeout=60_000).fill(row["party2"])
        self._find_in_any_frame(page, "#iStampPdBy", timeout=60_000).fill(row["paid_by"])
        stamp_amount = self._find_in_any_frame(page, "#iStampAmt", timeout=60_000)
        stamp_amount.fill(row["stamp_amount"])
        # Press Tab to trigger the onblur validation - any resulting alert()
        # is auto-accepted by the global dialog handler registered above.
        stamp_amount.press("Tab")
        page.wait_for_timeout(500)

        # Select RAZORPAY as payment mode
        self._check_stop()
        self.log("Selecting RAZORPAY payment mode...")
        self._find_in_any_frame(page, "#iPmtMode", timeout=60_000).select_option(value="RAZORPAY")

        # Press Save
        self._check_stop()
        self.log("Clicking Save...")
        self._find_in_any_frame(page, "input#btn_sub[value='Save']", timeout=60_000).click()
        page.wait_for_timeout(500)

        # Press Confirm
        self._check_stop()
        self.log("Clicking Confirm...")
        self._find_in_any_frame(page, "#btnConfirm", timeout=60_000).click()
        page.wait_for_timeout(500)

        # Accept terms & conditions checkbox
        self._check_stop()
        self.log("Checking 'I accept Terms and Conditions'...")
        self._find_in_any_frame(page, "#iChk", timeout=60_000).check()

        if self.collection_mode == "SELF":
            # At this point the actual Razorpay payment (bank login / UPI / OTP)
            # normally has to happen. The script does not attempt this - it just
            # waits for the certificate button, giving the user time to complete
            # payment manually in this same browser window.
            self.log(
                "Waiting for payment to complete and 'Print eStamp Certificate' "
                "button to appear. Complete the Razorpay payment manually if prompted..."
            )
            self._find_in_any_frame(page, "#btnPrintCert", timeout=900_000)

            self._check_stop()
            self.log("Clicking 'Print eStamp Certificate'...")
            self._find_in_any_frame(page, "#btnPrintCert", timeout=60_000).click()
            page.wait_for_timeout(500)

            # Wait for the final Print button and click it.
            self._check_stop()
            self.log("Waiting for 'Print' button...")
            self._find_in_any_frame(page, "#printBtn", timeout=180_000).click()
        else:
            self.log(
                "Complete the Razorpay payment manually. This collection mode does not "
                "self-print a certificate; wait for the portal to return to 'Pay Stamp Duty'."
            )

        self._check_stop()
        self.log("Waiting to return to 'Pay Stamp Duty' for the next record...")
        self._wait_for_pay_stamp_duty_link(page, timeout=900_000)

    def _select_collection_mode(self, page):
        mode = self.collection_mode
        option = self._find_in_any_frame(
            page, f"input[name='iOpt'][value='{mode}']", state="attached", timeout=60_000
        )
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            is_available = option.evaluate(
                """el => {
                    if (el.disabled) return false;
                    // The actual radio can be visually replaced by a styled
                    // label. Check its containers instead of the input itself.
                    for (let node = el.parentElement; node; node = node.parentElement) {
                        const style = getComputedStyle(node);
                        if (style.display === 'none' || style.visibility === 'hidden') return false;
                    }
                    return true;
                }"""
            )
            if is_available:
                break
            page.wait_for_timeout(150)
        else:
            raise RuntimeError(
                f"{mode} is not offered by the SHCIL portal for state {self.state_code}. "
                "Select a collection method shown by the portal."
            )

        # The radio is sometimes styled or otherwise not directly clickable.
        # Force-checking the attached control still invokes its native handler,
        # which keeps the portal's dependent fields in sync.
        self.log(f"Selecting collection mode {mode}...")
        option.check(force=True)

        if mode == "SRO":
            self._select_sro_location(page)
        elif mode == "CUR":
            self._fill_courier_address(page)

    def _select_sro_location(self, page):
        self.log(f"Selecting SRO location: {self.sro_location}...")
        location_select = self._find_in_any_frame(
            page, "#iSroLoc", state="attached", timeout=60_000
        )
        deadline = time.monotonic() + 60
        choices = []
        while time.monotonic() < deadline:
            options = location_select.locator("option")
            choices = [
                (options.nth(index).text_content() or "").strip()
                for index in range(options.count())
            ]
            if any(choice and choice != "Select SRO Location" for choice in choices):
                break
            page.wait_for_timeout(150)
        else:
            raise RuntimeError("The portal did not return any SRO locations for the selected state.")

        requested = self.sro_location.strip()
        match = next((choice for choice in choices if choice.casefold() == requested.casefold()), None)
        if match is None:
            available = ", ".join(choice for choice in choices if choice and choice != "Select SRO Location")
            raise RuntimeError(
                f"SRO location {requested!r} is not available for {self.state_code}. "
                f"Portal choices: {available}"
            )
        location_select.select_option(label=match)

    def _fill_courier_address(self, page):
        fields = {
            "#iCurAdd1": self.courier_address["line1"],
            "#iCurAdd2": self.courier_address["line2"],
            "#iCurLm": self.courier_address["landmark"],
            "#iCurCity": self.courier_address["city"],
            "#iCurPin": self.courier_address["pin"],
        }
        self.log("Filling courier delivery address...")
        for selector, value in fields.items():
            self._find_in_any_frame(page, selector, timeout=60_000).fill(value)


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
        collection_mode_values = {code for _label, code in COLLECTION_MODES}
        self.collection_mode_var = tk.StringVar(
            value=self.config.last_collection_mode
            if self.config.last_collection_mode in collection_mode_values
            else "SELF"
        )
        self.sro_location_var = tk.StringVar(value=self.config.last_sro_location)
        self.courier_line1_var = tk.StringVar(value=self.config.courier_address_line1)
        self.courier_line2_var = tk.StringVar(value=self.config.courier_address_line2)
        self.courier_landmark_var = tk.StringVar(value=self.config.courier_landmark)
        self.courier_city_var = tk.StringVar(value=self.config.courier_city)
        self.courier_pin_var = tk.StringVar(value=self.config.courier_pin)
        self.capture_references_var = tk.BooleanVar(value=self.config.capture_references)
        self.uid_var = tk.StringVar(value=saved_credentials.citizen_username if saved_credentials else "")
        self.pwd_var = tk.StringVar(value=saved_credentials.citizen_password if saved_credentials else "")
        self.credentials_saved = saved_credentials is not None

        self.log_queue = queue.Queue()
        self.stop_event = threading.Event()
        self.pause_event = threading.Event()
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

        ttk.Label(opts, text="Certificate Collection:").grid(row=2, column=0, sticky="w", **pad)
        collection_combo = ttk.Combobox(
            opts,
            state="readonly",
            width=37,
            values=[f"{label} ({code})" for label, code in COLLECTION_MODES],
        )
        collection_combo.grid(row=2, column=1, sticky="w", **pad)
        collection_codes = [code for _label, code in COLLECTION_MODES]
        collection_combo.current(collection_codes.index(self.collection_mode_var.get()))
        collection_combo.bind(
            "<<ComboboxSelected>>",
            lambda e: self._collection_mode_selected(collection_combo, collection_codes),
        )

        self.sro_settings = ttk.Frame(opts)
        ttk.Label(self.sro_settings, text="SRO location (exact portal name):").pack(side="left")
        ttk.Entry(self.sro_settings, textvariable=self.sro_location_var, width=38).pack(
            side="left", padx=(6, 0)
        )
        self.sro_settings.grid(row=3, column=1, sticky="w", **pad)

        self.courier_settings = ttk.Frame(opts)
        courier_fields = [
            ("Address line 1", self.courier_line1_var),
            ("Address line 2", self.courier_line2_var),
            ("Landmark", self.courier_landmark_var),
            ("City", self.courier_city_var),
            ("PIN", self.courier_pin_var),
        ]
        for index, (label, variable) in enumerate(courier_fields):
            ttk.Label(self.courier_settings, text=f"{label}:").grid(
                row=index, column=0, sticky="w", pady=2
            )
            ttk.Entry(
                self.courier_settings,
                textvariable=variable,
                width=45 if label != "PIN" else 12,
            ).grid(row=index, column=1, sticky="w", padx=(6, 0), pady=2)
        self.courier_settings.grid(row=4, column=1, sticky="w", **pad)

        ttk.Label(opts, text="Browser Time (IST):").grid(row=5, column=0, sticky="w", **pad)
        time_frame = ttk.Frame(opts)
        time_frame.grid(row=5, column=1, sticky="w", **pad)
        ttk.Entry(time_frame, textvariable=self.time_var, width=18).pack(side="left")
        ttk.Button(time_frame, text="Set 2:00 PM", command=lambda: self.time_var.set("14:00")).pack(
            side="left", padx=(6, 0)
        )
        ttk.Button(time_frame, text="Clear (System Time)", command=lambda: self.time_var.set("")).pack(
            side="left", padx=(6, 0)
        )
        ttk.Label(
            opts,
            text=(
                "Leave blank to use actual system time, or set custom time "
                "(e.g. 14:00, 02:00 PM, 2026-08-21 14:00)."
            ),
            foreground="#555555",
        ).grid(row=6, column=1, sticky="w", **pad)

        ttk.Checkbutton(
            opts,
            text="Save page HTML and connected resources to refs/captures",
            variable=self.capture_references_var,
            command=self._save_settings,
        ).grid(row=7, column=1, sticky="w", **pad)
        self._update_collection_fields()

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
        self.pause_btn = ttk.Button(controls, text="Pause", command=self._pause, state="disabled")
        self.pause_btn.pack(side="left", **pad)
        self.resume_btn = ttk.Button(controls, text="Resume", command=self._resume, state="disabled")
        self.resume_btn.pack(side="left", **pad)
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

    def _collection_mode_selected(self, collection_combo, collection_codes):
        self.collection_mode_var.set(collection_codes[collection_combo.current()])
        self._update_collection_fields()
        self._save_settings()

    def _update_collection_fields(self):
        if self.collection_mode_var.get() == "SRO":
            self.sro_settings.grid()
        else:
            self.sro_settings.grid_remove()
        if self.collection_mode_var.get() == "CUR":
            self.courier_settings.grid()
        else:
            self.courier_settings.grid_remove()

    def _save_settings(self):
        self.config.last_state = self.state_var.get()
        self.config.last_article = self.article_var.get()
        self.config.last_csv_path = self.csv_path.get().strip()
        self.config.last_time = self.time_var.get().strip()
        self.config.last_collection_mode = self.collection_mode_var.get()
        self.config.last_sro_location = self.sro_location_var.get().strip()
        self.config.courier_address_line1 = self.courier_line1_var.get().strip()
        self.config.courier_address_line2 = self.courier_line2_var.get().strip()
        self.config.courier_landmark = self.courier_landmark_var.get().strip()
        self.config.courier_city = self.courier_city_var.get().strip()
        self.config.courier_pin = self.courier_pin_var.get().strip()
        self.config.capture_references = self.capture_references_var.get()
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
        collection_mode = self.collection_mode_var.get()

        if not uid or not pwd:
            messagebox.showerror("Missing info", "Please enter User ID and Password.")
            return
        if self.state_var.get() == "0":
            messagebox.showerror("Missing info", "Please select a State.")
            return
        if not csv_path or not os.path.isfile(csv_path):
            messagebox.showerror("Missing CSV", "Please select a valid records CSV file.")
            return
        if collection_mode == "SRO" and not self.sro_location_var.get().strip():
            messagebox.showerror(
                "Missing SRO location",
                "Enter the exact SRO location name shown by the portal for the selected state.",
            )
            return
        if collection_mode == "CUR":
            courier_values = {
                "Address line 1": self.courier_line1_var.get().strip(),
                "Address line 2": self.courier_line2_var.get().strip(),
                "Landmark": self.courier_landmark_var.get().strip(),
                "City": self.courier_city_var.get().strip(),
                "PIN": self.courier_pin_var.get().strip(),
            }
            missing_fields = [label for label, value in courier_values.items() if not value]
            if missing_fields:
                messagebox.showerror(
                    "Missing courier address",
                    "Enter: " + ", ".join(missing_fields) + ".",
                )
                return
            if not (courier_values["PIN"].isdigit() and len(courier_values["PIN"]) == 6):
                messagebox.showerror("Invalid courier PIN", "Courier PIN must contain exactly six digits.")
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
            f"may involve real payments on the SHCIL portal using {collection_mode} collection. Continue?",
        ):
            return

        if not self._save_credentials():
            return
        self._save_settings()
        self._render_records(rows)

        self.stop_event.clear()
        self.pause_event.clear()
        self.start_btn.configure(state="disabled")
        self.pause_btn.configure(state="normal")
        self.resume_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")

        automation = EStampAutomation(
            user_id=uid,
            password=pwd,
            state_code=self.state_var.get(),
            article_value=self.article_var.get(),
            rows=rows,
            browser_executable=self.browser_path,
            browser_time=browser_time,
            collection_mode=collection_mode,
            sro_location=self.sro_location_var.get().strip(),
            courier_address={
                "line1": self.courier_line1_var.get().strip(),
                "line2": self.courier_line2_var.get().strip(),
                "landmark": self.courier_landmark_var.get().strip(),
                "city": self.courier_city_var.get().strip(),
                "pin": self.courier_pin_var.get().strip(),
            },
            log_fn=self._log,
            status_fn=self._record_status,
            stop_event=self.stop_event,
            pause_event=self.pause_event,
            capture_references=self.capture_references_var.get(),
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

    def _pause(self):
        self.pause_event.set()
        self.pause_btn.configure(state="disabled")
        self.resume_btn.configure(state="normal")
        self.status_var.set("Paused by user.")
        self._log("Pause requested - pausing automation.")

    def _resume(self):
        self.pause_event.clear()
        self.pause_btn.configure(state="normal")
        self.resume_btn.configure(state="disabled")
        self.status_var.set("Resuming automation...")
        self._log("Resume requested - continuing automation.")

    def _stop(self):
        self.stop_event.set()
        self.pause_event.clear()
        self.pause_btn.configure(state="disabled")
        self.resume_btn.configure(state="disabled")
        self._log("Stop requested - will halt before the next record starts.")

    def _run_finished(self):
        self.start_btn.configure(state="normal")
        self.pause_btn.configure(state="disabled")
        self.resume_btn.configure(state="disabled")
        self.stop_btn.configure(state="disabled")

    def _on_close(self):
        self._save_settings()
        if self.worker_thread is not None and self.worker_thread.is_alive():
            self.stop_event.set()
            self.pause_event.clear()
        self.destroy()


if __name__ == "__main__":
    app = App()
    app.mainloop()
