*** Settings ***
Documentation     Update Gate In entry with PO — TCODE: zgatein_update
...               Enter the gate in sequence number and execute.
...               SAP automatically links the PO from MIGO 103 to the gate in record.
...               Variables: GATE_IN_NUMBER, HISTORY_ID
...
...               MULTI-BOT COORDINATION (2026-09-03): login/close now go
...               through sap_coordination.resource instead of an
...               unconditional taskkill on the way in and out. Also fixes
...               the grid-cell-write step below, which used to re-derive
...               its own session via the buggy Children(0).Children(0)
...               index lookup instead of the correctly-bound ${session}.
...               See sap_coordination.resource for the full reasoning.
Library           SapGuiLibrary
Library           Process
Library           OperatingSystem
Library           String
Library           ${CURDIR}/sap_bot_coordination.py

*** Variables ***
${GATE_IN_NUMBER}       ${EMPTY}
${PO_NUMBER}            ${EMPTY}
${TCODE}                zgatein_update
${HISTORY_ID}           ${EMPTY}
${session}              ${NONE}

*** Test Cases ***
Update Gate In PO
    [Setup]    Initialize SAP And Login
    ${status}=    Enter Gate In And Execute
    Log To Console    RESULT:GATEIN_UPDATE_STATUS:${status}
    Sleep    3s
    [Teardown]    Close SAP Session

*** Keywords ***
Initialize SAP And Login
    ${env_path}=    Join Path    ${EXECDIR}    .env
    Evaluate    __import__('dotenv').load_dotenv(r'''${env_path}''')

    ${CLIENT}=      Evaluate    __import__('os').getenv('SAP_CLIENT')
    ${CONN_NAME}=   Evaluate    __import__('os').getenv('SAP_CONNECTION_NAME')
    ${LOGON_PATH}=  Evaluate    __import__('os').getenv('SAP_LOGON_PATH')

    # v16: per-user SAP credential pass-through (LDAP users only) -- see
    # gate_in.robot for the full explanation.
    ${USER_OVERRIDE}=    Evaluate    __import__('os').getenv('SAP_USER_OVERRIDE', '')
    ${PASS_OVERRIDE}=    Evaluate    __import__('os').getenv('SAP_PASS_OVERRIDE', '')
    IF    $USER_OVERRIDE != '' and $PASS_OVERRIDE != ''
        ${USERNAME}=    Set Variable    ${USER_OVERRIDE}
        ${PASSWORD}=    Set Variable    ${PASS_OVERRIDE}
        Log To Console    SAP LOGIN: using per-user credential for ${USERNAME}
    ELSE
        ${USERNAME}=    Evaluate    __import__('os').getenv('SAP_USERNAME')
        ${PASSWORD}=    Evaluate    __import__('os').getenv('SAP_PASSWORD')
        Log To Console    SAP LOGIN: using shared .env credential (${USERNAME})
    END

    Should Not Be Empty    ${LOGON_PATH}    SAP_LOGON_PATH not found in .env
    Should Not Be Empty    ${CONN_NAME}     SAP_CONNECTION_NAME not found in .env
    Should Not Be Empty    ${CLIENT}        SAP_CLIENT not found in .env
    Should Not Be Empty    ${USERNAME}      SAP username not resolved (no override, and SAP_USERNAME not found in .env)
    Should Not Be Empty    ${PASSWORD}      SAP password not resolved (no override, and SAP_PASSWORD not found in .env)

    # FIX (2026-09-03): this keyword validated the resolved
    # credentials but never actually called a login keyword or bound
    # ${session} -- added as part of migrating every bot onto
    # sap_bot_coordination.py.
    ${session}=    Coordinated SAP Login    ${LOGON_PATH}    ${CONN_NAME}    ${CLIENT}    ${USERNAME}    ${PASSWORD}
    Set Suite Variable    ${session}


Enter Gate In And Execute
    # Navigate to tcode
    SapGuiLibrary.Run Transaction     ${TCODE}
    #Send VKey     wnd[0]    0
    Sleep    2s
    Dismiss Any Popup

    # Wait for the zgatein_update screen to load
    Wait Until Keyword Succeeds    10s    1s    Element Should Be Present    wnd[0]/usr/ctxtZSEQ

    # Enter the gate in sequence number
    Set Focus     wnd[0]/usr/ctxtZSEQ
    SapGuiLibrary.Input Text     wnd[0]/usr/ctxtZSEQ       ${GATE_IN_NUMBER}
    Set Focus     wnd[0]/usr/ctxtZSEQ
    Sleep    1s
      # Click Execute
    SapGuiLibrary.Click Element     wnd[0]/tbar[1]/btn[8]
    Sleep     3s
    # MULTI-BOT FIX (2026-09-03): used to re-derive its own session here via
    # Children(0).Children(0) (always the FIRST SAP connection/session on
    # the machine) instead of reusing ${session}, which Coordinated SAP
    # Login already bound correctly to THIS bot's own connection.
    ${ns}=    Create Dictionary    session=${session}
    # Get the grid object
    ${grid}=    Evaluate
    ...    session.findById("wnd[0]/usr/cntlGRID1/shellcont/shell/shellcont[1]/shell")
    ...    namespace=${ns}
    ${gns}=    Create Dictionary    grid=${grid}
    # Modify the cell — row 0, column ZEBELN, value from PO_NUMBER
    Evaluate    grid.modifyCell(0, "ZEBELN", "${PO_NUMBER}")    namespace=${gns}
    Sleep    1s
    Log To Console    ✅ Cell ZEBELN row 0 set to ${PO_NUMBER}
    # Update the grid cell
    #SapGuiLibrary.Select Cell    wnd[0]/usr/cntlGRID1/shellcont/shell/shellcont[1]/shell    0    ZEBELN
    #SapGuiLibrary.Modify Cell    wnd[0]/usr/cntlGRID1/shellcont/shell/shellcont[1]/shell    0    ZEBELN    12345678
    # Set current column (if required)
    #SAP Set Current Cell Column    wnd[0]/usr/cntlGRID1/shellcont/shell/shellcont[1]/shell    ZEBELN
    # Click Save
    SapGuiLibrary.Click Element    wnd[0]/tbar[0]/btn[11]
    # Execute (F8) — SAP auto-links the PO from MIGO 103
    SapGuiLibrary.Click Element    wnd[0]/tbar[1]/btn[8]
    Sleep    3s
    Dismiss Any Popup

    # --- READBACK (ADDED 2026-08-29): verify the actual data, not just the
    # status bar text -- confirmed real case (history_id=511/512, GIN
    # 0000040889/0000040888) where SAP genuinely saved the PO number but
    # the status bar came back completely blank, producing a false
    # MANUAL_CHECK_REQUIRED even though nothing was actually wrong (the
    # user independently confirmed the PO was correctly linked in SAP for
    # both). Re-finds the grid FRESH via session.findById (not the ${grid}
    # reference captured before Save/Execute, which may not reflect the
    # post-Execute screen state) and reads the same cell straight back --
    # if it now shows the PO number we just set, that is definitive proof
    # it worked, independent of whatever the status bar does or doesn't
    # say. Same "read back what was actually kept" pattern already used in
    # migo_103.robot for its own MATNR verification.
    #
    # Wrapped in Run Keyword And Ignore Error: if the screen state isn't
    # readable at all anymore (e.g. a genuine session-level failure), this
    # just fails to confirm rather than crashing the whole run -- falls
    # through to the existing status-bar logic below unchanged.
    ${readback_status}    ${readback_value}=    Run Keyword And Ignore Error
    ...    Evaluate
    ...    session.findById("wnd[0]/usr/cntlGRID1/shellcont/shell/shellcont[1]/shell").getCellValue(0, "ZEBELN")
    ...    namespace=${ns}
    IF    '${readback_status}' == 'PASS'
        Log To Console    READBACK: ZEBELN cell now shows "${readback_value}" (expected "${PO_NUMBER}")
        ${po_clean}=          Strip String    ${PO_NUMBER}
        ${readback_clean}=    Strip String    ${readback_value}
        IF    '${readback_clean}' == '${po_clean}'
            RETURN    SUCCESS
        END
    ELSE
        Log To Console    READBACK: could not re-read ZEBELN cell (${readback_value}) -- falling back to status bar check.
    END

    # Check status bar
    # FIX (2026-08-29): this used to be a single, immediate Get Value --
    # confirmed real-world case (history_id=511, GIN 0000040889) where SAP
    # had NOT yet refreshed the status bar at the exact instant this ran,
    # so it read back empty even though the update itself may have gone
    # through. migo_103.robot already solved this identical class of
    # timing issue with a short retry loop (Read Status Bar With Retry) --
    # applying the same pattern here: retry a few times over a few seconds
    # instead of judging success/failure off a single, possibly-premature
    # read.
    ${status_msg}=    Read Status Bar With Retry
    Log    zgatein_update status: "${status_msg}"    level=INFO
    Log To Console    Status bar: ${status_msg}

    # Check for known error keywords first
    ${is_error}=    Run Keyword And Return Status
    ...    Should Match Regexp    ${status_msg}    (?i)(error|not found|authorization|invalid|failed|no authorization)
    IF    ${is_error}
        Log    SAP returned error: ${status_msg}    level=ERROR
        Log To Console    RESULT:GATEIN_UPDATE_STATUS:MANUAL_CHECK_REQUIRED
        RETURN    MANUAL_CHECK_REQUIRED
    END

    # Check for success keywords
    @{matches}=    Get Regexp Matches    ${status_msg}    (?i)(success|saved|updated|document|posted|changed)
    IF    len($matches) > 0
        RETURN    SUCCESS
    ELSE IF    '${status_msg}' != ''
        # Status bar has content but no known keyword — log and treat as success
        # Review the message and add the keyword to the success pattern above
        Log    Unknown status bar message (treating as SUCCESS — verify manually): ${status_msg}    level=WARN
        RETURN    SUCCESS
    ELSE
        Log To Console    RESULT:GATEIN_UPDATE_STATUS:MANUAL_CHECK_REQUIRED
        RETURN    MANUAL_CHECK_REQUIRED
    END

Read Status Bar With Retry
    # ADDED (2026-08-29): mirrors migo_103.robot's keyword of the same name
    # -- retries up to 5 times, 1s apart, instead of trusting a single
    # possibly-premature read. Unlike migo_103.robot's version, there's no
    # fixed digit pattern to wait for here (success is judged by keyword
    # matching against whatever text eventually shows up, not a document
    # number) -- so this simply keeps retrying until the status bar is
    # non-empty, or gives up after 5 attempts and returns whatever the
    # last (possibly still blank) read was.
    ${msg}=    Set Variable    ${EMPTY}
    FOR    ${attempt}    IN RANGE    1    6
        ${msg}=    Get Value    wnd[0]/sbar
        Log To Console    Status bar attempt ${attempt}: "${msg}"
        ${not_empty}=    Run Keyword And Return Status    Should Not Be Empty    ${msg}
        IF    ${not_empty}    RETURN    ${msg}
        Sleep    1s
    END
    Log To Console    Status bar still empty after 5 attempts.
    RETURN    ${msg}


Dismiss Any Popup
    ${present}=    Run Keyword And Return Status    Element Should Be Present    wnd[1]
    IF    ${present}
        Run Keyword And Ignore Error    Click Element    wnd[1]/tbar[0]/btn[0]
    END

Close SAP Session
    Coordinated Close SAP Session    ${session}
