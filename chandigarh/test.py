import os
import time
import pandas as pd
import customtkinter as ctk
from tkinter import filedialog, messagebox
import threading

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import Select, WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, StaleElementReferenceException

ctk.set_appearance_mode("Light")

class VendorFeederTest(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("🧪 Safe Feeding Logic Tester | V-8 UI")
        self.geometry("750x650")
        self.resizable(False, False)
        self.configure(fg_color="#F8F9FA")

        self.csv_path = ctk.StringVar()
        self.driver = None
        self.is_running = False
        
        self.pause_event = threading.Event()
        self.pause_event.set() 

        self.create_widgets()

    def create_widgets(self):
        header_frame = ctk.CTkFrame(self, fg_color="#FFFFFF", corner_radius=15, border_width=1, border_color="#E5E7EB")
        header_frame.pack(pady=20, padx=25, fill="x")
        ctk.CTkLabel(header_frame, text="⚡ E-Stamp Auto-Feeder (Safe Mode)", font=("Segoe UI", 24, "bold"), text_color="#111827").pack(pady=15)

        frame_settings = ctk.CTkFrame(self, fg_color="#FFFFFF", corner_radius=15, border_width=1, border_color="#E5E7EB")
        frame_settings.pack(pady=10, padx=25, fill="x")

        # CSV Config
        ctk.CTkLabel(frame_settings, text="📁 Data Source (CSV):", font=("Segoe UI", 14, "bold"), text_color="#374151").grid(row=0, column=0, padx=20, pady=25, sticky="w")
        csv_frame = ctk.CTkFrame(frame_settings, fg_color="transparent")
        csv_frame.grid(row=0, column=1, padx=10, pady=25, sticky="w")
        ctk.CTkEntry(csv_frame, textvariable=self.csv_path, width=250, font=("Segoe UI", 12), border_color="#D1D5DB").pack(side="left", padx=(0, 10))
        ctk.CTkButton(csv_frame, text="Browse", width=80, fg_color="#4B5563", hover_color="#374151", corner_radius=8, command=self.browse_csv).pack(side="left")

        # Action Buttons
        frame_actions = ctk.CTkFrame(self, fg_color="transparent")
        frame_actions.pack(pady=20)
        ctk.CTkButton(frame_actions, text="🌐 1. Open Portal", font=("Segoe UI", 14, "bold"), fg_color="#2563EB", hover_color="#1D4ED8", height=45, width=150, corner_radius=10, command=self.open_portal).pack(side="left", padx=10)
        ctk.CTkButton(frame_actions, text="🚀 2. START FEEDING", font=("Segoe UI", 14, "bold"), fg_color="#10B981", hover_color="#059669", height=45, width=180, corner_radius=10, command=self.start_feeding_thread).pack(side="left", padx=10)
        
        self.btn_pause = ctk.CTkButton(frame_actions, text="⏸️ PAUSE", font=("Segoe UI", 14, "bold"), fg_color="#EF4444", hover_color="#DC2626", height=45, width=130, corner_radius=10, state="disabled", command=self.toggle_pause)
        self.btn_pause.pack(side="left", padx=10)

        # Log Box 
        self.log_box = ctk.CTkTextbox(self, height=200, width=690, fg_color="#1F2937", text_color="#10B981", font=("Consolas", 12), corner_radius=10)
        self.log_box.pack(pady=10, padx=25)
        self.log("✅ Ready. Format: State, Article/Description, First_Party, Second_Party, Duty, Paid_By")

    def log(self, msg): 
        self.log_box.insert("end", f"> {msg}\n")
        self.log_box.see("end")
        
    def browse_csv(self): 
        filename = filedialog.askopenfilename()
        self.csv_path.set(filename)

    def toggle_pause(self):
        if self.pause_event.is_set():
            self.pause_event.clear()
            self.btn_pause.configure(text="▶️ RESUME", fg_color="#F59E0B", hover_color="#D97706")
            self.log("⏸️ PAUSED: Complete Payment Manually and click Resume.")
        else:
            self.pause_event.set()
            self.btn_pause.configure(text="⏸️ PAUSE", fg_color="#EF4444", hover_color="#DC2626")
            self.log("▶️ RESUMED: Feeding next row...")

    def open_portal(self):
        try:
            options = webdriver.ChromeOptions()
            options.add_experimental_option('excludeSwitches', ['enable-logging'])
            self.driver = webdriver.Chrome(options=options)
            self.driver.maximize_window()
            self.driver.get("https://www.shcilestamp.com/OnlineStamping/OlnEsi")
            self.log("🌐 Portal opened! Please Login and go to Dashboard.")
        except Exception as e:
            self.log(f"❌ Error: {e}")

    def start_feeding_thread(self):
        if not self.driver:
            messagebox.showerror("Error", "Please open the portal first!")
            return
        if not self.csv_path.get():
            messagebox.showerror("Data Error", "Please select an input CSV file!")
            return
        
        if not self.is_running:
            self.is_running = True
            self.btn_pause.configure(state="normal")
            threading.Thread(target=self.feeding_loop, daemon=True).start()

    def feeding_loop(self):
        self.log("🚀 Initializing Safe Feeding Logic...")
        
        try:
            df = pd.read_csv(self.csv_path.get(), header=None)
            total_rows = len(df)
        except Exception as e:
            self.log(f"❌ CSV Error: {e}")
            self.is_running = False
            return

        for index, row in df.iterrows():
            self.pause_event.wait() 
            
            try:
                state = str(row[0]).strip()
                article = str(row[1]).strip()
                first_party = str(row[2]).strip()
                second_party = str(row[3]).strip() if pd.notna(row[3]) else ""
                stamp_duty = str(row[4]).strip()
                paid_by = str(row[5]).strip() if pd.notna(row[5]) else first_party

                self.log(f"\n🔄 [{index+1}/{total_rows}] Feeding: {first_party} | Rs.{stamp_duty}")

                # Navigate to Pay Stamp Duty
                self.driver.switch_to.default_content()
                try: self.driver.switch_to.frame("loginFrame")
                except: pass
                try: self.driver.switch_to.frame("prodPage")
                except: pass
                
                self.driver.find_element(By.LINK_TEXT, "Pay Stamp Duty").click()
                time.sleep(1)
                
                try: self.driver.switch_to.frame("actionFrame")
                except: pass

                # Select State
                nonJudicial = Select(self.driver.find_element(By.NAME, 'iSttCd'))
                nonJudicial.select_by_value(state)
                time.sleep(0.5)

                # Set high self print limit to avoid portal blocking
                try:
                    self.driver.execute_script("var el = document.getElementById('iEsiSelfPrintLimit'); if (el) el.value = '999999';")
                except Exception:
                    pass

                # Select Self Printing for applicable states
                if state in ['AS', 'DL', 'CH']:
                    try:
                        self.driver.find_element(By.ID, 'iOptSELF').click()
                    except Exception:
                        pass

                # Article Selection Logic
                if state == 'DL':
                    self.driver.find_element(By.XPATH, '/html/body/form/table/tbody/tr[14]/td/table/tbody/tr[3]/td[1]/input').click()
                elif state == 'KA':
                    if article == 'KA-NRG-4':
                        self.driver.find_element(By.XPATH, '/html/body/form/table/tbody/tr[15]/td/table/tbody/tr[3]/td[1]/input').click()
                    else:
                        self.driver.find_element(By.XPATH, '/html/body/form/table/tbody/tr[15]/td/table/tbody/tr[3]/td[2]/input').click()
                elif state == 'CH':
                    # Do NOT choose Affidavit (CH-RG-4).
                    # Choose Article 5 (CH-RG-5: Agreement or Memorandum of an agreement) by default
                    # or map to specific non-affidavit code if provided.
                    target_art = 'CH-RG-5'
                    if article in ['CH-RG-5', 'CH-RG-47', 'CH-RG-33', 'CH-RG-39']:
                        target_art = article
                    
                    selected_radio = False
                    try:
                        radio_elem = self.driver.find_element(By.XPATH, f"//input[@name='iArticle' and @value='{target_art}']")
                        self.driver.execute_script("arguments[0].click();", radio_elem)
                        selected_radio = True
                    except Exception:
                        pass
                    
                    if not selected_radio:
                        try:
                            radio_elem = self.driver.find_element(By.XPATH, "//tr[@id='tr_CHstmpType']//input[@value='CH-RG-5']")
                            self.driver.execute_script("arguments[0].click();", radio_elem)
                            selected_radio = True
                        except Exception:
                            pass

                    if not selected_radio:
                        # Fallback to the first radio in Chandigarh table (Article 5)
                        try:
                            radio_elem = self.driver.find_element(By.XPATH, "//tr[@id='tr_CHstmpType']//input[@type='radio'][1]")
                            self.driver.execute_script("arguments[0].click();", radio_elem)
                        except Exception:
                            pass
                else:
                    self.driver.find_element(By.NAME, 'iRegisterType').click()
                    for _ in range(20):
                        try:
                            nonJ = Select(self.driver.find_element(By.NAME, 'iRSD'))
                            nonJ.select_by_value(article)
                            break
                        except: time.sleep(0.2)

                self.driver.find_element(By.NAME, 'btn_sub').click()
                time.sleep(1)

                # Fill Property / Document Description if required/present (min 30 chars)
                try:
                    desc_elem = self.driver.find_element(By.NAME, 'iPropDesc')
                    if desc_elem.is_enabled() and desc_elem.is_displayed():
                        desc_text = article.strip()
                        # If description is a code or too short, expand to valid description text >= 30 chars
                        if desc_text.startswith("CH-") or len(desc_text) < 30:
                            desc_text = f"Agreement and Transaction Document for {first_party}"
                        if len(desc_text) < 30:
                            desc_text = desc_text.ljust(30, '.')
                        desc_elem.clear()
                        desc_elem.send_keys(desc_text[:100])
                except Exception:
                    pass

                # Fill Consideration Price if required/present
                try:
                    price_elem = self.driver.find_element(By.NAME, 'iConPrice')
                    if price_elem.is_enabled() and price_elem.is_displayed():
                        if not price_elem.get_attribute('value'):
                            price_elem.clear()
                            price_elem.send_keys("0")
                except Exception:
                    pass

                # Fill Form Details using Standard Send Keys
                first_party_menu = self.driver.find_element(By.NAME, 'iParty1Nm')
                first_party_menu.clear()
                first_party_menu.send_keys(first_party)

                if second_party:
                    second_party_menu = self.driver.find_element(By.NAME, 'iParty2Nm')
                    second_party_menu.clear()
                    second_party_menu.send_keys(second_party)

                # Click blank area to trigger alert
                try:
                    self.driver.find_element(By.XPATH, '//tbody/tr/td[2]/span').click()
                    WebDriverWait(self.driver, 1).until(EC.alert_is_present()).accept()
                except Exception:
                    pass

                # Fill Amount
                stamp_duty_menu = self.driver.find_element(By.NAME, 'iStampAmt')
                stamp_duty_menu.clear()
                stamp_duty_menu.send_keys(stamp_duty)
                
                try:
                    self.driver.find_element(By.XPATH, '//tbody/tr/td[2]/span').click()
                    WebDriverWait(self.driver, 1).until(EC.alert_is_present()).accept()
                except Exception:
                    pass

                # Paid By using Standard Send Keys
                paid_by_menu = self.driver.find_element(By.NAME, 'iStampPdBy')
                paid_by_menu.clear()
                paid_by_menu.send_keys(paid_by)

                # Payment Gateway (Razorpay default)
                try:
                    Select(self.driver.find_element(By.NAME, 'iPmtMode')).select_by_value('RAZORPAY')
                except Exception:
                    pass

                # Save Data
                self.driver.find_element(By.NAME, 'btn_sub').click()
                try: WebDriverWait(self.driver, 1).until(EC.alert_is_present()).accept()
                except: pass
                
                self.driver.find_element(By.NAME, 'btnConfirm').click()
                try: WebDriverWait(self.driver, 1).until(EC.alert_is_present()).accept()
                except: pass

                # Agree Checkbox
                self.driver.find_element(By.NAME, 'iChk').click()
                
                doc_no = self.driver.find_element(By.XPATH, '//*[@id="frmConfSubm"]/table/tbody/tr[4]/td/table/tbody/tr[1]/td[2]').text
                self.log(f"✅ Filled Successfully! Doc No: {doc_no}")
                self.log("⏸️ Pausing... Complete Payment & Captcha manually, then click RESUME.")
                
                # Auto-Pause for manual payment
                self.pause_event.clear()
                self.after(0, lambda: self.btn_pause.configure(text="▶️ RESUME", fg_color="#F59E0B", hover_color="#D97706"))

            except Exception as err:
                self.log(f"❌ Error on row {index+1}: {err}")
            
        self.is_running = False
        self.btn_pause.configure(state="disabled")
        messagebox.showinfo("Task Completed", "All rows from CSV have been fed!")

if __name__ == "__main__":
    app = VendorFeederTest()
    app.mainloop()
