*** Settings ***
Documentation     PO List Fetch — SAP ME2N Open POs by Vendor Name
...               Enters vendor name with wildcard, executes search,
...               applies "not equal to 0" filter on open qty column,
...               reads PO numbers from results grid.
...               Outputs: RESULT:PO_LIST:<json_array>
Library           SapGuiLibrary
Library           Process
Library           OperatingSystem
Library           String
Library           Collections

*** Variables ***
${VENDOR_NAME}    ${EMPTY}

${ME2N_VENDOR_FIELD}    wnd[0]/usr/txtP_NAME1
${RESULT_TABLE}         wnd[0]/usr/cntlGRID1/shellcont/shell/shellcont[1]/shell/shellcont[1]/shell
${COL_PO_NUMBER}        EBELN
${COL_OPEN_QTY}         WTLIEF
 

*** Test Cases ***
Execute PO List Fetch
    [Setup]    Initialize SAP And Login
    ${po_list}=    FetchOpenPOs
    Log To Console    RESULT:PO_LIST:${po_list}
    ${po_str}=    Evaluate    str($po_list)
    Log To Console    RESULT:PO_LIST:${po_str}
    Sleep    2s
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
    Sleep    8s

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


FetchOpenPOs
    ${clean_name}=    Sanitize Vendor Name    ${VENDOR_NAME}
    Log To Console    Searching ME2N for vendor: *${clean_name}*

    Log To Console    SAP LOGIN DONE — starting ME2N
    Run Transaction    ME2N
    Sleep    3s
    Dismiss Any Popup

    Input Text    ${ME2N_VENDOR_FIELD}    *${clean_name}*
    Sleep    0.5s

    Click Element    wnd[0]/tbar[1]/btn[8]
    Sleep    4s
    Dismiss Any Popup

    # Apply filter: exclude rows where WTLIEF = 0
    Apply Open Qty Filter
    Dismiss Any Popup

    # Now just read all PO numbers — SAP has already filtered
    ${total_rows}=    Get Row Count    ${RESULT_TABLE}
    Log To Console    Total rows after filter: ${total_rows}

    @{po_list}=    Create List

    FOR    ${row_idx}    IN RANGE    0    ${total_rows}
        ${po_res}=    Run Keyword And Ignore Error
        ...    Get Cell Value    ${RESULT_TABLE}    ${row_idx}    ${COL_PO_NUMBER}

        IF    '${po_res}[0]' == 'FAIL'    BREAK

        ${po_number}=    Clean SAP Value    ${po_res}[1]
        IF    '${po_number}' == ''    CONTINUE

        ${item_json}=    Set Variable    {"po_number":"${po_number}"}
        Append To List    ${po_list}    ${item_json}
    END

    ${count}=    Get Length    ${po_list}
    Log To Console    Found ${count} open PO(s) for: *${clean_name}*

    IF    ${count} == 0
        RETURN    []
    END

    ${joined}=    Evaluate    ",".join($po_list)
    RETURN    [${joined}]

Apply Open Qty Filter
    # Select the WTLIEF column via Send Vkey to set focus, then use toolbar filter button
    # Mirror VB: selectColumn WTLIEF, then tbar[1]/btn[29]
    Select Table Column    ${RESULT_TABLE}    ${COL_OPEN_QTY}
    Sleep    0.5s

    # Toolbar filter button (btn[29]) — not context menu
    Click Element    wnd[0]/tbar[1]/btn[29]
    Sleep    1s

    # Open value selection for WTLIEF field
    Click Element    wnd[1]/usr/ssub%_SUBSCREEN_FREESEL:SAPLSSEL:1105/btn%_%%DYN002_%_APP_%-VALU_PUSH
    Sleep    1s

    # Switch to exclusion/not-equal tab
    Click Element    wnd[2]/usr/tabsTAB_STRIP/tabpNOINT
    Sleep    0.5s

    # Enter 0 in both low and high fields
    Input Text    wnd[2]/usr/tabsTAB_STRIP/tabpNOINT/ssubSCREEN_HEADER:SAPLALDB:3040/tblSAPLALDBINTERVAL_E/txtRSCSEL_255-ILOW_E[1,0]    0
    Input Text    wnd[2]/usr/tabsTAB_STRIP/tabpNOINT/ssubSCREEN_HEADER:SAPLALDB:3040/tblSAPLALDBINTERVAL_E/txtRSCSEL_255-IHIGH_E[2,0]    0

    # Execute (F8 on wnd[2]) then confirm (Enter on wnd[1])
    Click Element    wnd[2]/tbar[0]/btn[8]
    Sleep    0.5s
    Click Element    wnd[1]/tbar[0]/btn[0]
    Sleep    1.5s

Sanitize Vendor Name
    [Arguments]    ${raw_name}
    ${clean}=    Strip String    ${raw_name}
    ${clean}=    Replace String    ${clean}    *    ${EMPTY}
    ${clean}=    Replace String    ${clean}    ?    ${EMPTY}
    ${clean}=    Replace String    ${clean}    /    ${SPACE}
    ${clean}=    Replace String Using Regexp    ${clean}    ${SPACE}+    ${SPACE}
    ${clean}=    Strip String    ${clean}
    RETURN    ${clean}


Clean SAP Value
    [Arguments]    ${raw}
    ${val}=    Convert To String    ${raw}
    ${val}=    Strip String    ${val}
    ${val}=    Replace String    ${val}    "    '
    ${val}=    Replace String    ${val}    \\    /
    # Use Python evaluation to avoid RF variable substitution bug
    ${is_none}=    Evaluate    str('${val}') in ('None', 'null', '')
    IF    ${is_none}
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
    Log    PO List Fetch finished.    level=INFO
    Run Keyword And Ignore Error    Input Text    wnd[0]/tbar[0]/okcd    /nex
    Run Keyword And Ignore Error    Send VKey     wnd[0]    0
    Sleep    2s
    Run Keyword And Ignore Error    Run Process    taskkill    /F    /IM    saplogon.exe
    Sleep    2s
    Run Keyword And Ignore Error    Run Process    taskkill    /F    /IM    saplogon.exe    /T
    Log    SAP session closed and process terminated.    level=INFO