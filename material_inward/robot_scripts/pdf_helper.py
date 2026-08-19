import time
import pywinauto
 
 
def type_save_filename(filepath, wait_before=1.0, wait_after=1.5, timeout=15):
    """
    Types a filename into the native Windows "Save Print Output As" dialog
    (shown by the "Microsoft Print to PDF" driver after the SAP print popup)
    and saves it.
 
    FIX (2026-08-18): previously used WScript.Shell.SendKeys, which fires
    keystrokes at whatever window currently has OS focus, with no check
    that the Save dialog was actually open/focused yet. Confirmed in
    production (history_id=320, job 1114, output 3.xml): the RF log showed
    a clean PASS on every single keyword -- including this one -- yet no
    PDF was ever written to disk. SendKeys can't fail even when it types
    into the wrong window or into nothing, so a slow-to-render dialog (or
    literally anything else briefly stealing focus, e.g. another
    automation's own native dialog on the same shared desktop) silently
    swallowed the filename and Enter keypress with zero trace in the log.
 
    Switched to pywinauto -- already a project dependency (requirements.txt)
    and the same approach robot_scripts/file_dialog.py already uses for the
    DMS upload's native "Open" dialog -- to connect to the Save dialog BY
    TITLE and set the filename directly into its named control, so
    keystrokes can no longer land anywhere else. Polls for the dialog to
    actually appear (up to `timeout` seconds) instead of trusting a single
    fixed sleep, and raises a clear error if it never shows rather than
    silently reporting success with nothing saved.
    """
    time.sleep(wait_before)
 
    app = None
    deadline = time.time() + timeout
    last_error = None
    while time.time() < deadline:
        try:
            app = pywinauto.Application().connect(title_re="Save Print Output As.*")
            break
        except Exception as e:
            last_error = e
            time.sleep(0.5)
 
    if app is None:
        raise RuntimeError(
            f"'Save Print Output As' dialog never appeared within {timeout}s "
            f"-- cannot save {filepath!r}. Last connect error: {last_error}"
        )
 
    dlg = app.window(title_re="Save Print Output As.*")
    dlg.set_focus()
 
    # FIX (round 2, same day): "File name:Edit" -- the compound label+control
    # name file_dialog.py's Open-dialog pattern relies on -- doesn't exist on
    # THIS dialog. Confirmed from a real production MatchError (job 1124):
    # this Save dialog has no "File name:" label associated with its filename
    # field at all, just a bare Edit/ComboBoxEx32 pair. auto_id="1148" is the
    # long-standing, stable Windows common-dialog control ID for the filename
    # combo box (same ID since the classic GetSaveFileName API, still present
    # in the modern IFileDialog-based common dialogs) -- doesn't depend on a
    # label existing, unlike name-based best-match. Falls back to trying each
    # bare Edit control in turn if the ID ever differs across Windows builds.
    try:
        dlg.child_window(auto_id="1148", control_type="Edit").set_text(filepath)
    except Exception:
        set_ok = False
        for candidate in ("Edit", "Edit0", "Edit1", "Edit2"):
            try:
                dlg[candidate].set_text(filepath)
                set_ok = True
                break
            except Exception:
                continue
        if not set_ok:
            raise RuntimeError(
                f"Could not locate the filename field in the Save dialog to "
                f"type {filepath!r} -- dialog control layout may have changed."
            )
 
    time.sleep(0.3)
 
    # Same reasoning for the Save button -- the actual control dump from job
    # 1124 showed the literal name '&Save' (with the keyboard-accelerator
    # ampersand), not a plain 'Save'. Try both, then fall back to Enter.
    try:
        dlg["&Save"].click()
    except Exception:
        try:
            dlg["Save"].click()
        except Exception:
            dlg.type_keys("{ENTER}")
 
    time.sleep(wait_after)