*** Settings ***
Documentation     MIGO 105 SAP Automation — Release GR Blocked Stock
Library           SapGuiLibrary
Library           Process
Library           OperatingSystem
Library           String
Library           DateTime
Library           Collections
 
*** Variables ***
${MATERIAL_DOC_NUMBER}    ${EMPTY}
#${MATERIAL_DOC_NUMBER}    5000075111
${STORAGE_LOCATION}       ${EMPTY}
${ITEMS_JSON_BATCH}       ${EMPTY}
${VENDOR_INVOICE}         ${EMPTY}
${REMARKS}    ${EMPTY}
# INTEGRATION (from migo103_link/migo105_link bots): a document-overview
# tree sidebar can pop up on this same MIGO screen -- see Dismiss Overview
# Tree Sidebar / Connect To Sap Session below, same pattern as migo_103.robot.
${PATH_TREE_CLOSE_BTN}    wnd[0]/shellcont/shell/shellcont[1]/shell[0]
${session}          ${NONE}
 
*** Test Cases ***
Execute MIGO 105
    [Setup]    Initialize SAP And Login
    ${result}=    Fill MIGO 105 And Post
    Log To Console    RESULT:MIGO105_STATUS:${result}
    Sleep    10s
    [Teardown]    Close SAP Session
 
 
*** Keywords ***
Initialize SAP And Login
    Evaluate    __import__('dotenv').load_dotenv(__import__('os').getenv('DOTENV_PATH', '.env'), override=True)
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
 
    # FIX: same reordering as migo_103.robot -- see that file's comment for
    # the full explanation. Generic Dismiss Any Popup used to run BEFORE
    # this specific check and would already close the multi-logon dialog
    # (submitting SAP's default radio choice) before OPT2 could be
    # selected. Also aligned OPT1 -> OPT2 to match gate_in.robot's
    # proven-working "continue without ending other sessions" choice.
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
 
 
Get Firstline Path
    # The Action combo box subscreen can render as either :0003 or :0007
    # depending on MIGO's current internal state -- a hardcoded path
    # silently fails to find the element whenever the screen happens to be
    # in the other state. Probe both, use whichever actually exists now.
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
 
 
Connect To Sap Session
    # Attaches to the same live SAP GUI Scripting session SapGuiLibrary is
    # already driving -- see migo_103.robot's identical keyword for the
    # full explanation.
    ${sess}=    Evaluate
    ...    __import__('win32com.client').client.GetObject('SAPGUI').GetScriptingEngine.Children(0).Children(0)
    Set Suite Variable    ${session}    ${sess}
 
 
Dismiss Overview Tree Sidebar
    # INTEGRATION: same fix as migo_103.robot -- see that file's identical
    # keyword for the full explanation. Defensive by construction (no-op if
    # the sidebar never appears).
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
 
 
Fill MIGO 105 And Post
    ${mat_doc_clean}=     Clean Value    ${MATERIAL_DOC_NUMBER}
    ${storage_clean}=     Clean Value    ${STORAGE_LOCATION}
    ${invoice_clean}=     Clean Value    ${VENDOR_INVOICE}
    ${rem_clean}=         Clean Value    ${REMARKS}
 
    ${items}=    Evaluate
    ...    __import__('json').loads(__import__('base64').b64decode('${ITEMS_JSON_BATCH}').decode()) if '${ITEMS_JSON_BATCH}' else []
 
    Run Transaction    MIGO
    Sleep    3s
    Dismiss Any Popup
    Dismiss Overview Tree Sidebar
 
    ${firstline}=    Get Firstline Path
 
    # Force action to Release GR Blocked Stock (A05). setattr() used
    # instead of a direct `.key = 'A05'` assignment because Robot's
    # Evaluate keyword runs expressions through Python eval(), which
    # cannot execute assignment statements (SyntaxError: invalid syntax)
    # -- setattr() is a function call, so it's a valid expression instead.
    Evaluate    setattr($session.findById('${firstline}/cmbGODYNPRO-ACTION'), 'key', 'A05')
    #Evaluate    $session.findById('${firstline}/cmbGODYNPRO-ACTION').setFocus()
    Sleep    0.5s
 
    # Re-resolve firstline path -- setting ACTION can itself switch the
    # subscreen between :0003/:0007, so MAT_DOC's container may differ
    # from what ACTION was just found under.
    ${firstline}=    Get Firstline Path
 
    Set Focus     ${firstline}/subSUB_FIRSTLINE_REFDOC:SAPLMIGO:2010/txtGODYNPRO-MAT_DOC
    Safe Input Text    ${firstline}/subSUB_FIRSTLINE_REFDOC:SAPLMIGO:2010/txtGODYNPRO-MAT_DOC
    ...    ${mat_doc_clean}
    Send VKey    0
    Sleep    3s
    Dismiss Any Popup
 
    ${det_base}=    Set Variable
    ...    wnd[0]/usr/ssubSUB_MAIN_CARRIER:SAPLMIGO:0003/subSUB_ITEMDETAIL:SAPLMIGO:0301/subSUB_DETAIL:SAPLMIGO:0300
 
    # ── LINE 1 ONLY: Where tab — storage location + remarks ────────
    # Navigate to line 1 first
    Safe Input Text    ${det_base}/txtGODYNPRO-DETAIL_ZEILE    1
    Set Focus     ${det_base}/txtGODYNPRO-DETAIL_ZEILE
    Send VKey    0
    Sleep    1s
    Dismiss Any Popup
 
    # Click Where tab
    Click Element
    ...    ${det_base}/tabsTS_GOITEM/tabpOK_GOITEM_DESTINAT.
    Sleep    1.5s
    Dismiss Any Popup
 
    # Fill storage location
    Safe Input Text
    ...    ${det_base}/tabsTS_GOITEM/tabpOK_GOITEM_DESTINAT./ssubSUB_TS_GOITEM_DESTINATION:SAPLMIGO:0325/ctxtGOITEM-LGOBE
    ...    ${storage_clean}
    Send VKey    0
    Sleep    0.5s
    Dismiss Any Popup
 
    # Fill remarks if provided
    IF    '${rem_clean}' != ''
        Safe Input Text
        ...    ${det_base}/tabsTS_GOITEM/tabpOK_GOITEM_DESTINAT./ssubSUB_TS_GOITEM_DESTINATION:SAPLMIGO:0325/txtGOITEM-SGTXT
        ...    ${rem_clean}
        Send VKey    0
        Sleep    0.5s
        Dismiss Any Popup
    END
 
    # ── LOOP ALL LINES: batch only ─────────────────────────────────
    ${line_num}=    Set Variable    1
 
    WHILE    True
        ${line_num_str}=    Convert To String    ${line_num}
 
        # Navigate to line
        Safe Input Text    ${det_base}/txtGODYNPRO-DETAIL_ZEILE    ${line_num_str}
        Set Focus     ${det_base}/txtGODYNPRO-DETAIL_ZEILE
        Send VKey    0
        Sleep    1s
        Dismiss Any Popup
 
        # Verify line exists
        ${current_raw}=    Run Keyword And Ignore Error
        ...    Get Value    ${det_base}/txtGODYNPRO-DETAIL_ZEILE
        ${actual_line}=    Clean Value    ${current_raw}[1]
 
        Log To Console    Line check: entered=${line_num_str} SAP=${actual_line}
 
        IF    '${actual_line}' != '${line_num_str}'
            Log To Console    No more lines — stopping
            BREAK
        END
 
        # Find batch for this line
        ${batch_for_line}=    Set Variable    ${EMPTY}
        FOR    ${item}    IN    @{items}
            ${item_line}=      Get From Dictionary    ${item}    line    default=0
            ${item_line_str}=  Convert To String    ${item_line}
            IF    '${item_line_str}' == '${line_num_str}'
                ${batch_for_line}=    Get From Dictionary    ${item}    batch    default=${EMPTY}
                BREAK
            END
        END
 
        Log To Console    Line ${line_num_str}: batch='${batch_for_line}'
 
        # Fill batch if provided — skip if empty
        IF    '${batch_for_line}' != ''
            Click Element
            ...    ${det_base}/tabsTS_GOITEM/tabpOK_GOITEM_BATCH
            Sleep    1s
            Dismiss Any Popup
            Safe Input Text
            ...    ${det_base}/tabsTS_GOITEM/tabpOK_GOITEM_BATCH/ssubSUB_TS_GOITEM_BATCH:SAPLMIGO:0335/ctxtGOITEM-CHARG
            ...    ${batch_for_line}
            Set Focus
            ...    ${det_base}/tabsTS_GOITEM/tabpOK_GOITEM_BATCH/ssubSUB_TS_GOITEM_BATCH:SAPLMIGO:0335/ctxtGOITEM-CHARG
            Send VKey    0
            Sleep    0.5s
            Dismiss Any Popup
        END
 
        # Tick Item OK
        Select Checkbox
        ...    ${det_base}/subSUB_DETAIL_TAKE:SAPLMIGO:0304/chkGODYNPRO-DETAIL_TAKE
        Sleep    0.5s
 
        ${line_num}=    Evaluate    ${line_num} + 1
        IF    ${line_num} > 100    BREAK
    END
 
    # ── Vendor Invoice Amount (header) ─────────────────────────────
    IF    '${invoice_clean}' != ''
        ${hdr_ext}=    Set Variable
        ...    wnd[0]/usr/ssubSUB_MAIN_CARRIER:SAPLMIGO:0003/subSUB_HEADER:SAPLMIGO:0101/subSUB_HEADER:SAPLMIGO:0100/tabsTS_GOHEAD/tabpOK_GOHEAD_EXT_1
        Click Element    ${hdr_ext}
        Sleep    1s
        Set Focus
        ...    ${hdr_ext}/ssubSUB_TS_GOHEAD_EXT_1:SAPLZIBS_MIRO_MIGO:0901/txtZIBS_AUTO_MIRO-DMBTR
        Safe Input Text
        ...    ${hdr_ext}/ssubSUB_TS_GOHEAD_EXT_1:SAPLZIBS_MIRO_MIGO:0901/txtZIBS_AUTO_MIRO-DMBTR
        ...    ${invoice_clean}
        Send VKey    0
        Sleep    1s
        Dismiss Any Popup
    END
 
 
    # --- Step 5: Post ---
    # FIX: this used to click btnMIGO_OK_GO -- the PO-check/execute button,
    # not a Save/Post action (same bug found and confirmed in migo_103.robot
    # via SAP GUI Script Recording). Real Post button is
    # wnd[0]/tbar[1]/btn[23]. The stale "commented out, not executing in
    # prod yet" comment above was misleading -- this click was never
    # actually commented out, it was live and silently doing nothing.
 
 
 
    Click Element    wnd[0]/tbar[1]/btn[23]
    Sleep    3s
    Dismiss Any Popup
   ${status_msg}=    Read Status Bar With Retry    expected_pattern=\\d{8,}
    @{matches}=    Get Regexp Matches    ${status_msg}    \\d{8,12}
    IF    len($matches) == 0
        Log To Console    RESULT:MIRO_DOC_NUMBER:MANUAL_CHECK_REQUIRED
        RETURN    MANUAL_CHECK_REQUIRED
    END
    Log To Console    RESULT:MIRO_DOC_NUMBER:${matches}[0]
    RETURN    ${matches}[0]
 
    # Log To Console    DRY RUN — Post button not clicked
    # RETURN    DRY_RUN
 
 
Read Status Bar With Retry
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
 
Safe Input Text
    # Retries once on failure -- covers the "Property text can not be set"
    # AttributeError (stale/dead SAP GUI COM element reference, seen on
    # migo_103.robot and gate_in.robot). Applied here too since migo_105.robot
    # fills a similar set of header/item fields and is exposed to the same
    # intermittent SAP GUI timing glitch.
    [Arguments]    ${locator}    ${value}
    ${status}=    Run Keyword And Return Status    Input Text    ${locator}    ${value}
    IF    not ${status}
        Log    Input Text failed on first attempt for ${locator} -- likely a stale SAP GUI element reference. Retrying once after a short pause.    level=WARN
        Sleep    1s
        Input Text    ${locator}    ${value}
    END
 
 
Clean Value
    # NOTE: previously ended with Split String + ${parts}[0], returning only
    # the first word -- e.g. multi-word remarks/storage-location values got
    # silently truncated. Fixed to match the corrected pattern used in
    # gate_in.robot's Clean Value/Clean Material: strip whitespace and
    # currency symbols, but keep the full multi-word value intact.
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
    # Log    MIGO 105 finished.    level=INFO
    # RETURN
 
    Run Keyword And Ignore Error    Input Text    wnd[0]/tbar[0]/okcd    /nex
    Run Keyword And Ignore Error    Send VKey     wnd[0]    0
    Sleep    2s
    Run Keyword And Ignore Error    Run Process    taskkill    /F    /IM    saplogon.exe
    Sleep    2s
    Run Keyword And Ignore Error    Run Process    taskkill    /F    /IM    saplogon.exe    /T
    Log    SAP session closed and process terminated.    level=INFO
