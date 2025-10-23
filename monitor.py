#!/usr/bin/env python3
"""
Costco Tire Appointment Monitor (hardened)
- Explicit waits (no blind sleeps)
- Chrome (CDP) by default, Firefox fallback
- Tracks (date,time) slot tuples, not just dates
- Cooldown + de-dupe notifications
- Secrets via env (.env supported)
"""

import os
import re
import sys
import json
import time
import smtplib
import hashlib
import random
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.firefox.service import Service as FirefoxService
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from webdriver_manager.firefox import GeckoDriverManager

UA = [
    # rotate among a few stable UAs
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
]

def now_iso():
    return datetime.now().isoformat(timespec="seconds")

class AppointmentMonitor:
    def __init__(self, config_path="config.json"):
        load_dotenv()
        self.config = self._load_config(config_path)
        self.locations = self.config.get("locations", [])
        if not self.locations and self.config.get("booking_url"):
            self.locations = [{"name": "Default Location",
                               "url": self.config["booking_url"],
                               "address": ""}]
        self.check_interval = int(self.config.get("check_interval_minutes", 15) * 60)
        self.state_file = "state.json"
        self.previous_states = self._load_state()

        # Notification controls
        self.cooldown_minutes = 3  # avoid spam on flicker
        self._last_notified = {}   # location_name -> datetime

        self.driver = None
        self._init_driver()

    def _load_config(self, path):
        if not os.path.exists(path):
            print(f"Config '{path}' not found. Please create it.")
            sys.exit(1)
        with open(path, "r") as f:
            cfg = json.load(f)
        # Inject env-based password if present
        email_cfg = cfg.get("notifications", {}).get("email", {})
        env_key = email_cfg.get("password_env")
        if env_key and not email_cfg.get("password"):
            email_cfg["password"] = os.getenv(env_key, "")
            cfg["notifications"]["email"] = email_cfg
        return cfg

    def _load_state(self):
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, "r") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def _save_state(self, state):
        with open(self.state_file, "w") as f:
            json.dump(state, f, indent=2)

    def _init_driver(self):
        """Prefer Firefox; fall back to Chrome if Firefox init fails."""
        ua = random.choice(UA)
        # --- Try Firefox first ---
        try:
            ff_opts = webdriver.FirefoxOptions()
            ff_opts.add_argument("--headless")
            ff_opts.set_preference("general.useragent.override", ua)
            ff_opts.set_preference("devtools.console.stdout.content", False)
            service = FirefoxService(GeckoDriverManager().install())
            self.driver = webdriver.Firefox(service=service, options=ff_opts)
            print("Browser automation initialized (Firefox)")
            return
        except Exception as e:
            print(f"Firefox init failed: {e}. Falling back to Chrome…")

        # --- Chrome fallback ---
        try:
            chrome_opts = webdriver.ChromeOptions()
            chrome_opts.add_argument("--headless=new")
            chrome_opts.add_argument("--disable-gpu")
            chrome_opts.add_argument("--no-sandbox")
            chrome_opts.add_argument("--window-size=1920,1080")
            chrome_opts.add_argument(f"--user-agent={ua}")
            chrome_opts.add_experimental_option("excludeSwitches", ["enable-automation"])
            chrome_opts.add_experimental_option("useAutomationExtension", False)

            service = ChromeService(ChromeDriverManager().install())
            self.driver = webdriver.Chrome(service=service, options=chrome_opts)
            print("Browser automation initialized (Chrome)")
        except Exception as e:
            print(f"Warning: could not initialize any browser: {e}")
            self.driver = None

    # ---------- Fetch & parse ----------

    def _fetch_with_selenium(self, url):
        if not self.driver:
            return None
        try:
            self.driver.get(url)

            # Wait for body and any service toggles to be present
            wait = WebDriverWait(self.driver, 25)
            wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))

            # Prefer a stable “Continue / Next / Book” CTA
            def click_if_present(xpath_list, timeout=10):
                for xp in xpath_list:
                    try:
                        btn = WebDriverWait(self.driver, timeout).until(
                            EC.element_to_be_clickable((By.XPATH, xp))
                        )
                        self.driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
                        btn.click()
                        return True
                    except Exception:
                        continue
                return False

            # 1) pick a service (toggle)
            toggles = self.driver.find_elements(By.CSS_SELECTOR, 'button[data-cy*="toggle"]')
            if toggles:
                try:
                    self.driver.execute_script("arguments[0].click();", toggles[0])
                except Exception:
                    toggles[0].click()

            # 2) go forward in the flow
            click_if_present([
                "//button[contains(., 'Continue')]",
                "//button[contains(., 'Next')]",
                "//button[contains(., 'Book')]"
            ])

            # 3) wait until dates appear or skeletons disappear
            #    Look for buttons that look like calendar dates
            def dates_loaded(driver):
                # buttons with role=radio and aria-label like a date,
                # or buttons with data-cy containing 'date' or 'slot'
                btns = driver.find_elements(By.XPATH,
                    "//button[@role='radio' and @aria-label] | //button[contains(@data-cy,'date') or contains(@data-cy,'slot')]"
                )
                # also require skeletons to be gone or low count
                skeletons = driver.find_elements(By.CSS_SELECTOR, ".react-loading-skeleton")
                return len(btns) > 0 and len(skeletons) < 3

            WebDriverWait(self.driver, 30).until(dates_loaded)

            # Click a handful of visible dates to reveal their times
            date_buttons = self.driver.find_elements(By.XPATH,
                "//button[@role='radio' and @aria-label] | //button[contains(@data-cy,'dateslot')]"
            )
            date_buttons = date_buttons[:12]  # limit work

            for db in date_buttons:
                try:
                    self.driver.execute_script("arguments[0].scrollIntoView({block:'center'});", db)
                    label = db.get_attribute("aria-label") or db.text.strip()
                    # Avoid clicking already-selected date if aria-checked
                    if db.get_attribute("aria-checked") == "true":
                        pass
                    else:
                        try:
                            db.click()
                        except Exception:
                            self.driver.execute_script("arguments[0].click();", db)

                    # wait for times to load for this date
                    WebDriverWait(self.driver, 10).until(
                        lambda d: len(d.find_elements(
                            By.XPATH,
                            "//button[.//*[contains(text(),'AM')] or contains(text(),'AM') or contains(text(),'PM')]"
                        )) > 0 or "No available times" in d.page_source
                    )
                except Exception:
                    continue

            return self.driver.page_source
        except Exception as e:
            print(f"   fetch error: {e}")
            return None

    def fetch_page(self, url):
        if self.driver:
            html = self._fetch_with_selenium(url)
            if html:
                return html
        # fallback simple GET (rarely works for JS apps but harmless)
        try:
            r = requests.get(url, headers={"User-Agent": random.choice(UA)}, timeout=25)
            r.raise_for_status()
            return r.text
        except Exception:
            return None

    TIME_RE = re.compile(r'\b(1[0-2]|0?[1-9]):[0-5]\d\s*[APap][Mm]\b')

    def parse_appointments(self, html):
        soup = BeautifulSoup(html, "lxml")
        content_hash = hashlib.md5(html.encode()).hexdigest()

        # Collect times that are actually clickable (not disabled)
        slots = set()

        # Try to infer currently selected/visible date from the page globally
        inferred_selected_date = None
        try:
            # Selected radio button with aria-label
            sel = soup.select_one("button[role='radio'][aria-label][aria-checked='true'], button[aria-label].selected, button[aria-label].is-selected")
            if sel and sel.get("aria-label"):
                inferred_selected_date = sel.get("aria-label").strip()
            if not inferred_selected_date:
                # Heuristic: any weekday-like text near the time list
                weekday_like = soup.find(string=self.TIME_RE)  # locate a time, then look around
                if weekday_like:
                    # climb a bit to capture a concise container text
                    parent = weekday_like.parent
                    hop = 0
                    while parent and hop < 4 and not inferred_selected_date:
                        text = parent.get_text(" ", strip=True)
                        if text and 3 <= len(text) <= 60 and any(day in text for day in ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]):
                            inferred_selected_date = text
                            break
                        parent = parent.parent
                        hop += 1
        except Exception:
            pass

        # Buttons that look like times
        for btn in soup.find_all("button"):
            txt = (btn.get_text(" ", strip=True) or "").strip()
            if not txt:
                continue
            if self.TIME_RE.fullmatch(txt):
                # check disabled hints
                disabled = btn.has_attr("disabled")
                classes = " ".join(btn.get("class", [])).lower()
                aria_disabled = (btn.get("aria-disabled") or "").lower() == "true"
                if not (disabled or "disabled" in classes or aria_disabled):
                    # Try to infer the date context from nearby aria-label or parent date label
                    date_label = None
                    # parent/ancestor might have selected date label
                    parent = btn.find_parent()
                    while parent and not date_label:
                        # look for aria-label on selected radio button in siblings
                        radios = parent.find_all(attrs={"role": "radio"})
                        for r in radios:
                            if r.get("aria-checked") == "true" and r.get("aria-label"):
                                date_label = r["aria-label"]
                                break
                        parent = parent.parent if parent else None

                    # If nearby context fails, fall back to globally inferred selected date
                    if not date_label and inferred_selected_date:
                        date_label = inferred_selected_date

                    # If we couldn’t infer date at all, store as Unknown date
                    slots.add((date_label or "Unknown date", txt))

        # Fallback: regex times in page if zero buttons matched
        if not slots:
            text = soup.get_text(" ", strip=True)
            for m in self.TIME_RE.finditer(text):
                slots.add(((inferred_selected_date or "Unknown date"), m.group(0)))

        # Build normalized lists
        # unique readable lines for console & state
        pretty = sorted(f"{d} at {t}" if d != "Unknown date" else t for d, t in slots)

        return {
            "content_hash": content_hash,
            "slots": sorted(list({(d or "Unknown date", t) for d, t in slots})),
            "pretty": pretty,
            "timestamp": now_iso(),
            "count": len(slots),
        }

    # ---------- Diff & notify ----------

    def _diff(self, prev, curr):
        prev_set = set(map(tuple, prev.get("slots", []))) if prev else set()
        curr_set = set(map(tuple, curr.get("slots", [])))
        added = curr_set - prev_set
        removed = prev_set - curr_set
        return added, removed

    def _cooldown_ok(self, location):
        last = self._last_notified.get(location)
        if not last:
            return True
        return datetime.now() - last >= timedelta(minutes=self.cooldown_minutes)

    def _mark_notified(self, location):
        self._last_notified[location] = datetime.now()

    def notify(self, subject, message, location_name, location_url):
        print("\n" + "="*60)
        print(f"🚨 {subject} — {location_name}")
        print("="*60)
        print(message)
        print("="*60 + "\n")

        self._email(subject, message, location_name)
        self._desktop(subject, message, location_name)
        self._webhook(f"{subject}\n{message}", location_name, location_url)

    def _email(self, subject, message, location_name):
        cfg = self.config.get("notifications", {}).get("email", {})
        if not cfg.get("enabled"):
            return
        pwd = cfg.get("password")
        if not pwd:
            print("   (email) skipped — no password provided")
            return
        try:
            msg = MIMEMultipart()
            msg["From"] = cfg["from_email"]
            msg["To"] = cfg["to_email"]
            msg["Subject"] = f"{subject} - {location_name}"
            body = f"{message}\n\nTime: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            msg.attach(MIMEText(body, "plain"))

            s = smtplib.SMTP(cfg["smtp_server"], cfg["smtp_port"])
            s.starttls()
            s.login(cfg["from_email"], pwd)
            s.send_message(msg)
            s.quit()
            print("   ✉️  Email sent")
        except Exception as e:
            print(f"   ✉️  Email error: {e}")

    def _desktop(self, title, message, location_name):
        cfg = self.config.get("notifications", {}).get("desktop", {})
        if not cfg.get("enabled"):
            return
        try:
            safe_message = message.replace('"', '\\"').replace("'", "\\'")
            safe_title = f"{title} - {location_name}".replace('"', '\\"')
            os.system(f"osascript -e 'display notification \"{safe_message}\" with title \"{safe_title}\" sound name \"Glass\"'")
            print("   🔔 Desktop notification sent")
        except Exception as e:
            print(f"   🔔 Desktop notify error: {e}")

    def _webhook(self, message, location_name, location_url):
        cfg = self.config.get("notifications", {}).get("webhook", {})
        if not cfg.get("enabled"):
            return
        try:
            resp = requests.post(cfg["url"], json={
                "text": message,
                "location": location_name,
                "url": location_url,
                "timestamp": now_iso()
            }, timeout=10)
            resp.raise_for_status()
            print("   🌐 Webhook notification sent")
        except Exception as e:
            print(f"   🌐 Webhook error: {e}")

    # ---------- Runner ----------

    def check_appointments(self):
        print("\n" + "-"*70)
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Checking {len(self.locations)} location(s)…")
        print("-"*70)

        new_states = {}

        for i, loc in enumerate(self.locations, 1):
            name = loc.get("name", f"Location {i}")
            url = loc["url"]
            addr = loc.get("address", "")

            print(f"\n📍 [{i}/{len(self.locations)}] {name}")
            if addr:
                print(f"    {addr}")

            html = self.fetch_page(url)
            if not html:
                print("    ❌ Failed to fetch page")
                # keep prior state
                if name in self.previous_states:
                    new_states[name] = self.previous_states[name]
                continue

            curr = self.parse_appointments(html)
            prev = self.previous_states.get(name, {})

            if curr["pretty"]:
                print(f"    📅 Slots found ({len(curr['pretty'])}):")
                for line in curr["pretty"][:15]:
                    print(f"       • {line}")
                if len(curr["pretty"]) > 15:
                    print(f"       … and {len(curr['pretty'])-15} more")
            else:
                print("    📅 No bookable slots detected (may truly be none, or DOM changed)")

            added, removed = self._diff(prev, curr)

            # Decide notifications
            if added or removed:
                # De-spam on huge contractions (DOM change)
                if len(prev.get("slots", [])) > 10 and len(curr.get("slots", [])) < 2 and removed and not added:
                    print("    ℹ️  Likely DOM/parse change; suppressing alert")
                else:
                    if self._cooldown_ok(name):
                        added_text = ", ".join(sorted([f"{d} at {t}" if d != 'Unknown date' else t for d, t in added])[:5])
                        removed_text = ", ".join(sorted([f"{d} at {t}" if d != 'Unknown date' else t for d, t in removed])[:5])
                        parts = []
                        if added:
                            parts.append(f"Added {len(added)}: {added_text}{'…' if len(added) > 5 else ''}")
                        if removed:
                            parts.append(f"Removed {len(removed)}: {removed_text}{'…' if len(removed) > 5 else ''}")
                        message = "\n".join(parts) if parts else "Appointment availability changed."
                        slots_preview = "\n".join([f"  • {s}" for s in curr["pretty"][:15]])
                        full = f"{message}\n\nCurrent slots:\n{slots_preview}"
                        self.notify("Costco Tire Appointment Update", full, name, url)
                        self._mark_notified(name)
                    else:
                        print("    🔕 In cooldown; not notifying")
            else:
                print("    ℹ️  No changes")

            new_states[name] = curr

        self.previous_states = new_states
        self._save_state(new_states)

    def run(self):
        print("="*70)
        print("🚗 Costco Tire Appointment Monitor (Hardened)")
        print("="*70)
        print(f"Monitoring {len(self.locations)} location(s):")
        for idx, loc in enumerate(self.locations, 1):
            print(f"  {idx}. {loc['name']}")
            if loc.get("address"):
                print(f"     {loc['address']}")
        print(f"\nCheck interval: {self.check_interval/60:.1f} minutes "
              f"({self.check_interval} seconds)")
        print(f"Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("Alert mode: Changes in concrete (date, time) slots")
        print("="*70 + "\nPress Ctrl+C to stop\n")

        try:
            while True:
                self.check_appointments()
                print("\n" + "-"*70)
                print(f"⏱️  Next check in {self.check_interval} seconds…")
                print("-"*70 + "\n")
                time.sleep(self.check_interval)
        except KeyboardInterrupt:
            print("\nStopped by user 👋")
        finally:
            if self.driver:
                try:
                    self.driver.quit()
                except Exception:
                    pass

if __name__ == "__main__":
    monitor = AppointmentMonitor()
    monitor.run()