# einvoice_bot.py
# Scrapes e-invoice enabled/disabled status from:
#   https://einvoice1.gst.gov.in/Others/EinvEnabled
#
# Field extracted:
#   einvoice_status  ->  "NOT ENABLED for E-Invoice" | "ENABLED for E-Invoice"
#                        (exact text from the page)
#
# Captcha approach: same as the working GSTPortalBot —
#   HSV blue extraction (hue 85-145) + horizontal line removal +
#   CC area filter + dilate + 2x resize + 4-vote ddddocr
#
# XPATHs: taken directly from the proven GSTPortalBot script.
#
# Screenshots saved to C:\material_inward\gst_screenshots\einvoice\
# Auto-deleted after 24 h on each startup.
#
# Usage:
#   from services.einvoice_bot import EInvoiceBot
#   bot = EInvoiceBot(headless=True)
#   try:
#       result = bot.search("27AAACH3583Q1Z0")
#       print(result["einvoice_status"])   # "NOT ENABLED for E-Invoice"
#       print(result["screenshot"])        # C:\material_inward\gst_screenshots\...
#   finally:
#       bot.quit()

import re
import os
import glob
import time
import logging
import numpy as np
import cv2
import ddddocr
from collections import Counter
from datetime import datetime

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.edge.options import Options
from selenium.common.exceptions import TimeoutException, WebDriverException

from services.edge_driver_check import ensure_matching_edge_driver

logger = logging.getLogger(__name__)

SCREENSHOT_DIR = r"C:\Users\ctn_suresh\Agents\material_inward_FINAL (2)\material_inward_FINAL\material_inward\gst_screenshots\einvoice"
SCREENSHOT_TTL = 10 * 24 * 3600   # 10 days in seconds

# Persistent Edge profile -- was previously unset, so every run got a fresh
# temp profile that Selenium deletes on driver.quit(). That meant the native
# Windows/Edge "wants to Access other apps and services on this device"
# permission prompt (seen interfering with the bot -- it sits on top of the
# page as a browser-chrome dialog Selenium can't dismiss via normal DOM
# calls) could never be permanently resolved: even if someone clicked
# Allow/Block on the physical screen, that choice vanished with the profile
# and the prompt just came back on the next run. Pointing at a real,
# reused folder means a single manual Allow/Block click here sticks for
# every future run against this same site.
EDGE_PROFILE_DIR = r"C:\Users\ctn_suresh\Agents\material_inward_FINAL (2)\material_inward_FINAL\material_inward\gst_edge_profile\einvoice"


class EInvoiceBot:
    # ── Confirmed working URL from GSTPortalBot ────────────────────────────────
    URL      = "https://einvoice1.gst.gov.in/Others/EinvEnabled"
    MAX_CAP  = 10
    CAP_MIN  = 4    # site 1 captchas are 4-7 chars (not strictly 6)

    # ── Exact XPATHs from the proven GSTPortalBot script ─────────────────────
    GSTIN_INPUT_XPATHS = [
        "//input[@id='txtGstin']",
        "//input[@name='gSTINDetail.Gstin']",
    ]
    CAPTCHA_IMG_XPATHS = [
        "//img[@id='captcha_image']",
        "//img[contains(@src,'get-captcha')]",
    ]
    CAPTCHA_INPUT_XPATHS = [
        "//input[@id='txtCaptchaCode']",
        "//input[@name='CaptchaCode']",
    ]
    GO_BTN_XPATHS = [
        "//button[@type='submit' and contains(@class,'btn-primary')]",
        "//button[normalize-space()='Go']",
    ]
    REFRESH_XPATHS = [
        "//a[@id='captcha_reload']",
        "//i[contains(@class,'fa-sync-alt')]/..",
    ]

    # ── Init ──────────────────────────────────────────────────────────────────
    def __init__(self, headless: bool = True, chromedriver_path: str = None):
        self._cleanup_old_screenshots()
        ensure_matching_edge_driver(logger)

        os.makedirs(EDGE_PROFILE_DIR, exist_ok=True)

        opts = Options()
        if headless:
            opts.add_argument("--headless=new")
        opts.add_argument("--disable-blink-features=AutomationControlled")
        opts.add_argument("--no-sandbox")
        opts.add_argument("--disable-dev-shm-usage")
        opts.add_argument("--window-size=1280,800")
        opts.add_argument(f"--user-data-dir={EDGE_PROFILE_DIR}")
        opts.add_experimental_option("excludeSwitches", ["enable-automation"])
        opts.add_experimental_option("useAutomationExtension", False)

        self._driver = webdriver.Edge(options=opts)

        self._driver.execute_cdp_cmd(
            "Page.addScriptToEvaluateOnNewDocument",
            {"source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"},
        )
        self._wait = WebDriverWait(self._driver, 20)
        self._ocr  = ddddocr.DdddOcr(show_ad=False)
        self._ocrb = ddddocr.DdddOcr(show_ad=False, beta=True)
        logger.info("EInvoiceBot initialised")

    def quit(self):
        try:
            self._driver.quit()
        except Exception:
            pass

    # ── Public API ─────────────────────────────────────────────────────────────
    def search(self, gstin: str) -> dict:
        """
        Returns:
        {
          "gstin":           str,
          "einvoice_status": str,   # "NOT ENABLED for E-Invoice" / "ENABLED for E-Invoice"
          "screenshot":      str,   # absolute path, or ""
          "error":           str | None
        }
        """
        result = {
            "gstin":           gstin,
            "einvoice_status": "",
            "screenshot":      "",
            "error":           None,
        }

        try:
            passed = self._solve_captcha_and_submit(gstin)
            if not passed:
                # If most attempts failed on connectivity specifically (not
                # elements just missing from an otherwise-loaded page), say so
                # -- points at the portal/network rather than our scraping logic.
                conn_fails = getattr(self, "_last_connectivity_failures", 0)
                if conn_fails >= (self.MAX_CAP // 2):
                    result["error"] = (
                        f"Captcha bypass failed after {self.MAX_CAP} attempts -- {conn_fails} of them "
                        "failed on connectivity/timeout errors specifically. The E-Invoice portal is "
                        "likely slow, down, or unreachable rather than this being a data problem."
                    )
                else:
                    result["error"] = "Captcha bypass failed after max retries"
                return result

            self._scrape_result(result)
            result["screenshot"] = self._save_screenshot(gstin)

        except (TimeoutException, WebDriverException) as exc:
            # See the matching comment in taxpayer_search_bot.py's search() --
            # same reasoning: a connectivity/portal-availability failure is
            # categorically different from a bug in our own scraping logic,
            # and is worth labelling distinctly so it's obvious from the error
            # text alone that this likely isn't a data or code problem.
            result["error"] = (
                f"E-Invoice portal appears unreachable or timed out (connectivity "
                f"issue, not a data problem) — will retry automatically: {exc}"
            )
            logger.error(f"[EInvoiceBot] search({gstin}) connectivity failure: {exc}", exc_info=True)
        except Exception as exc:
            result["error"] = str(exc)
            logger.error(f"[EInvoiceBot] search({gstin}): {exc}", exc_info=True)

        return result

    # ── Captcha flow ──────────────────────────────────────────────────────────
    def _solve_captcha_and_submit(self, gstin: str) -> bool:
        self._driver.get(self.URL)
        time.sleep(2)

        self._last_connectivity_failures = 0   # exposed to search() for a
                                                 # clearer error message if this
                                                 # loop exhausts its attempts --
                                                 # see the matching counter/
                                                 # comment in taxpayer_search_bot.py
        for attempt in range(1, self.MAX_CAP + 1):
            logger.info(f"[cap] attempt {attempt}/{self.MAX_CAP}")
            try:
                # Fill GSTIN
                gstin_el = self._wait.until(
                    lambda d: self._find(self.GSTIN_INPUT_XPATHS)
                )
                if not gstin_el:
                    raise RuntimeError("GSTIN input not found")
                gstin_el.clear()
                gstin_el.send_keys(gstin)
                time.sleep(0.4)

                # Grab captcha image bytes (screenshot_as_png from the element)
                img_bytes = self._get_captcha_bytes()
                solved    = self._solve_captcha(img_bytes)
                logger.info(f"[cap] solved='{solved}'")

                # DEBUG: save raw + preprocessed captcha images for inspection
                try:
                    dbg_dir = os.path.join(SCREENSHOT_DIR, "captcha_debug")
                    os.makedirs(dbg_dir, exist_ok=True)
                    ts = datetime.now().strftime("%H%M%S_%f")
                    with open(os.path.join(dbg_dir, f"raw_{ts}_{solved}.png"), "wb") as _f:
                        _f.write(img_bytes)
                    preprocessed = self._preprocess(img_bytes)
                    with open(os.path.join(dbg_dir, f"pre_{ts}_{solved}.png"), "wb") as _f:
                        _f.write(preprocessed)
                except Exception:
                    pass

                if not (self.CAP_MIN <= len(solved) <= 7):
                    logger.warning(f"[cap] '{solved}' bad length — refresh")
                    self._refresh_captcha()
                    continue

                # Fill captcha input
                cap_el = self._find(self.CAPTCHA_INPUT_XPATHS)
                if not cap_el:
                    raise RuntimeError("Captcha input not found")
                cap_el.clear()
                cap_el.send_keys(solved)
                time.sleep(0.3)

                # Click Go
                go_el = self._find(self.GO_BTN_XPATHS)
                if not go_el:
                    raise RuntimeError("Go button not found")
                go_el.click()
                time.sleep(2.5)

                body = self._driver.find_element(By.TAG_NAME, "body").text

                if "invalid captcha" in body.lower():
                    logger.warning(f"[cap] attempt {attempt}: wrong captcha")
                    self._refresh_captcha()
                    continue

                if any(x in body.lower() for x in
                       ("e-invoice", "einvoice", "enabled",
                        "not enabled", "aato", "gstin details")):
                    logger.info(f"[cap] attempt {attempt}: PASSED")
                    return True

                # not found / no records is also a valid response (captcha passed)
                if any(x in body.lower() for x in ("not found", "no records")):
                    logger.info(f"[cap] attempt {attempt}: PASSED (no records)")
                    return True

                logger.warning(f"[cap] attempt {attempt}: unclear response")
                self._refresh_captcha()

            except (TimeoutException, WebDriverException) as exc:
                self._last_connectivity_failures += 1
                logger.error(f"[cap] attempt {attempt} connectivity error: {exc}")
                time.sleep(2)
            except RuntimeError as exc:
                logger.error(f"[cap] attempt {attempt} failed: {exc}")
                time.sleep(2)
            except Exception as exc:
                logger.error(f"[cap] attempt {attempt} error: {exc}", exc_info=True)
                time.sleep(2)

        return False

    def _get_captcha_bytes(self) -> bytes:
        for xpath in self.CAPTCHA_IMG_XPATHS:
            try:
                el = self._driver.find_element(By.XPATH, xpath)
                if el.is_displayed():
                    return el.screenshot_as_png
            except Exception:
                continue
        raise RuntimeError("Captcha image not found on page")

    def _refresh_captcha(self):
        el = self._find(self.REFRESH_XPATHS)
        if el:
            try:
                self._driver.execute_script("arguments[0].click();", el)
                time.sleep(1.0)
                return
            except Exception:
                pass
        self._driver.get(self.URL)
        time.sleep(2)

    # ── Result scraping ───────────────────────────────────────────────────────
    def _scrape_result(self, result: dict):
        """
        Scrape the e-invoice status text from the result page.
        The page shows:
          "This Taxpayer is NOT ENABLED for E-Invoice"
          OR
          "This Taxpayer is ENABLED for E-Invoice"
        """
        body_text = self._driver.find_element(By.TAG_NAME, "body").text

        # Primary patterns — match actual portal text variants
        patterns = [
            r"This Taxpayer is (NOT ENABLED for E-Invoice)",
            r"This Taxpayer is (ENABLED for E-Invoice)",
            r"(NOT ENABLED for E-Invoice)",
            r"(ENABLED for E-Invoice)",
            r"(Not Enabled for E-Invoice)",
            r"(Enabled for E-Invoice)",
            # Actual text seen on portal: "enabled for e-Invoicing as his/her AATO..."
            r"((?:not )?enabled for e-Invoicing[^\n]*)",
            r"taxpayer is ((?:not )?enabled[^\n]*)",
        ]
        for pat in patterns:
            m = re.search(pat, body_text, re.IGNORECASE)
            if m:
                result["einvoice_status"] = m.group(1).strip()
                logger.info(f"[scrape] einvoice_status='{result['einvoice_status']}'")
                return

        # Fallback: look for any <td> containing "enabled"
        try:
            for cell in self._driver.find_elements(By.TAG_NAME, "td"):
                txt = cell.text.strip()
                if "enabled" in txt.lower() and len(txt) > 5:
                    result["einvoice_status"] = txt
                    logger.info(f"[scrape] einvoice_status fallback td='{txt}'")
                    return
        except Exception:
            pass

        # Last resort: log full body so we can see what the page actually says
        logger.warning(f"[scrape] einvoice_status not found. Body preview: {body_text[:500]!r}")

    # ── Preprocessing: same approach as proven GSTPortalBot ──────────────────
    def _solve_captcha(self, img_bytes: bytes) -> str:
        """4-vote ddddocr: raw×2 models + preprocessed×2 models."""
        processed = self._preprocess(img_bytes)
        votes     = []
        for model, data in [
            (self._ocr,  img_bytes),
            (self._ocrb, img_bytes),
            (self._ocr,  processed),
            (self._ocrb, processed),
        ]:
            try:
                txt = re.sub(r'[^A-Za-z0-9]', '',
                             model.classification(data)).upper()
                if txt:
                    votes.append(txt)
            except Exception:
                pass

        if not votes:
            return ""
        winner = Counter(votes).most_common(1)[0][0]
        logger.info(f"[ocr] votes={votes} -> '{winner}'")
        return winner

    def _preprocess(self, img_bytes: bytes) -> bytes:
        """
        Preprocessing copied from the working GSTPortalBot._preprocess_image():
          1. HSV blue extraction (hue 85-145)
          2. Remove horizontal lines (morphological open with wide kernel)
          3. CC area filter >= 15
          4. Dilate 2x2
          5. Resize 2x
          6. Threshold + invert (dark text on white)
        """
        nparr   = np.frombuffer(img_bytes, np.uint8)
        img_bgr = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img_bgr is None:
            return img_bytes

        img_hsv = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2HSV)
        mask    = cv2.inRange(
            img_hsv,
            np.array([85, 25, 25]),
            np.array([145, 255, 255])
        )

        # Remove horizontal lines (same as GSTPortalBot)
        line_w   = max(img_bgr.shape[1] // 3, 20)
        h_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (line_w, 1))
        horiz    = cv2.morphologyEx(mask, cv2.MORPH_OPEN, h_kernel)
        mask     = cv2.subtract(mask, horiz)

        # CC area filter
        n, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
        clean = np.zeros_like(mask)
        for i in range(1, n):
            if stats[i, cv2.CC_STAT_AREA] >= 15:
                clean[labels == i] = 255

        # Dilate + resize 2x + threshold + invert
        clean = cv2.dilate(clean, np.ones((2, 2), np.uint8), iterations=1)
        h, w  = clean.shape
        clean = cv2.resize(clean, (w * 2, h * 2), interpolation=cv2.INTER_LINEAR)
        _, clean = cv2.threshold(clean, 127, 255, cv2.THRESH_BINARY)
        result   = cv2.bitwise_not(clean)

        _, buf = cv2.imencode('.png', result)
        return buf.tobytes()

    # ── Screenshot ────────────────────────────────────────────────────────────
    def _save_screenshot(self, gstin: str) -> str:
        try:
            os.makedirs(SCREENSHOT_DIR, exist_ok=True)
            ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
            path = os.path.join(SCREENSHOT_DIR, f"einvoice_{gstin}_{ts}.png")
            self._driver.save_screenshot(path)
            logger.info(f"[screenshot] saved: {path}")
            return path
        except Exception as exc:
            logger.warning(f"[screenshot] failed: {exc}")
            return ""

    # ── Public static version (called by gst_runner before instantiation) ─────
    @staticmethod
    def cleanup_old_screenshots():
        try:
            cutoff = time.time() - SCREENSHOT_TTL
            for f in glob.glob(os.path.join(SCREENSHOT_DIR, "*.png")):
                if os.path.getmtime(f) < cutoff:
                    os.remove(f)
        except Exception:
            pass

    def _cleanup_old_screenshots(self):
        EInvoiceBot.cleanup_old_screenshots()

    # ── Util ──────────────────────────────────────────────────────────────────
    def _find(self, xpaths):
        for xpath in xpaths:
            try:
                el = self._driver.find_element(By.XPATH, xpath)
                if el.is_displayed():
                    return el
            except Exception:
                continue
        return None
