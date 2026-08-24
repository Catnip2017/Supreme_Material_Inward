 
*** Settings ***
Documentation     PO Fetch — SAP ME23N PO Line Items Extractor
...               Enters PO number, reads all line items from the grid,
...               navigates to India tab per item for HSN/SAC.
...               Amount logic:
...                 - Multiple lines: amount per line = qty x net_price, total = sum
...                 - Single line: qty = quantity, amount = net_price as-is
...               Outputs: RESULT:PO_DATA:<json_array>
...
...               MULTI-BOT COORDINATION (added 2026-08-21): this bot may run
...               concurrently with other SAP bots (MIGO 103, MIGO 105, MIRO,
...               reversal, etc.) sharing one SAP Logon process. Credentials
...               are UNCHANGED -- still loaded from .env, still the shared
...               spl_rpa account, no per-run overrides. What's new is purely
...               coordination: each bot increments a shared counter file on
...               start and decrements it on finish. SAP processes are only
...               actually killed when the counter reaches 0 -- i.e. when
...               EVERY bot that registered has finished. A bot finishing
...               early just closes its own session tab (/nex) and leaves the
...               shared processes running for the others. The login+
...               session-bind sequence is serialized under a lock so two
...               bots can never open connections at the same instant and
...               race each other for "which connection is mine". Also
...               self-healing: if reusing an existing SAP Logon process
...               fails to connect after a real attempt, it's treated as
...               stale, killed, and a fresh one started.
...
...               Counter file: C:\temp\sap_bot_refcount.txt
...               Lock (mutex) dir: C:\temp\sap_bot_refcount.lock
Library           SapGuiLibrary
Library           Process
Library           OperatingSystem
Library           String
Library           Collections
Library           sap_helpers.py
*** Variables ***
${PO_NUMBER}      ${EMPTY}
${ITEM_COMBO}    wnd[0]/usr/subSUB0:SAPLMEGUI:0015/subSUB3:SAPLMEVIEWS:1100/subSUB2:SAPLMEVIEWS:1200/subSUB1:SAPLMEGUI:1301/subSUB1:SAPLMEGUI:6000/cmbDYN_6000-LIST
# ============================================================
# CONFIRMED PATHS FROM GUI RECORDING
# ============================================================
${TABLE}    wnd[0]/usr/subSUB0:SAPLMEGUI:0015/subSUB2:SAPLMEVIEWS:1100/subSUB2:SAPLMEVIEWS:1200/subSUB1:SAPLMEGUI:1211/tblSAPLMEGUITC_1211
 
# Field names (confirmed from recording)
${F_MATERIAL}    ctxtMEPO1211-EMATN
${F_SHORTTEXT}   txtMEPO1211-TXZ01
${F_QUANTITY}    txtMEPO1211-MENGE
${F_NETPRICE}    txtMEPO1211-NETPR
${F_UOM}    ctxtMEPO1211-MEINS
 
# Column indices (confirmed from recording)
${COL_MATERIAL}    4
${COL_SHORTTEXT}   5
${COL_QUANTITY}    6
${COL_NETPRICE}    10
${COL_UOM}    7
 
# India tab and HSN/SAC (confirmed from recording)
${INDIA_TAB}      wnd[0]/usr/subSUB0:SAPLMEGUI:0015/subSUB3:SAPLMEVIEWS:1100/subSUB2:SAPLMEVIEWS:1200/subSUB1:SAPLMEGUI:1301/subSUB2:SAPLMEGUI:1303/tabsITEM_DETAIL/tabpTABIDT13
${HSN_FIELD}    wnd[0]/usr/subSUB0:SAPLMEGUI:0015/subSUB3:SAPLMEVIEWS:1100/subSUB2:SAPLMEVIEWS:1200/subSUB1:SAPLMEGUI:1301/subSUB2:SAPLMEGUI:1303/tabsITEM_DETAIL/tabpTABIDT13/ssubTABSTRIPCONTROL1SUB:SAPLMEGUI:1344/ctxtMEPO1344-STEUC
${BTN_NEXT_ITEM}  wnd[0]/usr/subSUB0:SAPLMEGUI:0015/subSUB3:SAPLMEVIEWS:1100/subSUB2:SAPLMEVIEWS:1200/subSUB1:SAPLMEGUI:1301/subSUB1:SAPLMEGUI:6000/btn%#AUTOTEXT001
 
# PO number entry via Other Purchase Order button
${BTN_OTHER_PO}    wnd[0]/tbar[1]/btn[17]
${POPUP_PO_FIELD}  wnd[1]/usr/subSUB0:SAPLMEGUI:0003/ctxtMEPO_SELECT-EBELN
${POPUP_CONFIRM}   wnd[1]/tbar[0]/btn[0]
 
 
# Delivery tab and Open Quantity (confirmed from recording)
${DELIVERY_TAB}      wnd[0]/usr/subSUB0:SAPLMEGUI:0015/subSUB3:SAPLMEVIEWS:1100/subSUB2:SAPLMEVIEWS:1200/subSUB1:SAPLMEGUI:1301/subSUB2:SAPLMEGUI:1303/tabsITEM_DETAIL/tabpTABIDT6
${OPEN_QTY_TABLE}    wnd[0]/usr/subSUB0:SAPLMEGUI:0015/subSUB3:SAPLMEVIEWS:1100/subSUB2:SAPLMEVIEWS:1200/subSUB1:SAPLMEGUI:1301/subSUB2:SAPLMEGUI:1303/tabsITEM_DETAIL/tabpTABIDT6/ssubTABSTRIPCONTROL1SUB:SAPLMEGUI:1320/tblSAPLMEGUITC_1320
${F_OPENQTY}          txtMEPO1320-OBMNG
${COL_OPENQTY}        10
${BTN_NEXT_ITEM_OQ}    wnd[0]/usr/subSUB0:SAPLMEGUI:0015/subSUB3:SAPLMEVIEWS:1100/subSUB2:SAPLMEVIEWS:1200/subSUB1:SAPLMEGUI:1301/subSUB1:SAPLMEGUI:6000/btn%#AUTOTEXT002
# India tab presence-check path (captured AFTER Delivery/open-qty tab visit —
# screen number differs from the pre-open-qty INDIA_TAB path: 0019 vs 0015)
${INDIA_TAB_CHECK}    wnd[0]/usr/subSUB0:SAPLMEGUI:0019/subSUB3:SAPLMEVIEWS:1100/subSUB2:SAPLMEVIEWS:1200/subSUB1:SAPLMEGUI:1301/subSUB2:SAPLMEGUI:1303/tabsITEM_DETAIL/tabpTABIDT13
# Item number column — used to detect blank rows (Material can be
# empty for service/non-stock lines, but Itm is always populated)
${F_ITEM}      txtMEPO1211-EBELP
${COL_ITEM}    1
 
# Deletion indicator icon on grid rows (ADDED 2026-08-12 — confirmed from
# recording). B_DELE icon = line marked for deletion in SAP.
${F_DELFLAG}      btnMEPO1211-STATUSICON
${COL_DELFLAG}    0
${session}    ${NONE}
 
# --- Multi-bot coordination (added 2026-08-21) ------------------------------
${REFCOUNT_FILE}           C:\\temp\\sap_bot_refcount.txt
${REFCOUNT_LOCK_DIR}       C:\\temp\\sap_bot_refcount.lock
 
 
 
*** Test Cases ***
Execute PO Fetch
    [Setup]    Initialize SAP And Login
    ${po_data}=    Fetch PO Line Items
    Log To Console    RESULT:PO_DATA:${po_data}
    Sleep    3s
    [Teardown]    Close SAP Session
 
 
*** Keywords ***
Initialize SAP And Login
    Evaluate    __import__('dotenv').load_dotenv(__import__('os').getenv('DOTENV_PATH', '.env'), override=True)
    ${CLIENT}=      Evaluate    __import__('os').getenv('SAP_CLIENT')
    ${CONN_NAME}=   Evaluate    __import__('os').getenv('SAP_CONNECTION_NAME')
    ${LOGON_PATH}=  Evaluate    __import__('os').getenv('SAP_LOGON_PATH')
 
    # v16: PO Fetch (ME23N line-item read) ALWAYS uses the shared spl_rpa
    # .env account below -- unlike gate_in/migo_103/migo_105/miro/
    # zgatein_update, this bot never checks for a per-user SAP_USER_OVERRIDE/
    # SAP_PASS_OVERRIDE credential. It's a read-only PO lookup feeding
    # MIGO's line items, not an attributable posting, so rf_runner.py
    # deliberately never passes an override into this bot's environment --
    # by client decision, not an oversight. See .env for the full comment.
    ${USERNAME}=    Evaluate    __import__('os').getenv('SAP_USERNAME')
    ${PASSWORD}=    Evaluate    __import__('os').getenv('SAP_PASSWORD')
 
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
        Start Process    ${LOGON_PATH}
        Sleep    5s
        Wait Until Keyword Succeeds    10x    2s    Connect To Session
    END
 
    Open Connection    ${CONN_NAME}
 
    Input Text        wnd[0]/usr/txtRSYST-MANDT    ${CLIENT}
    Input Text        wnd[0]/usr/txtRSYST-BNAME    ${USERNAME}
    Input Password    wnd[0]/usr/pwdRSYST-BCODE    ${PASSWORD}
    Click Element     wnd[0]/tbar[0]/btn[0]
 
    # FIX: same reordering as gate_in.robot/migo_103.robot/migo_105.robot --
    # see migo_103.robot's comment for the full explanation. Generic
    # Dismiss Any Popup used to run first and would already close the
    # multi-logon dialog with SAP's default choice before this specific
    # OPT1 selection could run. Kept as OPT1 here (unchanged from
    # production -- NOT switched to OPT2; that's an unreconciled
    # discrepancy in the reference bot and wasn't part of what was asked
    # to change, so production's already-working choice is preserved).
    Sleep    3s
    ${multi}=    Run Keyword And Return Status    Element Should Be Present    wnd[1]
    IF    ${multi}
       Run Keyword And Ignore Error    Select Radio Button    wnd[1]/usr/radMULTI_LOGON_OPT1
       Run Keyword And Ignore Error    Click Element          wnd[1]/tbar[0]/btn[0]
       Sleep    2s
    END
 
    Dismiss Any Popup
    Maximize Window    0
    Connect To Sap Session
 
    # Only release the lock once THIS bot's own session is fully bound --
    # from here on, another bot is free to open its own connection without
    # any chance of grabbing this bot's window by mistake.
    Release Refcount Lock
 
 
Connect To Sap Session
    # MULTI-BOT FIX (2026-08-21): previously used
    # GetScriptingEngine.Children(0).Children(0), which always grabs the
    # FIRST connection/session that exists -- wrong bot under concurrency.
    # SapGuiLibrary itself already knows exactly which connection/session
    # THIS bot's own Open Connection call just created, so reuse that
    # instead of re-deriving it by index.
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
 
Dump Delflag Property
    [Documentation]    TEMP DIAGNOSTIC — run once per test PO, then remove.
    FOR    ${r}    IN RANGE    0    1
        ${result}=    Get Delflag Status    ${TABLE}    ${r}    ${session}
        Log To Console    Row ${r} -> ${result}
    END
 
Dump Screen Elements
    [Documentation]    Diagnostic only — logs the technical IDs of all elements
    ...    on the current SAP screen so we can find the correct paths for
    ...    this PO/document type. Run this once against PO 4700000699 with
    ...    the Conditions/Delivery tab open, then remove or comment out.
    ${session_info}=    Evaluate
    ...    __import__('subprocess').run(['echo'], shell=True)
    ${dump}=    Run Keyword And Ignore Error    Get Session Info
    Log To Console    SESSION INFO: ${dump}
 
 
Fetch PO Line Items
    # Navigate to ME23N
    Run Transaction    ME23N
    Sleep    3s
    Dismiss Any Popup
 
    # Enter PO number via Other Purchase Order button
    Click Element    ${BTN_OTHER_PO}
    Sleep    2s
    Input Text       ${POPUP_PO_FIELD}    ${PO_NUMBER}
    Click Element    ${POPUP_CONFIRM}
    Sleep    3s
    Dismiss Any Popup

    # ADDED: check status bar right after PO entry -- an invalid/
    # non-existent PO number produces "Document <PO_NUMBER> does not
    # exist" here instead of ever loading a real PO screen. If seen, stop
    # immediately -- sidebar/item-overview/grid steps make no sense on a
    # screen with no PO loaded, and would just produce blank/garbage
    # output. Close SAP Session still runs afterward as [Teardown].
    ${status_msg_res}=    Run Keyword And Ignore Error    Get Value    wnd[0]/sbar
    ${status_msg}=    Set Variable If    '${status_msg_res}[0]' == 'PASS'    ${status_msg_res}[1]    ${EMPTY}
    Log To Console    Status bar after PO entry: "${status_msg}"

    ${po_not_found}=    Run Keyword And Return Status
    ...    Should Match Regexp    ${status_msg}    (?i).*does not exist.*
    IF    ${po_not_found}
        Log To Console    PO ${PO_NUMBER} does not exist -- stopping, no further processing.
        # FIX (2026-08-24): was hand-glued string interpolation
        # ({"status":"...","message":"${status_msg}",...}) -- if SAP's raw
        # status bar text ever contains a double-quote character, that
        # breaks the JSON on the Python side silently (same bug class fixed
        # in migo_reversal.robot). Use json.dumps() instead so this is
        # always valid regardless of what SAP's message says. $status_msg/
        # $PO_NUMBER (no braces) passes the actual RF variables into the
        # Python expression rather than re-interpolating as text first.
        ${po_not_found_json}=    Evaluate
        ...    __import__('json').dumps({"status": "PO_NOT_FOUND", "message": $status_msg, "po_number": $PO_NUMBER})
        RETURN    [${po_not_found_json}]
    END

    Ensure Item Overview And Detail Open
    Dismiss PO Overview Sidebar
    # ADDED: Open Quantity column index shifts for 46xx/47xx POs -- the
    # OBMNG field lands in grid column 25 instead of the usual 10 for
    # these PO number ranges. Override the module-level default now that
    # the PO number is known, before STEP 2 builds any OPEN_QTY_TABLE paths.
    ${po_prefix}=    Evaluate    str($PO_NUMBER)[:2]
    IF    '${po_prefix}' == '46' or '${po_prefix}' == '47'
        Set Suite Variable    ${COL_OPENQTY}    25
        Log To Console    PO ${PO_NUMBER} starts with ${po_prefix} -- using COL_OPENQTY=25
    ELSE
        Set Suite Variable    ${COL_OPENQTY}    10
        Log To Console    PO ${PO_NUMBER} starts with ${po_prefix} -- using default COL_OPENQTY=10
    END
 
    Select Item Detail Tab    tabpTABIDT7
   
    Sleep    1s
    Select Item Detail Tab    tabpTABIDT6
    Sleep    1s
 
    # --------------------------------------------------------
    # STEP 1: Read all line items from the grid
    # --------------------------------------------------------
    @{items}=    Create List
    @{seen_item_nos}=    Create List
    ${row_idx}=    Set Variable    0
    ${scroll_pos}=    Set Variable    0
    ${visible_rows}=    Set Variable    2
    WHILE    True
        ${row_in_view}=    Evaluate    ${row_idx} - ${scroll_pos}
        IF    ${row_in_view} >= ${visible_rows}
            ${scroll_pos}=    Set Variable    ${row_idx}
            ${scroll_res}=    Run Keyword And Ignore Error    Scroll Table Via Vbs    ${TABLE}    ${scroll_pos}
            Log To Console    Scroll to row ${scroll_pos}: ${scroll_res}[0]
            Sleep    0.5s
 
            # FIX (2026-08-24): Scroll Table Via Vbs's result was only ever
            # LOGGED above, never checked -- scroll_pos advances in this RF
            # variable regardless of whether the on-screen grid actually
            # moved. If the VBS scroll silently failed (subprocess hiccup,
            # SAP GUI COM busy), every row read afterward in this "page"
            # computes the WRONG physical grid row (grid_row = row_idx -
            # scroll_pos) and re-reads rows already seen -- confirmed as the
            # real cause of a duplicate seat-number row appearing twice in a
            # real PO fetch (one copy wrongly carrying a stale Deleted
            # flag). Verify the scroll actually landed by reading the
            # table's own VerticalScrollbar.Position back; retry once if
            # it didn't, and log loudly either way so a persistent mismatch
            # is visible in the log instead of silently producing bad data.
            ${actual_pos_res}=    Run Keyword And Ignore Error
            ...    Evaluate    $session.findById('${TABLE}').VerticalScrollbar.Position
            ${scroll_confirmed}=    Run Keyword And Return Status
            ...    Should Be Equal As Integers    ${actual_pos_res}[1]    ${scroll_pos}
            IF    not ${scroll_confirmed}
                Log To Console    WARNING: scroll to ${scroll_pos} did not land (grid reports position ${actual_pos_res}[1]) -- retrying once.
                Run Keyword And Ignore Error    Scroll Table Via Vbs    ${TABLE}    ${scroll_pos}
                Sleep    0.8s
                ${actual_pos_res2}=    Run Keyword And Ignore Error
                ...    Evaluate    $session.findById('${TABLE}').VerticalScrollbar.Position
                ${scroll_confirmed2}=    Run Keyword And Return Status
                ...    Should Be Equal As Integers    ${actual_pos_res2}[1]    ${scroll_pos}
                IF    not ${scroll_confirmed2}
                    Log To Console    WARNING: scroll to ${scroll_pos} STILL not confirmed after retry (grid reports position ${actual_pos_res2}[1]) -- proceeding, but rows read from here on may be misaligned/duplicated. Check this PO's output carefully.
                END
            END
        END
        ${grid_row}=    Evaluate    ${row_idx} - ${scroll_pos}
 
        ${item_path}=   Set Variable    ${TABLE}/${F_ITEM}\[${COL_ITEM},${grid_row}\]
        ${mat_path}=    Set Variable    ${TABLE}/${F_MATERIAL}\[${COL_MATERIAL},${grid_row}\]
        ${txt_path}=    Set Variable    ${TABLE}/${F_SHORTTEXT}\[${COL_SHORTTEXT},${grid_row}\]
        ${qty_path}=    Set Variable    ${TABLE}/${F_QUANTITY}\[${COL_QUANTITY},${grid_row}\]
        ${price_path}=  Set Variable    ${TABLE}/${F_NETPRICE}\[${COL_NETPRICE},${grid_row}\]
        ${uom_path}=    Set Variable    ${TABLE}/${F_UOM}\[${COL_UOM},${grid_row}\]
 
        # Use the Itm column to decide whether this row is real — Material
        # can be blank for service/non-stock lines (e.g. AMC/ARC POs), but
        # Itm is always populated when the row exists.
        ${item_res}=    Run Keyword And Ignore Error    Get Value    ${item_path}
 
        IF    '${item_res}[0]' == 'FAIL'    BREAK
 
        ${item_no_raw}=    Clean SAP Value    ${item_res}[1]
 
        IF    '${item_no_raw}' == ''
            Log To Console    Row ${row_idx} is blank (Itm empty) — end of items, stopping scan
            BREAK
        END
 
        # --- Deletion detection (ADDED 2026-08-12) ---------------------
        # A SAP-deleted PO line still renders a grid row -- without this,
        # it was indistinguishable from a genuine active line with similar
        # data, reported as line items appearing to "repeat". Extract the
        # row's fields regardless of deletion status; just tag it.
        ${del_result}=    Get Delflag Status    ${TABLE}    ${grid_row}    ${session}
        ${is_deleted}=    Run Keyword And Return Status
        ...    Should Contain    ${del_result}    IconName='B_DELE'
        IF    ${is_deleted}
            ${row_status}=    Set Variable    Deleted
        ELSE
            ${row_status}=    Set Variable    Active
        END
        Log To Console    Row ${row_idx} (Item ${item_no_raw}) delflag="${del_result}" -> row_status=${row_status}
 
        ${mat_res}=    Run Keyword And Ignore Error    Get Value    ${mat_path}
        IF    '${mat_res}[0]' == 'PASS'
            ${material}=    Clean SAP Value    ${mat_res}[1]
        ELSE
            ${material}=    Set Variable    None
        END
 
        ${txt_res}=      Run Keyword And Ignore Error    Get Value    ${txt_path}
        IF    '${txt_res}[0]' == 'PASS'
            ${short_text}=    Clean SAP Value    ${txt_res}[1]
        ELSE
            ${short_text}=    Set Variable    None
        END
        ${qty_res}=      Run Keyword And Ignore Error    Get Value    ${qty_path}
        IF    '${qty_res}[0]' == 'PASS'
            ${qty_raw}=    Clean SAP Value    ${qty_res}[1]
        ELSE
            ${qty_raw}=    Set Variable    None
        END
        ${price_res}=    Run Keyword And Ignore Error    Get Value    ${price_path}
        IF    '${price_res}[0]' == 'PASS'
            ${price_raw}=    Clean SAP Value    ${price_res}[1]
        ELSE
            ${price_raw}=    Set Variable    None
        END
        ${uom_res}=      Run Keyword And Ignore Error    Get Value    ${uom_path}
        IF    '${uom_res}[0]' == 'PASS'
            ${uom_raw}=    Clean SAP Value    ${uom_res}[1]
        ELSE
            ${uom_raw}=    Set Variable    None
        END
        Log To Console    Row ${row_idx} READ: material="${material}" short_text="${short_text}" qty="${qty_raw}" net_price="${price_raw}"
 
        &{row_data}=    Create Dictionary
        ...    item_no=${item_no_raw}
        ...    material=${material}
        ...    short_text=${short_text}
        ...    net_price=${price_raw}
        ...    uom=${uom_raw}
        ...    qty=${qty_raw}
        ...    row_status=${row_status}
 
        # DEFENSIVE GUARD (2026-08-24): item_no (EBELP/PO seat number) is
        # always unique per PO in valid SAP data. If the same item_no shows
        # up twice in one fetch -- which should only happen if a scroll
        # silently failed and the loop re-read a row it already captured
        # (see the scroll-verification fix above) -- skip appending the
        # duplicate instead of saving two rows for one real PO line. This is
        # a safety net on top of the scroll fix, not a replacement for it.
        ${already_seen}=    Run Keyword And Return Status
        ...    List Should Contain Value    ${seen_item_nos}    ${item_no_raw}
        IF    ${already_seen}
            Log To Console    WARNING: item_no ${item_no_raw} already captured earlier in this fetch -- skipping duplicate row ${row_idx} (likely caused by a scroll that didn't land). Not appending.
        ELSE
            Append To List    ${seen_item_nos}    ${item_no_raw}
            Append To List    ${items}    ${row_data}
        END
        ${row_idx}=    Evaluate    ${row_idx} + 1
        IF    ${row_idx} >= 100    BREAK
    END
 
    ${total_rows}=    Get Length    ${items}
    Log To Console    Found ${total_rows} line item(s) for PO ${PO_NUMBER}
 
    IF    ${total_rows} == 0
        RETURN    []
    END
 
    # --------------------------------------------------------
    # Check India tab AFTER grid items are read. If absent,
    # store table/item data only — no HSN/open-qty extraction.
    # --------------------------------------------------------
 
 
    # --------------------------------------------------------
    # STEP 2: Read Open Quantity — click Delivery tab once,
    # then press next-item button between reads
    # --------------------------------------------------------
    @{open_qty_list}=    Create List
    Run Keyword And Ignore Error    Set Combo Via Vbs    ${ITEM_COMBO}    1
    Sleep    1s
    Run Keyword And Ignore Error    Click Element    ${DELIVERY_TAB}
    Sleep    1s
 
    # FIX (2026-08-10): backstop for STEP 1's item-count bug (see
    # sap_helpers.py's scroll_table_via_vbs comment for the root cause).
    # If total_rows is ever wrong again for any other reason, or SAP's GUI
    # session degrades mid-loop, 3 consecutive failed Open Qty reads now
    # stops this loop instead of blindly grinding through every remaining
    # (possibly dozens of) iteration at 3s each against a dead/confused
    # session. Confirmed from a real run's log (PO 4100035702): once Get
    # Value starts failing here, it does not recover on its own -- it
    # degrades further (element-not-found, then RPC server unavailable).
    # Any items not reached are already backfilled with "" by the padding
    # WHILE loop right after this FOR loop, so an early BREAK is safe.
    # Kept when the deletion-flag detection was added (2026-08-12) --
    # that's a separate fix (distinguishing deleted lines from active
    # ones), not a replacement for this session-degradation safety net.
    ${consec_fail}=    Set Variable    0
 
    FOR    ${i}    IN RANGE    ${total_rows}
        ${oq_path}=    Set Variable    ${OPEN_QTY_TABLE}/${F_OPENQTY}\[${COL_OPENQTY},0\]
        ${oq_res}=    Run Keyword And Ignore Error    Get Value    ${oq_path}
        Log To Console    Item ${i} Open Qty read: ${oq_res}[0] = ${oq_res}[1]
        IF    '${oq_res}[0]' == 'PASS'
            ${open_qty}=    Clean SAP Value    ${oq_res}[1]
            ${consec_fail}=    Set Variable    0
        ELSE
            ${open_qty}=    Set Variable    ${EMPTY}
            ${consec_fail}=    Evaluate    ${consec_fail} + 1
        END
        Append To List    ${open_qty_list}    ${open_qty}
        Log To Console    Item ${i} Open Quantity: ${open_qty}
 
        IF    ${consec_fail} >= 3
            Log To Console    3 consecutive Open Qty read failures at item ${i} -- stopping early (SAP session likely past last real item or degraded)
            BREAK
        END
 
        IF    ${i} < ${total_rows} - 1
            Run Keyword And Ignore Error    Click Element    ${BTN_NEXT_ITEM_OQ}
            Sleep    3s
        END
    END
    ${oq_count}=    Get Length    ${open_qty_list}
    WHILE    ${oq_count} < ${total_rows}
        Append To List    ${open_qty_list}    ${EMPTY}
        ${oq_count}=    Evaluate    ${oq_count} + 1
    END
   # --------------------------------------------------------
    # STEP 3: Read HSN/SAC per item — check India tab presence
    # for EACH item individually (some items on a PO may have it,
    # others may not). If present -> click it and read HSN. If not
    # present -> store "" for that item's hsn_sac and continue.
    # --------------------------------------------------------
    @{hsn_list}=    Create List
 
    FOR    ${i}    IN RANGE    ${total_rows}
        ${item_index}=    Evaluate    ${i} + 1
        ${combo_res}=    Run Keyword And Ignore Error    Set Combo Via Vbs    ${ITEM_COMBO}    ${item_index}
        Log To Console    Item ${i} combo: ${combo_res}[0]
        Sleep    1.5s
 
        ${india_present}=    Run Keyword And Return Status    Element Should Be Present    ${INDIA_TAB}
 
        IF    ${india_present}
            Run Keyword And Ignore Error    Click Element    ${INDIA_TAB}
            Sleep    1s
 
            ${hsn_res}=    Run Keyword And Ignore Error    Get Value    ${HSN_FIELD}
            Log To Console    Item ${i} HSN read: ${hsn_res}[0] = ${hsn_res}[1]
            IF    '${hsn_res}[0]' == 'PASS'
                ${hsn}=    Clean SAP Value    ${hsn_res}[1]
            ELSE
                ${hsn}=    Set Variable    ${EMPTY}
            END
        ELSE
            Log To Console    Item ${i} India tab NOT present — storing "" for hsn_sac
            ${hsn}=    Set Variable    ${EMPTY}
        END
        Append To List    ${hsn_list}    ${hsn}
        Log To Console    Item ${i} HSN/SAC: ${hsn}
    END
 
    ${hsn_count}=    Get Length    ${hsn_list}
    WHILE    ${hsn_count} < ${total_rows}
        Append To List    ${hsn_list}    ${EMPTY}
        ${hsn_count}=    Evaluate    ${hsn_count} + 1
    END
    # --------------------------------------------------------
    # STEP 5: Calculate amounts and build JSON output
    # Single row  → amount = net_price as-is
    # Multiple rows → amount per row = qty × net_price
    #                 append TOTAL row at end
    # --------------------------------------------------------
    @{json_items}=    Create List
    ${running_total}=    Set Variable    ${0}
 
    FOR    ${i}    IN RANGE    ${total_rows}
        ${row}=         Get From List    ${items}    ${i}
        ${hsn}=         Get From List    ${hsn_list}    ${i}
        ${open_qty}=    Get From List    ${open_qty_list}    ${i}
        ${row_status}=  Get From Dictionary    ${row}    row_status
        ${material}=    Get From Dictionary    ${row}    material
        ${short_text}=  Get From Dictionary    ${row}    short_text
        ${qty_str}=     Get From Dictionary    ${row}    qty
        ${price_str}=   Get From Dictionary    ${row}    net_price
        ${uom}=         Get From Dictionary    ${row}    uom
        ${item_no}=     Get From Dictionary    ${row}    item_no
 
        ${qty_clean}=    Remove String    ${qty_str}    ,
        ${price_clean}=  Remove String    ${price_str}    ,
        IF    '${qty_clean}' == 'None' or '${qty_clean}' == '${EMPTY}'
            ${qty_clean}=    Set Variable    0
        END
        IF    '${price_clean}' == 'None' or '${price_clean}' == '${EMPTY}'
            ${price_clean}=    Set Variable    0
        END
 
        # If the Rate/Net Price column doesn't exist for this PO type at
        # all, leave amount blank too rather than showing a fabricated 0.
        IF    '${price_str}' == '${EMPTY}'
            ${line_amount}=    Set Variable    ${EMPTY}
        ELSE
            IF    ${total_rows} == 1
                ${line_amount}=    Set Variable    ${price_str}
            ELSE
                ${line_amount}=    Evaluate
                ...    str(round(float('${qty_clean}' or '0') * float('${price_clean}' or '0'), 2))
                ${running_total}=    Evaluate
                ...    round(${running_total} + float('${qty_clean}' or '0') * float('${price_clean}' or '0'), 2)
            END
        END
 
        # FIX (2026-08-21): this used to be a synthetic guess --
        # Evaluate str(($i + 1) * 10) -- assuming PO items are always
        # numbered 10, 20, 30... in order with no gaps. Real POs can have
        # gaps (e.g. a line deleted in SAP), so that guess silently
        # diverged from the true SAP item number (EBELP). item_no was
        # already captured correctly from the grid in STEP 1 (see
        # Get Value ${F_ITEM} above) and stored in the row dict -- just
        # read it back instead of re-deriving it positionally. This is
        # what MIGO 103's EBELP-based matching depends on being correct.
 
        Log To Console    Item ${item_no} FINAL: material_code="${material}" short_text="${short_text}" uom="${uom}" qty="${qty_str}" rate="${price_str}" amount="${line_amount}" hsn_sac="${hsn}" open_qty="${open_qty}" row_status="${row_status}"
 
        ${json}=    Set Variable
        ...    {"item_no":"${item_no}","material_code":"${material}","short_text":"${short_text}","uom":"${uom}","rate":"${price_str}","qty":"${qty_str}","amount":"${line_amount}","hsn_sac":"${hsn}","open_qty":"${open_qty}","row_status":"${row_status}"}
        Append To List    ${json_items}    ${json}
    END
 
    IF    ${total_rows} > 1
        ${total_json}=    Set Variable
        ...    {"item_no":"TOTAL","material_code":"","short_text":"Total Amount","uom":"","rate":"","qty":"","amount":"${running_total}","hsn_sac":"","open_qty":"","row_status":""}
        Append To List    ${json_items}    ${total_json}
    END
 
    ${joined}=    Evaluate    ",".join($json_items)
    RETURN    [${joined}]
 
Dismiss PO Overview Sidebar
    [Documentation]    The ME23N/ME22N PO display screen can show a left-
    ...    side "Document Overview" tree sidebar (variant/hierarchy panel).
    ...    wnd[0]/tbar[1]/btn[9] closes it, btn[8] opens it -- both appear
    ...    to sit at fixed positions on this toolbar regardless of state,
    ...    so presence of btn[9] is used as the "currently open" signal:
    ...    if it's there, click it to close; if not, the sidebar isn't
    ...    open and there's nothing to do.
    ${sidebar_open}=    Run Keyword And Return Status
    ...    Element Should Be Present    wnd[0]/tbar[1]/btn[9]
    IF    ${sidebar_open}
        Run Keyword And Ignore Error    Click Element    wnd[0]/tbar[1]/btn[9]
        Sleep    1s
        Log To Console    PO overview sidebar was open -- closed it.
    ELSE
        Log To Console    PO overview sidebar not open -- nothing to do.
    END
 
 
Ensure Item Overview And Detail Open
    [Documentation]    Item Overview / Item Detail toggle. Detection uses
    ...    the CLOSE-button path (fixed at screen 0015). If NOT found,
    ...    section is closed -- press the OPEN-button path (fixed at
    ...    screen 0016). If found, section is already open -- do nothing.
    ...    Detection and action deliberately use DIFFERENT fixed paths
    ...    (not a probe loop over the same path) -- looping the same
    ...    relative path across 0015/0016 was matching the close button
    ...    at 0015 and pressing it, closing an already-open section.
 
    # --- Item Overview ---
    ${overview_close_found}=    Run Keyword And Return Status
    ...    Element Should Be Present
    ...    wnd[0]/usr/subSUB0:SAPLMEGUI:0015/subSUB2:SAPLMEVIEWS:1100/subSUB1:SAPLMEVIEWS:4001/btnDYN_4000-BUTTON
 
    IF    not ${overview_close_found}
        Run Keyword And Ignore Error    Evaluate
        ...    $session.findById('wnd[0]/usr/subSUB0:SAPLMEGUI:0016/subSUB2:SAPLMEVIEWS:1100/subSUB1:SAPLMEVIEWS:4001/btnDYN_4000-BUTTON').press()
        Sleep    1s
        Log To Console    Item Overview was closed -- opened it.
    ELSE
        Log To Console    Item Overview already open -- nothing to do.
    END
 
    # --- Item Detail ---
    ${detail_close_found}=    Run Keyword And Return Status
    ...    Element Should Be Present
    ...    wnd[0]/usr/subSUB0:SAPLMEGUI:0015/subSUB3:SAPLMEVIEWS:1100/subSUB1:SAPLMEVIEWS:4002/btnDYN_4000-BUTTON
 
    IF    not ${detail_close_found}
        Run Keyword And Ignore Error    Evaluate
        ...    $session.findById('wnd[0]/usr/subSUB0:SAPLMEGUI:0016/subSUB3:SAPLMEVIEWS:1100/subSUB1:SAPLMEVIEWS:4002/btnDYN_4000-BUTTON').press()
        Sleep    1s
        Log To Console    Item Detail was closed -- opened it.
    ELSE
        Log To Console    Item Detail already open -- nothing to do.
    END
 
 
Select Item Detail Tab
    [Arguments]    ${tab_key}
    # ITEM_DETAIL tabstrip's screen number shifts (0010 / 0015 / 0019 seen
    # so far, same pattern as the INDIA_TAB / INDIA_TAB_CHECK split above)
    # -- probe each variant instead of hardcoding one.
    FOR    ${scr}    IN    0010    0015    0019
        ${candidate}=    Set Variable
        ...    wnd[0]/usr/subSUB0:SAPLMEGUI:${scr}/subSUB3:SAPLMEVIEWS:1100/subSUB2:SAPLMEVIEWS:1200/subSUB1:SAPLMEGUI:1301/subSUB2:SAPLMEGUI:1303/tabsITEM_DETAIL/${tab_key}
        ${found}=    Run Keyword And Return Status    Element Should Be Present    ${candidate}
        IF    ${found}
            Evaluate    $session.findById('${candidate}').select()
            RETURN
        END
    END
    Fail    None of the known screen-number variants (0010/0015/0019) had ${tab_key}.
 
 
 
Clean SAP Value
    [Arguments]    ${raw}
    ${val}=    Convert To String    ${raw}
    ${val}=    Strip String    ${val}
    ${val}=    Replace String    ${val}    "    '
    ${val}=    Replace String    ${val}    \\    /
    IF    $val == 'None' or $val == 'null'
        RETURN    ${EMPTY}
    END
    RETURN    ${val}
 
 
Dismiss Any Popup
    ${p1}=    Run Keyword And Return Status    Element Should Be Present    wnd[1]
    IF    ${p1}
        Run Keyword And Ignore Error    Click Element    wnd[1]/tbar[0]/btn[0]
        Sleep    1s
    END
    ${p2}=    Run Keyword And Return Status    Element Should Be Present    wnd[2]
    IF    ${p2}
        Run Keyword And Ignore Error    Click Element    wnd[2]/tbar[0]/btn[0]
        Sleep    1s
    END
 
 
Close SAP Session
    ${remaining}=    Register Bot End
 
    # Always close THIS bot's own SAP session/tab (log off this window),
    # regardless of whether other bots are still active.
    Run Keyword And Ignore Error    Input Text    wnd[0]/tbar[0]/okcd    /nex
    Run Keyword And Ignore Error    Send VKey     wnd[0]    0
    Sleep    2s
 
    IF    ${remaining} > 0
        Log To Console    Another bot (or bots) still active (refcount=${remaining}) -- this bot's tab is closed, but the shared SAP processes stay running for the other bot(s).
    ELSE
        # FIX: this only ever killed saplogon.exe (twice, redundantly) -- never
        # saplgpad.exe or sapgui.exe, which is what's actually running/showing
        # the SAP GUI window once a session is logged in. Every other script in
        # this codebase (gate_in.robot's Close SAP On Error, migo103_link.robot,
        # migo_103.robot, etc.) kills all three on close/error; this one was
        # simply out of sync with that pattern. That's the real, confirmed cause
        # of "the bot finished, looped through everything, but SAP was still
        # open at the end" -- /nex + saplogon.exe alone can leave the actual GUI
        # window/process sitting there untouched. Matches the 3-process pattern
        # used everywhere else now. Now gated on being the LAST active bot
        # (2026-08-21) -- killing SAP out from under another still-running
        # bot was the exact collision this refcount system exists to prevent.
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
 