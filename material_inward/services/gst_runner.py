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

import os
import queue
import threading
from datetime import datetime, timedelta

from database.db_operations import get_history_details_by_id
from database.gst_operations import upsert_gst_approval, get_gst_approval, mark_auto_retry_exhausted
from config.logger import get_logger

logger = get_logger(__name__)

# Track which history_ids currently have a running bot thread
_running: set = set()
_running_lock = threading.Lock()

# ---------------------------------------------------------------------------
# Browser profile slot pools -- bounded concurrency for GST bots
# ---------------------------------------------------------------------------
# Each bot type used to point every run at ONE fixed, shared Edge profile
# folder (EDGE_PROFILE_DIR in einvoice_bot.py / taxpayer_search_bot.py).
# Chromium locks --user-data-dir exclusively, so two concurrent runs of the
# SAME bot type would collide on that folder today. Fix: a small, bounded
# pool of slot subfolders per bot type, handed out via a Queue that blocks
# when every slot is busy -- this both avoids the folder-lock collision and
# caps how many Edge processes of a given type can run at once (so a burst
# of triggers doesn't pile up an unbounded number of browsers/OCR models).
#
# Starting at 2 slots each -- deliberately conservative. Both GST portals
# are live government sites with no staging environment to test
# rate-limiting against; raise these only after watching real error/retry
# rates for a while. Each NEW slot folder needs the native Edge
# "Allow/Block" permission prompt clicked once, manually, the first time
# it's used -- same one-time step the original single shared folder needed,
# just repeated per slot (einvoice slot1/slot2, taxpayer slot1/slot2 = 4
# clicks total, once each, ever).
EINVOICE_POOL_SIZE = 2
TAXPAYER_POOL_SIZE = 2

_einvoice_slots: "queue.Queue[int]" = queue.Queue()
_taxpayer_slots: "queue.Queue[int]" = queue.Queue()
for _slot_n in range(1, EINVOICE_POOL_SIZE + 1):
    _einvoice_slots.put(_slot_n)
for _slot_n in range(1, TAXPAYER_POOL_SIZE + 1):
    _taxpayer_slots.put(_slot_n)

# Track auto-retry attempts per history_id: {history_id: {"count": int, "last": datetime}}
_attempts: dict = {}

# 5, not 3 -- now that approve/hold hard-stop retries below (see trigger_async),
# the only case this cap still governs is a genuinely unresolved record where the
# portal or bot glitched transiently. A little more headroom for a slow/flaky
# portal is safe now that it can no longer run indefinitely on an already-decided
# record. At 60s cooldown between attempts, 5 attempts spans ~5 minutes.
MAX_AUTO_RETRIES = 5            # after this many failures, stop auto-retrying (poll still returns last error)
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

    Human-decision gate (checked before anything else, including force=True):
      - approval_status == 'approved': NEVER runs again, unconditionally. A human
        has already signed off on this record; nothing here should touch it again,
        automatically or manually. (api_gst_rerun() in app.py also blocks the
        Re-run button itself on an approved record, as a second line of defense --
        this check is what actually enforces it either way.)
      - approval_status == 'hold': the automatic poll path (force=False, fired
        every 5s by the GST Approval tab just from having the page open) does
        NOT retry -- a human put this on hold and hasn't asked for anything to
        happen yet. force=True (an explicit Re-run button click) still works,
        since that's a deliberate human action, not the page silently retrying
        in the background.
    Previously neither of these was checked at all: a record could be approved
    or held with a lingering bot_error on its row, and every subsequent poll
    (page open, page reload, anyone viewing the record) would still see that
    stale error and keep auto-retrying in bursts of MAX_AUTO_RETRIES, resetting
    every time the app process restarted since _attempts is in-memory only --
    effectively unbounded over time despite the per-burst cap.
    """
    existing = get_gst_approval(history_id)
    approval_status = (existing or {}).get("approval_status")

    if approval_status == "approved":
        logger.info(
            f"[gst_runner] history_id={history_id} is approved -- re-run permanently blocked"
        )
        return False

    if approval_status == "hold" and not force:
        logger.info(
            f"[gst_runner] history_id={history_id} is on hold -- auto-retry skipped, "
            "manual Re-run required"
        )
        return False

    with _running_lock:
        if history_id in _running:
            logger.info(f"[gst_runner] already running for history_id={history_id}")
            return False

        if force:
            _attempts.pop(history_id, None)
        else:
            # Don't re-run if clean results already exist
            if existing and not existing.get("bot_error"):
                logger.info(f"[gst_runner] results already exist for history_id={history_id}")
                return False

            # v16 FIX: this must be checked BEFORE the in-memory _attempts
            # dict, and survives an app restart -- _attempts does not. Bug
            # this closes: a record that had already burned through its 5
            # auto-retries, with the cap noted only in _attempts, would
            # look completely fresh to a restarted process. The very next
            # poll from anyone simply opening this record's view page
            # would then start a brand-new burst of auto-retries, and keep
            # doing so indefinitely on every restart, with no user action
            # at all -- not what "auto-retry cap reached, wait for a
            # manual Re-run" was ever supposed to mean. Once this flag is
            # set, only an explicit Re-run (force=True) can clear it (see
            # reset_gst_for_rerun()).
            if existing and existing.get("auto_retry_exhausted"):
                logger.info(
                    f"[gst_runner] history_id={history_id} auto-retry already exhausted "
                    "(persisted) -- manual Re-run required, not retrying just from a page view"
                )
                return False

            if existing and existing.get("bot_error"):
                attempt = _attempts.get(history_id)
                if attempt:
                    if attempt["count"] >= MAX_AUTO_RETRIES:
                        logger.info(
                            f"[gst_runner] auto-retry cap ({MAX_AUTO_RETRIES}) reached for "
                            f"history_id={history_id} -- waiting for manual Re-run"
                        )
                        try:
                            mark_auto_retry_exhausted(history_id)
                        except Exception as e:
                            logger.error(
                                f"[gst_runner] failed to persist auto_retry_exhausted "
                                f"for history_id={history_id}: {e}"
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

        # v20 FIX: each site already has its own fully independent
        # captcha-solving loop (see EInvoiceBot/TaxpayerSearchBot's own
        # _solve_captcha_and_submit -- separate class instances, separate
        # browser sessions, no shared state). But THIS function has always
        # re-run BOTH sites from scratch on every retry, with no way to
        # resume just the half that failed -- so a Site 2 captcha/portal
        # hiccup needing a retry was also silently re-solving Site 1's
        # captcha for no reason, even though it already succeeded. Only
        # skip re-running Site 1 if its last stored result was genuinely
        # clean (einvoice_status present, no "Site1:" in the last
        # bot_error) -- if Site 1 itself failed too, or this is the very
        # first run for this history_id (no existing row), run it fresh
        # exactly as before. Site 2 always runs fresh below, unconditionally.
        existing = get_gst_approval(history_id)
        site1_reusable = bool(
            existing
            and existing.get("einvoice_status")
            and "Site1:" not in (existing.get("bot_error") or "")
        )

        if site1_reusable:
            logger.info(
                f"[gst_runner] Site1 already clean for history_id={history_id} "
                "-- reusing previous result, only retrying Site2"
            )
            result["einvoice_status"]     = existing.get("einvoice_status", "")
            result["einvoice_screenshot"] = existing.get("einvoice_screenshot", "")

        # v21 FIX: Site 1 and Site 2 previously ran sequentially in this one
        # thread even though they hit two completely independent sites, each
        # with its own separate Edge profile folder -- nothing about them
        # actually required running one after the other. Now launched
        # concurrently (when Site 1 isn't being reused), so one GST check
        # takes roughly as long as the SLOWER of the two sites instead of the
        # sum of both. Each still runs its own full try/except exactly as
        # before, just inside a thread target; result/errors are plain
        # dict-item/list writes to non-overlapping keys from each site, safe
        # under the GIL without extra locking. Each also now acquires a slot
        # from the bounded profile-pool queues above before launching its
        # browser, and always releases it in `finally` -- including on a
        # crash -- so a slot can never leak.
        def _do_site1():
            slot = _einvoice_slots.get()
            try:
                from services.einvoice_bot import EInvoiceBot, EDGE_PROFILE_DIR
                profile_dir = os.path.join(EDGE_PROFILE_DIR, f"slot{slot}")
                EInvoiceBot.cleanup_old_screenshots()
                bot1 = EInvoiceBot(headless=False, profile_dir=profile_dir)
                try:
                    r1 = bot1.search(gstin)
                    logger.info(f"[gst_runner] site1 (slot{slot}) raw result: {r1}")
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
            finally:
                _einvoice_slots.put(slot)

        def _do_site2():
            slot = _taxpayer_slots.get()
            try:
                from services.taxpayer_search_bot import TaxpayerSearchBot, EDGE_PROFILE_DIR
                profile_dir = os.path.join(EDGE_PROFILE_DIR, f"slot{slot}")
                TaxpayerSearchBot.cleanup_old_screenshots()
                bot2 = TaxpayerSearchBot(headless=False, profile_dir=profile_dir)
                try:
                    r2 = bot2.search(gstin)
                    logger.info(f"[gst_runner] site2 (slot{slot}) raw result: {r2}")
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
            finally:
                _taxpayer_slots.put(slot)

        threads = [threading.Thread(
            target=_do_site2, name=f"GST-Site2-{history_id}", daemon=True
        )]
        if not site1_reusable:
            threads.insert(0, threading.Thread(
                target=_do_site1, name=f"GST-Site1-{history_id}", daemon=True
            ))
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        if errors:
            result["bot_error"] = " | ".join(errors)
            if _is_terminal_error(errors):
                logger.info(
                    f"[gst_runner] terminal error for history_id={history_id} "
                    "(portal gave a definitive answer) -- will not auto-retry; "
                    "manual Re-run required"
                )
                # v16: persisted alongside the result in the same upsert
                # below (not just the in-memory _attempts dict -- see the
                # restart-survival note in trigger_async).
                result["auto_retry_exhausted"] = True
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
