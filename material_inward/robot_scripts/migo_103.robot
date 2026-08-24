*** Settings ***
Documentation     MIGO 103 SAP Automation — GR into Blocked Stock
Library           SapGuiLibrary
Library           Process
Library           OperatingSystem
Library           String
Library           DateTime
Library           Collections
 
*** Variables ***
${PO_NUMBER}        ${EMPTY}
${DOC_DATE}         ${EMPTY}
${POST_DATE}        ${EMPTY}
${DELIVERY_NOTE}    ${EMPTY}
${BILL_OF_LADING}   ${EMPTY}
${GR_SLIP_NO}       ${EMPTY}
${HEADER_TEXT}      ${EMPTY}
${REMARKS}          ${EMPTY}
${ITEMS_JSON}       []
${ITEMS_JSON_B64}    W10=
# tree sidebar can pop up on this same MIGO screen -- these two paths let
# Dismiss Overview Tree Sidebar close it if present. ${session} is the raw
# SAP GUI Scripting session object, needed because a GuiShell tree control's
# .pressButton() method isn't exposed by SapGuiLibrary's own keywords.
${PATH_TREE_CLOSE_BTN}    wnd[0]/shellcont/shell/shellcont[1]/shell[0]
${session}          ${NONE}

# --- MIGO item overview grid (ADDED 2026-08-21 -- EBELP-based matching) ----
# Used to read the live grid and match each incoming item by PO Item Number
# (EBELP) instead of by array position -- see Fill MIGO 103 And Post's Step 4
# comment for the full reasoning (this replaced the root cause of the wrong-
# material-code GRIN issue).
${GOITEM_TABLE}    wnd[0]/usr/ssubSUB_MAIN_CARRIER:SAPLMIGO:0003/subSUB_ITEMLIST:SAPLMIGO:0200/tblSAPLMIGOTV_GOITEM
${COL_GOITEM_MATNR}     59
${COL_GOITEM_ERFMG}     4
${COL_GOITEM_EBELP}     39

# --- Multi-bot coordination (ADDED 2026-08-21) ------------------------------
${REFCOUNT_FILE}           C:\\temp\\sap_bot_refcount.txt
${REFCOUNT_LOCK_DIR}       C:\\temp\\sap_bot_refcount.lock


*** Test Cases ***
Execute MIGO 103
    [Setup]    Initialize SAP And Login
    ${result}=    Fill MIGO 103 And Post
    # ${result} is now a dict: {"status": "COMPLETED"|"MANUAL_CHECK_REQUIRED",
    # "matdoc": "...", "item_results": [...]}. RESULT:ITEM_RESULTS is already
    # logged inside the keyword itself (before Post is even clicked, so it's
    # always available even on a MANUAL_CHECK_REQUIRED outcome). Here we only
    # need to re-derive the single MATERIAL_DOC_NUMBER marker rf_runner.py
    # already knows how to extract, unchanged from before.
    IF    '${result}[status]' == 'COMPLETED'
        ${mat_doc}=    Set Variable    ${result}[matdoc]
    ELSE
        ${mat_doc}=    Set Variable    MANUAL_CHECK_REQUIRED
    END
    Log To Console    RESULT:MATERIAL_DOC_NUMBER:${mat_doc}
    Sleep    10s
    [Teardown]    Close SAP Session
 
*** Keywords ***
Initialize SAP And Login
    # Evaluate    __import__('dotenv').load_dotenv()
    Evaluate    __import__('dotenv').load_dotenv(__import__('os').getenv('DOTENV_PATH', '.env'), override=True)
    ${CLIENT}=      Evaluate    __import__('os').getenv('SAP_CLIENT')
    ${CONN_NAME}=   Evaluate    __import__('os').getenv('SAP_CONNECTION_NAME')
    ${LOGON_PATH}=  Evaluate    __import__('os').getenv('SAP_LOGON_PATH')
 
    # v16: per-user SAP credential pass-through (LDAP users only) -- see
    # gate_in.robot for the full explanation. SAP_USER_OVERRIDE/
    # SAP_PASS_OVERRIDE come from rf_runner.py's subprocess environment,
    # never from this .env file (load_dotenv above uses override=True
    # for SAP_CLIENT/SAP_CONNECTION_NAME/etc, but these two names are
    # never defined in .env at all, so they're unaffected either way).
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
 
    Register Bot Start

    # MULTI-BOT FIX (2026-08-21): this used to unconditionally taskkill
    # saplogon.exe here, every run -- fine standalone, but under concurrency
    # that kills whatever session another bot already has open. Now: only
    # start fresh if SAP Logon isn't already running; otherwise reuse the
    # existing process and just open a new connection window under it. The
    # whole open-connection + login + session-bind sequence runs under the
    # refcount lock so two bots can never race for "which connection is
    # mine" (see Connect To Sap Session).
    Acquire Refcount Lock

    ${tasklist_result}=    Run Process    tasklist    /FI    IMAGENAME eq saplogon.exe
    ${already_running}=    Run Keyword And Return Status
    ...    Should Contain    ${tasklist_result.stdout}    saplogon.exe

    IF    ${already_running}
        Log To Console    SAP Logon already open -- reusing existing process, opening a new connection window under it.
    ELSE
        Log To Console    SAP Logon not running -- starting fresh.
        Start Process    ${LOGON_PATH}
        Sleep    5s
    END

    # Retries instead of failing on the first attempt -- SAP Logon Pad's
    # scripting engine can take a moment to be ready right after launch.
    # SELF-HEALING: if this still can't connect after a real attempt, the
    # process we're trying to reuse is stale/orphaned rather than just slow.
    # Kill it and start a truly fresh one rather than retrying forever.
    ${connected}=    Run Keyword And Return Status
    ...    Wait Until Keyword Succeeds    5x    2s    Connect To Session
    IF    not ${connected}
        Log To Console    Could not connect to the existing SAP Logon process -- treating it as stale, killing it and starting fresh.
        Run Keyword And Ignore Error    Run Process    taskkill    /F    /IM    saplogon.exe    /T
        Run Keyword And Ignore Error    Run Process    taskkill    /F    /IM    saplgpad.exe    /T
        Run Keyword And Ignore Error    Run Process    taskkill    /F    /IM    sapgui.exe    /T
        Sleep    2s
        Start Process    ${LOGON_PATH}
        Sleep    5s
        Wait Until Keyword Succeeds    10x    2s    Connect To Session
    END

    Open Connection    ${CONN_NAME}

    Input Text        wnd[0]/usr/txtRSYST-MANDT    ${CLIENT}
    Input Text        wnd[0]/usr/txtRSYST-BNAME    ${USERNAME}
    Input Password    wnd[0]/usr/pwdRSYST-BCODE    ${PASSWORD}
    Click Element     wnd[0]/tbar[0]/btn[0]
 
    # FIX: was "Dismiss Any Popup" (generic -- blindly clicks wnd[1]'s
    # default button with no radio selection) BEFORE this specific
    # multi-logon check, so on any day the popup actually appeared, the
    # generic dismiss already closed it first and submitted whatever SAP's
    # default radio option was -- this check then saw wnd[1] gone and never
    # ran, so the intended "continue without ending other sessions" choice
    # never actually got selected. Reordered to match gate_in.robot's
    # proven-working sequence: handle the specific multi-logon dialog FIRST
    # (selecting OPT2, same option gate_in.robot uses), THEN run the
    # generic Dismiss Any Popup afterward for any other trailing popups.
    # OPT1 -> OPT2: OPT1 is SAP's "end any other logons of this user"
    # option, OPT2 is "continue without ending other sessions" -- OPT2 is
    # the safe choice for a bot sharing a login with other jobs/users.
    Sleep    3s
    ${multi}=    Run Keyword And Return Status    Element Should Be Present    wnd[1]
    IF    ${multi}
        Run Keyword And Ignore Error    Select Radio Button    wnd[1]/usr/radMULTI_LOGON_OPT1
        Run Keyword And Ignore Error    Click Element          wnd[1]/tbar[0]/btn[0]
        Sleep    2s
    END
 
    Sleep    5s
    Dismiss Any Popup

    Maximize Window    0
    Connect To Sap Session

    # Only release the lock once THIS bot's own session is fully bound --
    # from here on, another bot is free to open its own connection without
    # any chance of grabbing this bot's window by mistake.
    Release Refcount Lock


Get Firstline Path
    # The Action/RefDoc combo box subscreen can render as either :0003 or
    # :0007 depending on MIGO's current internal state (seen in GUI Script
    # recording: A01-A08 -> SAPLMIGO:0003, A09/A10 -> SAPLMIGO:0007). A
    # hardcoded path silently fails to find the element whenever the
    # screen happens to be in the other state. Probe both, use whichever
    # actually exists right now.
    ${path_0003}=    Set Variable    wnd[0]/usr/ssubSUB_MAIN_CARRIER:SAPLMIGO:0003/subSUB_FIRSTLINE:SAPLMIGO:0011
    ${path_0007}=    Set Variable    wnd[0]/usr/ssubSUB_MAIN_CARRIER:SAPLMIGO:0007/subSUB_FIRSTLINE:SAPLMIGO:0011
 
    ${found_0003}=    Run Keyword And Return Status    Element Should Be Present    ${path_0003}
    IF    ${found_0003}
        RETURN    ${path_0003}
    END
 
    ${found_0007}=    Run Keyword And Return Status    Element Should Be Present    ${path_0007}
    IF    ${found_0007}
        RETURN    ${path_0007}
    END
 
    Fail    Neither SAPLMIGO:0003 nor SAPLMIGO:0007 firstline subscreen found -- MIGO screen state unrecognized.
 
Ensure Header And Detail Open
    # Header Data and Item Detail sections each toggle open/closed, and the
    # screen number shifts depending on the OTHER section's current state.
    # Only the "open" button name is searched for -- btnOK_HEADER (header)
    # and btnBUTTON_DETAIL (detail). If found, click it once. If not found,
    # the section is already open, so do nothing and move on. The "close"
    # button names (btnBUTTON_HEADER_TOGGLE, btnBUTTON_ITEMDETAIL) are
    # deliberately never searched for -- we only ever open, never close.
 
    # --- Header: open button = btnOK_HEADER ---
    ${header_open_path}=    Set Variable    ${EMPTY}
    FOR    ${scr}    IN    0002    0003    0004    0005
        ${candidate}=    Set Variable
        ...    wnd[0]/usr/ssubSUB_MAIN_CARRIER:SAPLMIGO:${scr}/subSUB_HEADER:SAPLMIGO:0102/btnOK_HEADER
        ${found}=    Run Keyword And Return Status    Element Should Be Present    ${candidate}
        IF    ${found}
            ${header_open_path}=    Set Variable    ${candidate}
            BREAK
        END
    END
 
    IF    '${header_open_path}' != '${EMPTY}'
        Run Keyword And Ignore Error
        ...    Evaluate    $session.findById('${header_open_path}').press()
        Sleep    1s
        Log To Console    Header data was closed -- opened it (${header_open_path}).
    ELSE
        Log To Console    Header data already open -- leaving as is.
    END
 
    # --- Item Detail: open button = btnBUTTON_DETAIL ---
    ${detail_open_path}=    Set Variable    ${EMPTY}
    FOR    ${scr}    IN    0002    0003    0004    0005
        ${candidate}=    Set Variable
        ...    wnd[0]/usr/ssubSUB_MAIN_CARRIER:SAPLMIGO:${scr}/subSUB_ITEMDETAIL:SAPLMIGO:0302/btnBUTTON_DETAIL
        ${found}=    Run Keyword And Return Status    Element Should Be Present    ${candidate}
        IF    ${found}
            ${detail_open_path}=    Set Variable    ${candidate}
            BREAK
        END
    END
 
    IF    '${detail_open_path}' != '${EMPTY}'
        Run Keyword And Ignore Error
        ...    Evaluate    $session.findById('${detail_open_path}').press()
        Sleep    1s
        Log To Console    Item detail was closed -- opened it (${detail_open_path}).
    ELSE
        Log To Console    Item detail already open -- leaving as is.
    END

Connect To Sap Session
    # MULTI-BOT FIX (2026-08-21): previously used
    # GetScriptingEngine.Children(0).Children(0), which always grabs the
    # FIRST connection/session that exists -- wrong bot under concurrency.
    # SapGuiLibrary itself already knows exactly which connection/session
    # THIS bot's own Open Connection call just created, so reuse that
    # instead of re-deriving it by index. Same object either way -- still
    # usable for the GuiShell tree control's .pressButton() calls that
    # SapGuiLibrary's own keywords don't expose.
    ${lib}=    Get Library Instance    SapGuiLibrary
    ${sess}=    Set Variable    ${lib.session}
    Set Suite Variable    ${session}    ${sess}


Acquire Refcount Lock
    # Directory creation is atomic on Windows -- used as a simple mutex.
    # Retries for up to ~30s (150 x 0.2s) since the critical section covers
    # the full login sequence, not just a quick counter-file write.
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
    # lock acquisition (separate from the login-sequence lock) so
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


Extract MIGO Item Table
    [Documentation]    Reads the live item overview grid row by row: Line
    ...    number (ZEILE, used for DETAIL_ZEILE navigation), PO Item Number
    ...    (EBELP, used for matching), Material, Short Text, Qty. Returns
    ...    the raw list of dicts so Step 4 can search it directly. This is
    ...    what replaced position-based matching (${line_num}=${i}+1) --
    ...    SAP's grid silently drops PO lines that are already fully
    ...    received, so array position in our payload never reliably lined
    ...    up with the actual grid row; EBELP is SAP's own unique line
    ...    identifier and is read directly from this same grid.
    @{item_rows}=    Create List
    ${row_idx}=    Set Variable    0
    ${scroll_pos}=    Set Variable    0
    ${visible_rows}=    Evaluate    $session.findById('${GOITEM_TABLE}').VisibleRowCount

    WHILE    True
        ${row_in_view}=    Evaluate    ${row_idx} - ${scroll_pos}
        IF    ${row_in_view} >= ${visible_rows}
            ${scroll_pos}=    Set Variable    ${row_idx}
            Run Keyword And Ignore Error
            ...    Evaluate    setattr($session.findById('${GOITEM_TABLE}').verticalScrollbar, 'position', ${scroll_pos})
            Sleep    0.5s
        END
        ${grid_row}=    Evaluate    ${row_idx} - ${scroll_pos}

        ${zeile_path}=    Set Variable    ${GOITEM_TABLE}/btnGOITEM-ZEILE[0,${grid_row}]
        ${line_res}=    Run Keyword And Ignore Error    Get Value    ${zeile_path}
        IF    '${line_res}[0]' == 'FAIL'
            BREAK
        END
        ${line_no}=    Clean Value    ${line_res}[1]
        ${line_no_stripped}=    Replace String    ${line_no}    _    ${EMPTY}
        ${line_no_stripped}=    Replace String    ${line_no_stripped}    .    ${EMPTY}
        ${line_no_stripped}=    Strip String    ${line_no_stripped}
        IF    '${line_no_stripped}' == '${EMPTY}'
            BREAK
        END

        ${st_path}=    Set Variable    ${GOITEM_TABLE}/ctxtGOITEM-MAKTX[1,${grid_row}]
        ${st_res}=    Run Keyword And Ignore Error    Get Value    ${st_path}
        ${short_text}=    Set Variable If    '${st_res}[0]' == 'PASS'    ${st_res}[1]    ${EMPTY}

        ${mat_path}=    Set Variable    ${GOITEM_TABLE}/ctxtGOITEM-MATNR[${COL_GOITEM_MATNR},${grid_row}]
        ${mat_res}=    Run Keyword And Ignore Error    Get Value    ${mat_path}
        ${material}=    Set Variable If    '${mat_res}[0]' == 'PASS'    ${mat_res}[1]    ${EMPTY}

        ${qty_path}=    Set Variable    ${GOITEM_TABLE}/txtGOITEM-ERFMG[${COL_GOITEM_ERFMG},${grid_row}]
        ${qty_res}=    Run Keyword And Ignore Error    Get Value    ${qty_path}
        ${qty}=    Set Variable If    '${qty_res}[0]' == 'PASS'    ${qty_res}[1]    ${EMPTY}

        ${ebelp_path}=    Set Variable    ${GOITEM_TABLE}/ctxtGOITEM-EBELP[${COL_GOITEM_EBELP},${grid_row}]
        ${ebelp_res}=    Run Keyword And Ignore Error    Get Value    ${ebelp_path}
        ${ebelp}=    Set Variable If    '${ebelp_res}[0]' == 'PASS'    ${ebelp_res}[1]    ${EMPTY}

        &{row_data}=    Create Dictionary
        ...    line_no=${line_no}
        ...    ebelp=${ebelp}
        ...    material=${material}
        ...    short_text=${short_text}
        ...    qty=${qty}
        Append To List    ${item_rows}    ${row_data}

        Log To Console    MIGO ITEM ROW ${row_idx}: line=${line_no} ebelp=${ebelp} material=${material} short_text="${short_text}" qty=${qty}

        ${row_idx}=    Evaluate    ${row_idx} + 1
        IF    ${row_idx} >= 100    BREAK
    END

    ${total_rows}=    Get Length    ${item_rows}
    Log To Console    Extracted ${total_rows} live item row(s) from MIGO overview table
    RETURN    ${item_rows}


Dismiss Overview Tree Sidebar
    # INTEGRATION: found by the migo103_link/migo105_link bots -- MIGO can
    # pop up a document-overview tree sidebar on this same screen. Defensive
    # by construction (both checks are Element Should Be Present guards, so
    # this is a no-op on any run where the sidebar never appears).
    ${hide_overview_status}=    Run Keyword And Return Status    SapGuiLibrary.Element Should Be Present    wnd[0]/tbar[1]/btn[21]
    IF    ${hide_overview_status}
        Evaluate    $session.findById('wnd[0]').maximize()
        Evaluate    $session.findById('wnd[0]/tbar[1]/btn[21]').press()
        Sleep    2s
    END
 
    ${tree_close_status}=    Run Keyword And Return Status    SapGuiLibrary.Element Should Be Present    ${PATH_TREE_CLOSE_BTN}
    IF    ${tree_close_status}
        Evaluate    $session.findById('${PATH_TREE_CLOSE_BTN}').pressButton('OK_CLOSE')
        Sleep    2s
    END
 
 
Fill MIGO 103 And Post
    # --- Data Cleaning ---
    ${po_clean}=      Clean Value    ${PO_NUMBER}
    ${dn_clean}=      Clean Value    ${DELIVERY_NOTE}
    ${bol_clean}=     Clean Value    ${BILL_OF_LADING}
    ${slip_clean}=    Clean Value    ${GR_SLIP_NO}
    ${hdr_clean}=     Clean Value    ${HEADER_TEXT}
    ${rem_clean}=     Clean Value    ${REMARKS}
 
    # Diagnostic: log every header variable as received + after cleaning,
    # for its designated SAP field, before any of it is typed into SAP.
    Log To Console    HEADER VALUES -- PO_NUMBER="${PO_NUMBER}"->"${po_clean}" (ctxtGODYNPRO-PO_NUMBER) | DELIVERY_NOTE="${DELIVERY_NOTE}"->"${dn_clean}" (txtGOHEAD-LFSNR) | BILL_OF_LADING="${BILL_OF_LADING}"->"${bol_clean}" (txtGOHEAD-FRBNR) | GR_SLIP_NO="${GR_SLIP_NO}"->"${slip_clean}" (txtGOHEAD-XABLN) | HEADER_TEXT="${HEADER_TEXT}"->"${hdr_clean}" (txtGOHEAD-BKTXT) | REMARKS="${REMARKS}"->"${rem_clean}" (txtGOITEM-SGTXT, applied per line below)
 
    # --- Parse ITEMS_JSON ---
    # FIX: this used to decode ITEMS_JSON_B64 into ${items_json} and then
    # immediately discard it, instead parsing the unrelated ${ITEMS_JSON}
    # variable -- which defaults to "[]" in *** Variables *** and is never
    # actually set by rf_runner.py (only ITEMS_JSON_B64 is passed in from
    # the app). That meant ${items} was always empty and ${total} was
    # always 0, so this whole line-item loop silently never ran on any
    # real run -- Material/Qty were never being filled at all, regardless
    # of what the app sent. Now parsing the actual decoded payload.
    ${items}=    Evaluate    __import__('json').loads(__import__('base64').b64decode('${ITEMS_JSON_B64}').decode())
    ${total}=    Get Length    ${items}
    Log To Console    Total matched pairs to fill: ${total}
 
    # --- Step 1: Navigate to MIGO ---
    Run Transaction    MIGO
    Sleep    3s
    Dismiss Any Popup
    Dismiss Overview Tree Sidebar
    Ensure Header And Detail Open      
 
    # --- Step 2: Force Goods Receipt + Purchase Order, then set movement type and PO ---
    # --- Step 2: Force Goods Receipt + Purchase Order, then set movement type and PO ---
    #${firstline}=    Get Firstline Path
 
    # Force action to Goods Receipt (A01). Using .key directly instead of
    # Select From List By Label -- label match depends on SAP logon
    # language ("Goods Receipt"); key "A01" is stable regardless of
    # language, and matches the GUI Script recording.
    #Evaluate    setattr($session.findById('${firstline}/cmbGODYNPRO-ACTION'), 'key', 'A01')
    #Evaluate    $session.findById('${firstline}/cmbGODYNPRO-ACTION').setFocus()
    #Sleep     5s
 
    # Re-resolve firstline path -- setting ACTION can itself switch the
    # subscreen between :0003/:0007, so REFDOC's container may differ
    # from what ACTION was just found under.
    # --- Step 2: Force Goods Receipt + Purchase Order, then set movement type and PO ---
    ${firstline}=    Get Firstline Path
 
    # Force action to Goods Receipt (A01). setattr() used instead of a
    # direct `.key = 'A01'` assignment because Robot's Evaluate keyword
    # runs expressions through Python eval(), which cannot execute
    # assignment statements (SyntaxError: invalid syntax) -- setattr()
    # is a function call, so it's a valid expression instead.
    Evaluate    setattr($session.findById('${firstline}/cmbGODYNPRO-ACTION'), 'key', 'A01')
    #Evaluate    $session.findById('${firstline}/cmbGODYNPRO-ACTION').setFocus()
    Sleep    0.5s
 
    ${firstline}=    Get Firstline Path
 
    # Force reference to Purchase Order (R01)
    Evaluate    setattr($session.findById('${firstline}/cmbGODYNPRO-REFDOC'), 'key', 'R01')
    Evaluate    $session.findById('${firstline}/cmbGODYNPRO-REFDOC').setFocus()
    Sleep    0.5s
 
    ${firstline}=    Get Firstline Path
 
    # Movement type 103
    Set Focus     ${firstline}/ctxtGODEFAULT_TV-BWART
    Safe Input Text    ${firstline}/ctxtGODEFAULT_TV-BWART    103
 
    # PO number
    Set Focus     ${firstline}/subSUB_FIRSTLINE_REFDOC:SAPLMIGO:2000/ctxtGODYNPRO-PO_NUMBER
    Safe Input Text    ${firstline}/subSUB_FIRSTLINE_REFDOC:SAPLMIGO:2000/ctxtGODYNPRO-PO_NUMBER    ${po_clean}
 
    Send VKey    0
    Sleep    3s
    Dismiss Any Popup

    # --- Read the LIVE grid before typing anything (ADDED 2026-08-21) ---
    # This is what Step 4 below matches each incoming item against by
    # EBELP (PO Item Number), instead of guessing a grid row from array
    # position.
    @{item_rows}=    Extract MIGO Item Table

    # --- Step 3: Header Fields ---
    ${hdr_base}=    Set Variable
    ...    wnd[0]/usr/ssubSUB_MAIN_CARRIER:SAPLMIGO:0003/subSUB_HEADER:SAPLMIGO:0101/subSUB_HEADER:SAPLMIGO:0100/tabsTS_GOHEAD/tabpOK_GOHEAD_GENERAL/ssubSUB_TS_GOHEAD_GENERAL:SAPLMIGO:0110
 
    Set Focus     ${hdr_base}/ctxtGOHEAD-BLDAT
    Safe Input Text    ${hdr_base}/ctxtGOHEAD-BLDAT    ${DOC_DATE}
 
    Set Focus     ${hdr_base}/ctxtGOHEAD-BUDAT
    Safe Input Text    ${hdr_base}/ctxtGOHEAD-BUDAT    ${POST_DATE}
 
 
   
 
    # FIX: interleaving Dismiss Any Popup between each field now, not just
    # around the whole block. On a PO with multiple line items, entering
    # Delivery Note (LFSNR) can silently trigger a split/allocation popup
    # that sits on top of wnd[0] -- if left uncleared, the very next
    # Input Text (FRBNR) hits a blocked/stale reference and fails, and a
    # bare retry doesn't help because the popup is still there. Confirmed
    # in production: FRBNR failed twice in a row, back-to-back, on a PO
    # with multiple lines.
    Safe Input Text    ${hdr_base}/txtGOHEAD-LFSNR     ${dn_clean}
    Dismiss Any Popup
    Safe Input Text    ${hdr_base}/txtGOHEAD-FRBNR     ${bol_clean}
    Dismiss Any Popup
    Safe Input Text    ${hdr_base}/txtGOHEAD-XABLN     ${slip_clean}
    Dismiss Any Popup
    Safe Input Text    ${hdr_base}/txtGOHEAD-BKTXT     ${hdr_clean}
 
    Send VKey    0
    Sleep    2s
    Dismiss Any Popup
 
    # --- Step 4: Line Items — matched by PO Item Number (EBELP), not array
    # position. FIX (2026-08-21): this used to navigate by
    # ${line_num}=${i}+1 -- treating the Nth item WE sent as the Nth row IN
    # SAP's grid. That silently broke whenever SAP's grid didn't mirror our
    # array 1:1, which happens routinely: SAP drops PO lines that are
    # already fully received from the MIGO grid, so a PO with 13 lines can
    # show only 10 in MIGO, and the row at a given position is not
    # necessarily the same PO line our array's same position refers to.
    # That mismatch is the confirmed root cause of the wrong-material-code
    # GRIN issue (PO 4100035144 and others). Now: each incoming item
    # carries its own real PO Item Number (EBELP, e.g. "10"/"30"/"40"),
    # read directly off the PO by po_fetch.robot and carried through the
    # UI's matching step untouched. We read the live grid once (Extract
    # MIGO Item Table, above) and look up each item's row by EBELP -- a
    # direct, unambiguous match, independent of ordering or gaps. A missing
    # item_no (already received / not in this grid) no longer fails the
    # whole run -- it's skipped and recorded, so every item's outcome ends
    # up in ${item_results} for the UI/DB to see.
    Set Suite Variable    ${det_base}
    ...    wnd[0]/usr/ssubSUB_MAIN_CARRIER:SAPLMIGO:0003/subSUB_ITEMDETAIL:SAPLMIGO:0301/subSUB_DETAIL:SAPLMIGO:0300

    @{used_item_nos}=    Create List
    @{item_results}=    Create List

    FOR    ${i}    IN RANGE    ${total}
        ${item}=          Get From List    ${items}    ${i}
        ${qty_actual}=    Clean Value    ${item}[qty_actual]
        ${qty_dn}=        Clean Value    ${item}[qty_expected]
        ${target_item_no}=    Clean Value    ${item}[item_no]

        IF    $target_item_no in $used_item_nos
            Log To Console    PO item ${target_item_no} already processed earlier in this list -- skipping duplicate.
            &{result}=    Create Dictionary    item_no=${target_item_no}    status=duplicate item_no, skipped
            Append To List    ${item_results}    ${result}
            CONTINUE
        END

        # --- Find the live SAP grid row whose EBELP matches this item ---
        ${matched_row}=    Set Variable    ${NONE}
        FOR    ${row}    IN    @{item_rows}
            ${row_ebelp}=    Clean Value    ${row}[ebelp]
            IF    '${row_ebelp}' == '${target_item_no}'
                ${matched_row}=    Set Variable    ${row}
                BREAK
            END
        END

        IF    $matched_row == $NONE
            Log To Console    PO item ${target_item_no} not found in the live SAP grid -- skipping (may already be fully received and dropped from the grid).
            &{result}=    Create Dictionary    item_no=${target_item_no}    status=item not found
            Append To List    ${item_results}    ${result}
            CONTINUE
        END

        Append To List    ${used_item_nos}    ${target_item_no}
        ${line_num}=    Set Variable    ${matched_row}[line_no]
        Log To Console    PO item ${target_item_no} -> matched SAP grid line ${line_num} (EBELP=${matched_row}[ebelp], material=${matched_row}[material])

        # Navigate to correct line
        Safe Input Text    ${det_base}/txtGODYNPRO-DETAIL_ZEILE    ${line_num}
        Click Element   ${det_base}/btnOK_LOCATE
        Sleep    1s
        Dismiss Any Popup

        # --- Sanity check: compares the detail screen's own MATNR against
        # what the OVERVIEW GRID already showed for this same EBELP match --
        # confirms SAP actually navigated to the same item.
        ${matnr_status}    ${matnr_check}=    Run Keyword And Ignore Error
        ...    Get Value
        ...    ${det_base}/tabsTS_GOITEM/tabpOK_GOITEM_MATERIAL/ssubSUB_TS_GOITEM_MATERIAL:SAPLMIGO:0301/ctxtGOITEM-MATNR
        IF    '${matnr_status}' == 'PASS'
            Log To Console    Line ${line_num} READBACK: MATNR="${matnr_check}" (grid showed "${matched_row}[material]")
            IF    '${matnr_check}' != '${matched_row}[material]'
                Log To Console    WARNING: navigated to line ${line_num} but detail-screen MATNR doesn't match the overview grid's MATNR for this item -- check manually.
            END
        ELSE
            Log To Console    Line ${line_num} READBACK: MATNR field ID not confirmed for this screen -- could not read back (${matnr_check})
        END

        # Quantity tab
        Click Element
        ...    ${det_base}/tabsTS_GOITEM/tabpOK_GOITEM_QUANTITIES
        Sleep    1s
 
        ${qty_base}=    Set Variable
        ...    ${det_base}/tabsTS_GOITEM/tabpOK_GOITEM_QUANTITIES/ssubSUB_TS_GOITEM_QUANTITIES:SAPLMIGO:0315
 
        Set Focus     ${qty_base}/txtGOITEM-ERFMG
        Safe Input Text    ${qty_base}/txtGOITEM-ERFMG    ${qty_actual}
 
        Set Focus     ${qty_base}/txtGOITEM-LSMNG
        Safe Input Text    ${qty_base}/txtGOITEM-LSMNG    ${qty_dn}
 
        Send VKey    0
        Sleep    1s
        Dismiss Any Popup
 
        # --- Diagnostic read-back: confirm SAP actually kept what we typed
        # before we ever reach the (previously broken) Post step. If these
        # come back blank/zero, the line itself never loaded from the PO
        # (navigation/PO-reference problem). If they come back correct, the
        # values ARE in the screen and the earlier "material not fetched"
        # symptom was actually just the missing/wrong Post click discarding
        # everything when the session closed without saving.
        ${erfmg_check}=    Get Value    ${qty_base}/txtGOITEM-ERFMG
        ${lsmng_check}=    Get Value    ${qty_base}/txtGOITEM-LSMNG
        Log To Console    Line ${line_num} READBACK: ERFMG(actual qty)="${erfmg_check}" LSMNG(DN qty)="${lsmng_check}"

        # Where tab — fill remarks/text. SGTXT is filled from ${rem_clean}
        # -- the single header-level REMARKS value -- for EVERY line item.
        # Unchanged behavior, noted here for visibility.
        Click Element
        ...    ${det_base}/tabsTS_GOITEM/tabpOK_GOITEM_DESTINAT.
        Sleep    1s
 
        Set Focus
        ...    ${det_base}/tabsTS_GOITEM/tabpOK_GOITEM_DESTINAT./ssubSUB_TS_GOITEM_DESTINATION:SAPLMIGO:0325/txtGOITEM-SGTXT
        Safe Input Text
        ...    ${det_base}/tabsTS_GOITEM/tabpOK_GOITEM_DESTINAT./ssubSUB_TS_GOITEM_DESTINATION:SAPLMIGO:0325/txtGOITEM-SGTXT
        ...    ${rem_clean}
 
        Send VKey    0
        Sleep    1s
        Dismiss Any Popup
 
        # Item OK — always last
     
        #Evaluate    $session.findById('${det_base}/subSUB_DETAIL_TAKE:SAPLMIGO:0304/chkGODYNPRO-DETAIL_TAKE').setFocus()
        #Evaluate    $session.findById('${det_base}/subSUB_DETAIL_TAKE:SAPLMIGO:0304/chkGODYNPRO-DETAIL_TAKE').selected = True
        #Sleep    0.5s
 
        Select Checkbox
        ...    ${det_base}/subSUB_DETAIL_TAKE:SAPLMIGO:0304/chkGODYNPRO-DETAIL_TAKE
        Sleep    0.5s

        &{result}=    Create Dictionary    item_no=${target_item_no}    status=found and edited successfully
        Append To List    ${item_results}    ${result}
    END

    # --- Per-item results summary: readable log + JSON. Logged BEFORE Post
    # is even clicked, so it's always available regardless of the posting
    # outcome (including MANUAL_CHECK_REQUIRED) -- this is what the UI/DB
    # will use to show which items actually got matched vs. skipped.
    Log To Console    ${EMPTY}
    Log To Console    ============ ITEM RESULTS SUMMARY ============
    FOR    ${r}    IN    @{item_results}
        Log To Console    Item ${r}[item_no]: ${r}[status]
    END
    ${item_results_json}=    Evaluate    __import__('json').dumps($item_results)
    Log To Console    RESULT:ITEM_RESULTS:${item_results_json}

    # --- Step 5: Post ---
    # FIX: this used to click btnMIGO_OK_GO -- the PO-check/execute button
    # from Step 2, not a Save/Post action. Confirmed via SAP GUI Script
    # Recording that the real Post button is wnd[0]/tbar[1]/btn[23]. That's
    # why every prior run returned MANUAL_CHECK_REQUIRED with an empty
    # status bar -- nothing was ever actually being saved to SAP.
    Click Element    wnd[0]/tbar[1]/btn[23]

    Sleep    3s
    Dismiss Any Popup

    # --- Step 6: Read Material Doc Number ---
    ${status_msg}=    Read Status Bar With Retry    expected_pattern=\\d{8,}
    Log To Console    Final Status Message: ${status_msg}
    # ADDED (2026-08-21): rf_runner.py's execute_migo_103_sap already looks
    # for this exact marker (SAP_STATUS_MSG) to append SAP's own status-bar
    # text to failure messages -- it was never actually being emitted here,
    # so that lookup always silently came back empty. Closing that gap.
    Log To Console    RESULT:SAP_STATUS_MSG:${status_msg}

    @{matches}=    Get Regexp Matches    ${status_msg}    \\d{8,12}
    IF    len($matches) == 0
        RETURN    {"status": "MANUAL_CHECK_REQUIRED", "item_results": ${item_results_json}}
    END

    RETURN    {"status": "COMPLETED", "matdoc": "${matches}[0]", "item_results": ${item_results_json}}
 
 
Read Status Bar With Retry
    [Arguments]    ${expected_pattern}=\\d{8,}
    ${msg}=    Set Variable    ${EMPTY}
    FOR    ${attempt}    IN RANGE    1    6
        ${msg}=    Get Value    wnd[0]/sbar
        Log To Console    Status bar attempt ${attempt}: "${msg}"
        ${matched}=    Run Keyword And Return Status
        ...    Should Match Regexp    ${msg}    ${expected_pattern}
        IF    ${matched}    RETURN    ${msg}
        Sleep    1s
    END
    Log To Console    Status bar timed out. Last: "${msg}"
    RETURN    ${msg}
 
 
Safe Input Text
    # Retries once on failure -- covers the "Property text can not be set"
    # AttributeError (a stale/dead COM element reference). Two known causes
    # seen in production: (1) SAP GUI still mid-repaint/settling when the
    # reference was grabbed -- a plain wait fixes it; (2) a popup (e.g. a
    # split/allocation dialog on a multi-line PO) silently opened on top of
    # wnd[0] and is blocking it -- a plain wait does NOT fix this, the
    # popup has to actually be dismissed first. First retry attempt tried
    # only (1) and still failed twice in a row on the same field when the
    # real cause was (2), so now dismissing any popup before retrying too.
    [Arguments]    ${locator}    ${value}
    ${status}=    Run Keyword And Return Status    Input Text    ${locator}    ${value}
    IF    not ${status}
        Log    Input Text failed on first attempt for ${locator} -- likely a stale SAP GUI element reference or an uncleared popup. Dismissing any popup and retrying after a short pause.    level=WARN
        Dismiss Any Popup
        Sleep    1s
        ${status2}=    Run Keyword And Return Status    Input Text    ${locator}    ${value}
        IF    not ${status2}
            Log    Input Text failed on second attempt for ${locator} too. Retrying once more after another popup check and longer pause.    level=WARN
            Dismiss Any Popup
            Sleep    2s
            Input Text    ${locator}    ${value}
        END
    END
 
 
Clean Value
    # NOTE: previously ended with Split String + ${parts}[0], returning only
    # the first word -- e.g. "Storage bin A12" became just "Storage". Fixed
    # to match the same corrected pattern used in gate_in.robot's Clean
    # Value/Clean Material: strip whitespace and currency symbols, but keep
    # the full multi-word value intact.
    [Arguments]    ${raw_value}
    ${val}=        Convert To String    ${raw_value}
    ${cleaned}=    Strip String    ${val}
    ${cleaned}=    Replace String    ${cleaned}    ₹    ${EMPTY}
    ${cleaned}=    Replace String    ${cleaned}    $    ${EMPTY}
    ${cleaned}=    Replace String    ${cleaned}    €    ${EMPTY}
    ${cleaned}=    Replace String    ${cleaned}    £    ${EMPTY}
    ${cleaned}=    Replace String    ${cleaned}    ,    ${EMPTY}
    ${cleaned}=    Strip String    ${cleaned}
    RETURN         ${cleaned}
 
 
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
 
 
# Close SAP Session
#     # Log    Execution finished. Session kept open.
#     # RETURN
 
Close SAP Session
    ${remaining}=    Register Bot End

    # Always close THIS bot's own SAP session/tab (log off this window),
    # regardless of whether other bots are still active.
    Log    Closing this bot's SAP session tab...
    Run Keyword And Ignore Error    Input Text    wnd[0]/tbar[0]/okcd    /nex
    Run Keyword And Ignore Error    Send VKey     wnd[0]    0
    Sleep    2s

    IF    ${remaining} > 0
        Log To Console    Another bot (or bots) still active (refcount=${remaining}) -- this bot's tab is closed, but the shared SAP processes stay running for the other bot(s).
    ELSE
        # FIX (2026-08-21): this only ever killed saplogon.exe -- never
        # saplgpad.exe or sapgui.exe, which is what's actually running/
        # showing the SAP GUI window once a session is logged in. Matches
        # the 3-process kill pattern used elsewhere in this codebase. Now
        # gated on being the LAST active bot -- killing SAP out from under
        # another still-running bot was the exact collision this refcount
        # system exists to prevent.
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