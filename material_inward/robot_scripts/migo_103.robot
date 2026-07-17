

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

    # --- Parse ITEMS_JSON ---
    ${items_json}=    Evaluate    __import__('base64').b64decode('${ITEMS_JSON_B64}').decode()

    ${items}=    Evaluate    __import__('json').loads('${ITEMS_JSON}'.replace("'", '"'))
    ${total}=    Get Length    ${items}
    Log To Console    Total matched pairs to fill: ${total}

    # --- Step 1: Navigate to MIGO ---
    Run Transaction    MIGO
    Sleep    3s
    Dismiss Any Popup

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
    Input Text    ${firstline}/ctxtGODEFAULT_TV-BWART    103

    # PO number
    Set Focus     ${firstline}/subSUB_FIRSTLINE_REFDOC:SAPLMIGO:2000/ctxtGODYNPRO-PO_NUMBER
    Input Text    ${firstline}/subSUB_FIRSTLINE_REFDOC:SAPLMIGO:2000/ctxtGODYNPRO-PO_NUMBER    ${po_clean}

    Send VKey    0
    Sleep    3s
    Dismiss Any Popup

    # --- Step 3: Header Fields ---
    ${hdr_base}=    Set Variable
    ...    wnd[0]/usr/ssubSUB_MAIN_CARRIER:SAPLMIGO:0003/subSUB_HEADER:SAPLMIGO:0101/subSUB_HEADER:SAPLMIGO:0100/tabsTS_GOHEAD/tabpOK_GOHEAD_GENERAL/ssubSUB_TS_GOHEAD_GENERAL:SAPLMIGO:0110

    Set Focus     ${hdr_base}/ctxtGOHEAD-BLDAT
    Input Text    ${hdr_base}/ctxtGOHEAD-BLDAT    ${DOC_DATE}

    Set Focus     ${hdr_base}/ctxtGOHEAD-BUDAT
    Input Text    ${hdr_base}/ctxtGOHEAD-BUDAT    ${POST_DATE}

    Input Text    ${hdr_base}/txtGOHEAD-LFSNR     ${dn_clean}
    Input Text    ${hdr_base}/txtGOHEAD-FRBNR     ${bol_clean}
    Input Text    ${hdr_base}/txtGOHEAD-XABLN     ${slip_clean}
    Input Text    ${hdr_base}/txtGOHEAD-BKTXT     ${hdr_clean}

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

        Log To Console    Line ${line_num}: qty_actual=${qty_actual} qty_dn=${qty_dn}

        # Navigate to correct line
        Input Text      ${det_base}/txtGODYNPRO-DETAIL_ZEILE    ${line_num}
        Click Element   ${det_base}/btnOK_LOCATE
        Sleep    1s
        Dismiss Any Popup

        # Quantity tab
        Click Element
        ...    ${det_base}/tabsTS_GOITEM/tabpOK_GOITEM_QUANTITIES
        Sleep    1s

        ${qty_base}=    Set Variable
        ...    ${det_base}/tabsTS_GOITEM/tabpOK_GOITEM_QUANTITIES/ssubSUB_TS_GOITEM_QUANTITIES:SAPLMIGO:0315

        Set Focus     ${qty_base}/txtGOITEM-ERFMG
        Input Text    ${qty_base}/txtGOITEM-ERFMG    ${qty_actual}

        Set Focus     ${qty_base}/txtGOITEM-LSMNG
        Input Text    ${qty_base}/txtGOITEM-LSMNG    ${qty_dn}

        Send VKey    0
        Sleep    1s
        Dismiss Any Popup

        # Where tab — fill remarks/text
        Click Element
        ...    ${det_base}/tabsTS_GOITEM/tabpOK_GOITEM_DESTINAT.
        Sleep    1s

        Set Focus
        ...    ${det_base}/tabsTS_GOITEM/tabpOK_GOITEM_DESTINAT./ssubSUB_TS_GOITEM_DESTINATION:SAPLMIGO:0325/txtGOITEM-SGTXT
        Input Text
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
    # Log To Console    DRY RUN — Skipping post button click
    # Log    RESULT:MATERIAL_DOC_NUMBER:DRY_RUN    level=INFO    # ADD THIS

    # RETURN    DRY_RUN

    Set Focus        wnd[0]/usr/ssubSUB_MAIN_CARRIER:SAPLMIGO:0003/subSUB_FIRSTLINE:SAPLMIGO:0011/subSUB_FIRSTLINE_REFDOC:SAPLMIGO:2000/btnMIGO_OK_GO
    Click Element    wnd[0]/usr/ssubSUB_MAIN_CARRIER:SAPLMIGO:0003/subSUB_FIRSTLINE:SAPLMIGO:0011/subSUB_FIRSTLINE_REFDOC:SAPLMIGO:2000/btnMIGO_OK_GO

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