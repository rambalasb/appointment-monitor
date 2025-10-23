#!/usr/bin/env python3
"""
Costco Tire Appointment Monitor
Monitors the Waitwhile booking page for new appointment availability
"""

import requests
import time
import json
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from bs4 import BeautifulSoup
import hashlib
import sys
import re

from selenium import webdriver
from selenium.webdriver.firefox.service import Service
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.firefox import GeckoDriverManager


class AppointmentMonitor:
    def __init__(self, config_path='config.json'):
        """Initialize the monitor with configuration"""
        self.config = self.load_config(config_path)
        self.locations = self.config.get('locations', [])
        # Support old config format with single booking_url
        if not self.locations and self.config.get('booking_url'):
            self.locations = [{'name': 'Default Location', 'url': self.config.get('booking_url'), 'address': ''}]
        self.check_interval = self.config.get('check_interval_minutes', 15) * 60
        self.state_file = 'state.json'
        self.previous_states = self.load_state()
        self.driver = None
        self._init_driver()

    def _init_driver(self):
        """Initialize Selenium WebDriver (Firefox by default; headful if HEADFUL=1)."""
        try:
            firefox_options = Options()
            headful = os.getenv("HEADFUL") == "1"
            if not headful:
                firefox_options.add_argument('--headless')  # Run in background

            firefox_options.add_argument('--width=1920')
            firefox_options.add_argument('--height=1080')

            # Faster page readiness for SPAs
            firefox_options.set_preference('webdriver.load.strategy', 'eager')

            # Set user agent
            firefox_options.set_preference(
                'general.useragent.override',
                'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            )

            # Reduce headless-detection flakes (best-effort)
            firefox_options.set_preference('dom.webdriver.enabled', False)
            firefox_options.set_preference('useAutomationExtension', False)

            # Suppress logging
            firefox_options.set_preference('devtools.console.stdout.content', False)

            service = Service(GeckoDriverManager().install())
            self.driver = webdriver.Firefox(service=service, options=firefox_options)
            self.driver.set_page_load_timeout(60)
            print(f"Browser automation initialized (Firefox, {'headful' if headful else 'headless'})")
        except Exception as e:
            print(f"Warning: Could not initialize browser automation: {e}")
            print("   Falling back to basic HTML parsing (may not work with JavaScript sites)")
            self.driver = None

    def load_config(self, config_path):
        """Load configuration from JSON file"""
        if not os.path.exists(config_path):
            print(f"Error: Config file '{config_path}' not found!")
            print("Please create a config.json file. See config.example.json for reference.")
            sys.exit(1)

        with open(config_path, 'r') as f:
            return json.load(f)

    def load_state(self):
        """Load previous state from file"""
        if os.path.exists(self.state_file):
            with open(self.state_file, 'r') as f:
                return json.load(f)
        return {}

    def save_state(self, state):
        """Save current state to file"""
        with open(self.state_file, 'w') as f:
            json.dump(state, f, indent=2)

    # ---------- Helpers for banners & debugging ----------

    def _dismiss_banners(self, timeout=8):
        """Close cookie/consent banners or modals that can block clicks."""
        if not self.driver:
            return
        selectors = [
            "//button[contains(., 'Accept')]",
            "//button[contains(., 'I agree')]",
            "//button[contains(., 'Allow all')]",
            "//button[contains(., 'Got it')]",
            "//button[contains(., 'OK')]",
            "//*[@role='dialog']//button[contains(., 'Accept') or contains(., 'OK') or contains(., 'Close')]",
        ]
        deadline = time.time() + timeout
        while time.time() < deadline:
            clicked = False
            for xp in selectors:
                try:
                    btn = WebDriverWait(self.driver, 2).until(
                        EC.element_to_be_clickable((By.XPATH, xp))
                    )
                    self.driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
                    try:
                        btn.click()
                    except Exception:
                        self.driver.execute_script("arguments[0].click();", btn)
                    clicked = True
                    time.sleep(0.2)
                except Exception:
                    continue
            if not clicked:
                break

    def _dump_debug(self, prefix="waitwhile"):
        """Save screenshot + HTML when stuck."""
        try:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            shot = f"{prefix}_{ts}.png"
            html = f"{prefix}_{ts}.html"
            self.driver.save_screenshot(shot)
            with open(html, "w", encoding="utf-8") as f:
                f.write(self.driver.page_source)
            print(f"   🖼  Saved screenshot: {shot}")
            print(f"   📄 Saved HTML:       {html}")
        except Exception as e:
            print(f"   ⚠️  Debug dump failed: {e}")

    # ---------- Fetch & parse ----------

    def fetch_page_selenium(self, url):
        """Fetch the booking page content using Selenium"""
        try:
            self.driver.get(url)

            # Wait for the page to load
            wait = WebDriverWait(self.driver, 20)
            wait.until(EC.presence_of_element_located((By.TAG_NAME, "body")))

            # NEW: try to close consent/cookie banners early
            self._dismiss_banners(timeout=6)

            # Try to navigate through the booking flow to get to appointment times
            try:
                # First try to find the specific "Seasonal Exchange 4 Wheels/8 Tires" service button
                try:
                    seasonal_exchange_button = self.driver.find_element(
                        By.XPATH, "//button[contains(@data-cy, 'toggle-seasonal-exchange-4-wheels/8-tires')]"
                    )
                    if seasonal_exchange_button:
                        print(f"   🔍 Found Seasonal Exchange 4 Wheels/8 Tires button, clicking...")
                        seasonal_exchange_button.click()
                        time.sleep(1.0)
                        self._dismiss_banners(timeout=2)
                except Exception:
                    # If specific button not found, try to find any service button
                    service_buttons = self.driver.find_elements(By.CSS_SELECTOR, 'button[data-cy*="toggle"]')
                    if service_buttons:
                        print(f"   🔍 Seasonal Exchange button not found, clicking first available service...")
                        try:
                            self.driver.execute_script("arguments[0].scrollIntoView({block:'center'});", service_buttons[0])
                            try:
                                service_buttons[0].click()
                            except Exception:
                                self.driver.execute_script("arguments[0].click();", service_buttons[0])
                        except Exception:
                            pass
                        time.sleep(0.8)
                        self._dismiss_banners(timeout=2)
                    else:
                        print(f"   🔍 No service buttons found, will try to parse current page")

                # Look for "Continue" or "Next" button
                continue_buttons = self.driver.find_elements(
                    By.XPATH, "//button[contains(text(), 'Continue') or contains(text(), 'Next') or contains(text(), 'Book')]"
                )
                if continue_buttons:
                    print(f"   🔍 Found continue button, clicking...")
                    try:
                        self.driver.execute_script("arguments[0].scrollIntoView({block:'center'});", continue_buttons[0])
                        try:
                            continue_buttons[0].click()
                        except Exception:
                            self.driver.execute_script("arguments[0].click();", continue_buttons[0])
                    except Exception:
                        pass
                    time.sleep(1.0)
                    self._dismiss_banners(timeout=2)

                    # Now we should be on the date/time selection page
                    print(f"   🔍 Now on date/time selection page")

                    # NEW: Wait for dates OR "No available times" OR skeletons to drop
                    print(f"   ⏳ Waiting for date buttons to load...")

                    def dates_or_no_times(d):
                        has_dates = len(d.find_elements(
                            By.XPATH,
                            "//button[@role='radio' and @aria-label] | //button[contains(@data-cy,'date') or contains(@data-cy,'slot')]"
                        )) > 0
                        has_no_times_msg = len(d.find_elements(
                            By.XPATH, "//*[contains(., 'No available times')]"
                        )) > 0
                        skeletons = d.find_elements(By.CSS_SELECTOR, ".react-loading-skeleton")
                        return has_dates or has_no_times_msg or len(skeletons) < 2

                    try:
                        WebDriverWait(self.driver, 25).until(dates_or_no_times)
                    except Exception:
                        print("   ⏳ Dates still not ready — dumping debug artifacts")
                        self._dump_debug(prefix="waitwhile_stall")
                        # one more banner sweep; sometimes the modal arrives late
                        self._dismiss_banners(timeout=4)

                    # Try to navigate to the actual date/time selection page
                    try:
                        # Look for calendar or date selection elements
                        date_elements = self.driver.find_elements(
                            By.CSS_SELECTOR, '[data-cy*="date"], [data-cy*="calendar"], [data-cy*="slot"]'
                        )
                        if date_elements:
                            print(f"   🔍 Found {len(date_elements)} date/time elements")

                        # Try clicking on multiple dates to reveal all time slots
                        # Look for date buttons with data-cy attributes first
                        date_buttons = self.driver.find_elements(By.XPATH, "//button[contains(@data-cy, 'dateslot')]")
                        print(f"   🔍 Found {len(date_buttons)} date buttons with data-cy='dateslot'")

                        # Also try to find date buttons by role and aria-label
                        if not date_buttons:
                            date_buttons = self.driver.find_elements(
                                By.XPATH, "//button[@role='radio' and @aria-label]"
                            )
                            print(f"   🔍 Found {len(date_buttons)} date buttons by role='radio'")

                        # Also try to find date buttons by gridcell role
                        if not date_buttons:
                            date_buttons = self.driver.find_elements(By.XPATH, "//div[@role='gridcell']//button")
                            print(f"   🔍 Found {len(date_buttons)} date buttons in gridcell")

                        if date_buttons:
                            print(f"   🔍 Clicking multiple dates to reveal all time slots...")
                            print(f"   Will click on {min(len(date_buttons), 15)} dates to find all time slots...")
                            for i, date_button in enumerate(date_buttons[:15]):  # Click up to 15 dates
                                try:
                                    button_text = date_button.text.strip()
                                    aria_label = date_button.get_attribute('aria-label')
                                    label_for_log = aria_label or button_text or f"date #{i+1}"
                                    print(f"   📅 Clicking date {i+1}: '{label_for_log}'")

                                    # Scroll to the button to make it clickable
                                    self.driver.execute_script("arguments[0].scrollIntoView({block:'center'});", date_button)
                                    time.sleep(0.2)

                                    # Try to click the button
                                    try:
                                        date_button.click()
                                    except Exception as click_error:
                                        # Try JavaScript click as fallback
                                        self.driver.execute_script("arguments[0].click();", date_button)

                                    # Wait for time slots or "No available times"
                                    def times_or_none(d):
                                        has_times = len(d.find_elements(
                                            By.XPATH, "//button[contains(text(), 'AM') or contains(text(), 'PM')]"
                                        )) > 0
                                        return has_times or "No available times" in d.page_source

                                    try:
                                        WebDriverWait(self.driver, 8).until(times_or_none)
                                    except Exception:
                                        pass

                                    # Look for time slots after clicking
                                    time_buttons = self.driver.find_elements(
                                        By.XPATH, "//button[contains(text(), 'AM') or contains(text(), 'PM')]"
                                    )
                                    print(f"   🕐 Found {len(time_buttons)} time buttons after clicking date {i+1}")

                                    # If no time buttons found, try alternative selectors
                                    if not time_buttons:
                                        alt_selectors = [
                                            "//button[contains(@class, 'time')]",
                                            "//div[contains(@class, 'time')]//button",
                                            "//*[contains(text(), 'AM') or contains(text(), 'PM')]",
                                            "//button[contains(@data-cy, 'time')]"
                                        ]
                                        for selector in alt_selectors:
                                            alt_buttons = self.driver.find_elements(By.XPATH, selector)
                                            if alt_buttons:
                                                print(f"   🔍 Found {len(alt_buttons)} time buttons with alternative selector: {selector}")
                                                time_buttons = alt_buttons
                                                break

                                    # Check if any time slots are actually clickable (not disabled)
                                    clickable_times = []
                                    for j, time_btn in enumerate(time_buttons):
                                        time_text = time_btn.text.strip()
                                        is_disabled_attr = time_btn.get_attribute('disabled')
                                        classes = (time_btn.get_attribute('class') or '')
                                        is_disabled = bool(is_disabled_attr) or ('disabled' in classes.lower())
                                        is_clickable = time_btn.is_enabled() and time_btn.is_displayed()
                                        print(f"      {j+1}. '{time_text}' (disabled: {is_disabled}, clickable: {is_clickable})")
                                        if is_clickable and not is_disabled and time_text:
                                            clickable_times.append(time_text)

                                    if not hasattr(self, '_dates_with_times'):
                                        self._dates_with_times = set()
                                    if not hasattr(self, '_times_for_dates'):
                                        self._times_for_dates = {}

                                    if clickable_times:
                                        self._dates_with_times.add(label_for_log)
                                        self._times_for_dates[label_for_log] = clickable_times
                                        print(f"   Stored {len(clickable_times)} time slots for {label_for_log}: {clickable_times}")
                                    else:
                                        # Check if there's a "No available times" message
                                        no_times_elements = self.driver.find_elements(
                                            By.XPATH, "//*[contains(text(), 'No available times') or contains(text(), 'unavailable') or contains(text(), 'No available times for Bays')]"
                                        )
                                        if no_times_elements:
                                            print(f"   ❌ Date '{label_for_log}' shows 'No available times'")
                                        else:
                                            print(f"   ℹ️  No clickable slots found for '{label_for_log}' (could be fully booked)")

                                except Exception as click_error:
                                    print(f"   ❌ Failed to click date {i+1}: {click_error}")
                                    continue
                        else:
                            print(f"   ⚠️  No date buttons found by common selectors")

                    except Exception as date_error:
                        print(f"   ⚠️  Date navigation failed: {date_error}")
                        print(f"   ℹ️  Will try to parse current page content")

                else:
                    print(f"   🔍 Continue/Next button not found; will parse current page as-is")

            except Exception as nav_error:
                print(f"   ⚠️  Navigation failed: {nav_error}")
                print(f"   ℹ️  Will try to parse current page content")

            # Get the final rendered HTML
            html_content = self.driver.page_source
            return html_content

        except Exception as e:
            print(f"   ❌ Error fetching page with Selenium: {e}")
            self._dump_debug(prefix="waitwhile_error")
            return None

    def fetch_page(self, url):
        """Fetch the booking page content"""
        if self.driver:
            return self.fetch_page_selenium(url)

        # Fallback to basic requests
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            }
            response = requests.get(url, headers=headers, timeout=30)
            response.raise_for_status()
            return response.text
        except requests.RequestException as e:
            print(f"   ❌ Error fetching page: {e}")
            return None

    def parse_appointments(self, html_content):
        """Parse available appointments from the page"""
        soup = BeautifulSoup(html_content, 'html.parser')

        # Create a hash of the relevant content to detect changes
        content_hash = hashlib.md5(html_content.encode()).hexdigest()

        times_found = set()
        inferred_selected_date = None

        # Look for both dates AND times in appointment slots
        # Pattern 1: Look for buttons that are clickable appointment slots (not disabled)
        all_buttons = soup.find_all('button')
        for button in all_buttons:
            # Skip disabled buttons
            if button.get('disabled') or 'disabled' in (button.get('class') or []):
                continue

            text = button.get_text(strip=True)

            # Include time slots (e.g., "9:00 AM", "2:30 PM")
            if re.match(r'^\d{1,2}:\d{2}\s*[APap][Mm]$', text):
                times_found.add(text)

        # Pattern 2: Look for buttons with specific data attributes that suggest appointment slots
        slot_buttons = soup.find_all('button', attrs={'data-cy': re.compile(r'(slot|time|appointment)', re.I)})
        for button in slot_buttons:
            text = button.get_text(strip=True)
            if re.match(r'^\d{1,2}:\d{2}\s*[APap][Mm]$', text):
                times_found.add(text)

        # Pattern 3: Look for buttons with classes that suggest they're appointment slots
        appointment_buttons = soup.find_all('button', class_=re.compile(r'(slot|time|appointment|available)', re.I))
        for button in appointment_buttons:
            text = button.get_text(strip=True)
            if re.match(r'^\d{1,2}:\d{2}\s*[APap][Mm]$', text):
                times_found.add(text)

        # Pattern 4: Look for divs/spans that might be clickable appointment slots
        clickable_elements = soup.find_all(['div', 'span'], attrs={'role': 'button'})
        for elem in clickable_elements:
            text = elem.get_text(strip=True)
            if re.match(r'^\d{1,2}:\d{2}\s*[APap][Mm]$', text):
                times_found.add(text)

        # Try to infer a selected/visible date from the HTML when Selenium mapping is unavailable
        try:
            # Common Waitwhile patterns: aria-label on selected date button, or data-cy attributes
            # 1) Selected radio button with aria-label
            selected_btn = soup.select_one("button[role='radio'][aria-label][aria-checked='true'], button[aria-label].selected, button[aria-label].is-selected")
            if selected_btn and selected_btn.get('aria-label'):
                inferred_selected_date = selected_btn.get('aria-label').strip()
            if not inferred_selected_date:
                # 2) Any visible date-like label near the time list
                possible = soup.find(string=re.compile(r"\b(Mon|Tue|Wed|Thu|Fri|Sat|Sun)\b", re.I))
                if possible and possible.parent:
                    text = possible.parent.get_text(" ", strip=True)
                    # keep it short and date-like
                    if 3 <= len(text) <= 40:
                        inferred_selected_date = text
        except Exception:
            pass

        # Only report appointments that have actual clickable time slots
        combined_appointments = set()

        # Use dates that actually have time slots (from Selenium navigation)
        if hasattr(self, '_dates_with_times') and self._dates_with_times:
            # For each date that has time slots, show it with its specific available times
            for date in sorted(self._dates_with_times):
                if hasattr(self, '_times_for_dates') and date in self._times_for_dates:
                    # Use the actual time slots found for this specific date
                    for time_str in sorted(self._times_for_dates[date]):
                        combined_appointments.add(f"{date} at {time_str}")
                else:
                    # Fallback to general times if specific times not found
                    for time_str in sorted(times_found):
                        combined_appointments.add(f"{date} at {time_str}")
        elif times_found:
            # If only times found, try to pair with inferred date if available
            if inferred_selected_date:
                for time_str in sorted(times_found):
                    combined_appointments.add(f"{inferred_selected_date} at {time_str}")
            else:
                combined_appointments.update(times_found)
        # Don't report dates without time slots - they're not bookable appointments

        # Clean up and filter appointments
        cleaned_appointments = set()
        for appointment in combined_appointments:
            appointment = appointment.strip()
            # Filter out very long strings or strings without digits
            if appointment and len(appointment) < 100 and any(char.isdigit() for char in appointment):
                # Skip common false positives
                if not any(skip in appointment.lower() for skip in ['cookie', 'version', 'privacy', 'terms']):
                    cleaned_appointments.add(appointment)

        appointments_list = sorted(list(cleaned_appointments))

        return {
            'content_hash': content_hash,
            'appointments': list(cleaned_appointments),
            'dates': appointments_list,
            'timestamp': datetime.now().isoformat(),
            'appointment_count': len(cleaned_appointments),
            'dates_count': len(appointments_list)
        }

    def detect_changes(self, location_name, current_state, previous_state):
        """Detect if appointments have changed"""
        if not previous_state:
            # First run - don't alert, just save state
            return False, "Initial check - monitoring started (no alert)"

        # Compare the full appointment data (both dates and times)
        prev_dates = set(previous_state.get('dates', []))
        curr_dates = set(current_state.get('dates', []))

        # Only alert if there are actual changes in available appointments
        if prev_dates != curr_dates:
            new_appointments = curr_dates - prev_dates
            removed_appointments = prev_dates - curr_dates

            # Don't alert if we're going from many appointments to fewer appointments
            # This prevents false "removed" notifications when we improve the parsing
            if len(prev_dates) > 10 and len(curr_dates) < 5:
                return False, "Improved parsing accuracy - no alert"

            # Only send alerts if there are actual bookable appointments
            if new_appointments and removed_appointments:
                return True, f"Appointments changed! Added {len(new_appointments)}, Removed {len(removed_appointments)}"
            elif new_appointments:
                # Only alert if the new appointments are actual time slots (not just dates)
                actual_time_slots = [apt for apt in new_appointments if ' at ' in apt or re.match(r'^\d{1,2}:\d{2}\s*[APap][Mm]$', apt)]
                if actual_time_slots:
                    new_appointments_str = ", ".join(sorted(list(actual_time_slots))[:3])
                    return True, f"🎉 New appointments available! {new_appointments_str}{'...' if len(actual_time_slots) > 3 else ''}"
                else:
                    return False, "Found dates but no actual time slots - no alert"
            elif removed_appointments:
                return True, f"⚠️ Appointments taken! Removed {len(removed_appointments)} slot(s)"

        return False, "No changes detected"

    def send_email_notification(self, subject, message, location_name, location_url):
        """Send email notification"""
        email_config = self.config.get('notifications', {}).get('email', {})

        if not email_config.get('enabled', False):
            return

        try:
            msg = MIMEMultipart()
            msg['From'] = email_config['from_email']
            msg['To'] = email_config['to_email']
            msg['Subject'] = f"{subject} - {location_name}"

            body = f"""
{message}

Location: {location_name}
Booking URL: {location_url}

Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

---
Costco Tire Appointment Monitor
            """

            msg.attach(MIMEText(body, 'plain'))

            server = smtplib.SMTP(email_config['smtp_server'], email_config['smtp_port'])
            server.starttls()
            server.login(email_config['from_email'], email_config['password'])
            server.send_message(msg)
            server.quit()

            print(f"   ✉️  Email sent to {email_config['to_email']}")
        except Exception as e:
            print(f"   ❌ Error sending email: {e}")

    def send_desktop_notification(self, title, message, location_name):
        """Send desktop notification"""
        notif_config = self.config.get('notifications', {}).get('desktop', {})

        if not notif_config.get('enabled', False):
            return

        try:
            # macOS notification - escape quotes in message
            safe_message = message.replace('"', '\\"').replace("'", "\\'")
            safe_title = f"{title} - {location_name}".replace('"', '\\"')
            os.system(f"""
                osascript -e 'display notification "{safe_message}" with title "{safe_title}" sound name "Glass"'
            """)
            print(f"   🔔 Desktop notification sent")
        except Exception as e:
            print(f"   ❌ Error sending desktop notification: {e}")

    def send_webhook_notification(self, message, location_name, location_url):
        """Send webhook notification (Slack, Discord, etc.)"""
        webhook_config = self.config.get('notifications', {}).get('webhook', {})

        if not webhook_config.get('enabled', False):
            return

        try:
            payload = {
                'text': message,
                'location': location_name,
                'url': location_url,
                'timestamp': datetime.now().isoformat()
            }

            response = requests.post(webhook_config['url'], json=payload, timeout=10)
            response.raise_for_status()
            print(f"   🌐 Webhook notification sent")
        except Exception as e:
            print(f"   ❌ Error sending webhook: {e}")

    def notify(self, subject, message, location_name, location_url):
        """Send notifications via all enabled channels"""
        print(f"\n   {'='*50}")
        print(f"   🚨 ALERT: {subject}")
        print(f"   {'='*50}")
        print(f"   {message}")
        print(f"   {'='*50}\n")

        self.send_email_notification(subject, message, location_name, location_url)
        self.send_desktop_notification(subject, message, location_name)
        self.send_webhook_notification(f"{subject}\n{message}", location_name, location_url)

    def check_appointments(self):
        """Check for appointment updates across all locations"""
        print(f"\n{'='*70}")
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Checking {len(self.locations)} location(s)...")
        print(f"{'='*70}")

        new_states = {}

        for idx, location in enumerate(self.locations, 1):
            location_name = location.get('name', 'Unknown Location')
            location_url = location.get('url')
            location_address = location.get('address', '')

            print(f"\n📍 [{idx}/{len(self.locations)}] {location_name}")
            if location_address:
                print(f"   {location_address}")

            html_content = self.fetch_page(location_url)
            if not html_content:
                print(f"   ❌ Failed to fetch page. Skipping...")
                # Keep previous state for this location
                if location_name in self.previous_states:
                    new_states[location_name] = self.previous_states[location_name]
                continue

            current_state = self.parse_appointments(html_content)
            previous_state = self.previous_states.get(location_name, {})

            # Display available dates/times
            dates = current_state.get('dates', [])
            if dates:
                print(f"   📅 Available dates/times found ({len(dates)}):")
                for date in dates[:15]:  # Show first 15
                    print(f"      • {date}")
                if len(dates) > 15:
                    print(f"      ... and {len(dates) - 15} more")
            else:
                print(f"   📅 No appointment dates detected")
                # If we only found bare times, hint that date couldn't be inferred
                # Note: current_state['appointments'] may contain bare times if date inference failed
                bare_times = [t for t in current_state.get('appointments', []) if re.match(r'^\d{1,2}:\d{2}\s*[APap][Mm]$', t)]
                if bare_times:
                    print(f"   ℹ️  Found times without date context (page hid date label)")
                else:
                    print(f"   ℹ️  (This might mean no appointments are available, or the page structure changed)")

            # Detect changes
            changed, change_message = self.detect_changes(location_name, current_state, previous_state)

            if changed:
                # Include dates in notification
                dates_text = "\n".join([f"  • {d}" for d in dates[:15]])
                full_message = f"{change_message}\n\nAvailable dates:\n{dates_text}"
                if len(dates) > 15:
                    full_message += f"\n  ... and {len(dates) - 15} more"

                self.notify("Costco Tire Appointment Update", full_message, location_name, location_url)
                print(f"   {change_message}")
            else:
                print(f"   ℹ️  {change_message}")

            # Save current state for this location
            new_states[location_name] = current_state

        # Save all states
        self.previous_states = new_states
        self.save_state(new_states)

    def cleanup(self):
        """Clean up resources"""
        if self.driver:
            try:
                self.driver.quit()
                print("\nBrowser automation cleaned up")
            except Exception:
                pass

    def run(self):
        """Main monitoring loop"""
        print("="*70)
        print("🚗 Costco Tire Appointment Monitor (Enhanced with Browser Automation)")
        print("="*70)
        print(f"Monitoring {len(self.locations)} location(s):")
        for idx, location in enumerate(self.locations, 1):
            print(f"  {idx}. {location['name']}")
            if location.get('address'):
                print(f"     {location['address']}")
        print(f"\nCheck interval: {self.check_interval/60} minutes ({int(self.check_interval)} seconds)")
        print(f"Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"Alert mode: Only on date changes")
        print("="*70)
        print("\nPress Ctrl+C to stop\n")

        try:
            while True:
                self.check_appointments()
                print(f"\n{'='*70}")
                print(f"⏱️  Next check in {int(self.check_interval)} seconds...")
                print(f"{'='*70}\n")
                time.sleep(self.check_interval)
        except KeyboardInterrupt:
            print("\n\n" + "="*70)
            print("Monitoring stopped by user.")
            print("Goodbye! 👋")
            print("="*70)
        finally:
            self.cleanup()


if __name__ == "__main__":
    monitor = AppointmentMonitor()
    monitor.run()