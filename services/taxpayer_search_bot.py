# taxpayer_search_bot.py
# Scrapes GSTIN status, legal name, taxpayer type, and GSTR filing info from:
#   https://services.gst.gov.in/services/searchtp
#
# Captcha approach (from working gst_captcha_test.py):
#   Site 2 has diagonal crosshatch grid + red lines — different from site 1.
#   - Remove red lines (ddddocr hasn't seen this noise)
#   - 4 preprocessing variants: colour / CLAHE-gray / Otsu-binary / adaptive-clean
#   - 10-vote ddddocr: raw×2 + 4 variants × 2 models
#   - Captcha is exactly 6 alphanumeric chars on this portal
#
# Screenshots saved to gst_screenshots\taxpayer\
# Auto-deleted after 10 days on each startup.
#
# Usage:
#   from services.taxpayer_search_bot import TaxpayerSearchBot
#   bot = TaxpayerSearchBot(headless=False)
#   try:
#       result = bot.search("27AAACH3583Q1Z0")
#       bot.save_screenshot("27AAACH3583Q1Z0")
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

from services.edge_driver_check import ensure_matching_edge_driver

logger = logging.getLogger(__name__)

SCREENSHOT_DIR = r"C:\Users\ctn_suresh\Agents\material_inward_FINAL (2)\material_inward_FINAL\material_inward\gst_screenshots\taxpayer"
SCREENSHOT_TTL = 10 * 24 * 3600   # 10 days in seconds

# Persistent Edge profile -- was previously unset, so every run got a fresh
# temp profile Selenium deletes on driver.quit(). That meant the native
# Windows/Edge "wants to Access other apps and services on this device"
# permission prompt (a browser-chrome dialog Selenium can't dismiss via
# normal DOM calls, seen sitting on top of the page and likely contributing
# to the "page never reached a recognizable state" captcha/search-element
# failures) could never be permanently resolved -- even a manual Allow/Block
# click vanished with the temp profile. Reusing a real folder means one
# manual click here sticks for every future run against this site.
EDGE_PROFILE_DIR = r"C:\Users\ctn_suresh\Agents\material_inward_FINAL (2)\material_inward_FINAL\material_inward\gst_edge_profile\taxpayer"


class TaxpayerSearchBot:
    URL     = "https://services.gst.gov.in/services/searchtp"
    CAP_LEN = 6   # site 2 captcha is exactly 6 chars
    MAX_SEARCH_ATTEMPTS = 10   # hard cap -- previously unbounded (while True with
                               # no ceiling), so a persistent failure like a
                               # missing GSTIN input element would retry forever
                               # (seen looping 130+ times) instead of failing cleanly.

    # XPATHs from gst_captcha_test.py (proven working on prod)
    GSTIN_INPUT_XPATHS = [
        "//input[@id='for_gstin']",
        "//input[contains(@placeholder,'GSTIN')]",
        "//input[contains(@placeholder,'gstin')]",
        "//input[@type='text'][1]",
    ]
    CAPTCHA_IMG_XPATHS = [
        "//img[contains(@src,'captcha')]",
        "//img[contains(@id,'captcha')]",
        "//img[contains(@class,'captcha')]",
        "//canvas[contains(@id,'captcha')]",
    ]
    CAPTCHA_INPUT_XPATHS = [
        "//input[@id='captcha']",
        "//input[contains(@placeholder,'captcha')]",
        "//input[contains(@placeholder,'haracter')]",
        "//input[@type='text'][2]",
    ]
    SEARCH_BTN_XPATHS = [
        "//button[contains(text(),'Search') or contains(text(),'SEARCH')]",
        "//input[@type='submit']",
        "//button[@type='submit']",
    ]
    REFRESH_XPATHS = [
        "//img[contains(@src,'refresh')]",
        "//a[contains(@onclick,'refresh') or contains(@onclick,'Refresh')]",
        "//span[contains(@class,'refresh')]",
        "//*[@title='Refresh' or @title='refresh']",
    ]

    # ── Init ──────────────────────────────────────────────────────────────────
    def __init__(self, headless: bool = True):
        self._cleanup_old_screenshots()
        ensure_matching_edge_driver(logger)

        os.makedirs(EDGE_PROFILE_DIR, exist_ok=True)

        opts = Options()
        if headless:
            opts.add_argument("--headless=new")
        opts.add_argument("--disable-blink-features=AutomationControlled")
        opts.add_argument("--no-sandbox")
        opts.add_argument("--disable-dev-shm-usage")
        opts.add_argument("--window-size=1366,768")
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
        logger.info("TaxpayerSearchBot initialised")

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
          "gstin":             str,
          "gstin_status":      str,   # "Active" / "Cancelled" / etc.
          "legal_name":        str,
          "taxpayer_type":     str,
          "gstr3b_last_filed": str,
          "gstr3b_tax_period": str,
          "gstr3b_status":     str,
          "gstr1_last_filed":  str,
          "gstr1_tax_period":  str,
          "gstr1_status":      str,
          "error":             str | None
        }
        """
        result = {
            "gstin":             gstin,
            "gstin_status":      "",
            "legal_name":        "",
            "taxpayer_type":     "",
            "gstr3b_last_filed": "",
            "gstr3b_tax_period": "",
            "gstr3b_status":     "",
            "gstr1_last_filed":  "",
            "gstr1_tax_period":  "",
            "gstr1_status":      "",
            "error":             None,
        }

        try:
            passed = self._solve_captcha_and_submit(gstin)

            if not passed:
                # GSTIN itself was rejected by the portal — don't scrape
                result["error"] = (
                    "GSTIN rejected by portal — the extracted GSTIN may be wrong. "
                    "Please correct it in the Extracted Data tab and Re-run."
                )
                result["gstin_status"] = "Invalid / Rejected"
                return result

            if self._gstin_not_found:
                # Captcha solved but GSTIN doesn't exist on portal
                result["gstin_status"] = "Not Found on Portal"
                result["error"] = (
                    "GSTIN not found on portal — it may be incorrect. "
                    "Please correct it in the Extracted Data tab and Re-run."
                )
                return result

            self._load_filing_table()   # click SHOW FILING TABLE → SEARCH for FY
            self._scrape_result(result)

        except Exception as exc:
            result["error"] = str(exc)
            logger.error(f"[TaxpayerSearchBot] search({gstin}): {exc}", exc_info=True)

        return result

    def save_screenshot(self, gstin: str) -> str:
        """Called explicitly by gst_runner after search()."""
        try:
            os.makedirs(SCREENSHOT_DIR, exist_ok=True)
            ts   = datetime.now().strftime("%Y%m%d_%H%M%S")
            path = os.path.join(SCREENSHOT_DIR, f"taxpayer_{gstin}_{ts}.png")
            self._driver.save_screenshot(path)
            logger.info(f"[screenshot] saved: {path}")
            return path
        except Exception as exc:
            logger.warning(f"[screenshot] failed: {exc}")
            return ""

    # ── Captcha flow ──────────────────────────────────────────────────────────
    def _solve_captcha_and_submit(self, gstin: str) -> bool:
        """
        Returns True  — captcha solved, result page loaded (GSTIN may or may not exist).
        Returns False — GSTIN itself was rejected by the portal (stop, don't retry).

        Sets self._gstin_not_found = True when portal says GSTIN doesn't exist.
        Sets self._gstin_rejected  = True when portal rejects the GSTIN format/value.
        """
        self._gstin_not_found = False
        self._gstin_rejected  = False

        self._driver.get(self.URL)
        time.sleep(3)

        attempt = 0
        while attempt < self.MAX_SEARCH_ATTEMPTS:
            attempt += 1
            logger.info(f"[cap] attempt {attempt}/{self.MAX_SEARCH_ATTEMPTS}")
            try:
                # Fill GSTIN
                gstin_el = self._find(self.GSTIN_INPUT_XPATHS)
                if not gstin_el:
                    raise RuntimeError("GSTIN input not found")
                gstin_el.clear()
                gstin_el.send_keys(gstin)
                time.sleep(0.3)

                # Grab captcha image
                cap_img_el = self._find(self.CAPTCHA_IMG_XPATHS)
                if not cap_img_el:
                    raise RuntimeError("Captcha image not found")
                img_bytes = cap_img_el.screenshot_as_png

                solved = self._solve_captcha(img_bytes, f"a{attempt:02d}")
                logger.info(f"[cap] solved='{solved}'")

                if len(solved) != self.CAP_LEN:
                    logger.warning(f"[cap] '{solved}' not {self.CAP_LEN} chars — refresh")
                    self._refresh_captcha()
                    continue

                # Fill captcha input
                cap_el = self._find(self.CAPTCHA_INPUT_XPATHS)
                if not cap_el:
                    raise RuntimeError("Captcha input not found")
                cap_el.clear()
                cap_el.send_keys(solved)
                time.sleep(0.3)

                # Click Search
                btn = self._find(self.SEARCH_BTN_XPATHS)
                if not btn:
                    raise RuntimeError("Search button not found")
                btn.click()
                time.sleep(2.5)

                body = self._driver.find_element(By.TAG_NAME, "body").text.lower()

                # ── Priority 1: GSTIN itself rejected by portal ───────────────
                # Must check BEFORE captcha-error check because "enter valid" could
                # match both captcha errors AND GSTIN validation errors.
                gstin_rejection_phrases = (
                    "invalid gstin", "valid gstin", "enter valid gstin",
                    "gstin is not valid", "not a valid gstin",
                    "invalid uin", "valid uin", "enter valid uin",
                    "please enter 15", "15 digit gstin",
                    "gstin format", "uin format",
                )
                if any(x in body for x in gstin_rejection_phrases):
                    logger.error(
                        f"[cap] attempt {attempt}: portal rejected the GSTIN itself — stopping"
                    )
                    self._gstin_rejected = True
                    return False

                # ── Priority 2: Captcha wrong — refresh and retry ─────────────
                captcha_error_phrases = (
                    "invalid captcha", "wrong captcha", "captcha is not",
                    "valid captcha", "valid letters", "enter captcha",
                    "captcha code", "please enter the captcha",
                )
                if any(x in body for x in captcha_error_phrases):
                    logger.warning(f"[cap] attempt {attempt}: wrong captcha — retry")
                    self._refresh_captcha()
                    continue

                # Legacy generic "invalid/incorrect" — only treat as captcha error
                # if GSTIN-specific text is NOT present (already checked above).
                if any(x in body for x in ("invalid", "incorrect", "wrong")):
                    logger.warning(f"[cap] attempt {attempt}: invalid/incorrect response — retry")
                    self._refresh_captcha()
                    continue

                # ── Priority 3: GSTIN not found — valid captcha, GSTIN missing ─
                if any(x in body for x in ("not found", "no record")):
                    logger.info(f"[cap] attempt {attempt}: PASSED (GSTIN not found on portal)")
                    self._gstin_not_found = True
                    return True

                # ── Priority 4: Success ───────────────────────────────────────
                if any(x in body for x in ("taxpayer", "trade name", "legal name",
                                            "gstin status", "active", "cancelled")):
                    logger.info(f"[cap] attempt {attempt}: PASSED")
                    return True

                # ── Unclear response — save screenshot and retry ──────────────
                logger.warning(f"[cap] attempt {attempt}: unclear — saving page screenshot")
                self._driver.save_screenshot(
                    os.path.join(SCREENSHOT_DIR, f"unclear_a{attempt}.png")
                )
                self._refresh_captcha()

            except RuntimeError as exc:
                logger.error(f"[cap] attempt {attempt} failed: {exc}")
                time.sleep(2)
            except Exception as exc:
                logger.error(f"[cap] attempt {attempt} error: {exc}", exc_info=True)
                time.sleep(2)

        # Cap reached -- previously this loop had no ceiling and would spin
        # forever (seen looping 130+ times) when a failure was persistent
        # rather than transient (e.g. GSTIN input never appearing). Raising
        # here lets search()'s existing except Exception handler catch it and
        # populate result["error"] cleanly, same as any other bot failure.
        raise RuntimeError(
            f"Gave up after {self.MAX_SEARCH_ATTEMPTS} attempts -- page never "
            "reached a recognizable state (captcha/search element issue)."
        )

    def _refresh_captcha(self):
        el = self._find(self.REFRESH_XPATHS)
        if el:
            try:
                self._driver.execute_script("arguments[0].click();", el)
                time.sleep(1.2)
                return
            except Exception:
                pass
        self._driver.get(self.URL)
        time.sleep(2.5)

    # ── Filing table loader ───────────────────────────────────────────────────
    def _load_filing_table(self):
        """
        After the GSTIN captcha search, the result page shows basic taxpayer
        info but the GSTR filing tables are NOT loaded yet.

        Required steps (confirmed from DevTools screenshots):
          1. Click "SHOW FILING TABLE" button (id='filingTable')
          2. Leave Financial Year dropdown at default (current year)
          3. Click the SEARCH button inside the filing section
          4. Wait for Angular to render the GSTR3B / GSTR-1 tables

        This must run before _scrape_result().
        """
        try:
            # Step 1 — click "SHOW FILING TABLE" tab
            filing_btn = self._find([
                "//button[@id='filingTable']",
                "//button[contains(normalize-space(.),'SHOW FILING TABLE')]",
                "//button[contains(normalize-space(.),'FILING TABLE')]",
            ])
            if filing_btn:
                self._driver.execute_script("arguments[0].click();", filing_btn)
                time.sleep(1.5)
                logger.info("[filing] clicked SHOW FILING TABLE")
            else:
                logger.warning("[filing] SHOW FILING TABLE button not found — continuing anyway")

            # Step 2 — click SEARCH for Financial Year
            # Primary: anchor off the FY <select> — the SEARCH button is always right after it
            # Fallback: JS click on any visible button whose text is SEARCH/Search
            clicked = False

            search_btn = self._find([
                # Sibling/following button after the FY dropdown
                "//select[contains(@class,'form-control')]/following-sibling::button[1]",
                "//select[contains(@class,'form-control')]/following::button[1]",
                # By text (all-caps variant seen in screenshots)
                "//button[normalize-space(text())='SEARCH']",
                "//button[normalize-space(.)='SEARCH']",
                # Mixed-case fallback
                "//button[translate(normalize-space(.),'search','SEARCH')='SEARCH']",
            ])
            if search_btn:
                self._driver.execute_script("arguments[0].click();", search_btn)
                clicked = True
                logger.info(f"[filing] clicked SEARCH via XPath (text={search_btn.text!r})")

            if not clicked:
                # JS fallback — find any visible button with SEARCH text
                result_js = self._driver.execute_script("""
                    var btns = document.querySelectorAll('button');
                    for (var i = 0; i < btns.length; i++) {
                        var txt = btns[i].textContent.trim().toUpperCase();
                        var rect = btns[i].getBoundingClientRect();
                        if (txt === 'SEARCH' && rect.width > 0) {
                            btns[i].click();
                            return btns[i].textContent.trim();
                        }
                    }
                    return null;
                """)
                if result_js:
                    clicked = True
                    logger.info(f"[filing] clicked SEARCH via JS (text={result_js!r})")
                else:
                    logger.warning("[filing] SEARCH button not found by XPath or JS")

            if clicked:
                time.sleep(3.0)   # wait for Angular to render GSTR tables
                logger.info("[filing] waiting done — GSTR tables should now be in DOM")

        except Exception as exc:
            logger.warning(f"[filing] _load_filing_table error: {exc}")

    # ── Result scraping ───────────────────────────────────────────────────────
    def _scrape_result(self, result: dict):
        """
        Parse taxpayer details from the result page.

        Page structure (services.gst.gov.in/services/searchtp after search):
          - Basic info table: rows with label/value pairs
          - "Filing details for GSTR3B" heading  → table:
              cols: Financial Year | Tax Period | Date of filing | Status
          - "Filing details for GSTR-1/IFF" heading → table (same columns)
        """
        body_text = self._driver.find_element(By.TAG_NAME, "body").text
        # Log full text — helps diagnose heading/column name mismatches
        logger.info(f"[scrape] page text:\n{body_text}")

        # ── Basic taxpayer fields (label-value table rows) ─────────────────
        try:
            for row in self._driver.find_elements(By.XPATH, "//table//tr"):
                cells = row.find_elements(By.TAG_NAME, "td")
                if len(cells) < 2:
                    continue
                label = cells[0].text.strip().lower()
                value = cells[1].text.strip()
                if "legal name" in label:
                    result["legal_name"] = value
                elif "gstin status" in label or "uin status" in label:
                    result["gstin_status"] = value
                elif "taxpayer type" in label or "type of taxpayer" in label:
                    result["taxpayer_type"] = value
        except Exception as exc:
            logger.warning(f"[scrape] basic table error: {exc}")

        # ── GSTR sections ──────────────────────────────────────────────────
        #
        # Approach A (XPath): uses contains(.) NOT contains(text()) so it
        # works even when heading text is split across nested <span>/<b> tags.
        # string-length < 60 stops us matching <body> / large parent containers.
        #
        # Approach B (body_text regex): reliable fallback — Selenium .text
        # flattens the page to visible text, so headings + table rows appear
        # as plain lines regardless of DOM nesting.
        # Expected shape: "Filing details for GSTR3B\n<header row>\n<data row>"

        def _xpath_row(heading_text: str):
            """
            Return (tax_period, date_filed, status) from the table that immediately
            follows the exact heading element.  Uses normalize-space(.)=<exact text>
            so nav/tab elements that merely *contain* the keyword don't match.
            Columns: [0]=Financial Year  [1]=Tax Period  [2]=Date of filing  [3]=Status
            """
            try:
                xp = (
                    f"//*[normalize-space(.)='{heading_text}']"
                    f"/following::table[1]//tr[td][1]"
                )
                rows = self._driver.find_elements(By.XPATH, xp)
                logger.debug(f"[scrape] xpath heading='{heading_text}' rows_found={len(rows)}")
                if rows:
                    c = rows[0].find_elements(By.TAG_NAME, "td")
                    logger.debug(f"[scrape] row cells: {[x.text.strip() for x in c]}")
                    if len(c) >= 4:
                        return c[1].text.strip(), c[2].text.strip(), c[3].text.strip()
            except Exception as exc:
                logger.debug(f"[scrape] xpath {heading_text!r} err: {exc}")
            return None, None, None

        def _regex_row(pattern: str):
            """
            Pattern must have 4 capture groups:
              (financial_year) (tax_period) (date_filed) (status)
            """
            m = re.search(pattern, body_text, re.IGNORECASE | re.DOTALL)
            if m:
                return m.group(2), m.group(3), m.group(4).strip()
            return None, None, None

        # GSTR-3B
        period, filed, status = _xpath_row("Filing details for GSTR3B")
        if not filed:
            period, filed, status = _xpath_row("Filing details for GSTR-3B")
        if not filed:
            # regex: skip past "GSTR3B" heading and optional header row,
            # then grab financial_year  tax_period  date  status
            period, filed, status = _regex_row(
                r'GSTR.?3B[^\n]*\n[^\n]*\n(\d{4}-\d{4})\s+(\S+)\s+(\d{1,2}/\d{2}/\d{4})\s+([^\n]+)'
            )
        if not filed:
            # looser: heading anywhere, data row anywhere after it
            period, filed, status = _regex_row(
                r'GSTR.?3B.*?(\d{4}-\d{4})\s+(\S+)\s+(\d{1,2}/\d{2}/\d{4})\s+([^\n]+)'
            )
        if filed:
            result["gstr3b_tax_period"] = period or ""
            result["gstr3b_last_filed"] = filed
            result["gstr3b_status"]     = status or ""
            logger.info(f"[scrape] GSTR3B: period={period!r} filed={filed!r} status={status!r}")
        else:
            logger.warning("[scrape] GSTR3B not found — check page text above")

        # GSTR-1/IFF
        period, filed, status = _xpath_row("Filing details for GSTR-1/IFF")
        if not filed:
            period, filed, status = _xpath_row("Filing details for GSTR-1")
        if not filed:
            period, filed, status = _xpath_row("Filing details for GSTR1")
        if not filed:
            # [^3\n] after "1" prevents accidentally matching GSTR-13 or GSTR3B
            period, filed, status = _regex_row(
                r'GSTR.?1[^3\n][^\n]*\n[^\n]*\n(\d{4}-\d{4})\s+(\S+)\s+(\d{1,2}/\d{2}/\d{4})\s+([^\n]+)'
            )
        if not filed:
            period, filed, status = _regex_row(
                r'GSTR.?1[^3].*?(\d{4}-\d{4})\s+(\S+)\s+(\d{1,2}/\d{2}/\d{4})\s+([^\n]+)'
            )
        if filed:
            result["gstr1_tax_period"] = period or ""
            result["gstr1_last_filed"] = filed
            result["gstr1_status"]     = status or ""
            logger.info(f"[scrape] GSTR1: period={period!r} filed={filed!r} status={status!r}")
        else:
            logger.warning("[scrape] GSTR1 not found — check page text above")

        # ── Regex fallbacks for basic fields ──────────────────────────────
        if not result["gstin_status"]:
            m = re.search(
                r'(?:GSTIN\s*/?\s*UIN\s+Status|GSTIN\s+Status)\s*[:\-]?\s*(Active|Cancelled|Suspended|Inactive)',
                body_text, re.IGNORECASE
            )
            if m:
                result["gstin_status"] = m.group(1).strip()

        if not result["legal_name"]:
            m = re.search(r'Legal\s+Name(?:\s+of\s+Business)?\s*[:\-]?\s*(.+)', body_text, re.IGNORECASE)
            if m:
                result["legal_name"] = m.group(1).strip()

        if not result["taxpayer_type"]:
            m = re.search(r'Taxpayer\s+Type\s*[:\-]?\s*(.+)', body_text, re.IGNORECASE)
            if m:
                result["taxpayer_type"] = m.group(1).strip()

        logger.info(
            f"[scrape] FINAL: status='{result['gstin_status']}' "
            f"legal_name='{result['legal_name']}' "
            f"type='{result['taxpayer_type']}' "
            f"gstr3b={result['gstr3b_last_filed']!r} "
            f"gstr1={result['gstr1_last_filed']!r}"
        )

    # ── OCR: 10-vote system (from gst_captcha_test.py) ───────────────────────
    def _solve_captcha(self, img_bytes: bytes, label: str = "") -> str:
        """10-vote ddddocr: raw×2 + 4 preprocessed variants × 2 models each."""
        variants = self._preprocess(img_bytes)
        votes    = []

        # Raw bytes first — ddddocr may handle the grid natively
        for model in (self._ocr, self._ocrb):
            try:
                txt = re.sub(r'[^A-Za-z0-9]', '', model.classification(img_bytes)).upper()
                if txt:
                    votes.append(txt)
            except Exception:
                pass

        # All four preprocessed variants
        for key, img_np in variants.items():
            _, buf = cv2.imencode('.png', img_np)
            data   = buf.tobytes()
            for model in (self._ocr, self._ocrb):
                try:
                    txt = re.sub(r'[^A-Za-z0-9]', '', model.classification(data)).upper()
                    if txt:
                        votes.append(txt)
                except Exception:
                    pass

        if not votes:
            return ""
        winner = Counter(votes).most_common(1)[0][0]
        logger.info(f"[ocr] votes={votes} -> '{winner}'")
        return winner

    def _remove_red(self, img_bgr):
        """Remove red diagonal lines — replace with mid-gray."""
        img_hsv = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2HSV)
        red1 = cv2.inRange(img_hsv, np.array([0,   50, 50]), np.array([10,  255, 255]))
        red2 = cv2.inRange(img_hsv, np.array([160, 50, 50]), np.array([180, 255, 255]))
        mask = cv2.dilate(cv2.bitwise_or(red1, red2), np.ones((2, 2), np.uint8), iterations=1)
        out  = img_bgr.copy()
        out[mask > 0] = [160, 160, 160]   # mid-gray, not white
        return out

    def _preprocess(self, img_bytes: bytes) -> dict:
        """
        4-variant preprocessing for site 2's diagonal-grid captcha.
        Taken directly from gst_captcha_test.py preprocess_site2().

        Returns dict {A, B, C, D} of numpy arrays.
        """
        nparr   = np.frombuffer(img_bytes, np.uint8)
        img_bgr = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img_bgr is None:
            return {}

        no_red = self._remove_red(img_bgr)
        h, w   = no_red.shape[:2]
        gray   = cv2.cvtColor(no_red, cv2.COLOR_BGR2GRAY)

        # A: red removed, colour, 3x upscale
        varA = cv2.resize(no_red, (w * 3, h * 3), interpolation=cv2.INTER_CUBIC)

        # B: grayscale + CLAHE + 3x upscale
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(4, 4))
        varB  = cv2.resize(clahe.apply(gray), (w * 3, h * 3), interpolation=cv2.INTER_CUBIC)

        # C: Otsu binary, dark-text-on-white, 3x upscale
        _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
        up        = cv2.resize(binary, (w * 3, h * 3), interpolation=cv2.INTER_LINEAR)
        _, up     = cv2.threshold(up, 127, 255, cv2.THRESH_BINARY)
        varC      = cv2.bitwise_not(up)

        # D: adaptive threshold + CC area filter (removes grid dots), 3x upscale
        #    blockSize=21 spans ~3 grid cells so threshold adapts beyond grid periodicity
        adapt = cv2.adaptiveThreshold(
            gray, 255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY_INV,
            blockSize=21, C=4
        )
        n, labels, stats, _ = cv2.connectedComponentsWithStats(adapt, connectivity=8)
        clean = np.zeros_like(adapt)
        for i in range(1, n):
            if stats[i, cv2.CC_STAT_AREA] >= 20:
                clean[labels == i] = 255
        up    = cv2.resize(clean, (w * 3, h * 3), interpolation=cv2.INTER_LINEAR)
        _, up = cv2.threshold(up, 127, 255, cv2.THRESH_BINARY)
        varD  = cv2.bitwise_not(up)

        return {"A": varA, "B": varB, "C": varC, "D": varD}

    # ── Screenshot cleanup ────────────────────────────────────────────────────
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
        TaxpayerSearchBot.cleanup_old_screenshots()

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
