

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
# INTEGRATION (from migo103_link/migo105_link bots): a document-overview
# tree sidebar can pop up on this same MIGO screen -- these two paths let
# Dismiss Overview Tree Sidebar close it if present. ${session} is the raw
# SAP GUI Scripting session object, needed because a GuiShell tree control's
# .pressButton() method isn't exposed by SapGuiLibrary's own keywords.
${PATH_TREE_CLOSE_BTN}    wnd[0]/shellcont/shell/shellcont[1]/shell[0]
${session}          ${NONE}


*** Test Cases ***
Execute MIGO 103
    [Setup]    Initialize SAP And Login
    ${mat_doc}=    Fill MIGO 103 And Post
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

    Run Keyword And Ignore Error    Run Process    taskkill    /F    /IM    saplogon.exe    /T
    Sleep    2s

    Start Process    ${LOGON_PATH}   
    Sleep    5s

    Connect To Session
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


Connect To Sap Session
    # Attaches to the same live SAP GUI Scripting session SapGuiLibrary is
    # already driving, so Evaluate can call session.findById(...).pressButton(...)
    # -- a GuiShell tree control method SapGuiLibrary's own keywords don't
    # expose. Same pattern as robot_scripts/migo_invoice_link.robot's own
    # Connect To Sap Session. Requires: pip install pywin32 (already a
    # dependency for every other bot here).
    ${sess}=    Evaluate
    ...    __import__('win32com.client').client.GetObject('SAPGUI').GetScriptingEngine.Children(0).Children(0)
    Set Suite Variable    ${session}    ${sess}


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
    ${items_json}=    Evaluate    __import__('base64').b64decode('${ITEMS_JSON_B64}').decode()
    ${items}=         Evaluate    __import__('json').loads('''${items_json}''')
    ${total}=    Get Length    ${items}
    Log To Console    Total matched pairs to fill: ${total}

    # --- Step 1: Navigate to MIGO ---
    Run Transaction    MIGO
    Sleep    3s
    Dismiss Any Popup
    Dismiss Overview Tree Sidebar

    # --- Step 2: Force Goods Receipt + Purchase Order, then set movement type and PO ---
    ${firstline}=    Set Variable
    ...    wnd[0]/usr/ssubSUB_MAIN_CARRIER:SAPLMIGO:0003/subSUB_FIRSTLINE:SAPLMIGO:0011

    # Force action to Goods Receipt (A01)
    Select From List By Label
    ...    ${firstline}/cmbGODYNPRO-ACTION
    ...    Goods Receipt
    Sleep    0.5s

    # Force reference to Purchase Order (R01)
    Select From List By Label
    ...    ${firstline}/cmbGODYNPRO-REFDOC
    ...    Purchase Order
    Sleep    0.5s

    # Movement type 103
    Set Focus     ${firstline}/ctxtGODEFAULT_TV-BWART
    Safe Input Text    ${firstline}/ctxtGODEFAULT_TV-BWART    103

    # PO number
    Set Focus     ${firstline}/subSUB_FIRSTLINE_REFDOC:SAPLMIGO:2000/ctxtGODYNPRO-PO_NUMBER
    Safe Input Text    ${firstline}/subSUB_FIRSTLINE_REFDOC:SAPLMIGO:2000/ctxtGODYNPRO-PO_NUMBER    ${po_clean}

    Send VKey    0
    Sleep    3s
    Dismiss Any Popup

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

    # --- Step 4: Line Items — dynamic, n pairs from ITEMS_JSON ---
    ${det_base}=    Set Variable
    ...    wnd[0]/usr/ssubSUB_MAIN_CARRIER:SAPLMIGO:0003/subSUB_ITEMDETAIL:SAPLMIGO:0301/subSUB_DETAIL:SAPLMIGO:0300

    FOR    ${i}    IN RANGE    ${total}
        ${item}=        Get From List    ${items}    ${i}
        ${line_num}=    Evaluate    ${i} + 1

        ${qty_actual}=    Clean Value    ${item}[qty_actual]
        ${qty_dn}=        Clean Value    ${item}[qty_expected]
        ${item_short_text}=    Run Keyword And Ignore Error    Set Variable    ${item}[short_text]

        # Diagnostic: log this item's own short_text alongside the header
        # REMARKS value that actually gets typed into SGTXT today (see FIX
        # note at the Post step below) -- makes it easy to see, per run,
        # whether they match coincidentally (single-item runs) or diverge
        # (multi-item runs), without having to guess from SAP afterwards.
        Log To Console    Line ${line_num}: qty_actual=${qty_actual} qty_dn=${qty_dn} item_short_text=${item_short_text}[1] (SGTXT actually filled from REMARKS="${rem_clean}" for every line -- see Step 4 note)

        # Navigate to correct line
        Safe Input Text    ${det_base}/txtGODYNPRO-DETAIL_ZEILE    ${line_num}
        Click Element   ${det_base}/btnOK_LOCATE
        Sleep    1s
        Dismiss Any Popup

        # --- Diagnostic: confirm the line actually loaded from the PO and
        # Material auto-populated, BEFORE we touch quantities. We don't have
        # a verified field ID for the Material tab's MATNR control in this
        # environment, so this is a best-effort guess wrapped in Run Keyword
        # And Ignore Error -- if the ID is wrong it just logs "N/A" instead
        # of failing the whole run. If the real ID differs, send us this
        # log line and we'll correct it.
        ${matnr_status}    ${matnr_check}=    Run Keyword And Ignore Error
        ...    Get Value
        ...    ${det_base}/tabsTS_GOITEM/tabpOK_GOITEM_MATERIAL/ssubSUB_TS_GOITEM_MATERIAL:SAPLMIGO:0301/ctxtGOITEM-MATNR
        IF    '${matnr_status}' == 'PASS'
            Log To Console    Line ${line_num} READBACK: MATNR (material)="${matnr_check}"
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

        # Where tab — fill remarks/text
        # NOTE (flagged for review, not yet changed): SGTXT below is filled
        # from ${rem_clean} -- the single header-level REMARKS value -- for
        # EVERY line item, not from this item's own short_text (item[short_text]
        # from items_data is read and logged above for visibility, but not
        # used here). With a single line item this is indistinguishable from
        # "per-item text working correctly", since there's only one value
        # either way -- it only becomes visible with 2+ items that have
        # genuinely different short_text from each other and from REMARKS.
        # Left as-is pending an explicit decision on whether SGTXT should
        # switch to using ${item}[short_text] per line instead.
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
        Select Checkbox
        ...    ${det_base}/subSUB_DETAIL_TAKE:SAPLMIGO:0304/chkGODYNPRO-DETAIL_TAKE
        Sleep    0.5s
    END
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

    @{matches}=    Get Regexp Matches    ${status_msg}    \\d{8,12}
    IF    len($matches) == 0
        RETURN    MANUAL_CHECK_REQUIRED
    END

    RETURN    ${matches}[0]


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
    Log    Closing SAP session...
    Run Keyword And Ignore Error    Input Text    wnd[0]/tbar[0]/okcd    /nex
    Run Keyword And Ignore Error    Send VKey     wnd[0]    0
    Sleep    2s
    Run Keyword And Ignore Error    Run Process    taskkill    /F    /IM    saplogon.exe
    Sleep    2s
    Run Keyword And Ignore Error    Run Process    taskkill    /F    /IM    saplogon.exe    /T
    Log    SAP session closed and process terminated.    level=INFO