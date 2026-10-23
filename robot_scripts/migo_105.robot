*** Settings ***
Documentation     MIGO 105 SAP Automation — Release GR Blocked Stock
...
...               MULTI-BOT COORDINATION (2026-09-03): login/close now go
...               through sap_coordination.resource instead of an
...               unconditional taskkill on the way in and out. Also fixes
...               Connect To Sap Session below -- despite its comment
...               claiming parity with migo_103.robot, it was still using
...               the old Children(0).Children(0) index-based lookup, not
...               the fixed version. See sap_coordination.resource for the
...               full reasoning on both.
Library           SapGuiLibrary
Library           Process
Library           OperatingSystem
Library           String
Library           DateTime
Library           Collections
Library           ${CURDIR}/sap_bot_coordination.py
 
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
 
    # MULTI-BOT FIX (2026-09-03): used to unconditionally taskkill
    # saplogon.exe here every run -- see sap_coordination.resource for why
    # that's no longer safe under concurrency. Coordinated SAP Login also
    # binds ${session} correctly (Bind SAP Session), replacing the old
    # Connect To Sap Session call below.
    ${session}=    Coordinated SAP Login    ${LOGON_PATH}    ${CONN_NAME}    ${CLIENT}    ${USERNAME}    ${PASSWORD}
    Set Suite Variable    ${session}


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
    Ensure Header And Detail Open

 
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

        # FIX (2026-10-07): storage location used to be entered on line 1
        # only (block above the loop), so line 2 onward kept whatever SAP
        # showed. Storage location must be set on EVERY line. Line 1 is
        # already done above and is left exactly as it was; this fills
        # lines 2+. Skipped if no storage location was supplied so an
        # empty value can never blank a line.
        IF    ${line_num} > 1 and '${storage_clean}' != ''
            Click Element
            ...    ${det_base}/tabsTS_GOITEM/tabpOK_GOITEM_DESTINAT.
            Sleep    1.5s
            Dismiss Any Popup
            Safe Input Text
            ...    ${det_base}/tabsTS_GOITEM/tabpOK_GOITEM_DESTINAT./ssubSUB_TS_GOITEM_DESTINATION:SAPLMIGO:0325/ctxtGOITEM-LGOBE
            ...    ${storage_clean}
            Send VKey    0
            Sleep    0.5s
            Dismiss Any Popup
            Log To Console    Line ${line_num_str}: storage location '${storage_clean}' entered
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
 
 
    # --- Step 5: Check, then Post (twice, with logging for monitoring) ---
    # RESTORED (2026-09-03): this Check-before-Post + retry-Post error
    # handling existed in an older local copy and was dropped somewhere
    # along the way when the Post button itself got fixed (see the FIX
    # note below) -- putting it back now at the user's request. Check
    # (btn[7]) is clicked first -- SAP's own validation step, separate
    # from Post. If it raises any popup (warnings/errors), that message is
    # captured and logged before confirming it, using the same Capture
    # And Dismiss Popup Message keyword the Post popups below use (it
    # both reads the message AND clicks confirm internally).
    #
    # FIX (kept from the single-Post version): Post used to click
    # btnMIGO_OK_GO -- the PO-check/execute button, not a Save/Post action
    # (same bug found and confirmed in migo_103.robot via SAP GUI Script
    # Recording). Real Post button is wnd[0]/tbar[1]/btn[23].
    Log To Console    Clicking Check (btn[7])...
    Click Element    wnd[0]/tbar[1]/btn[7]
    Sleep    2s
    ${check_popup_message}=    Capture And Dismiss Popup Message
    Log To Console    CHECK POPUP MESSAGE: "${check_popup_message}"

    # POST -- 1st click. If it posts immediately with no popup, the
    # document is already posted and a 2nd click is NOT needed and is
    # skipped. If a popup DOES appear, its message is captured and logged
    # (Capture And Dismiss Popup Message also confirms it internally),
    # and only then is Post clicked a second time to actually commit the
    # document.
    Log To Console    Clicking Post (1st time)...
    Click Element    wnd[0]/tbar[1]/btn[23]
    Sleep    3s
    ${post_popup_message_1}=    Capture And Dismiss Popup Message
    Log To Console    POST POPUP MESSAGE (1st click): "${post_popup_message_1}"

    ${popup1_present}=    Run Keyword And Return Status
    ...    Should Not Be Empty    ${post_popup_message_1}
    ${post_popup_message_2}=    Set Variable    ${EMPTY}
    IF    ${popup1_present}
        Log To Console    Popup appeared after 1st Post click -- confirmed above, clicking Post again (2nd time)...
        Click Element    wnd[0]/tbar[1]/btn[23]
        Sleep    3s
        ${post_popup_message_2}=    Capture And Dismiss Popup Message
        Log To Console    POST POPUP MESSAGE (2nd click): "${post_popup_message_2}"
    ELSE
        Log To Console    No popup after 1st Post click -- document likely posted already, skipping 2nd Post click.
    END

    ${status_msg}=    Read Status Bar With Retry    expected_pattern=\\d{8,}

    # Prefer whichever popup message actually had content, most recent
    # first, then fall back to the status bar underneath.
    ${popup2_not_empty}=    Run Keyword And Return Status    Should Not Be Empty    ${post_popup_message_2}
    ${popup1_not_empty}=    Run Keyword And Return Status    Should Not Be Empty    ${post_popup_message_1}
    IF    ${popup2_not_empty}
        ${final_message}=    Set Variable    ${post_popup_message_2}
    ELSE IF    ${popup1_not_empty}
        ${final_message}=    Set Variable    ${post_popup_message_1}
    ELSE
        ${final_message}=    Set Variable    ${status_msg}
    END
    Log To Console    RESULT:MIGO105_STATUS_MSG:${final_message}

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


Dump Element Texts
    [Documentation]    Recursively collects any non-empty .Text values from
    ...    the given live SAP GUI element (a COM object reference, not a
    ...    path string) and its children, up to 4 levels deep. Same keyword
    ...    as migo_103.robot -- see that file for the full explanation.
    [Arguments]    ${element}    ${depth}=${0}
    @{collected}=    Create List
    IF    ${depth} > 4
        RETURN    ${collected}
    END

    ${text_res}=    Run Keyword And Ignore Error    Evaluate    $element.Text
    IF    '${text_res}[0]' == 'PASS'
        ${text}=    Clean Value    ${text_res}[1]
        ${text_not_empty}=    Run Keyword And Return Status    Should Not Be Empty    ${text}
        IF    ${text_not_empty}
            Append To List    ${collected}    ${text}
        END
    END

    ${count_res}=    Run Keyword And Ignore Error    Evaluate    $element.Children.Count
    IF    '${count_res}[0]' == 'PASS'
        ${child_count}=    Convert To Integer    ${count_res}[1]
        FOR    ${i}    IN RANGE    ${child_count}
            ${child}=    Evaluate    $element.Children.ElementAt(${i})
            @{child_texts}=    Dump Element Texts    ${child}    ${depth + 1}
            FOR    ${t}    IN    @{child_texts}
                Append To List    ${collected}    ${t}
            END
        END
    END

    RETURN    ${collected}


Capture And Dismiss Popup Message
    [Documentation]    ADDED -- same keyword as migo_103.robot (see that
    ...    file for the full explanation). If a popup (wnd[1]) is present,
    ...    reads its message text before dismissing it. Tries the standard
    ...    single/multi-line popup fields (txtSPOP-TEXT_LINE1..4) first,
    ...    falls back to a table-dump for SAP's "Display logs"-style popups.
    ${popup_present}=    Run Keyword And Return Status    Element Should Be Present    wnd[1]
    ${message}=    Set Variable    ${EMPTY}
    IF    ${popup_present}
        FOR    ${line}    IN    1    2    3    4
            ${res}=    Run Keyword And Ignore Error
            ...    Evaluate    $session.findById('wnd[1]/usr/txtSPOP-TEXT_LINE${line}').Text
            IF    '${res}[0]' == 'PASS'
                ${line_text}=    Clean Value    ${res}[1]
                ${line_not_empty}=    Run Keyword And Return Status    Should Not Be Empty    ${line_text}
                IF    ${line_not_empty}
                    ${message}=    Catenate    SEPARATOR=${SPACE}    ${message}    ${line_text}
                    ${message}=    Strip String    ${message}
                END
            END
        END

        ${message_is_empty}=    Run Keyword And Return Status    Should Be Empty    ${message}
        IF    ${message_is_empty}
            ${popup_root}=    Run Keyword And Ignore Error
            ...    Evaluate    $session.findById('wnd[1]')
            IF    '${popup_root}[0]' == 'PASS'
                @{all_texts}=    Dump Element Texts    ${popup_root}[1]
                Log To Console    Popup table-dump candidates: @{all_texts}
                @{meaningful}=    Create List
                FOR    ${t}    IN    @{all_texts}
                    ${tlen}=    Get Length    ${t}
                    IF    ${tlen} >= 12
                        Append To List    ${meaningful}    ${t}
                    END
                END
                # CHANGED (2026-10-22): keep everything SAP shows in the popup (title, headings, messages) but drop
                # lines that repeat (e.g. "Enter Location" appearing several times).
                ${message}=    Evaluate    ' | '.join(dict.fromkeys($meaningful))
            END
        END

        Log To Console    Popup message captured: "${message}"
        Run Keyword And Ignore Error    Click Element    wnd[1]/tbar[0]/btn[0]
        Sleep    1s
    END
    RETURN    ${message}


Close SAP Session
    Coordinated Close SAP Session    ${session}
