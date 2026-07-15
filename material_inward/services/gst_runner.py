"""
services/gst_runner.py
Orchestrates the two GST bots and stores results to the DB.

Flow:
  1. Called from the GST Approval tab poll route when no gst_approval
     row exists yet for this history_id.
  2. Spins up a background thread so the HTTP response returns immediately.
  3. Background thread:
       a. Reads seller GSTIN from invoice_data.seller_gstin.
       b. Runs EInvoiceBot  (site 1) -> einvoice_status + screenshot.
       c. Runs TaxpayerSearchBot (site 2) -> gstin_status, gstr3b, gstr1 + screenshot.
       d. Upserts results into gst_approval table.
  4. Tab polls /api/gst/status/<history_id> every 5 s -- returns
     {"status": "checking"} while running, full data when done.

Public API:
    trigger_async(history_id)   -- fire-and-forget background thread
    is_running(history_id)      -- True while thread is active
"""

import threading
from datetime import datetime, timedelta

from database.db_operations import get_history_details_by_id
from database.gst_operations import upsert_gst_approval, get_gst_approval
from config.logger import get_logger

logger = get_logger(__name__)

# Track which history_ids currently have a running bot thread
_running: set = set()
_running_lock = threading.Lock()

# Track auto-retry attempts per history_id: {history_id: {"count": int, "last": datetime}}
_attempts: dict = {}

MAX_AUTO_RETRIES = 3            # after this many failures, stop auto-retrying (poll still returns last error)
RETRY_COOLDOWN = timedelta(seconds=60)   # minimum gap between auto-retries

# Error substrings that mean "the portal gave a definitive, correct answer" rather than
# "the bot malfunctioned." These are NOT retried automatically at all -- retrying a
# deterministic rejection just wastes time and hammers the portal for no benefit.
# Only a manual Re-run (force=True) should try again, e.g. after the user fixes the GSTIN.
_TERMINAL_ERROR_SUBSTRINGS = (
    "rejected by portal",
)


def _is_terminal_error(errors: list) -> bool:
    combined = " | ".join(errors).lower()
    return any(sub in combined for sub in _TERMINAL_ERROR_SUBSTRINGS)


def is_running(history_id: int) -> bool:
    with _running_lock:
        return history_id in _running


def trigger_async(history_id: int, force: bool = False) -> bool:
    """
    Spin up a background thread for history_id if not already running.
    force=True: skip the "results already exist" / retry-cap checks (used by the Re-run button)
    and resets the retry counter.
    Returns True if a thread was started, False otherwise.
    """
    with _running_lock:
        if history_id in _running:
            logger.info(f"[gst_runner] already running for history_id={history_id}")
            return False

        if force:
            _attempts.pop(history_id, None)
        else:
            # Don't re-run if clean results already exist
            existing = get_gst_approval(history_id)
            if existing and not existing.get("bot_error"):
                logger.info(f"[gst_runner] results already exist for history_id={history_id}")
                return False

            if existing and existing.get("bot_error"):
                attempt = _attempts.get(history_id)
                if attempt:
                    if attempt["count"] >= MAX_AUTO_RETRIES:
                        logger.info(
                            f"[gst_runner] auto-retry cap ({MAX_AUTO_RETRIES}) reached for "
                            f"history_id={history_id} -- waiting for manual Re-run"
                        )
                        return False
                    if datetime.now() - attempt["last"] < RETRY_COOLDOWN:
                        logger.info(
                            f"[gst_runner] cooldown active for history_id={history_id}, skipping auto-retry"
                        )
                        return False

            attempt = _attempts.setdefault(history_id, {"count": 0, "last": datetime.now()})
            attempt["count"] += 1
            attempt["last"] = datetime.now()

        _running.add(history_id)

    t = threading.Thread(
        target=_run_bots,
        args=(history_id,),
        daemon=True,
        name=f"GSTBot-{history_id}"
    )
    t.start()
    logger.info(f"[gst_runner] thread started for history_id={history_id}")
    return True


# ---------------------------------------------------------------------------
# Background worker
# ---------------------------------------------------------------------------

def _run_bots(history_id: int) -> None:
    try:
        gstin = _get_seller_gstin(history_id)
        if not gstin:
            logger.error(f"[gst_runner] no seller GSTIN found for history_id={history_id}")
            upsert_gst_approval(history_id, {
                "bot_error": "Seller GSTIN not found in extracted data",
                "checked_at": datetime.now(),
            })
            return

        logger.info(f"[gst_runner] history_id={history_id} GSTIN={gstin} -- starting bots")
        result = {"checked_at": datetime.now()}
        errors = []

        # Site 1: EInvoiceBot
        try:
            from services.einvoice_bot import EInvoiceBot
            EInvoiceBot.cleanup_old_screenshots()
            bot1 = EInvoiceBot(headless=False)
            try:
                r1 = bot1.search(gstin)
                logger.info(f"[gst_runner] site1 raw result: {r1}")
                result["einvoice_status"]     = r1.get("einvoice_status", "")
                result["einvoice_screenshot"] = r1.get("screenshot", "")
                if r1.get("error"):
                    errors.append(f"Site1: {r1['error']}")
                    logger.warning(f"[gst_runner] site1 error: {r1['error']}")
                else:
                    logger.info(f"[gst_runner] site1 einvoice_status='{result['einvoice_status']}'")
            finally:
                bot1.quit()
        except Exception as e:
            errors.append(f"Site1 exception: {e}")
            logger.error(f"[gst_runner] site1 crashed: {e}", exc_info=True)

        # Site 2: TaxpayerSearchBot
        try:
            from services.taxpayer_search_bot import TaxpayerSearchBot
            TaxpayerSearchBot.cleanup_old_screenshots()
            bot2 = TaxpayerSearchBot(headless=False)
            try:
                r2 = bot2.search(gstin)
                logger.info(f"[gst_runner] site2 raw result: {r2}")
                result["gstin_status"]        = r2.get("gstin_status", "")
                result["legal_name"]          = r2.get("legal_name", "")
                result["taxpayer_type"]       = r2.get("taxpayer_type", "")
                result["gstr3b_last_filed"]   = r2.get("gstr3b_last_filed", "")
                result["gstr3b_tax_period"]   = r2.get("gstr3b_tax_period", "")
                result["gstr3b_status"]       = r2.get("gstr3b_status", "")
                result["gstr1_last_filed"]    = r2.get("gstr1_last_filed", "")
                result["gstr1_tax_period"]    = r2.get("gstr1_tax_period", "")
                result["gstr1_status"]        = r2.get("gstr1_status", "")
                result["taxpayer_screenshot"] = bot2.save_screenshot(gstin)
                if r2.get("error"):
                    errors.append(f"Site2: {r2['error']}")
                    logger.warning(f"[gst_runner] site2 error: {r2['error']}")
                else:
                    logger.info(
                        f"[gst_runner] site2 gstin_status='{result['gstin_status']}' "
                        f"legal_name='{result['legal_name']}' "
                        f"taxpayer_type='{result['taxpayer_type']}'"
                    )
            finally:
                bot2.quit()
        except Exception as e:
            errors.append(f"Site2 exception: {e}")
            logger.error(f"[gst_runner] site2 crashed: {e}", exc_info=True)

        if errors:
            result["bot_error"] = " | ".join(errors)
            if _is_terminal_error(errors):
                logger.info(
                    f"[gst_runner] terminal error for history_id={history_id} "
                    "(portal gave a definitive answer) -- will not auto-retry; "
                    "manual Re-run required"
                )
                with _running_lock:
                    _attempts[history_id] = {"count": MAX_AUTO_RETRIES, "last": datetime.now()}
        else:
            with _running_lock:
                _attempts.pop(history_id, None)

        logger.info(f"[gst_runner] final result to upsert: {result}")
        upsert_gst_approval(history_id, result)
        logger.info(f"[gst_runner] done for history_id={history_id}")

    except Exception as e:
        logger.error(
            f"[gst_runner] unexpected error history_id={history_id}: {e}", exc_info=True
        )
        try:
            upsert_gst_approval(history_id, {
                "bot_error": str(e),
                "checked_at": datetime.now(),
            })
        except Exception:
            pass
    finally:
        with _running_lock:
            _running.discard(history_id)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _get_seller_gstin(history_id: int) -> str:
    """
    Pull seller GSTIN from extracted invoice data.
    Primary:  invoice_data.seller_gstin  (confirmed DB column)
    Fallback: invoice_data.gstin         (some OCR output variants)
    Returns empty string if not found or not exactly 15 chars.
    """
    try:
        details = get_history_details_by_id(history_id)
        inv = details.get("invoice_data") or {}
        gstin = (inv.get("seller_gstin") or inv.get("gstin") or "").strip().upper()
        if gstin and len(gstin) == 15:
            return gstin
    except Exception as e:
        logger.error(f"[gst_runner] _get_seller_gstin error: {e}")
    return ""
