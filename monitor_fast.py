#!/usr/bin/env python3
"""
Fast Costco Tire Appointment Monitor
- Jumps using "Go to next available date" until a day with time slots is found
"""

import os
import re
import sys
import json
import time
from datetime import datetime

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.firefox.service import Service as FirefoxService
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from webdriver_manager.firefox import GeckoDriverManager


TIME_RE = re.compile(r"\b(1[0-2]|0?[1-9]):[0-5]\d\s*[APap][Mm]\b")


def click_seasonal_exchange_service(driver, sleep_after: float = 0.5) -> str:
    """
    On the Select Services step, prefer "Seasonal Exchange 4 Wheels/8 Tires".
    Returns 'seasonal', 'fallback', or 'none'.
    """
    selectors = [
        (By.XPATH, "//button[contains(@data-cy, 'toggle-seasonal-exchange-4-wheels')]"),
        (By.XPATH, "//button[contains(@data-cy, 'toggle') and contains(., 'Seasonal Exchange 4 Wheels')]"),
        (
            By.XPATH,
            "//*[self::button or @role='button'][contains(., 'Seasonal Exchange 4 Wheels/8 Tires')]",
        ),
        (By.XPATH, "//button[contains(., 'Seasonal Exchange 4 Wheels/8 Tires')]"),
    ]

    def _click(el) -> None:
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
        try:
            el.click()
        except Exception:
            driver.execute_script("arguments[0].click();", el)

    for by, sel in selectors:
        for el in driver.find_elements(by, sel):
            try:
                if not el.is_displayed():
                    continue
                _click(el)
                time.sleep(sleep_after)
                return "seasonal"
            except Exception:
                continue

    toggles = driver.find_elements(By.CSS_SELECTOR, "button[data-cy*='toggle']")
    if not toggles:
        return "none"
    try:
        _click(toggles[0])
        time.sleep(sleep_after)
        return "fallback"
    except Exception:
        return "none"


class FastMonitor:
    _DATEISH = re.compile(
        r"\b(Mon|Tue|Wed|Thu|Fri|Sat|Sun|Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\b"
        r"|(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\.?\s+\d{1,2}",
        re.I,
    )

    def __init__(self, config_path: str = "config.json") -> None:
        self.config = self._load_config(config_path)
        self.locations = self.config.get("locations", [])
        if not self.locations and self.config.get("booking_url"):
            self.locations = [{"name": "Default Location", "url": self.config["booking_url"], "address": ""}]
        self.check_interval = int(self.config.get("check_interval_minutes", 15) * 60)
        self.driver = None
        self._init_driver()

    def _load_config(self, path: str) -> dict:
        if not os.path.exists(path):
            print(f"Config '{path}' not found. Please create it.")
            sys.exit(1)
        with open(path, "r") as f:
            return json.load(f)

    def _init_driver(self) -> None:
        """Prefer Firefox; fall back to Chrome."""
        try:
            ff_opts = webdriver.FirefoxOptions()
            ff_opts.add_argument("--headless")
            service = FirefoxService(GeckoDriverManager().install())
            self.driver = webdriver.Firefox(service=service, options=ff_opts)
            print("Browser automation initialized (Firefox)")
            return
        except Exception as e:
            print(f"Firefox init failed: {e}. Falling back to Chrome…")

        try:
            chrome_opts = webdriver.ChromeOptions()
            chrome_opts.add_argument("--headless=new")
            chrome_opts.add_argument("--disable-gpu")
            chrome_opts.add_argument("--no-sandbox")
            chrome_opts.add_argument("--window-size=1920,1080")
            service = ChromeService(ChromeDriverManager().install())
            self.driver = webdriver.Chrome(service=service, options=chrome_opts)
            print("Browser automation initialized (Chrome)")
        except Exception as e:
            print(f"Warning: could not initialize any browser: {e}")
            self.driver = None

    # ---------- Core flow ----------

    def _calendar_date_buttons_visible(self) -> bool:
        return bool(
            self.driver.find_elements(
                By.XPATH,
                "//button[contains(@data-cy,'dateslot')]",
            )
        )

    def _go_to_date_page(self) -> None:
        """Attempt to reach the select date/time page by clicking common CTAs."""
        if not self.driver:
            return
        # Deep links like /time often show the calendar after the SPA paints — wait before acting.
        try:
            WebDriverWait(self.driver, 20).until(
                lambda d: self._calendar_date_buttons_visible()
                or len(d.find_elements(By.XPATH, "//button[contains(@data-cy,'toggle')]")) > 0
                or "Select date" in (d.page_source or "")
                or "Select Services" in (d.page_source or "")
            )
        except Exception:
            pass

        if self._calendar_date_buttons_visible():
            return

        # Select Services: only when we're not already on the calendar
        try:
            click_seasonal_exchange_service(self.driver, sleep_after=0.4)
        except Exception:
            pass

        if self._calendar_date_buttons_visible():
            return

        # Continue / Next / Book — only if calendar still not visible
        try:
            for xp in [
                "//button[contains(., 'Continue')]",
                "//button[contains(., 'Next')]",
                "//button[contains(., 'Book')]",
            ]:
                if self._calendar_date_buttons_visible():
                    break
                try:
                    btn = WebDriverWait(self.driver, 4).until(EC.element_to_be_clickable((By.XPATH, xp)))
                    self.driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
                    try:
                        btn.click()
                    except Exception:
                        self.driver.execute_script("arguments[0].click();", btn)
                    time.sleep(0.6)
                    break
                except Exception:
                    continue
        except Exception:
            pass

    def _extract_selected_date(self) -> str:
        """Return a readable selected date label if visible, else empty string."""
        try:
            # aria-checked date button with aria-label has the date text
            btn = self.driver.find_element(By.XPATH, "//button[@role='radio' and @aria-checked='true' and @aria-label]")
            label = btn.get_attribute("aria-label")
            return (label or "").strip()
        except Exception:
            pass

        # Fallback: find a header-like element that contains a weekday + month (e.g., "Friday, Nov 21")
        try:
            months = [
                "Jan", "Feb", "Mar", "Apr", "May", "Jun",
                "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
                "January", "February", "March", "April", "June", "July",
                "August", "September", "October", "November", "December"
            ]
            days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun",
                    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
            candidates = self.driver.find_elements(By.XPATH, "//h1|//h2|//h3|//h4|//h5|//h6|//div|//span")
            for el in candidates[:400]:  # scan a reasonable number of nodes
                text = (el.text or "").strip()
                if not text:
                    continue
                if any(d in text for d in days) and any(m in text for m in months):
                    # keep it concise (avoid dumping long paragraphs)
                    if 4 <= len(text) <= 80:
                        return text
        except Exception:
            pass

        return ""

    def _extract_times(self) -> list:
        """Return list of clickable time strings on the page."""
        times = []
        try:
            for b in self.driver.find_elements(By.TAG_NAME, "button"):
                txt = " ".join((b.text or "").split())
                if not txt or not TIME_RE.fullmatch(txt):
                    continue
                if b.get_attribute("disabled") is not None:
                    continue
                if (b.get_attribute("aria-disabled") or "").lower() == "true":
                    continue
                cls = (b.get_attribute("class") or "").lower()
                if "disabled" in cls:
                    continue
                try:
                    if not b.is_displayed():
                        continue
                except Exception:
                    continue
                times.append(txt)
        except Exception:
            pass
        return self._sort_times_unique(times)

    @staticmethod
    def _sort_times_unique(times: list) -> list:
        def key(s: str):
            try:
                return datetime.strptime(s.strip(), "%I:%M %p")
            except ValueError:
                return datetime.min

        return sorted(list(dict.fromkeys(times)), key=key)

    def _click_next_available(self) -> bool:
        """Click the 'Go to next available date' link if present. Return True if clicked."""
        try:
            xp = "//a[contains(., 'Go to next available date')] | //button[contains(., 'Go to next available date')]"
            link = WebDriverWait(self.driver, 5).until(EC.element_to_be_clickable((By.XPATH, xp)))
            self.driver.execute_script("arguments[0].scrollIntoView({block:'center'});", link)
            try:
                link.click()
            except Exception:
                self.driver.execute_script("arguments[0].click();", link)
            # wait for either new date or times
            WebDriverWait(self.driver, 15).until(
                lambda d: bool(self._extract_times()) or (self._extract_selected_date() != "")
            )
            return True
        except Exception:
            return False

    def _find_date_buttons(self) -> list:
        """Return calendar day buttons — prefer Waitwhile dateslot; avoid unrelated role=radio."""
        try:
            by_cy = self.driver.find_elements(By.XPATH, "//button[contains(@data-cy,'dateslot')]")
            if by_cy:
                return by_cy[:14]

            grid = self.driver.find_elements(By.XPATH, "//div[@role='gridcell']//button")
            if grid:
                return grid[:14]

            radios = self.driver.find_elements(By.XPATH, "//button[@role='radio' and @aria-label]")
            date_like = []
            for b in radios:
                lab = f"{b.get_attribute('aria-label') or ''} {b.text or ''}"
                if self._DATEISH.search(lab):
                    date_like.append(b)
            if date_like:
                return date_like[:14]
            return radios[:14]
        except Exception:
            pass
        return []

    def _click_date_and_wait(self, btn) -> None:
        """Click a date button and wait for times or a 'No available times' state to resolve."""
        try:
            self.driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
            try:
                btn.click()
            except Exception:
                self.driver.execute_script("arguments[0].click();", btn)
            # Wait for either times, a next-available link, or a 'No available times' message
            WebDriverWait(self.driver, 18).until(
                lambda d: bool(self._extract_times())
                or len(d.find_elements(By.XPATH, "//*[contains(., 'No available times')]") ) > 0
                or len(d.find_elements(By.XPATH, "//a[contains(., 'Go to next available date')] | //button[contains(., 'Go to next available date')]") ) > 0
            )
        except Exception:
            pass

    def find_next_slots(self, url: str) -> dict:
        """Open location URL, jump via 'next available date' until times appear or attempts exhausted."""
        if not self.driver:
            return {"date": "", "times": []}

        self.driver.get(url)
        WebDriverWait(self.driver, 20).until(EC.presence_of_element_located((By.TAG_NAME, "body")))

        # Try to get to date page quickly
        self._go_to_date_page()
        try:
            WebDriverWait(self.driver, 25).until(
                lambda d: len(d.find_elements(By.XPATH, "//button[contains(@data-cy,'dateslot')]")) > 0
                or len(d.find_elements(By.XPATH, "//button[@role='radio' and @aria-label]")) > 0,
            )
        except Exception:
            pass

        # Strategy:
        # 1) Try the currently selected date, then a handful of visible dates.
        # 2) If a clicked date shows no times, follow the "next available" link if present.
        tried_next = 0

        # Build candidate date buttons (ensure selected first if present)
        candidates = self._find_date_buttons()
        # Move selected (aria-checked=true) to front if we can detect it
        try:
            selected = self.driver.find_elements(By.XPATH, "//button[@role='radio' and @aria-checked='true']")
            if selected:
                selected_btn = selected[0]
                # Reorder candidates so selected appears first
                candidates = [selected_btn] + [b for b in candidates if b != selected_btn]
        except Exception:
            pass

        for btn in candidates:
            # Click a date
            self._click_date_and_wait(btn)
            times = self._extract_times()
            if times:
                return {"date": self._extract_selected_date(), "times": times}

            # If no times, try following next-available link a few times
            inner_hops = 0
            while inner_hops < 10 and self._click_next_available():
                tried_next += 1
                inner_hops += 1
                times = self._extract_times()
                if times:
                    return {"date": self._extract_selected_date(), "times": times}

        # Last check for times even if we gave up on link
        return {"date": self._extract_selected_date(), "times": self._extract_times()}

    # ---------- Runner ----------

    def run(self) -> None:
        print("=" * 70)
        print("🚗 Costco Tire Appointment Monitor (Fast Next-Date Mode)")
        print("=" * 70)
        print(f"Monitoring {len(self.locations)} location(s):")
        for idx, loc in enumerate(self.locations, 1):
            print(f"  {idx}. {loc['name']}")
            if loc.get("address"):
                print(f"     {loc['address']}")
        print(f"\nStarted at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print("=" * 70)

        for i, loc in enumerate(self.locations, 1):
            name = loc.get("name", f"Location {i}")
            url = loc["url"]
            print(f"\n📍 [{i}/{len(self.locations)}] {name}")
            result = self.find_next_slots(url)
            date_label = result.get("date", "").strip()
            times = result.get("times", [])
            if times:
                if date_label:
                    print(f"   📅 {date_label}")
                print(f"   🕐 Available times ({len(times)}):")
                for t in times:
                    print(f"      • {t}")
            else:
                print("   ℹ️  No times found via next-available navigation")

        print("\nDone.")


if __name__ == "__main__":
    monitor = FastMonitor()
    monitor.run()


