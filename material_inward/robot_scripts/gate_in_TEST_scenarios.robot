*** Settings ***
Documentation     STANDALONE TEST SCRIPT — Gate In SAP Automation (TCODE: zmmtmn)
...               Mirrors the exact field logic used by gate_in.html + rf_runner.py + gate_in.robot,
...               so each Test Case below reproduces a real scenario the live app can produce.
...
...               This file is self-contained: SAP password and connection are HARDCODED below
...               (SAP Quality System, per request) — it does NOT read .env, and it does NOT
...               touch or import the production gate_in.robot. Safe to run standalone against QA.
...
...               Run a single scenario:
...                 robot --test "Hand Delivery - Truck Fields Empty" gate_in_TEST_scenarios.robot
...               Run all scenarios:
...                 robot gate_in_TEST_scenarios.robot
Library           SapGuiLibrary
Library           Process
Library           OperatingSystem
Library           String
Library           DateTime
Library           Collections

*** Variables ***
# ── HARDCODED SAP LOGIN (SAP Quality System / QA) ─────────────────────────────
# Replace SAP_LOGON_PATH / SAP_CLIENT / SAP_USERNAME below with your real QA values
# if they differ — only PASSWORD and CONNECTION NAME were specified, so those two
# are hardcoded exactly as requested; the rest are placeholders you may need to edit.
${SAP_LOGON_PATH}       C:\Program Files (x86)\SAP\FrontEnd\SAPgui\saplogon.exe
${SAP_CONNECTION_NAME}  SAP Quality Systems
${SAP_CLIENT}           100
${SAP_USERNAME}         TESTUSER
${SAP_PASSWORD}         India@2026

*** Test Cases ***

Truck Delivery - All Fields Filled
    [Documentation]    Normal truck delivery, every field the app can send is populated.
    ...                Matches gate_in.html when delivery == 'truck' and the user filled
    ...                every optional field too (BOE No, PO, Gate Pass No, Note).
    [Setup]    Initialize SAP And Login
    ${gate_in_number}=    Fill Gate In Form And Submit
    ...    vendor_name=DUMMYVENDOR01
    ...    transporter=DUMMYTRANS
    ...    truck_no=HR55AB1234
    ...    driver_name=RAMESH KUMAR
    ...    license_no=HR061234567890123
    ...    container_no=CONT123456
    ...    category=D
    ...    material=RM12345
    ...    challan_no=987654
    ...    challan_qty=100
    ...    boe_no=BOE0001
    ...    purchase_order=4500001234
    ...    num_persons=1
    ...    gate_pass_no=GP0001
    ...    note=Test note - truck full scenario
    Log To Console    RESULT [Truck Full]: ${gate_in_number}
    [Teardown]    Close SAP Session

Hand Delivery - Truck Fields Empty
    [Documentation]    Exactly what the live app sends today for delivery == 'hand':
    ...                truckNo, licenseNo, containerNo are forced to '' by gate_in.html
    ...                (see lines 587/589/590) regardless of what's typed in those
    ...                now-hidden fields. Everything else is populated normally.
    ...                THIS is the scenario to watch for a SAP mandatory-field error
    ...                on ctxtP_TR_NO / txtP_DIR_LI / txtP_ZCON.
    [Setup]    Initialize SAP And Login
    ${gate_in_number}=    Fill Gate In Form And Submit
    ...    vendor_name=DUMMYVENDOR01
    ...    transporter=DUMMYTRANS
    ...    truck_no=${EMPTY}
    ...    driver_name=RAMESH KUMAR
    ...    license_no=${EMPTY}
    ...    container_no=${EMPTY}
    ...    category=D
    ...    material=RM12345
    ...    challan_no=987654
    ...    challan_qty=100
    ...    boe_no=BOE0001
    ...    purchase_order=4500001234
    ...    num_persons=1
    ...    gate_pass_no=GP0001
    ...    note=Test note - hand delivery scenario
    Log To Console    RESULT [Hand Delivery]: ${gate_in_number}
    [Teardown]    Close SAP Session

Hand Delivery - Optional Fields Also Empty
    [Documentation]    Hand delivery PLUS the optional/non-mandatory UI fields left blank
    ...                (BOE No, PO, Gate Pass No, Note) — the most "empty" real combination
    ...                the current app UI can actually produce. Required fields (vendor,
    ...                transporter, driver, category, material, challan no/qty) still filled,
    ...                since the app's own client-side validation blocks submission otherwise.
    [Setup]    Initialize SAP And Login
    ${gate_in_number}=    Fill Gate In Form And Submit
    ...    vendor_name=DUMMYVENDOR01
    ...    transporter=DUMMYTRANS
    ...    truck_no=${EMPTY}
    ...    driver_name=RAMESH KUMAR
    ...    license_no=${EMPTY}
    ...    container_no=${EMPTY}
    ...    category=D
    ...    material=RM12345
    ...    challan_no=987654
    ...    challan_qty=100
    ...    boe_no=${EMPTY}
    ...    purchase_order=${EMPTY}
    ...    num_persons=${EMPTY}
    ...    gate_pass_no=${EMPTY}
    ...    note=${EMPTY}
    Log To Console    RESULT [Hand + Optional Empty]: ${gate_in_number}
    [Teardown]    Close SAP Session

*** Keywords ***
Initialize SAP And Login
    Run Keyword And Ignore Error    Run Process    taskkill    /F    /IM    saplogon.exe    /T
    Sleep    2s

    Start Process    ${SAP_LOGON_PATH}
    Sleep    5s

    Connect To Session
    Open Connection    ${SAP_CONNECTION_NAME}

    Input Text        wnd[0]/usr/txtRSYST-MANDT    ${SAP_CLIENT}
    Input Text        wnd[0]/usr/txtRSYST-BNAME    ${SAP_USERNAME}
    Input Password    wnd[0]/usr/pwdRSYST-BCODE    ${SAP_PASSWORD}
    Click Element     wnd[0]/tbar[0]/btn[0]
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
    [Arguments]
    ...    ${vendor_name}
    ...    ${transporter}
    ...    ${truck_no}
    ...    ${driver_name}
    ...    ${license_no}
    ...    ${container_no}
    ...    ${category}
    ...    ${material}
    ...    ${challan_no}
    ...    ${challan_qty}
    ...    ${boe_no}
    ...    ${purchase_order}
    ...    ${num_persons}
    ...    ${gate_pass_no}
    ...    ${note}
    ...    ${subcategory}=D

    # --- Data Cleaning (identical logic to production gate_in.robot) ---
    ${vendor_clean}         Clean Value      ${vendor_name}
    ${trans_clean}          Clean Value      ${transporter}
    ${truck_clean}          Clean Value      ${truck_no}
    ${challan_no_clean}     Clean Numeric    ${challan_no}
    ${challan_qty_clean}    Clean Value      ${challan_qty}
    ${material_clean}       Clean Material   ${material}

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
    Input Text    wnd[0]/usr/ctxtP_ZCAT      ${category}
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

    Input Text    wnd[0]/usr/txtP_BOENO      ${boe_no}
    Input Text    wnd[0]/usr/ctxtP_EBELN     ${purchase_order}

    # --- Driver / Personnel Details ---
    Input Text    wnd[0]/usr/txtP_DRI_N      ${driver_name}
    Input Text    wnd[0]/usr/txtP_PER        ${num_persons}

    # --- License, Container, Gate Pass, Note ---
    Input Text    wnd[0]/usr/txtP_DIR_LI     ${license_no}
    Input Text    wnd[0]/usr/txtP_ZCON       ${container_no}
    Input Text    wnd[0]/usr/ctxtP_AUFNR     ${gate_pass_no}
    Input Text    wnd[0]/usr/txtP_NOTE1      ${note}

    # --- Step 6: Final Submit ---
    Set Focus        wnd[0]/tbar[1]/btn[8]
    Click Element    wnd[0]/tbar[1]/btn[8]

    Sleep    3s
    # --- Subcategory Popup Handling for Dispatch ---
    IF    '${category}' == 'D'
        Sleep    2s
        ${popup_present}=    Run Keyword And Return Status
        ...    Element Should Be Present
        ...    wnd[1]

        IF    ${popup_present}
            Input Text
            ...    wnd[1]/usr/ctxtP_SUB1
            ...    ${subcategory}
            Send VKey    0
            Click Element    wnd[1]/tbar[0]/btn[8]
            Sleep    1s
            Log    Subcategory entered: ${subcategory}    level=INFO
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
    Log    Closing SAP session...
    Run Keyword And Ignore Error    Input Text    wnd[0]/tbar[0]/okcd    /nex
    Run Keyword And Ignore Error    Send VKey     wnd[0]    0
    Sleep    2s
    Run Keyword And Ignore Error    Run Process    taskkill    /F    /IM    saplogon.exe
    Sleep    2s
    Run Keyword And Ignore Error    Run Process    taskkill    /F    /IM    saplogon.exe    /T
    Log    SAP session closed and process terminated.    level=INFO
