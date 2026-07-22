*** Settings ***
Documentation     STANDALONE TEST — MIGO 103 SAP Automation, GR into Blocked Stock.
...               Run this file directly (no Flask app / queue needed) to test
...               the item-fill logic with hardcoded dummy data and multiple
...               line items. All business data below is hardcoded in
...               *** Variables *** -- edit those values for your test PO
...               before running. SAP login credentials still come from
...               your existing .env file (not hardcoded here, since those
...               are real secrets).
...
...               SAFETY: ${DRY_RUN} defaults to ${TRUE}, meaning this will
...               fill every field and do the diagnostic read-back of what
...               SAP actually captured, but will NOT click Post and will
...               NOT create a real material document. Once you've confirmed
...               the read-back values look correct, set ${DRY_RUN} to
...               ${FALSE} below to also test the real Post click -- only do
...               that against a PO you don't mind actually posting a GR
...               against (ideally a test/UAT PO, not a live production one).
Library           SapGuiLibrary
Library           Process
Library           OperatingSystem
Library           String
Library           DateTime
Library           Collections

*** Variables ***
# --- Toggle: keep as ${TRUE} until you've verified the read-back log lines
# look correct. Only flip to ${FALSE} to test an actual Post click.
${DRY_RUN}          ${TRUE}

# --- Hardcoded dummy header data -- EDIT these for your test PO ---
${PO_NUMBER}        4500001234
${DOC_DATE}         17.07.2026
${POST_DATE}        17.07.2026
${DELIVERY_NOTE}    DN-TEST-001
${BILL_OF_LADING}   BOL-TEST-001
${GR_SLIP_NO}       GRSLIP-TEST-001
${HEADER_TEXT}      Test GR header text
${REMARKS}          Automated test run -- dummy data

# --- Hardcoded dummy line items -- 3 lines, edit material_code/qty to match
# real lines on your test PO. qty_expected = DN qty, qty_actual = actual
# received qty (can differ from expected to test partial-receipt handling).
${ITEMS_JSON}       [{"material_code": "100000123", "qty_expected": "10", "qty_actual": "10"}, {"material_code": "100000456", "qty_expected": "5", "qty_actual": "5"}, {"material_code": "100000789", "qty_expected": "20", "qty_actual": "18"}]

*** Test Cases ***
Execute MIGO 103 Test
    [Setup]    Initialize SAP And Login
    ${mat_doc}=    Fill MIGO 103 And Post
    Log To Console    RESULT:MATERIAL_DOC_NUMBER:${mat_doc}
    Sleep    10s
    [Teardown]    Close SAP Session

*** Keywords ***
Initialize SAP And Login
    Evaluate    __import__('dotenv').load_dotenv(__import__('os').getenv('DOTENV_PATH', '.env'), override=True)
    ${CLIENT}=      Evaluate    __import__('os').getenv('SAP_CLIENT')
    ${USERNAME}=    Evaluate    __import__('os').getenv('SAP_USERNAME')
    ${PASSWORD}=    Evaluate    __import__('os').getenv('SAP_PASSWORD')
    ${CONN_NAME}=   Evaluate    __import__('os').getenv('SAP_CONNECTION_NAME')
    ${LOGON_PATH}=  Evaluate    __import__('os').getenv('SAP_LOGON_PATH')

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

    Sleep    8s
    Dismiss Any Popup

    ${multi}=    Run Keyword And Return Status    Element Should Be Present    wnd[1]
    IF    ${multi}
        Run Keyword And Ignore Error    Select Radio Button    wnd[1]/usr/radMULTI_LOGON_OPT1
        Run Keyword And Ignore Error    Click Element          wnd[1]/tbar[0]/btn[0]
        Sleep    2s
    END

    Maximize Window    0


Fill MIGO 103 And Post
    # --- Data Cleaning ---
    ${po_clean}=      Clean Value    ${PO_NUMBER}
    ${dn_clean}=      Clean Value    ${DELIVERY_NOTE}
    ${bol_clean}=     Clean Value    ${BILL_OF_LADING}
    ${slip_clean}=    Clean Value    ${GR_SLIP_NO}
    ${hdr_clean}=     Clean Value    ${HEADER_TEXT}
    ${rem_clean}=     Clean Value    ${REMARKS}

    # --- Parse hardcoded ITEMS_JSON directly (no base64 round-trip needed
    # for a standalone test -- ITEMS_JSON above is already plain JSON) ---
    ${items}=    Evaluate    __import__('json').loads('''${ITEMS_JSON}''')
    ${total}=    Get Length    ${items}
    Log To Console    Total dummy line items to fill: ${total}

    # --- Step 1: Navigate to MIGO ---
    Run Transaction    MIGO
    Sleep    3s
    Dismiss Any Popup

    # --- Step 2: Force Goods Receipt + Purchase Order, then set movement type and PO ---
    ${firstline}=    Set Variable
    ...    wnd[0]/usr/ssubSUB_MAIN_CARRIER:SAPLMIGO:0003/subSUB_FIRSTLINE:SAPLMIGO:0011

    Select From List By Label
    ...    ${firstline}/cmbGODYNPRO-ACTION
    ...    Goods Receipt
    Sleep    0.5s

    Select From List By Label
    ...    ${firstline}/cmbGODYNPRO-REFDOC
    ...    Purchase Order
    Sleep    0.5s

    Set Focus     ${firstline}/ctxtGODEFAULT_TV-BWART
    Safe Input Text    ${firstline}/ctxtGODEFAULT_TV-BWART    103

    Set Focus     ${firstline}/subSUB_FIRSTLINE_REFDOC:SAPLMIGO:2000/ctxtGODYNPRO-PO_NUMBER
    Safe Input Text    ${firstline}/subSUB_FIRSTLINE_REFDOC:SAPLMIGO:2000/ctxtGODYNPRO-PO_NUMBER    ${po_clean}

    Send VKey    0
    Sleep    3s
    Dismiss Any Popup

    # --- Diagnostic: confirm the PO was actually accepted before we go any
    # further -- read back the header status bar right after loading the PO.
    ${po_load_msg}=    Get Value    wnd[0]/sbar
    Log To Console    After PO load, status bar says: "${po_load_msg}"

    # --- Step 3: Header Fields ---
    ${hdr_base}=    Set Variable
    ...    wnd[0]/usr/ssubSUB_MAIN_CARRIER:SAPLMIGO:0003/subSUB_HEADER:SAPLMIGO:0101/subSUB_HEADER:SAPLMIGO:0100/tabsTS_GOHEAD/tabpOK_GOHEAD_GENERAL/ssubSUB_TS_GOHEAD_GENERAL:SAPLMIGO:0110

    Set Focus     ${hdr_base}/ctxtGOHEAD-BLDAT
    Safe Input Text    ${hdr_base}/ctxtGOHEAD-BLDAT    ${DOC_DATE}

    Set Focus     ${hdr_base}/ctxtGOHEAD-BUDAT
    Safe Input Text    ${hdr_base}/ctxtGOHEAD-BUDAT    ${POST_DATE}

    Safe Input Text    ${hdr_base}/txtGOHEAD-LFSNR     ${dn_clean}
    Safe Input Text    ${hdr_base}/txtGOHEAD-FRBNR     ${bol_clean}
    Safe Input Text    ${hdr_base}/txtGOHEAD-XABLN     ${slip_clean}
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
        ${mat_expected}=  Clean Value    ${item}[material_code]

        Log To Console    Line ${line_num}: material_code(expected)=${mat_expected} qty_actual=${qty_actual} qty_dn=${qty_dn}

        # Navigate to correct line
        Safe Input Text    ${det_base}/txtGODYNPRO-DETAIL_ZEILE    ${line_num}
        Click Element   ${det_base}/btnOK_LOCATE
        Sleep    1s
        Dismiss Any Popup

        # --- Diagnostic: did Material auto-populate from the PO line? Best
        # effort -- field ID for this screen isn't confirmed, so wrapped in
        # Run Keyword And Ignore Error. If this logs "field ID not
        # confirmed", grab the real technical ID from SAP GUI Script
        # Recording and send it over so we can fix this properly.
        ${matnr_status}    ${matnr_check}=    Run Keyword And Ignore Error
        ...    Get Value
        ...    ${det_base}/tabsTS_GOITEM/tabpOK_GOITEM_MATERIAL/ssubSUB_TS_GOITEM_MATERIAL:SAPLMIGO:0301/ctxtGOITEM-MATNR
        IF    '${matnr_status}' == 'PASS'
            Log To Console    Line ${line_num} READBACK: MATNR (material)="${matnr_check}" (expected ${mat_expected})
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

        # --- Diagnostic read-back: confirm SAP actually kept the qty values ---
        ${erfmg_check}=    Get Value    ${qty_base}/txtGOITEM-ERFMG
        ${lsmng_check}=    Get Value    ${qty_base}/txtGOITEM-LSMNG
        Log To Console    Line ${line_num} READBACK: ERFMG(actual qty)="${erfmg_check}" (expected ${qty_actual}) LSMNG(DN qty)="${lsmng_check}" (expected ${qty_dn})

        # Where tab — fill remarks/text
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

    # --- Step 5: Post (skipped entirely if ${DRY_RUN} is ${TRUE}) ---
    IF    ${DRY_RUN}
        Log To Console    DRY_RUN is TRUE -- skipping Post click. No material document will be created. Review the READBACK log lines above, then set \${DRY_RUN} to \${FALSE} once you're confident, to also test a real Post.
        RETURN    DRY_RUN_NO_POST
    END

    # FIX: confirmed via SAP GUI Script Recording -- the real Post button
    # is wnd[0]/tbar[1]/btn[23] (previously this clicked btnMIGO_OK_GO,
    # the PO-check/execute button from Step 2, which never actually saved
    # anything).
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
    # AttributeError (stale/dead SAP GUI COM element reference). Mirrors
    # the same fix applied to production migo_103.robot and gate_in.robot.
    [Arguments]    ${locator}    ${value}
    ${status}=    Run Keyword And Return Status    Input Text    ${locator}    ${value}
    IF    not ${status}
        Log    Input Text failed on first attempt for ${locator} -- likely a stale SAP GUI element reference. Retrying once after a short pause.    level=WARN
        Sleep    1s
        Input Text    ${locator}    ${value}
    END


Clean Value
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


Close SAP Session
    Log    Closing SAP session...
    Run Keyword And Ignore Error    Input Text    wnd[0]/tbar[0]/okcd    /nex
    Run Keyword And Ignore Error    Send VKey     wnd[0]    0
    Sleep    2s
    Run Keyword And Ignore Error    Run Process    taskkill    /F    /IM    saplogon.exe
    Sleep    2s
    Run Keyword And Ignore Error    Run Process    taskkill    /F    /IM    saplogon.exe    /T
    Log    SAP session closed and process terminated.    level=INFO
