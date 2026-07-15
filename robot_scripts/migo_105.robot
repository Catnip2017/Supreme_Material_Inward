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
# ${MATERIAL_DOC_NUMBER}    5000060194
${STORAGE_LOCATION}       ${EMPTY}
${ITEMS_JSON_BATCH}       ${EMPTY}
${VENDOR_INVOICE}         ${EMPTY}
${REMARKS}    ${EMPTY}

*** Test Cases ***
Execute MIGO 105
    [Setup]    Initialize SAP And Login
    ${result}=    Fill MIGO 105 And Post
    Log To Console    RESULT:MIGO105_STATUS:${result}
    Sleep    10s
    [Teardown]    Close SAP Session


*** Keywords ***
Initialize SAP And Login
    Evaluate    __import__('dotenv').load_dotenv()
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

    ${firstline}=    Set Variable
    ...    wnd[0]/usr/ssubSUB_MAIN_CARRIER:SAPLMIGO:0003/subSUB_FIRSTLINE:SAPLMIGO:0011

    Select From List By Label
    ...    ${firstline}/cmbGODYNPRO-ACTION
    ...    Release GR Blocked Stock
    Sleep    0.5s

    Set Focus     ${firstline}/subSUB_FIRSTLINE_REFDOC:SAPLMIGO:2010/txtGODYNPRO-MAT_DOC
    Input Text    ${firstline}/subSUB_FIRSTLINE_REFDOC:SAPLMIGO:2010/txtGODYNPRO-MAT_DOC
    ...    ${mat_doc_clean}
    Send VKey    0
    Sleep    3s
    Dismiss Any Popup

    ${det_base}=    Set Variable
    ...    wnd[0]/usr/ssubSUB_MAIN_CARRIER:SAPLMIGO:0003/subSUB_ITEMDETAIL:SAPLMIGO:0301/subSUB_DETAIL:SAPLMIGO:0300

    # ── LINE 1 ONLY: Where tab — storage location + remarks ────────
    # Navigate to line 1 first
    Input Text    ${det_base}/txtGODYNPRO-DETAIL_ZEILE    1
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
    Input Text
    ...    ${det_base}/tabsTS_GOITEM/tabpOK_GOITEM_DESTINAT./ssubSUB_TS_GOITEM_DESTINATION:SAPLMIGO:0325/ctxtGOITEM-LGOBE
    ...    ${storage_clean}
    Send VKey    0
    Sleep    0.5s
    Dismiss Any Popup

    # Fill remarks if provided
    IF    '${rem_clean}' != ''
        Input Text
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
        Input Text    ${det_base}/txtGODYNPRO-DETAIL_ZEILE    ${line_num_str}
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
            Input Text
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
        Input Text
        ...    ${hdr_ext}/ssubSUB_TS_GOHEAD_EXT_1:SAPLZIBS_MIRO_MIGO:0901/txtZIBS_AUTO_MIRO-DMBTR
        ...    ${invoice_clean}
        Send VKey    0
        Sleep    1s
        Dismiss Any Popup
    END


    # --- Step 5: Post — commented out, not executing in prod yet ---
    Set Focus        wnd[0]/usr/ssubSUB_MAIN_CARRIER:SAPLMIGO:0003/subSUB_FIRSTLINE:SAPLMIGO:0011/subSUB_FIRSTLINE_REFDOC:SAPLMIGO:2010/btnMIGO_OK_GO
    Click Element    wnd[0]/usr/ssubSUB_MAIN_CARRIER:SAPLMIGO:0003/subSUB_FIRSTLINE:SAPLMIGO:0011/subSUB_FIRSTLINE_REFDOC:SAPLMIGO:2010/btnMIGO_OK_GO
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

Clean Value
    [Arguments]    ${raw_value}
    ${val}=        Convert To String    ${raw_value}
    ${cleaned}=    Strip String    ${val}
    ${cleaned}=    Replace String    ${cleaned}    ₹    ${EMPTY}
    ${cleaned}=    Replace String    ${cleaned}    $    ${EMPTY}
    ${cleaned}=    Replace String    ${cleaned}    €    ${EMPTY}
    ${cleaned}=    Replace String    ${cleaned}    £    ${EMPTY}
    ${cleaned}=    Replace String    ${cleaned}    ,    ${EMPTY}
    @{parts}=      Split String      ${cleaned}    ${SPACE}
    RETURN         ${parts}[0]


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
