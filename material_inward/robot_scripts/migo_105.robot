*** Settings ***
Documentation     MIGO 105 SAP Automation — Release GR Blocked Stock
Library           SapGuiLibrary
Library           Process
Library           OperatingSystem
Library           String
Library           DateTime
Library           Collections

*** Variables ***
# ${MATERIAL_DOC_NUMBER}    ${EMPTY}
${MATERIAL_DOC_NUMBER}    5000060194
${STORAGE_LOCATION}       ${EMPTY}
${BATCH}                  ${EMPTY}
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
    # --- Clean inputs ---
    ${mat_doc_clean}=     Clean Value    ${MATERIAL_DOC_NUMBER}
    ${storage_clean}=     Clean Value    ${STORAGE_LOCATION}
    ${invoice_clean}=     Clean Value    ${VENDOR_INVOICE}
    ${batch_clean}=       Clean Value    ${BATCH}
    # ${rem_clean}=         Clean Value    ${REMARKS}    # ADD HERE

    # # --- Parse items ---
    # ${items}=    Evaluate    __import__('json').loads('${ITEMS_JSON}'.replace("'", '"'))
    # ${total}=    Get Length    ${items}
    # Log To Console    Total line items: ${total}

    # --- Step 1: Navigate to MIGO ---
    Run Transaction    MIGO
    Sleep    3s
    Dismiss Any Popup

    # # --- Step 2: Enter mat doc number ---
    # # A05 + Material Document are auto-set by SAP when opening /omigo
    # ${firstline}=    Set Variable
    # ...    wnd[0]/usr/ssubSUB_MAIN_CARRIER:SAPLMIGO:0003/subSUB_FIRSTLINE:SAPLMIGO:0011

    # Set Focus     ${firstline}/subSUB_FIRSTLINE_REFDOC:SAPLMIGO:2010/txtGODYNPRO-MAT_DOC
    # Input Text    ${firstline}/subSUB_FIRSTLINE_REFDOC:SAPLMIGO:2010/txtGODYNPRO-MAT_DOC
    # ...    ${mat_doc_clean}
    # # ...    5000060194

    # Send VKey    0
    # Sleep    3s
    # Dismiss Any Popup

    # --- Step 2: Force A05 + enter mat doc ---
    ${firstline}=    Set Variable
    ...    wnd[0]/usr/ssubSUB_MAIN_CARRIER:SAPLMIGO:0003/subSUB_FIRSTLINE:SAPLMIGO:0011

    # Force Release GR Blocked Stock (A05) — don't rely on SAP memory
    Select From List By Label
    ...    ${firstline}/cmbGODYNPRO-ACTION
    ...    Release GR Blocked Stock
    Sleep    0.5s

    Set Focus     ${firstline}/subSUB_FIRSTLINE_REFDOC:SAPLMIGO:2010/txtGODYNPRO-MAT_DOC
    Input Text    ${firstline}/subSUB_FIRSTLINE_REFDOC:SAPLMIGO:2010/txtGODYNPRO-MAT_DOC
    # ...    ${mat_doc_clean}
    ...    5000060194


    Send VKey    0
    Sleep    3s
    Dismiss Any Popup

    # --- Step 3: Loop lines dynamically ---
    ${det_base}=    Set Variable
    ...    wnd[0]/usr/ssubSUB_MAIN_CARRIER:SAPLMIGO:0003/subSUB_ITEMDETAIL:SAPLMIGO:0301/subSUB_DETAIL:SAPLMIGO:0300

    ${line_num}=    Set Variable    1

    WHILE    True
        # Navigate to line
        Input Text      ${det_base}/txtGODYNPRO-DETAIL_ZEILE    ${line_num}
        Click Element   ${det_base}/btnOK_LOCATE
        Sleep    1s
        Dismiss Any Popup

        # Check if line loaded — if Where tab not present, no more lines
        ${current_line}=    Run Keyword And Ignore Error
        ...    Get Value    ${det_base}/txtGODYNPRO-DETAIL_ZEILE

        ${actual_line}=    Clean Value    ${current_line}[1]

        # If SAP didn't move to requested line — we're past the last line
        IF    '${actual_line}' != '${line_num}'    BREAK

        # Where tab — fill storage location
        Click Element    ${det_base}/tabsTS_GOITEM/tabpOK_GOITEM_DESTINAT.
        Sleep    1s

        Input Text
        ...    ${det_base}/tabsTS_GOITEM/tabpOK_GOITEM_DESTINAT./ssubSUB_TS_GOITEM_DESTINATION:SAPLMIGO:0325/ctxtGOITEM-LGOBE
        ...    ${storage_clean}

        Send VKey    0
        Sleep    1s
        Dismiss Any Popup

    # --- Step 5: Remarks ---
      
        # Batch tab — only if batch provided
        IF    '${batch_clean}' != ''
            Click Element    ${det_base}/tabsTS_GOITEM/tabpOK_GOITEM_BATCH
            Sleep    1s
            Input Text
            ...    ${det_base}/tabsTS_GOITEM/tabpOK_GOITEM_BATCH/ssubSUB_TS_GOITEM_BATCH:SAPLMIGO:0335/ctxtGOITEM-CHARG
            ...    ${batch_clean}
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


    # --- Step 4: Vendor Invoice Amount ---
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

    # # --- Step 5: Remarks ---
    # IF    '${rem_clean}' != ''
    #     ${hdr_base}=    Set Variable
    #     ...    wnd[0]/usr/ssubSUB_MAIN_CARRIER:SAPLMIGO:0003/subSUB_HEADER:SAPLMIGO:0101/subSUB_HEADER:SAPLMIGO:0100/tabsTS_GOHEAD/tabpOK_GOHEAD_GENERAL/ssubSUB_TS_GOHEAD_GENERAL:SAPLMIGO:0110
    #     Click Element    ${hdr_base}
    #     Sleep    1s
    #     Input Text    ${hdr_base}/txtGOHEAD-BKTXT    ${rem_clean}
    #     Send VKey    0
    #     Sleep    1s
    #     Dismiss Any Popup
    # END

    # --- Step 5: Post — commented out, not executing in prod yet ---
    # Set Focus        wnd[0]/usr/ssubSUB_MAIN_CARRIER:SAPLMIGO:0003/subSUB_FIRSTLINE:SAPLMIGO:0011/subSUB_FIRSTLINE_REFDOC:SAPLMIGO:2010/btnMIGO_OK_GO
    # Click Element    wnd[0]/usr/ssubSUB_MAIN_CARRIER:SAPLMIGO:0003/subSUB_FIRSTLINE:SAPLMIGO:0011/subSUB_FIRSTLINE_REFDOC:SAPLMIGO:2010/btnMIGO_OK_GO
    # Sleep    3s
    # Dismiss Any Popup
    # ${status_msg}=    Read Status Bar With Retry    expected_pattern=\\d{8,}
    # Log To Console    Final Status Message: ${status_msg}
    # @{matches}=    Get Regexp Matches    ${status_msg}    \\d{8,12}
    # IF    len($matches) == 0
    #     RETURN    MANUAL_CHECK_REQUIRED
    # END
    # RETURN    ${matches}[0]

    Log To Console    DRY RUN — Post button not clicked
    RETURN    DRY_RUN


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
    Log    MIGO 105 finished.    level=INFO
    RETURN

    Run Keyword And Ignore Error    Input Text    wnd[0]/tbar[0]/okcd    /nex
    Run Keyword And Ignore Error    Send VKey     wnd[0]    0
    Sleep    2s
    Run Keyword And Ignore Error    Run Process    taskkill    /F    /IM    saplogon.exe
    Sleep    2s
    Run Keyword And Ignore Error    Run Process    taskkill    /F    /IM    saplogon.exe    /T
    Log    SAP session closed and process terminated.    level=INFO
