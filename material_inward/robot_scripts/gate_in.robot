

*** Settings ***
Documentation     Gate In SAP Automation — TCODE: zmmtmn
Library           SapGuiLibrary
Library           Process
Library           OperatingSystem
Library           String
Library           DateTime
Library           Collections

*** Variables ***
${SAP_LOGON_PATH}       ${EMPTY}
${SAP_CONNECTION_NAME}  ${EMPTY}

${VENDOR_NAME}          ${EMPTY}
${TRANSPORTER}          ${EMPTY}
${TRUCK_NO}             ${EMPTY}
${DRIVER_NAME}          ${EMPTY}
${LICENSE_NO}           ${EMPTY}
${CONTAINER_NO}         ${EMPTY}
${CATEGORY}             ${EMPTY}
${MATERIAL}             ${EMPTY}
${CHALLAN_NO}           ${EMPTY}
${CHALLAN_QTY}          ${EMPTY}
${BOE_NO}               ${EMPTY}
${PURCHASE_ORDER}       ${EMPTY}
${NUM_PERSONS}          1
${GATE_PASS_NO}         ${EMPTY}
${NOTE}                 ${EMPTY}

*** Test Cases ***
Execute Gate In
    [Setup]    Initialize SAP And Login
    ${gate_in_number}=    Fill Gate In Form And Submit
    Log To Console    RESULT:GATE_IN_NUMBER:${gate_in_number}
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

    # Start Process    ${SAP_LOGON_PATH}
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

Fill Gate In Form And Submit
    # --- Data Cleaning ---
    ${vendor_clean}         Clean Value    ${VENDOR_NAME}
    ${trans_clean}          Clean Value    ${TRANSPORTER}
    ${truck_clean}          Clean Value    ${TRUCK_NO}
    
    ${challan_no_clean}     Clean Numeric    ${CHALLAN_NO}
    ${challan_qty_clean}    Clean Value    ${CHALLAN_QTY}
    ${material_clean}       Clean Material    ${MATERIAL}

    # --- Step 1: Navigate to T-Code ---
    Run Transaction    zmmtmn
    Wait Until Keyword Succeeds    15s    1s    Element Should Be Present    wnd[0]/usr/radA2
    
    Select Radio Button    wnd[0]/usr/radA2
    Click Element          wnd[0]/usr/btn%P002007_1000
    Sleep    2s
    Dismiss Any Popup

    # --- Step 3: Truck Details ---
    Set Focus     wnd[0]/usr/ctxtP_ZVEN
    Input Text    wnd[0]/usr/ctxtP_ZVEN      ${vendor_clean}
    
    Set Focus     wnd[0]/usr/ctxtP_TRANS
    Input Text    wnd[0]/usr/ctxtP_TRANS     ${trans_clean}
    
    Input Text    wnd[0]/usr/ctxtP_TR_NO     ${truck_clean}

    # --- Step 4: Category and Material Handling ---
    Set Focus     wnd[0]/usr/ctxtP_ZCAT
    Input Text    wnd[0]/usr/ctxtP_ZCAT      ${CATEGORY}
    Send VKey     0    
    Sleep    2s
    Dismiss Any Popup

    Set Focus     wnd[0]/usr/txtP_MATNR
    Input Text    wnd[0]/usr/txtP_MATNR      ${material_clean}
    Send VKey     0    
    Sleep    2s
    Dismiss Any Popup
    
    # --- Step 5: Challan and PO Details ---
    Input Text    wnd[0]/usr/txtP_CH_NO      ${challan_no_clean}
    
    Set Focus     wnd[0]/usr/txtP_CHAN
    Input Text    wnd[0]/usr/txtP_CHAN       ${challan_qty_clean}
    
    Send VKey     0
    Sleep    2s
    Dismiss Any Popup

    Input Text    wnd[0]/usr/txtP_BOENO      ${BOE_NO}
    Input Text    wnd[0]/usr/ctxtP_EBELN     ${PURCHASE_ORDER}

    # ✅ --- FINAL FIXED FIELD BLOCK (added safely) ---
    # --- Truck Details ---
    Input Text    wnd[0]/usr/txtP_DRI_N      ${DRIVER_NAME}
    Input Text    wnd[0]/usr/txtP_PER        ${NUM_PERSONS}

    # --- Gate Pass and Note ---
    # Input Text    wnd[0]/usr/txtP_G_PASS     ${GATE_PASS_NO}
    Input Text    wnd[0]/usr/txtP_NOTE1      ${NOTE}

    Input Text    wnd[0]/usr/txtP_DIR_LI      ${LICENSE_NO}
    Input Text    wnd[0]/usr/txtP_ZCON        ${CONTAINER_NO}
    Input Text    wnd[0]/usr/ctxtP_AUFNR      ${GATE_PASS_NO}
    Input Text    wnd[0]/usr/txtP_NOTE1       ${NOTE}

    # --- Step 6: Final Submit ---
    Set Focus        wnd[0]/tbar[1]/btn[8]
    Click Element    wnd[0]/tbar[1]/btn[8]
    
    Sleep    3s
    Dismiss Any Popup

    ${status_msg}=    Get Value    wnd[0]/sbar
    Log To Console    Final Status Message: ${status_msg}
    
    @{matches}=    Get Regexp Matches    ${status_msg}    \\d{6,12}
    IF    len($matches) == 0
        RETURN    MANUAL_CHECK_REQUIRED
    END

    RETURN    ${matches}[0]

Clean Material
    [Arguments]    ${raw_material}
    ${val}=        Convert To String    ${raw_material}
    ${stripped}=   Strip String    ${val}
    @{parts}=      Split String    ${stripped}    ${SPACE}
    RETURN         ${parts}[0]

Clean Value
    [Arguments]    ${raw_value}
    ${val}=        Convert To String    ${raw_value}
    ${cleaned}=    Strip String    ${val}
    @{parts}=      Split String    ${cleaned}    ${SPACE}
    RETURN         ${parts}[0]

Clean Numeric
    [Arguments]    ${raw_value}
    ${val}=        Convert To String    ${raw_value}
    ${cleaned}=    Replace String Using Regexp    ${val}    [^\\d.]    ${EMPTY}
    ${cleaned}=    Strip String    ${cleaned}
    RETURN         ${cleaned}

Dismiss Any Popup
    ${present}=    Run Keyword And Return Status    Element Should Be Present    wnd[1]
    IF    ${present}
        Run Keyword And Ignore Error    Click Element    wnd[1]/tbar[0]/btn[0]
    END

Close SAP Session
    Log    Execution finished. Session kept open.
    RETURN

    Run Keyword And Ignore Error    Input Text    wnd[0]/tbar[0]/okcd    /nex
    Run Keyword And Ignore Error    Send VKey     wnd[0]    0
    Sleep    2s
    Run Keyword And Ignore Error    Run Process    taskkill    /F    /IM    saplogon.exe
    Sleep    2s
    Run Keyword And Ignore Error    Run Process    taskkill    /F    /IM    saplogon.exe    /T
    Log    SAP session closed and process terminated.    level=INFO