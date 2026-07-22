
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
${SUBCATEGORY}          D
${MATERIAL}             ${EMPTY}
${CHALLAN_NO}           ${EMPTY}
${CHALLAN_QTY}          ${EMPTY}
${BOE_NO}               ${EMPTY}
${PURCHASE_ORDER}       ${EMPTY}
${NUM_PERSONS}          ${EMPTY}
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
    # FIX: Use EXECDIR so .env is always found relative to the robot file location,
    # regardless of where the robot is launched from
    ${env_path}=    Join Path    ${EXECDIR}    .env
    Evaluate    __import__('dotenv').load_dotenv(r'''${env_path}''')
 
    ${CLIENT}=      Evaluate    __import__('os').getenv('SAP_CLIENT')
    ${USERNAME}=    Evaluate    __import__('os').getenv('SAP_USERNAME')
    ${PASSWORD}=    Evaluate    __import__('os').getenv('SAP_PASSWORD')
    ${CONN_NAME}=   Evaluate    __import__('os').getenv('SAP_CONNECTION_NAME')
    ${LOGON_PATH}=  Evaluate    __import__('os').getenv('SAP_LOGON_PATH')
 
    # FIX: Fail early with a clear message if any critical variable is missing
    Should Not Be Empty    ${LOGON_PATH}     SAP_LOGON_PATH not found in .env file at: ${env_path}
    Should Not Be Empty    ${CONN_NAME}      SAP_CONNECTION_NAME not found in .env file at: ${env_path}
    Should Not Be Empty    ${CLIENT}         SAP_CLIENT not found in .env file at: ${env_path}
    Should Not Be Empty    ${USERNAME}       SAP_USERNAME not found in .env file at: ${env_path}
    Should Not Be Empty    ${PASSWORD}       SAP_PASSWORD not found in .env file at: ${env_path}
 
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
    # --- Handle possible popup after login ---
   
    Sleep    3s
 
    ${login_popup}=    Run Keyword And Return Status
    ...    Element Should Be Present
    ...    wnd[1]
 
    IF    ${login_popup}
 
     Run Keyword And Ignore Error
     ...    Select Radio Button    wnd[1]/usr/radMULTI_LOGON_OPT2
 
     Run Keyword And Ignore Error
     ...    Click Element    wnd[1]/tbar[0]/btn[0]
 
     Sleep    2s
 
     Log    Login popup handled successfully    level=INFO
    END
 
    Sleep    8s
    Dismiss Any Popup
    Maximize Window    0
 
Fill Gate In Form And Submit
    # --- Data Cleaning ---
    ${vendor_clean}         Clean Value      ${VENDOR_NAME}
    ${trans_clean}          Clean Value      ${TRANSPORTER}
    ${truck_clean}          Clean Value      ${TRUCK_NO}
    ${challan_no_clean}     Clean Numeric    ${CHALLAN_NO}
    ${challan_qty_clean}    Clean Value      ${CHALLAN_QTY}
    ${material_clean}       Clean Material   ${MATERIAL}
 
    # --- Step 1: Navigate to T-Code ---
    Run Transaction    zmmtmn
    Wait Until Keyword Succeeds    15s    1s    Element Should Be Present    wnd[0]/usr/radA2
 
    Select Radio Button    wnd[0]/usr/radA2
    Click Element          wnd[0]/usr/btn%P002007_1000
    Sleep    2s
    Dismiss Any Popup
 
    # --- Step 3: Truck Details ---
    Set Focus          wnd[0]/usr/ctxtP_ZVEN
    Safe Input Text    wnd[0]/usr/ctxtP_ZVEN      ${vendor_clean}

    Set Focus          wnd[0]/usr/ctxtP_TRANS
    Safe Input Text    wnd[0]/usr/ctxtP_TRANS     ${trans_clean}

    Safe Input Text    wnd[0]/usr/ctxtP_TR_NO     ${truck_clean}

    # --- Step 4: Category and Material Handling ---
    Set Focus          wnd[0]/usr/ctxtP_ZCAT
    Safe Input Text    wnd[0]/usr/ctxtP_ZCAT      ${CATEGORY}
    Send VKey     0
    Sleep    2s
    Dismiss Any Popup

    Set Focus          wnd[0]/usr/txtP_MATNR
    Safe Input Text    wnd[0]/usr/txtP_MATNR      ${material_clean}
    Send VKey     0
    Sleep    2s
    Dismiss Any Popup

    # --- Step 5: Challan and PO Details ---
    Safe Input Text    wnd[0]/usr/txtP_CH_NO      ${challan_no_clean}

    Set Focus          wnd[0]/usr/txtP_CHAN
    Safe Input Text    wnd[0]/usr/txtP_CHAN       ${challan_qty_clean}

    Send VKey     0
    Sleep    2s
    Dismiss Any Popup

    Safe Input Text    wnd[0]/usr/txtP_BOENO      ${BOE_NO}
    Safe Input Text    wnd[0]/usr/ctxtP_EBELN     ${PURCHASE_ORDER}

    # --- Driver / Personnel Details ---
    Safe Input Text    wnd[0]/usr/txtP_DRI_N      ${DRIVER_NAME}
    Safe Input Text    wnd[0]/usr/txtP_PER        ${NUM_PERSONS}

    # --- License, Container, Gate Pass, Note ---
    Safe Input Text    wnd[0]/usr/txtP_DIR_LI     ${LICENSE_NO}
    Safe Input Text    wnd[0]/usr/txtP_ZCON       ${CONTAINER_NO}
    Safe Input Text    wnd[0]/usr/ctxtP_AUFNR     ${GATE_PASS_NO}
    Safe Input Text    wnd[0]/usr/txtP_NOTE1      ${NOTE}
 
    # --- Step 6: Final Submit ---
    Set Focus        wnd[0]/tbar[1]/btn[8]
    Click Element    wnd[0]/tbar[1]/btn[8]
 
    Sleep    3s
    # --- Subcategory Popup Handling for Dispatch ---
    IF    '${CATEGORY}' == 'D'
     
       Sleep    2s
 
       ${popup_present}=    Run Keyword And Return Status
       ...    Element Should Be Present
       ...    wnd[1]
 
       IF    ${popup_present}
 
         Safe Input Text
         ...    wnd[1]/usr/ctxtP_SUB1
         ...    ${SUBCATEGORY}
         Send VKey    0
         Click Element    wnd[1]/tbar[0]/btn[8]
 
         Sleep    1s
 
         Log    Subcategory entered: ${SUBCATEGORY}    level=INFO
 
        END
    END
    Dismiss Any Popup
 
    ${status_msg}=    Get Value    wnd[0]/sbar
    Log To Console    Final Status Message: ${status_msg}
    Log    Gate In status bar: "${status_msg}"    level=INFO

 
    @{matches}=    Get Regexp Matches    ${status_msg}    \\d{6,12}
    IF    len($matches) == 0
        Log To Console    RESULT:GATE_IN_NUMBER:MANUAL_CHECK_REQUIRED
        Log To Console    RESULT:GATE_IN_STATUS_MSG:${status_msg}
        RETURN    MANUAL_CHECK_REQUIRED
    END
 
    RETURN    ${matches}[0]
 
Clean Material
    # NOTE: previously split on spaces and returned only the first word
    # (parts[0]), same bug as the old Clean Value -- e.g. "Steel Pipe 25mm"
    # became just "Steel". Material descriptions are legitimately multi-word,
    # so this now only trims leading/trailing whitespace, matching Clean Value.
    [Arguments]    ${raw_material}
    ${val}=        Convert To String    ${raw_material}
    ${cleaned}=    Strip String    ${val}
    RETURN         ${cleaned}
 
Clean Value
    # NOTE: previously split on spaces and returned only the first word
    # (parts[0]), which silently truncated any multi-word value -- e.g.
    # "Shree Datta Services" became just "Shree". Vendor/Transporter/Truck No
    # are legitimately multi-word, so this now only trims leading/trailing
    # whitespace and does not drop anything after the first space.
    [Arguments]    ${raw_value}
    ${val}=        Convert To String    ${raw_value}
    ${cleaned}=    Strip String    ${val}
    RETURN         ${cleaned}
 
Clean Numeric
    [Arguments]    ${raw_value}
    ${val}=        Convert To String    ${raw_value}
    ${cleaned}=    Replace String Using Regexp    ${val}    [^\\d.]    ${EMPTY}
    ${cleaned}=    Strip String    ${cleaned}
    RETURN         ${cleaned}
 
Safe Input Text
    # Retries once on failure -- covers the "Property text can not be set"
    # AttributeError (a stale/dead COM element reference, usually because
    # SAP GUI was still mid-repaint/settling when the reference was grabbed,
    # most often right after Dismiss Any Popup closed a dialog). Seen twice
    # in production on two different fields; this makes every field-fill
    # call in the main form resilient to that same class of transient
    # timing glitch instead of failing the whole ~30s SAP session outright.
    [Arguments]    ${locator}    ${value}
    ${status}=    Run Keyword And Return Status    Input Text    ${locator}    ${value}
    IF    not ${status}
        Log    Input Text failed on first attempt for ${locator} -- likely a stale SAP GUI element reference. Retrying once after a short pause.    level=WARN
        Sleep    1s
        Input Text    ${locator}    ${value}
    END

Dismiss Any Popup
    ${present}=    Run Keyword And Return Status    Element Should Be Present    wnd[1]
    IF    ${present}
        Run Keyword And Ignore Error    Click Element    wnd[1]/tbar[0]/btn[0]
        # Give SAP GUI a moment to finish closing the popup and repaint
        # wnd[0] before the caller grabs a new element reference from it.
        # Without this, an Input Text called immediately after can grab a
        # stale/dead COM reference and fail with "Property text can not be
        # set" -- seen twice now (vendor field, then Material field), both
        # times on the very next Input Text right after this keyword ran.
        Sleep    1s
    END
 
# Close SAP Session
#     Log    Execution finished. Session kept open.
#     RETURN

Close SAP Session
    Log    Closing SAP session...
    Run Keyword And Ignore Error    Input Text    wnd[0]/tbar[0]/okcd    /nex
    Run Keyword And Ignore Error    Send VKey     wnd[0]    0
    Sleep    2s
    Run Keyword And Ignore Error    Run Process    taskkill    /F    /IM    saplogon.exe
    Sleep    2s
    Run Keyword And Ignore Error    Run Process    taskkill    /F    /IM    saplogon.exe    /T
    Log    SAP session closed and process terminated.    level=INFO