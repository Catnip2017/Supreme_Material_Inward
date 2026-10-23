*** Settings ***
Documentation     SAP multi-bot coordination — shared login/close/refcount logic.
...
...               ADDED (2026-09-03): factored out of po_fetch.robot and
...               migo_103.robot, the two scripts that already had this
...               working since the 2026-08-21 multi-bot fix. Every other
...               SAP bot (gate_in, miro, migo_105, migo103_link,
...               migo105_link, miro_link, zgrn) was still doing its own
...               unconditional "kill everything, log in fresh, kill
...               everything again on the way out" -- safe only because,
...               until now, the app only ever ran one robot script at a
...               time system-wide, so "kill after I finish" and "kill
...               after EVERYONE finishes" happened to always mean the same
...               thing. Now that several bots can run at once (up to 6
...               windows for spl_rpa), that stops being true, so every
...               bot needs to check in/out through the same shared counter
...               instead of assuming it's alone.
...
...               Rather than copy-pasting po_fetch.robot's ~90 lines into
...               6 more files (which is how po_fetch.robot and
...               migo_103.robot ended up as two independently-maintained
...               copies of the same logic in the first place), every bot
...               now imports this resource and calls two keywords:
...               `Coordinated SAP Login` and `Coordinated SAP Close`.
...               One implementation, nine callers.
...
...               Also folds in a second, separate bug found while doing
...               this: `Connect To Sap Session` in gate_in.robot,
...               miro.robot, zgrn.robot, migo103_link.robot,
...               migo105_link.robot, and miro_link.robot (and, despite an
...               inaccurate comment claiming otherwise, migo_105.robot
...               too) all still bind to
...               `GetScriptingEngine.Children(0).Children(0)` -- always
...               the FIRST connection/session that exists, regardless of
...               which one this bot's own login just created. Under
...               concurrency that's not just "wrong window, job fails" --
...               it's "this bot silently types into and clicks through
...               SOMEONE ELSE'S open window," which could post against
...               the wrong document entirely. po_fetch.robot/migo_103.robot
...               already fixed this (`Get Library Instance SapGuiLibrary`,
...               which asks SapGuiLibrary directly for the session THIS
...               bot's own Open Connection call just created, instead of
...               re-deriving it by index) -- `Bind SAP Session` below is
...               that same fix, now shared.
Library           SapGuiLibrary
Library           Process
Library           OperatingSystem
Library           String
Library           Collections
Library           ${CURDIR}/sap_bot_coordination.py


*** Variables ***
# Shared across every SAP bot on this machine -- same file, same lock,
# regardless of which script or which SAP username is running.
${REFCOUNT_FILE}           C:\\temp\\sap_bot_refcount.txt
${REFCOUNT_LOCK_DIR}       C:\\temp\\sap_bot_refcount.lock


*** Keywords ***
Acquire Refcount Lock
    # Directory creation is atomic on Windows -- used as a simple mutex.
    # Retries for up to ~30s (150 x 0.2s) since the critical section can
    # cover a full login sequence, not just a quick counter-file write.
    FOR    ${attempt}    IN RANGE    1    150
        ${status}=    Run Keyword And Return Status
        ...    Create Directory    ${REFCOUNT_LOCK_DIR}
        IF    ${status}
            RETURN
        END
        Sleep    0.2s
    END
    Log    Could not acquire refcount lock after retries -- proceeding anyway to avoid deadlock.    level=WARN


Release Refcount Lock
    Run Keyword And Ignore Error    Remove Directory    ${REFCOUNT_LOCK_DIR}


Register Bot Start
    # Increments the shared counter. Creates the counter file at 0 first
    # if this is the very first bot to ever run. Uses its own short-lived
    # lock acquisition (separate from the login-sequence lock below) so
    # registering doesn't hold the lock any longer than necessary.
    Create Directory    C:\\temp
    Acquire Refcount Lock

    ${exists}=    Run Keyword And Return Status
    ...    File Should Exist    ${REFCOUNT_FILE}
    ${current}=    Set Variable    ${0}
    IF    ${exists}
        ${raw}=    Get File    ${REFCOUNT_FILE}
        ${raw}=    Strip String    ${raw}
        IF    '${raw}' != ''
            ${current}=    Convert To Integer    ${raw}
        END
    END

    ${new_count}=    Evaluate    ${current} + 1
    ${new_count_str}=    Convert To String    ${new_count}
    Create File    ${REFCOUNT_FILE}    ${new_count_str}

    Log To Console    SAP bot refcount: ${current} -> ${new_count} (this bot registered as active)

    Release Refcount Lock


Register Bot End
    # Decrements the shared counter and returns the count remaining AFTER
    # this bot's decrement. If it returns 0, this bot was the last one
    # active and should be the one to close SAP.
    Acquire Refcount Lock

    ${exists}=    Run Keyword And Return Status
    ...    File Should Exist    ${REFCOUNT_FILE}
    IF    ${exists}
        ${current}=    Get File    ${REFCOUNT_FILE}
        ${current}=    Convert To Integer    ${current}
    ELSE
        ${current}=    Set Variable    ${0}
    END

    ${new_count}=    Evaluate    max(0, ${current} - 1)
    ${new_count_str}=    Convert To String    ${new_count}
    Create File    ${REFCOUNT_FILE}    ${new_count_str}

    Log To Console    SAP bot refcount: ${current} -> ${new_count} (this bot deregistering)

    Release Refcount Lock
    RETURN    ${new_count}


Bind SAP Session
    # MULTI-BOT FIX (originally 2026-08-21, po_fetch.robot/migo_103.robot):
    # previously every bot used
    # GetScriptingEngine.Children(0).Children(0), which always grabs the
    # FIRST connection/session that exists on the machine -- fine with one
    # bot running, silently WRONG bot under concurrency (this bot would
    # bind to, and then type/click through, whichever window happened to
    # be first -- possibly another bot's open, in-progress window).
    # SapGuiLibrary already knows exactly which connection/session THIS
    # bot's own Open Connection call just created, so reuse that instead
    # of re-deriving it by index. Sets suite-scope ${session}, same as
    # every bot's own Connect To Sap Session used to -- calling suite must
    # already have `${session}    ${NONE}` declared in its own
    # *** Variables *** section.
    ${lib}=    Get Library Instance    SapGuiLibrary
    ${sess}=    Set Variable    ${lib.session}
    Set Suite Variable    ${session}    ${sess}


Coordinated SAP Login
    [Documentation]    Full login sequence, safe to call while other SAP
    ...    bots may already be running. Reuses an already-open SAP Logon
    ...    process instead of killing it; only kills-and-restarts if the
    ...    existing process turns out to be genuinely stale (can't connect
    ...    after real retries). Registers this bot in the shared counter
    ...    BEFORE touching SAP, and holds the refcount lock for the whole
    ...    login dance so two bots can never race for "which new window is
    ...    mine." Ends with ${session} bound (via Bind SAP Session) and the
    ...    lock released. Calling suite must have `${session}    ${NONE}`
    ...    declared.
    ...    FIX 12-equivalent (2026-09-04): everything after registration
    ...    is wrapped in TRY/EXCEPT so that if ANY step here fails (not
    ...    just the multi-logon popup below), the refcount increment from
    ...    Register Bot Start is rolled back before the failure is
    ...    re-raised. Before this, a failure anywhere in this keyword
    ...    left the counter permanently stuck counted as "active" --
    ...    the exact bug (found 2026-09-04 via application_9.log) that
    ...    left SAP Logon Pad stuck open overnight after 18 failed login
    ...    attempts. sap_bot_coordination.py got this fix first; this
    ...    file is the same fix, kept in sync.
    [Arguments]    ${client}    ${conn_name}    ${logon_path}    ${username}    ${password}

    Register Bot Start
    Acquire Refcount Lock

    TRY
        ${tasklist_result}=    Run Process    tasklist    /FI    IMAGENAME eq saplogon.exe
        ${already_running}=    Run Keyword And Return Status
        ...    Should Contain    ${tasklist_result.stdout}    saplogon.exe

        IF    ${already_running}
            Log To Console    SAP Logon already open -- reusing existing process, opening a new connection window under it.
        ELSE
            Log To Console    SAP Logon not running -- starting fresh.
            Start Process    ${logon_path}
            Sleep    5s
        END

        # SELF-HEALING: if we still can't connect after a real attempt, the
        # process we're trying to reuse is stale/orphaned (e.g. left over from
        # an earlier killed run) rather than just slow. Kill it and start a
        # truly fresh one rather than retrying forever.
        ${connected}=    Run Keyword And Return Status
        ...    Wait Until Keyword Succeeds    5x    2s    Connect To Session
        IF    not ${connected}
            Log To Console    Could not connect to the existing SAP Logon process -- treating it as stale, killing it and starting fresh.
            Run Keyword And Ignore Error    Run Process    taskkill    /F    /IM    saplogon.exe    /T
            Run Keyword And Ignore Error    Run Process    taskkill    /F    /IM    saplgpad.exe    /T
            Run Keyword And Ignore Error    Run Process    taskkill    /F    /IM    sapgui.exe    /T
            Sleep    2s
            Start Process    ${logon_path}
            Sleep    5s
            Wait Until Keyword Succeeds    10x    2s    Connect To Session
        END

        Open Connection    ${conn_name}

        Input Text        wnd[0]/usr/txtRSYST-MANDT    ${client}
        Input Text        wnd[0]/usr/txtRSYST-BNAME    ${username}
        Input Password    wnd[0]/usr/pwdRSYST-BCODE    ${password}
        Click Element     wnd[0]/tbar[0]/btn[0]

        # FIXED (2026-09-03): this used to select OPT1 ("end this user's other
        # SAP logons") -- carried forward from po_fetch.robot/migo_103.robot,
        # where it was safe only because the app never ran more than one SAP
        # bot at a time system-wide. Under today's per-username concurrency
        # pools (spl_rpa can now have several windows open at once, and
        # zgrn_print always logs in as the shared spl_rpa account regardless
        # of which real person clicked Print), this popup fires whenever a
        # SECOND bot under the SAME SAP username logs in while an earlier one
        # is still mid-transaction -- and OPT1 was killing that earlier bot's
        # LIVE session out from under it. Confirmed 2026-09-03: two zgrn_print
        # jobs run at once, one crashed mid-transaction with SAP's own "ABAP
        # session closed by system" the instant the other one logged in.
        #
        # That's why this was changed (2026-09-03) to press Cancel (F12)
        # instead -- aborting THIS bot's own new logon so the other bot's
        # session was never touched, at the cost of this bot's own job
        # failing and needing a retry.
        #
        # FIX 14 (2026-09-04, matching sap_bot_coordination.py): client
        # confirmed the SAP IDs these bots use (up_pawar, rk_yadav,
        # dv_vidwans, etc.) are server-side/bot-only -- no real person is
        # ever logged into them by hand elsewhere, so "the other session"
        # this popup complains about is always some other automation
        # attempt on this same server, never live human work. That removes
        # the original reason for backing off, so this now goes back to
        # ending the other session and continuing with THIS login -- using
        # the exact same shared logic (Select Multi Logon End Other Session,
        # from sap_bot_coordination.py) that gate_in.robot, miro.robot, and
        # the other Python-coordinated bots use, instead of a second,
        # separately-maintained copy of this fix living only here.
        #
        # STILL TRUE: if two bots under this SAME shared SAP ID genuinely
        # start logging in within the same few seconds on THIS server, this
        # popup can still fire for both, and ending "the other one" here
        # would then end a real, currently-running job's session -- the same
        # mechanism as the 2026-09-03 zgrn_print incident above. That risk
        # is accepted, not removed, by this change (confirmed with the
        # client, 2026-09-04).
        Sleep    3s
        ${multi}=    Run Keyword And Return Status    Element Should Be Present    wnd[1]
        IF    ${multi}
            Log To Console    Multi-logon popup shown for ${username} -- selecting 'Continue with this logon and end any other logons' and proceeding with this login.
            # CONFIRM BEFORE COMMIT (2026-09-04): only click OK on this
            # popup if Select Multi Logon End Other Session actually
            # confirms it selected "Continue with this logon..." -- never
            # click OK "blind" on whatever SAP had pre-selected by
            # default, since that default is "Terminate this logon", the
            # wrong one. If it can't confirm, back off the same safe way
            # this keyword used to (Cancel + fail cleanly, letting the
            # TRY/EXCEPT above roll back the refcount and this job retry)
            # instead of guessing.
            ${confirmed}=    Select Multi Logon End Other Session    ${username}
            IF    ${confirmed}
                Run Keyword And Ignore Error    Click Element    wnd[1]/tbar[0]/btn[0]
                Sleep    2s
            ELSE
                Run Keyword And Ignore Error    Send VKey    wnd[1]    12
                Fail    SAP multi-logon conflict for ${username} -- another session for this username is already logged in elsewhere, and this bot could not positively confirm the 'Continue with this logon' button on the popup shown, so it cancelled its own new logon instead of guessing. This job can safely retry.
            END
        END

        Dismiss Any Popup
        Maximize Window    0
        Bind SAP Session

        # Only release the lock once THIS bot's own session is fully bound --
        # from here on, another bot is free to open its own connection without
        # any chance of grabbing this bot's window by mistake.
        Release Refcount Lock
    EXCEPT    AS    ${login_error}
        # Roll back the Register Bot Start increment inline (not via
        # Register Bot End, which tries to Acquire Refcount Lock itself --
        # we are still holding that same lock here, and it is not
        # reentrant) so this bot's failed login never stays counted as
        # an active session.
        ${cnt_exists}=    Run Keyword And Return Status    File Should Exist    ${REFCOUNT_FILE}
        IF    ${cnt_exists}
            ${cnt}=    Get File    ${REFCOUNT_FILE}
            ${cnt}=    Convert To Integer    ${cnt}
            ${cnt}=    Evaluate    max(0, ${cnt} - 1)
            ${cnt_str}=    Convert To String    ${cnt}
            Create File    ${REFCOUNT_FILE}    ${cnt_str}
            Log To Console    SAP bot refcount: rolled back to ${cnt_str} (this bot's login/setup failed, so it never became a real active session and must not stay counted as one).
        END
        Release Refcount Lock
        Fail    ${login_error}
    END


Coordinated SAP Close
    [Documentation]    Always closes THIS bot's own SAP session/tab.
    ...    Only fully kills the shared SAP processes if this was the LAST
    ...    bot still registered (refcount reaches 0 after this bot
    ...    deregisters) -- otherwise leaves everything running for
    ...    whichever other bot(s) are still active. Calling suite must
    ...    have ${session} bound (from Coordinated SAP Login).
    ${remaining}=    Register Bot End

    Log    Closing this bot's SAP session tab...
    Run Keyword And Ignore Error    Input Text    wnd[0]/tbar[0]/okcd    /nex
    Run Keyword And Ignore Error    Send VKey     wnd[0]    0
    Sleep    2s

    IF    ${remaining} > 0
        Log To Console    Another bot (or bots) still active (refcount=${remaining}) -- this bot's tab is closed, but the shared SAP processes stay running for the other bot(s).
    ELSE
        Log    This was the last active bot -- shutting down all SAP processes fully...
        Run Keyword And Ignore Error    Run Process    taskkill    /F    /IM    saplogon.exe
        Run Keyword And Ignore Error    Run Process    taskkill    /F    /IM    saplgpad.exe
        Run Keyword And Ignore Error    Run Process    taskkill    /F    /IM    sapgui.exe
        Sleep    2s
        Run Keyword And Ignore Error    Run Process    taskkill    /F    /IM    saplogon.exe    /T
        Run Keyword And Ignore Error    Run Process    taskkill    /F    /IM    saplgpad.exe    /T
        Run Keyword And Ignore Error    Run Process    taskkill    /F    /IM    sapgui.exe    /T
        Log    SAP session closed and process terminated.    level=INFO
    END


Dismiss Any Popup
    ${popup1}=    Run Keyword And Return Status    Element Should Be Present    wnd[1]
    IF    ${popup1}
        Run Keyword And Ignore Error    Click Element    wnd[1]/tbar[0]/btn[0]
        Sleep    1s
    END
    ${popup2}=    Run Keyword And Return Status    Element Should Be Present    wnd[2]
    IF    ${popup2}
        Run Keyword And Ignore Error    Click Element    wnd[2]/tbar[0]/btn[0]
        Sleep    1s
    END
