import os
import time
import pywinauto


def _wait_until_dialog_closed(title_re, timeout=8):
    """
    ADDED (2026-08-25): polls until no top-level window matching title_re
    remains on screen, or timeout elapses. Best-effort -- if it times out we
    just move on (this is a defensive wait to stop the NEXT dialog with the
    same title from becoming ambiguous, not a hard requirement for this
    dialog itself, which has already been saved and clicked through by the
    time this runs).
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            matches = pywinauto.Desktop(backend="win32").windows(title_re=title_re)
        except Exception:
            matches = []
        if not matches:
            return True
        time.sleep(0.3)
    return False


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
    # FIX (2026-08-27, round 3): CONFIRMED PRODUCTION CASE (history_id=514,
    # output 14.xml/15.xml) -- the on-disk check added in round 2 only
    # checked os.path.isfile(), which a STALE file from an earlier same-day
    # retry (filenames are fully deterministic per material doc + date, see
    # zgrn.robot) can satisfy even when THIS run's save silently failed
    # again (e.g. same missed-overwrite-dialog scenario). That's exactly
    # why execute_zgrn_print_sap's own check (rf_runner.py) uses a _fresh()
    # helper requiring mtime >= this run's start time -- and it correctly
    # kept rejecting the file as stale even after round 2's weaker check
    # here started passing. Recording the real start time now so the
    # verification at the end can apply the same freshness requirement,
    # instead of just existence.
    call_started_at = time.time()
    time.sleep(wait_before)

    app = None
    dlg_handle = None
    deadline = time.time() + timeout
    last_error = None
    while time.time() < deadline:
        try:
            # FIX (2026-08-27, round 2): CONFIRMED PRODUCTION BUG (output
            # 12.xml/log 8.html) -- title_re alone isn't specific enough.
            # A SAP-owned window (visible caption "Print:", real class
            # WindowsFormsSapFocus per the ElementNotFoundError's parent
            # info) apparently has an internal title that ALSO satisfies
            # "Save Print Output As.*", even though it isn't the real
            # dialog and its caption doesn't visually say that. connect()
            # matched THAT window instead of the genuine native Save
            # dialog sitting behind/around it (confirmed via screenshot --
            # the real dialog was open and completely normal, with a
            # visible, working File name box). class_name="#32770" is the
            # standard Win32 class for every native common dialog (Open/
            # Save/etc.) -- SAP's own windows are never this class, so
            # adding it here makes a SAP window structurally impossible to
            # match, regardless of what title text it happens to expose.
            app = pywinauto.Application().connect(
                title_re="Save Print Output As.*", class_name="#32770"
            )
            # FIX (2026-08-27): CONFIRMED PRODUCTION BUG (history_id/job from
            # output 11.xml, zgrn print_twice) -- connect() above succeeded
            # against exactly one dialog, but the ORIGINAL code then called
            # `app.window(title_re="Save Print Output As.*")` a SECOND time
            # further down (previously line 97) to get `dlg`. pywinauto
            # resolves that lazily -- the actual window lookup only happens
            # when a method like .set_focus() is called on it -- so by the
            # time that lookup actually ran (a second or so later), the
            # print_twice flow's SECOND dialog had already opened, making
            # that second by-title search ambiguous even though connect()
            # itself was fine. Same root cause as the connect()-time fallback
            # right below, just resurfacing at a different call site because
            # of pywinauto's lazy resolution -- the 2026-08-25 fix only
            # covered the connect() call, not this second re-search. Fixed
            # by capturing this dialog's concrete window handle immediately
            # after a successful connect, so nothing later ever re-searches
            # by title again.
            dlg_handle = app.top_window().handle
            break
        except Exception as e:
            # FIX (2026-08-25): CONFIRMED PRODUCTION BUG (history_id=408, job
            # 1432, output 8.xml) -- zgrn.robot's print_twice flow calls this
            # function twice back to back (once for the "GRN" copy, once for
            # "CGD"). The second call's connect() found TWO windows matching
            # "Save Print Output As.*" -- "There are 2 elements that match
            # the criteria" -- because the FIRST dialog from moments earlier
            # hadn't actually been destroyed as a window yet when the second
            # one opened. That's an ambiguous match, not "never appeared",
            # but the retry loop kept hitting the same ambiguity every 0.5s
            # for the full 15s timeout (it doesn't resolve on its own) and
            # ultimately failed the whole print job -- both copies ended up
            # missing from disk. Primary fix is below (wait for THIS dialog
            # to actually close before returning, so the SECOND call never
            # finds a leftover one) -- this is the defensive fallback for
            # if ambiguity still happens anyway (e.g. some other automation's
            # own dialog on the same shared desktop): rather than guarantee
            # failure, connect directly to the most recently created match
            # by window handle instead of by title alone.
            if "elements that match" in str(e) or "Ambiguous" in type(e).__name__:
                try:
                    candidates = pywinauto.Desktop(backend="win32").windows(
                        title_re="Save Print Output As.*", class_name="#32770"
                    )
                    if candidates:
                        dlg_handle = candidates[-1].handle
                        app = pywinauto.Application().connect(handle=dlg_handle)
                        break
                except Exception:
                    pass
            last_error = e
            time.sleep(0.5)

    if app is None or dlg_handle is None:
        raise RuntimeError(
            f"'Save Print Output As' dialog never appeared within {timeout}s "
            f"-- cannot save {filepath!r}. Last connect error: {last_error}"
        )

    # Resolve by the CONCRETE handle captured above, never by title_re again
    # -- see the FIX comment above for why a second by-title search here was
    # the actual bug, even after the connect()-time fix.
    dlg = app.window(handle=dlg_handle)
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
    # FIX (2026-08-31): CONFIRMED PRODUCTION CASE (history_id=538, material
    # doc 5000078570) -- this lookup used to be a single shot: try auto_id
    # 1148, and if that raises, immediately try each bare Edit/Edit0/Edit1/
    # Edit2 fallback once each, and give up for good if every single one of
    # those also failed. The RF log showed the real "Save Print Output As"
    # dialog was genuinely open and visually normal at the time (confirmed
    # via screenshot) -- but ALL FIVE lookups failed at once, which a wrong
    # control ID alone can't explain (the name-based fallbacks aren't tied
    # to any specific ID). The much more likely explanation: this is the
    # Explorer-style Save dialog, and its child controls -- including the
    # filename box -- aren't guaranteed to be enumerable via UI Automation
    # the instant the dialog appears, especially if the target folder
    # (zgrn_print) is slow to list because it's accumulated a large number
    # of files over time. pywinauto's own internal wait on each lookup is
    # short and wasn't enough. Now retries the WHOLE auto_id+fallback
    # sequence up to 3 times, 1.5s apart, before giving up -- giving the
    # dialog's control tree more real time to finish populating rather than
    # failing the run the first time it isn't ready yet.
    set_ok = False
    last_lookup_error = None
    for lookup_attempt in range(1, 4):
        try:
            dlg.child_window(auto_id="1148", control_type="Edit").set_text(filepath)
            set_ok = True
            break
        except Exception as e:
            last_lookup_error = e
        for candidate in ("Edit", "Edit0", "Edit1", "Edit2"):
            try:
                dlg[candidate].set_text(filepath)
                set_ok = True
                break
            except Exception as e:
                last_lookup_error = e
                continue
        if set_ok:
            break
        if lookup_attempt < 3:
            print(
                f"Filename field not found in Save dialog yet (attempt "
                f"{lookup_attempt}/3) -- dialog controls may still be "
                f"loading, retrying in 1.5s."
            )
            time.sleep(1.5)
    if not set_ok:
        raise RuntimeError(
            f"Could not locate the filename field in the Save dialog to "
            f"type {filepath!r} after 3 attempts -- dialog control layout "
            f"may have changed, or the dialog never finished loading. "
            f"Last error: {last_lookup_error}"
        )

    time.sleep(0.3)

    # FIX (2026-08-29): CONFIRMED PRODUCTION CASE (history_id=525, material
    # doc 5000078177) -- the Save dialog was open and correctly filled with
    # the right filename (screenshot confirmed), yet nothing was ever
    # written and the dialog never closed. Ruled out a desktop/DMS
    # collision this time (application.log confirmed ZGRN correctly waited
    # for a clear desktop before starting at all). That points at the
    # click itself occasionally not registering, with zero trace in the
    # log either way -- a single click attempt followed by only a passive
    # wait can't recover from that. Wrapped the whole click+confirm+verify
    # sequence in a retry loop (up to 3 attempts): if the file still isn't
    # freshly written after a short poll, and the dialog is still there,
    # try clicking Save again rather than only ever giving up after one
    # attempt.
    file_written = False
    stale_file_seen = False
    expected_path = filepath + ".pdf"
    max_save_attempts = 3
    for save_attempt in range(1, max_save_attempts + 1):
        if save_attempt > 1:
            print(
                f"Save click attempt {save_attempt}/{max_save_attempts} for "
                f"{filepath!r}: file not written yet after previous attempt "
                f"-- retrying the click."
            )
            try:
                if not dlg.exists():
                    # Dialog is already gone -- nothing left to click.
                    # Nothing more this loop can do; break out and let the
                    # final check below report the failure honestly.
                    break
                dlg.set_focus()
            except Exception:
                break

        # Same reasoning for the Save button -- the actual control dump
        # from job 1124 showed the literal name '&Save' (with the
        # keyboard-accelerator ampersand), not a plain 'Save'. Try both,
        # then fall back to Enter.
        try:
            dlg["&Save"].click()
        except Exception:
            try:
                dlg["Save"].click()
            except Exception:
                try:
                    dlg.type_keys("{ENTER}")
                except Exception:
                    pass

        # FIX (2026-08-21): the GRN filename is fully deterministic --
        # <MIGO_NUMBER>_<DDMMYYYY>_<GRN|CGD>.pdf -- so printing the same
        # MIGO document a second time on the same day (a normal retry
        # after a failed/timed-out first attempt, or an intentional
        # reprint) hits an already-existing file. Windows then raises its
        # own "Confirm Save As" overwrite dialog ON TOP of this Save
        # dialog. Same failure class as the SendKeys bug fixed above: the
        # click on Save above still "succeeds" (it did open a dialog, just
        # not the one we expected), the RF log shows PASS, but nothing
        # gets written until this second dialog is answered -- and
        # rf_runner.py's execute_zgrn_print_sap only checks whether the
        # PDF exists on disk afterward, which a STALE file from an earlier
        # run would already satisfy, masking the failure entirely.
        # Overwriting is the intended outcome for a legitimate reprint, so
        # this confirms "Yes" if that dialog appears; if it never appears
        # (first print of the day), this is a fast no-op.
        try:
            # FIX (2026-08-27): CONFIRMED PRODUCTION CASE (history_id=460,
            # job 1568, output 13.xml) -- both Type Save Filename calls
            # (GRN and CGD) reported PASS with zero exceptions, but the CGD
            # PDF never actually landed on disk. That run was a same-day
            # retry for a material doc already printed earlier, so this
            # overwrite-confirm dialog very likely DID appear -- but the
            # old 3s window, under a desktop already busy with multiple
            # popups that run, was too short to catch it. Bumped to 8s
            # (matches the dialog-close wait below) to give it a realistic
            # chance under load.
            confirm_deadline = time.time() + 8
            confirm_app = None
            confirm_handle = None
            while time.time() < confirm_deadline:
                try:
                    confirm_app = pywinauto.Application().connect(title_re="Confirm Save As")
                    # Same handle-capture fix as above, applied here too
                    # for consistency/defense-in-depth even though no
                    # ambiguity has been observed on this specific dialog
                    # yet.
                    confirm_handle = confirm_app.top_window().handle
                    break
                except Exception:
                    time.sleep(0.3)
            if confirm_app is not None and confirm_handle is not None:
                confirm_dlg = confirm_app.window(handle=confirm_handle)
                confirm_dlg.set_focus()
                try:
                    confirm_dlg["&Yes"].click()
                except Exception:
                    try:
                        confirm_dlg["Yes"].click()
                    except Exception:
                        confirm_dlg.type_keys("{ENTER}")
                time.sleep(0.5)
        except Exception:
            # Defensive only -- never let the overwrite-confirmation check
            # itself break a save that otherwise worked fine (no existing
            # file today).
            pass

        time.sleep(wait_after if save_attempt == 1 else 1.0)

        # Poll for the file on THIS attempt -- shorter than the original
        # single-shot 5s budget (3s here) since we may loop again; total
        # budget across all 3 attempts still comfortably fits inside the
        # calling robot's own step timeout.
        poll_deadline = time.time() + 3
        while time.time() < poll_deadline:
            if os.path.isfile(expected_path):
                try:
                    if os.path.getmtime(expected_path) >= call_started_at - 1:
                        file_written = True
                        break
                    stale_file_seen = True
                except OSError:
                    pass
            time.sleep(0.5)

        if file_written:
            break

    # FIX (2026-08-25): the actual root-cause prevention for the bug
    # described above -- wait here until THIS "Save Print Output As" dialog
    # has genuinely closed before handing control back to zgrn.robot. That
    # way, by the time a print_twice run triggers its second print, there is
    # nothing left over for that second call's connect() to be ambiguous
    # about. Best-effort: if it's still lingering after 8s, log a warning
    # and move on rather than hanging the whole run over a UI cleanup
    # formality -- the fallback added above still gives the next call a
    # chance to recover even if this wait times out.
    closed = _wait_until_dialog_closed("Save Print Output As.*", timeout=8)
    if not closed:
        print(
            f"WARNING: 'Save Print Output As' dialog for {filepath!r} did not "
            f"fully close within 8s after Save -- a subsequent print in the "
            f"same run may hit an ambiguous-window error."
        )

    # FIX (2026-08-27, extended 2026-08-29): CONFIRMED PRODUCTION CASE --
    # see the comment above the retry loop. This function previously
    # trusted that a clean run through the UI steps (no exceptions) meant
    # the file was actually written -- but a missed overwrite-confirm
    # dialog, or a click that simply didn't register, could let it return
    # normally with nothing on disk, and neither zgrn.robot nor the caller
    # ever knew until execute_zgrn_print_sap's file-existence check caught
    # it well after the fact. file_written/stale_file_seen/expected_path
    # are set by the retry loop above (up to 3 click attempts, each with
    # its own short poll) -- this is just the final honest report if none
    # of those attempts actually got the file written.
    if not file_written:
        detail = (
            "a file with that name already exists from an earlier attempt "
            "today, but it was NOT updated by this run"
            if stale_file_seen else
            "no such file was found at all"
        )
        raise RuntimeError(
            f"'Save Print Output As' dialog was completed with no error, but "
            f"{expected_path!r} was not freshly written after {max_save_attempts} "
            f"click attempt(s) ({detail}) -- the save likely didn't actually "
            f"go through. Not reporting success."
        )
