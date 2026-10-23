
*** Settings ***
Documentation     MIRO SAP Automation — TCODE: ZMM35
...               Steps:
...               1. SAP Login with credentials
...               2. Enter ZMM35 tcode
...               3. Set document date: From = 3 days ago, To = today
...               4. Select Parked Documents radio button and Execute
...               5. Dynamically select each document and verify Reference Number
...               6. Change Posting Date to current date
...               7. Go to Withholding Tax tab — remove second TDS row entirely
...               8. Simulate → Post
Library           SapGuiLibrary
Library           Process
Library           OperatingSystem
Library           String
Library           DateTime
Library           Collections
Library           SapGuiLibrary    screenshots_on_failure=False
Library           ${CURDIR}/sap_bot_coordination.py
*** Variables ***

${POSTING_DATE}           ${EMPTY}
${DATE_FROM}              ${EMPTY}
${DATE_TO}                ${EMPTY}
${CLIENT}                 ${EMPTY}
${USERNAME}               ${EMPTY}
${PASSWORD}               ${EMPTY}
${CONN_NAME}              ${EMPTY}
${LOGON_PATH}             ${EMPTY}
# MULTI-BOT FIX (2026-09-03): this used to be re-derived by raw index
# (GetScriptingEngine.Children(0).Children(0)) at 3 separate call sites in
# this file every time the grid needed to be read directly -- always grabbed
# the FIRST SAP connection/session on the machine, which under concurrency
# could silently be a DIFFERENT bot's open window. Now bound once at login
# via Bind SAP Session (see sap_coordination.resource) and reused from here.
${session}                ${NONE}
# Passed from rf_runner.py
# ${MATERIAL_DOC_NUMBER}    ${EMPTY}    # From MIGO 103 DB — used to find the right row in list
${REFERENCE_NUMBER}       ${EMPTY}    # Invoice number — verified against document after 105
${PO_NUMBER}              ${EMPTY}    # Purchase order number (optional filter)
 
# These are computed at runtime — do not set manually
${POSTING_DATE}           ${EMPTY}    # Filled dynamically = today
${DATE_FROM}              ${EMPTY}    # Filled dynamically = today minus 3 days
${DATE_TO}                ${EMPTY}    # Filled dynamically = today
 
*** Test Cases ***
Execute MIRO
    [Setup]    Initialize SAP and Login
    Execute MIRO Flow
    [Teardown]    Close SAP Session
    # Debug Find Delete Button
 
*** Keywords ***
 
# ─────────────────────────────────────────────
# SAP LOGIN
# ─────────────────────────────────────────────
 
Initialize SAP and Login
    [Documentation]    Loads .env credentials, launches SAP Logon, logs in, handles multi-logon popup.
    ${env_path}=    Join Path    ${EXECDIR}    .env
    Evaluate    __import__('dotenv').load_dotenv(r'''${env_path}''')
    ${CLIENT}=      Evaluate    __import__('os').getenv('SAP_CLIENT')
    ${CONN_NAME}=   Evaluate    __import__('os').getenv('SAP_CONNECTION_NAME')

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

    ${LOGON_PATH}=    Evaluate    __import__('os').getenv('SAP_LOGON_PATH')
    # Every other bot in this app requires SAP_LOGON_PATH from .env with no
    # code-level fallback (see gate_in.robot/migo_105.robot/etc.) -- this
    # used to be the one script with its own hardcoded saplogon.exe path
    # instead of reading .env at all. Matching the same convention here
    # rather than hand-writing an escaped Windows path inside this Evaluate
    # call, which is its own source of subtle escaping bugs.
    Should Not Be Empty    ${LOGON_PATH}    SAP_LOGON_PATH not found in .env

    # MULTI-BOT FIX (2026-09-03): used to unconditionally taskkill
    # saplogon.exe/saplgpad.exe here every run -- see sap_coordination.resource
    # for why that's no longer safe under concurrency. Also now binds
    # ${session} correctly (Coordinated SAP Login -> Bind SAP Session)
    # instead of the 3 separate raw Children(0).Children(0) lookups this
    # file used to do further down whenever it needed the grid directly.
    ${session}=    Coordinated SAP Login    ${LOGON_PATH}    ${CONN_NAME}    ${CLIENT}    ${USERNAME}    ${PASSWORD}
    Set Suite Variable    ${session}

    Log    SAP login successful    level=INFO
 
 
# ─────────────────────────────────────────────
# UTILITY KEYWORDS
# ─────────────────────────────────────────────
 
Clean Value
    # NOTE: previously ended with Split String (limit=1) + ${parts}[0],
    # returning only the first word -- same bug as gate_in.robot's old
    # Clean Material and migo_103/105.robot's old Clean Value. Not currently
    # called anywhere in this file (fields are used raw), but fixed
    # pre-emptively so it doesn't carry the bug forward if wired in later.
    [Documentation]    Strips currency symbols, commas, and leading/trailing spaces from a value.
    [Arguments]    ${raw_value}
    ${cleaned}=    Strip String    ${raw_value}
    ${cleaned}=    Replace String    ${cleaned}    ₹    ${EMPTY}
    ${cleaned}=    Replace String    ${cleaned}    $    ${EMPTY}
    ${cleaned}=    Replace String    ${cleaned}    €    ${EMPTY}
    ${cleaned}=    Replace String    ${cleaned}    £    ${EMPTY}
    ${cleaned}=    Replace String    ${cleaned}    ,    ${EMPTY}
    ${cleaned}=    Strip String    ${cleaned}
    RETURN    ${cleaned}
 
 
Dismiss Any Popup
    [Documentation]    Checks for popups on wnd[1] and wnd[2] and dismisses them with Enter.
    ${popup1}=    Run Keyword And Return Status    Element Should Be Present    wnd[1]
    IF    ${popup1}
        Log    Popup on wnd[1] — dismissing    level=WARN
        Run Keyword And Ignore Error    Click Element    wnd[1]/tbar[0]/btn[0]
        Sleep    1s
    END
    ${popup2}=    Run Keyword And Return Status    Element Should Be Present    wnd[2]
    IF    ${popup2}
        Log    Popup on wnd[2] — dismissing    level=WARN
        Run Keyword And Ignore Error    Click Element    wnd[2]/tbar[0]/btn[0]
        Sleep    1s
    END
 
 
Read Status Bar With Retry
    [Documentation]    Polls the SAP status bar up to 5 times looking for a pattern match.
    [Arguments]    ${expected_pattern}=\\d{4,}
    ${msg}=    Set Variable    ${EMPTY}
    FOR    ${attempt}    IN RANGE    1    6
        ${msg}=    Get Value    wnd[0]/sbar
        Log    Status bar attempt ${attempt}: "${msg}"    level=INFO
        ${matched}=    Run Keyword And Return Status
        ...    Should Match Regexp    ${msg}    ${expected_pattern}
        IF    ${matched}    RETURN    ${msg}
        Sleep    1s
    END
    Log    Status bar check timed out. Last: "${msg}"    level=WARN
    RETURN    ${msg}
 
 
Get Today And Date From
    [Documentation]
    ...    Computes:
    ...      POSTING_DATE / DATE_TO = today in DD.MM.YYYY (SAP format)
    ...      DATE_FROM              = today minus 3 days in DD.MM.YYYY
    ${now}=           Get Current Date    result_format=%d.%m.%Y
    ${three_ago}=     Get Current Date    increment=-7 days    result_format=%d.%m.%Y
    Set Suite Variable    ${POSTING_DATE}    ${now}
    Set Suite Variable    ${DATE_TO}         ${now}
    Set Suite Variable    ${DATE_FROM}       ${three_ago}
    Log    Posting/To date: ${now}    level=INFO
    Log    From date (7 days ago): ${three_ago}    level=INFO
 
 
 
# ─────────────────────────────────────────────
# TDS / WITHHOLDING TAX CLEANUP
# ─────────────────────────────────────────────
 
Delete Second TDS Row
    [Documentation]
    ...    Navigates to the Withholding Tax tab and removes ALL TDS rows except the first (index 0).
 
    Log    Navigating to Withholding Tax tab    level=INFO
 
    Click Element    wnd[0]/usr/subHEADER_AND_ITEMS:SAPLMR1M:6005/tabsHEADER/tabpHEADER_WT
    Sleep    2s
    Dismiss Any Popup
 
    ${tbl_path}=    Set Variable
    ...    wnd[0]/usr/subHEADER_AND_ITEMS:SAPLMR1M:6005/tabsHEADER/tabpHEADER_WT/ssubHEADER_SCREEN:SAPLFDCB:0080/subSUB_WT:SAPLFWTD:0120/tblSAPLFWTDWT_DIALOG
 
    # MULTI-BOT FIX (2026-09-03): ${engine} used to be re-derived here via
    # Children(0).Children(0) but was never actually used anywhere in this
    # keyword (the FOR loop below only ever calls SapGuiLibrary's own
    # Element Should Be Present / Get Value / Set Focus keywords, which use
    # SapGuiLibrary's own already-correctly-bound session) -- removed as
    # dead code rather than fixed, since it did nothing either way.

    FOR    ${row_idx}    IN RANGE    1    10
 
        ${cell_path}=    Set Variable
        ...    ${tbl_path}/ctxtACWT_ITEM-WT_WITHCD[1,${row_idx}]
 
        ${row_exists}=    Run Keyword And Return Status
        ...    Element Should Be Present    ${cell_path}
 
        IF    not ${row_exists}
            Log    No more rows at index ${row_idx} — done    level=INFO
            BREAK
        END
 
        ${cell_value}=    Get Value    ${cell_path}
 
        IF    '${cell_value}' == ''
            Log    Row ${row_idx} is empty — skipping    level=INFO
            CONTINUE
        END
 
        Log    Row ${row_idx} has value '${cell_value}' — deleting via keyboard    level=WARN
 
        # Focus the cell
        Set Focus    ${cell_path}
        Sleep    0.5s
 
        # Send Backspace using pyautogui (bypasses SapGuiLibrary VKey issues)
        Evaluate    __import__('pyautogui').hotkey('backspace')    modules=pyautogui
        Sleep    0.5s
        Dismiss Any Popup
 
        # Confirm cleared
        ${new_value}=    Get Value    ${cell_path}
        IF    '${new_value}' == ''
            Log    Row ${row_idx} cleared successfully    level=INFO
        ELSE
            Log    Backspace failed — trying Delete key    level=WARN
            Set Focus    ${cell_path}
            Sleep    0.3s
            Evaluate    __import__('pyautogui').hotkey('delete')    modules=pyautogui
            Sleep    0.5s
            Dismiss Any Popup
        END
 
    END
 
    # Safety check — row 0 must still have value
    ${row0_value}=    Get Value
    ...    ${tbl_path}/ctxtACWT_ITEM-WT_WITHCD[1,0]
 
    IF    '${row0_value}' == ''
        Fail    ERROR: Row 0 (194Q) was cleared! Aborting.
    END
 
    Log    TDS cleanup done — row 0 (${row0_value}) intact    level=INFO
 
# ─────────────────────────────────────────────
# DEBUG — RUN ONCE TO FIND DELETE BUTTON ID
# ─────────────────────────────────────────────
 
 
 
Debug Find Delete Button
    [Documentation]    Run this ONCE to find the correct delete button ID
 
    Click Element    wnd[0]/usr/subHEADER_AND_ITEMS:SAPLMR1M:6005/tabsHEADER/tabpHEADER_WT
    Sleep    2s
 
    # MULTI-BOT FIX (2026-09-03): this debug-only keyword (not called from
    # the live "Execute MIRO" flow -- see the commented-out call in
    # *** Test Cases ***) used to re-derive its own session via
    # Children(0).Children(0). Fixed for consistency even though dead code,
    # so nobody re-enables it later with the buggy pattern still in place.
    # Get all children of the WT subscreen to find buttons
    ${sub}=    Evaluate
    ...    $session.findById('wnd[0]/usr/subHEADER_AND_ITEMS:SAPLMR1M:6005/tabsHEADER/tabpHEADER_WT/ssubHEADER_SCREEN:SAPLFDCB:0080/subSUB_WT:SAPLFWTD:0120')
 
    ${count}=    Evaluate    $sub.Children.Count
 
    Log    Total children in subSUB_WT: ${count}    level=WARN
 
    FOR    ${i}    IN RANGE    0    ${count}
        ${child}=    Evaluate    $sub.Children.ElementAt(${i})
        ${child_id}=    Evaluate    $child.Id
        ${child_type}=    Evaluate    $child.Type
        ${child_text}=    Evaluate    $child.Text
        Log    [${i}] ID=${child_id} | Type=${child_type} | Text=${child_text}    level=WARN
    END
# ─────────────────────────────────────────────
# MAIN FLOW
# ─────────────────────────────────────────────
 
Execute MIRO Flow
    [Documentation]
    ...    Full MIRO sequence:
    ...    1. Compute dates (today, today-3)
    ...    2. ZMM35 → set date range → Parked Documents → Execute
    ...    3. Dynamically iterate documents → match Reference Number
    ...    4. Fill Posting Date (today) on matched document
    ...    5. First Simulate
    ...    6. Withholding Tax tab → delete second TDS row
    ...    7. Second Simulate → verify
    ...    8. Post → confirm status bar
 
    # ── Compute all dates upfront ──
    Get Today And Date From
 
    # ── STEP 1: Navigate to ZMM35 ──
     SapGuiLibrary.Run Transaction    ZMM35
   
    Dismiss Any Popup
    Log    Navigated to ZMM35    level=INFO
 
    # ── STEP 2: Set Document Date range (From = 7 days ago, To = today) ──
    # First date field = "Document Date From"
    # TODO: Confirm element IDs by recording a GUI script on this selection screen
#Input Text    wnd[0]/usr/ctxtS_BLDAT-LOW    ${DATE_FROM}
    # Second date field = "Document Date To"
#Input Text    wnd[0]/usr/ctxtS_BLDAT-HIGH    ${DATE_TO}
    Sleep    1s
    Log    Date range set: ${DATE_FROM} to ${DATE_TO}    level=INFO
 
    # ── STEP 3: Select Parked Documents radio button ──
    # Radio option 3 typically = Parked Documents in ZMM35
    # TODO: Confirm radio button ID from your SAP system
    Select Radio Button    wnd[0]/usr/radP_PARK
    Sleep    1s
   
    Log    Parked Documents radio selected    level=INFO
 
    # ── STEP 4: Execute — loads parked document list ──
    Click Element    wnd[0]/tbar[1]/btn[8]
    Sleep    4s
    Dismiss Any Popup
    Log    Execute clicked — parked document list loaded    level=INFO
 
 
 
    # ── STEP 5: Iterate through grid rows and match by Reference Number ──
   
 
    # ── DIAGNOSTIC: print all columns and rows ──
    # MULTI-BOT FIX (2026-09-03): used to re-derive the session by raw
    # index (Children(0).Children(0)) right here -- always the FIRST SAP
    # connection/session on the machine, which under concurrency could
    # silently be a DIFFERENT bot's open window. This is the live grid
    # this keyword actually clicks/double-clicks into further down, so
    # this was the most dangerous of the 3 spots in this file. ${session}
    # is now bound once at login (Coordinated SAP Login -> Bind SAP
    # Session) to the connection THIS bot's own login just created.
    ${grid}=    Evaluate
    ...    $session.findById('wnd[0]/usr/cntlGRID1/shellcont/shell/shellcont[1]/shell')
 
    ${rows}=    Evaluate    $grid.RowCount
    ${cols}=    Evaluate    $grid.ColumnCount
    Log    ROWS=${rows} COLS=${cols}    level=WARN
 
    # Print all column names
    FOR    ${c}    IN RANGE    0    ${cols}
        ${col}=    Evaluate    $grid.ColumnOrder[${c}]
        Log    COLUMN: ${col}    level=WARN
    END
 
    # Print first 3 rows all columns
    # ── STEP 5: Open matching document from grid ──
   # ── Iterate rows and match by MATERIAL_DOC_NUMBER ──
    FOR    ${r}    IN RANGE    0    ${rows}
        ${doc_val}=    Evaluate    $grid.GetCellValue(${r}, 'BELNR')
        ${doc_val}=    Strip String    ${doc_val}
        ${doc_clean}=  Strip String    ${MATERIAL_DOC_NUMBER}
 
        IF    '${doc_val}' == '${doc_clean}'
            Log    Match found at row ${r} by MATERIAL_DOC_NUMBER    level=WARN
            Evaluate    $grid.setCurrentCell(${r}, 'BELNR')
            Sleep    0.5s
            Evaluate    $grid.doubleClick(${r}, 'BELNR')
            Sleep    3s
            Dismiss Any Popup
 
            ${still_on_list}=    Run Keyword And Return Status
            ...    Element Should Be Present
            ...    wnd[0]/usr/cntlGRID1/shellcont/shell/shellcont[1]/shell
 
            IF    ${still_on_list}
                Log    Double-click failed — trying Enter    level=WARN
                SapGuiLibrary.Send VKey    wnd[0]    0
                Sleep    3s
                Dismiss Any Popup
            END
 
            ${still_on_list}=    Run Keyword And Return Status
            ...    Element Should Be Present
            ...    wnd[0]/usr/cntlGRID1/shellcont/shell/shellcont[1]/shell
            IF    ${still_on_list}
                Fail    Could not open MIRO document at row ${r}.
            END
 
            Log    MIRO document opened successfully    level=INFO
            BREAK
        END
    END
 
 
 
    # ── Set Posting Date — now inside the open document ──
    # ── Set Posting Date — confirmed field ID ──
    Sleep    2s
    Dismiss Any Popup
 
    SapGuiLibrary.Input Text
    ...    wnd[0]/usr/subHEADER_AND_ITEMS:SAPLMR1M:6005/tabsHEADER/tabpHEADER_TOTAL/ssubHEADER_SCREEN:SAPLFDCB:0010/ctxtINVFO-BUDAT
    ...    ${POSTING_DATE}
   
    Sleep    1s
    Log    Posting date set to: ${POSTING_DATE}    level=INFO
 
    # ── STEP 7: First Simulate ──
    # Simulate button is typically F9 (VKey 9) or toolbar btn[1] in MIRO
    # TODO: Confirm Simulate button element ID
    Click Element    wnd[0]/tbar[0]/btn[1]
    Sleep    4s
    Dismiss Any Popup
    Log    First Simulate complete    level=INFO
 
    # ── STEP 8: Navigate to Withholding Tax tab and delete second TDS row ──
    Delete Second TDS Row
    Sleep    1s
 
    # ── STEP 9: Second Simulate — verify accounting entries are correct ──
    Click Element    wnd[0]/tbar[0]/btn[1]
    Sleep    4s
    Dismiss Any Popup
    Log    Second Simulate complete — entries verified    level=INFO
 
    # Check status bar after simulate — should not show an error
    ${sim_status}=    Read Status Bar With Retry    expected_pattern=.{3,}
    Log    Simulate status: "${sim_status}"    level=INFO
    ${is_error}=    Run Keyword And Return Status
    ...    Should Match Regexp    ${sim_status}    (?i)(error|fehler|E\\s)
    IF    ${is_error}
        Fail    Simulate returned an error: "${sim_status}". Aborting post.
    END
 
    # ── STEP 10: Post the document ──
    # Post button is typically the Save/Post button: tbar[0]/btn[11] or Ctrl+S
    # TODO: Confirm Post button element ID
    Click Element    wnd[0]/tbar[0]/btn[11]
    Sleep    5s
    Dismiss Any Popup
 
    # ── STEP 11: Read and validate status bar for document number ──
    ${status_msg}=    Read Status Bar With Retry    expected_pattern=\\d{9,}
    Log    MIRO post status: "${status_msg}"    level=INFO
 
    # Status bar on success typically contains the new FI document number (9+ digits)
    ${posted_ok}=    Run Keyword And Return Status
    ...    Should Match Regexp    ${status_msg}    \\d{9,}
    IF    ${posted_ok}
        Log    MIRO posted successfully. FI Document: "${status_msg}"    level=INFO
        Log To Console    RESULT:FI_DOC_NUMBER:${status_msg}
    ELSE
        Log    Post may have failed — status bar: "${status_msg}"    level=WARN
        # Uncomment to hard-fail:
        # Fail    MIRO post did not return a document number. Status: "${status_msg}"
    END
 
 
# ─────────────────────────────────────────────
# TEARDOWN
# ─────────────────────────────────────────────
 
Close SAP Session
    [Documentation]    Gracefully exits SAP; only kills the shared SAP
    ...    processes if no other bot is still active (see
    ...    sap_coordination.resource).
    Coordinated Close SAP Session    ${session}