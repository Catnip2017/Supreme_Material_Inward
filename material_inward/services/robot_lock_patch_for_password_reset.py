"""
PATCH FOR: C:\\Users\\ctn_suresh\\Agents\\password reset\\app.py

1. Add this function anywhere near the top (e.g. after get_logger definition).
2. Call it once inside submit_form(), just before the RPA robot subprocess.run block.

See STEP 2 comment below.
"""

# ── ADD THIS FUNCTION to app.py ───────────────────────────────────────────────
import os as _os
import time as _time

MATERIAL_INWARD_LOCK_FILE = r"C:\material_inward\robot.lock"
LOCK_WAIT_MAX_SECONDS     = 600   # wait up to 10 minutes
LOCK_POLL_INTERVAL        = 30    # check every 30 seconds

def wait_for_material_inward(log) -> bool:
    """
    Block until the Material Inward robot lock is free, then return True.
    If still locked after LOCK_WAIT_MAX_SECONDS, return False (caller should abort).

    Material Inward creates C:\\material_inward\\robot.lock before every SAP/robot
    run and deletes it when done. We wait here so our Desktop keystrokes and
    clipboard operations don't collide with whatever Material Inward is doing.
    """
    if not _os.path.exists(MATERIAL_INWARD_LOCK_FILE):
        return True  # nothing running — proceed immediately

    log.warning(
        "[Lock] Material Inward robot is running. "
        f"Waiting up to {LOCK_WAIT_MAX_SECONDS // 60} min before starting Password Reset..."
    )
    start = _time.time()
    while _os.path.exists(MATERIAL_INWARD_LOCK_FILE):
        elapsed = _time.time() - start
        if elapsed >= LOCK_WAIT_MAX_SECONDS:
            log.error(
                f"[Lock] Timed out after {LOCK_WAIT_MAX_SECONDS}s — "
                "Material Inward lock still held. Aborting Password Reset."
            )
            return False
        log.info(f"[Lock] Still waiting... {int(elapsed)}s elapsed. Retrying in {LOCK_POLL_INTERVAL}s.")
        _time.sleep(LOCK_POLL_INTERVAL)

    log.info("[Lock] Material Inward lock cleared — proceeding with Password Reset.")
    return True


# ── WHERE TO CALL IT in submit_form() ─────────────────────────────────────────
#
# Find this block in submit_form() (around line 130):
#
#     log.info(f"Triggering RPA Robot - Action: {action}...")
#
# Add the lock check IMMEDIATELY BEFORE it, like this:
#
#     # ── Priority lock: wait for Material Inward to finish ────────────────
#     if not wait_for_material_inward(log):
#         return jsonify({
#             'status': 'error',
#             'message': (
#                 'The SAP automation server is currently busy with Material Inward processing. '
#                 'Please try again in a few minutes.'
#             )
#         }), 503
#
#     log.info(f"Triggering RPA Robot - Action: {action}...")
#     ...rest of existing code unchanged...
