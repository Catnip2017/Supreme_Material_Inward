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
    [Arguments]    ${client}    ${conn_name}    ${logon_path}    ${username}    ${password}

    Register Bot Start
    Acquire Refcount Lock

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

    # Matches production's already-running choice (confirmed still in use
    # in po_fetch.robot/migo_103.robot as of this file's creation) -- OPT1
    # ends this username's other SAP logons rather than coexisting with
    # them. That's deliberately fine for the normal path here: joining an
    # already-open connection above (createSession-style reuse) never
    # triggers this popup at all, so OPT1 only ever fires in the rare case
    # where a genuinely separate/stale logon for this username exists
    # outside this bot's own tracking -- logged loudly below so it's
    # visible in the rare event it ever ends something that mattered.
    Sleep    3s
    ${multi}=    Run Keyword And Return Status    Element Should Be Present    wnd[1]
    IF    ${multi}
        Log To Console    WARNING: multi-logon popup shown for ${username} -- selecting OPT1 (ends this user's other SAP logons). If another bot for this same username was legitimately still active, its window just closed.
        Run Keyword And Ignore Error    Select Radio Button    wnd[1]/usr/radMULTI_LOGON_OPT1
        Run Keyword And Ignore Error    Click Element          wnd[1]/tbar[0]/btn[0]
        Sleep    2s
    END

    Dismiss Any Popup
    Maximize Window    0
    Bind SAP Session

    # Only release the lock once THIS bot's own session is fully bound --
    # from here on, another bot is free to open its own connection without
    # any chance of grabbing this bot's window by mistake.
    Release Refcount Lock


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
