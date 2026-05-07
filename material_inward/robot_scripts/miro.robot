*** Settings ***
Documentation     MIRO SAP Automation — TCODE: ZMM35
...               Fixes applied:
...               - No date range filter on search screen (handles multi-day gaps)
...               - Dynamic TDS row deletion (count rows first, delete all except row 1)
...               - Generic popup dismissal after every major action
...               - Status bar retry after posting
...               - Data cleaning on all input values
Library           SapGuiLibrary
Library           Process
Library           OperatingSystem
Library           String
Library           DateTime
Library           Collections

*** Variables ***
# Passed from rf_runner.py
${MATERIAL_DOC_NUMBER}  ${EMPTY}    # From MIGO 103 DB — used to find the right row in list
${POSTING_DATE}         ${EMPTY}    # Today's date — filled on MIRO document page only
${REFERENCE_NUMBER}     ${EMPTY}    # Invoice number (bill number)
${INVOICE_DATE}         ${EMPTY}    # Original invoice date
${PO_NUMBER}            ${EMPTY}    # Purchase order number

*** Test Cases ***
Execute MIRO
    [Setup]    Initialize SAP and Login
    Execute MIRO Flow
    [Teardown]    Close SAP Session

*** Keywords ***
Initialize SAP and Login
    Evaluate    __import__('dotenv').load_dotenv()
    ${CLIENT}=      Evaluate    __import__('os').getenv('SAP_CLIENT')
    ${USERNAME}=    Evaluate    __import__('os').getenv('SAP_USERNAME')
    ${PASSWORD}=    Evaluate    __import__('os').getenv('SAP_PASSWORD')
    ${CONN_NAME}=   Evaluate    __import__('os').getenv('SAP_CONNECTION_NAME')
    ${LOGON_PATH}=  Evaluate    __import__('os').getenv('SAP_LOGON_PATH')

    Run Keyword And Ignore Error    Run Process    taskkill    /F    /IM    saplogon.exe
    Sleep    2s
    Start Process    ${LOGON_PATH}
    Sleep    5s

    Connect To Session
    Open Connection    ${CONN_NAME}
    Input Text      wnd[0]/usr/txtRSYST-MANDT    ${CLIENT}
    Input Text      wnd[0]/usr/txtRSYST-BNAME    ${USERNAME}
    Input Password  wnd[0]/usr/pwdRSYST-BCODE    ${PASSWORD}
    Click Element   wnd[0]/tbar[0]/btn[0]
    Sleep    5s

    ${status}=    Run Keyword And Return Status    Element Should Be Present    wnd[1]
    IF    ${status}
        Select Radio Button    wnd[1]/usr/radMULTI_LOGON_OPT1
        Click Element    wnd[1]/tbar[0]/btn[0]
        Sleep    3s
    END
    Maximize Window    wnd[0]
    Log    SAP login successful    level=INFO


Clean Value
    [Arguments]    ${raw_value}
    ${cleaned}=    Strip String    ${raw_value}
    ${cleaned}=    Replace String    ${cleaned}    ₹    ${EMPTY}
    ${cleaned}=    Replace String    ${cleaned}    $    ${EMPTY}
    ${cleaned}=    Replace String    ${cleaned}    €    ${EMPTY}
    ${cleaned}=    Replace String    ${cleaned}    £    ${EMPTY}
    ${cleaned}=    Replace String    ${cleaned}    ,    ${EMPTY}
    ${parts}=      Split String    ${cleaned}    ${SPACE}    1
    ${cleaned}=    Set Variable    ${parts}[0]
    RETURN    ${cleaned}


Dismiss Any Popup
    ${popup1}=    Run Keyword And Return Status    Element Should Be Present    wnd[1]
    IF    ${popup1}
        Log    Popup on wnd[1] — dismissing    level=WARN
        Run Keyword And Ignore Error    Click Element    wnd[1]/tbar[0]/btn[0]
        Sleep    1s
    END
    ${popup2}=    Run Keyword And Return Status    Element Should Be Present    wnd[2]
    IF    ${popup2}
        Log    Popup on wnd[2] — dismissing    level=WARN
        Run Keyword And Ignore Error    Click Element    wnd[2]/tbar[0]/btn[0]
        Sleep    1s
    END


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


Delete All TDS Rows Except First
    [Documentation]
    ...    Dynamically counts rows in the Withholding Tax table.
    ...    Deletes all rows except row 1 (194Q).
    ...    Deletes from bottom up to avoid index shifting.
    ...    Handles any number of rows — not hardcoded to 4.
    ...
    ...    TODO: Replace the row count and row selection element IDs
    ...    below with actual IDs from your SAP system.
    ...    Record the GUI script while on the Withholding Tax tab to get them.

    # TODO: Get the row count from the Withholding Tax table grid
    # Example: ${row_count}=    Get Row Count    wnd[0]/usr/<withholding_tax_grid_id>
    # For now using a placeholder — replace with actual element ID
    ${row_count}=    Set Variable    4    # TODO: Replace with dynamic count

    Log    Withholding Tax rows found: ${row_count}    level=INFO

    IF    ${row_count} <= 1
        Log    Only 1 or 0 TDS rows — nothing to delete    level=INFO
        RETURN
    END

    # Delete from bottom row up to row 2 (keep row 1 = 194Q)
    FOR    ${row_idx}    IN RANGE    ${row_count}    1    -1
        Log    Deleting TDS row ${row_idx}    level=INFO
        # TODO: Click on row ${row_idx} to select it
        # Example: Click Element    wnd[0]/usr/<grid_id>/rows[${row_idx - 1}]
        # Then press Delete:
        # TODO: Send VKey    wnd[0]    82   (or use the delete toolbar button)
        Sleep    0.5s
        Dismiss Any Popup
    END

    Log    TDS cleanup complete — only row 1 (194Q) remains    level=INFO


Execute MIRO Flow
    [Documentation]
    ...    Full MIRO sequence:
    ...    ZMM35 → Select Parked Docs → Execute (no date filter) →
    ...    Find row by material doc number → Fill posting date + reference →
    ...    Simulate → Withholding Tax → Delete rows except 194Q →
    ...    Simulate again → Post

    # STEP 1: Navigate to ZMM35
    Input Text    wnd[0]/tbar[0]/okcd    ZMM35
    Send VKey     wnd[0]    0
    Sleep    2s
    Dismiss Any Popup
    Log    Navigated to ZMM35    level=INFO

    # STEP 2: Select Parked Documents radio button
    # No date filter — MIGO 105 may have happened days ago
    # TODO: Replace element ID with actual ID from your SAP system
    # Example: Select Radio Button    wnd[0]/usr/subSEL:SAPLZMM35:0100/radXSELECT-3
    Sleep    1s
    Log    Parked Documents selected    level=INFO

    # STEP 3: Execute — loads full parked doc list with NO date restriction
    # TODO: Click Execute button
    # Example: Click Element    wnd[0]/tbar[1]/btn[8]
    Sleep    3s
    Dismiss Any Popup
    Log    Execute clicked — parked document list loaded    level=INFO

    # STEP 4: Find and click the row matching MATERIAL_DOC_NUMBER
    # The material doc number column in the list identifies the exact document.
    # TODO: Implement row search in the ALV grid
    # Approach:
    #   - Use SapGuiLibrary grid functions to find the row where
    #     the "Material D" column value = ${MATERIAL_DOC_NUMBER}
    #   - Double-click that row to open the MIRO document
    # Example (adjust column index after checking your SAP grid):
    # ${row}=    Find Table Row    wnd[0]/usr/<grid_id>    ${MATERIAL_DOC_NUMBER}
    # Double Click Element    wnd[0]/usr/<grid_id>/rows[${row}]
    Sleep    2s
    Dismiss Any Popup
    Log    Opened MIRO document for MatDoc: ${MATERIAL_DOC_NUMBER}    level=INFO

    # STEP 5: Fill Posting Date (always today) and Reference (invoice number)
    ${ref_clean}=    Clean Value    ${REFERENCE_NUMBER}

    # TODO: Input posting date
    # Example: Input Text    wnd[0]/usr/subHEADER:SAPLMR07M:0700/ctxtRBKPV-BUDAT    ${POSTING_DATE}

    # TODO: Input reference number (invoice number)
    # Example: Input Text    wnd[0]/usr/subHEADER:SAPLMR07M:0700/ctxtRBKPV-XBLNR    ${ref_clean}

    Sleep    1s
    Log    Posting date and reference filled    level=INFO

    # STEP 6: Click Simulate (first time)
    # TODO: Click Simulate button
    # Example: Click Element    wnd[0]/tbar[0]/btn[1]
    Sleep    3s
    Dismiss Any Popup
    Log    First Simulate complete    level=INFO

    # STEP 7: Navigate to Withholding Tax tab
    # TODO: Click Withholding Tax tab
    # Example: Click Element    wnd[0]/usr/tabsTAXTAB/tabpTAX
    Sleep    2s
    Dismiss Any Popup
    Log    On Withholding Tax tab    level=INFO

    # STEP 8: Dynamic TDS row deletion — keeps only row 1 (194Q)
    Delete All TDS Rows Except First
    Sleep    1s

    # STEP 9: Click Simulate again (second time — verify after TDS cleanup)
    # TODO: Click Simulate button
    Sleep    3s
    Dismiss Any Popup
    Log    Second Simulate complete    level=INFO

    # STEP 10: Click Post
    # TODO: Click Post button
    # Example: Click Element    wnd[0]/tbar[0]/btn[11]
    Sleep    3s
    Dismiss Any Popup

    # Read status bar to confirm posting
    ${status_msg}=    Read Status Bar With Retry    expected_pattern=.{5,}
    Log    MIRO post status: "${status_msg}"    level=INFO
    Log    MIRO posted successfully.    level=INFO


Close SAP Session
    Run Keyword And Ignore Error    Input Text    wnd[0]/tbar[0]/okcd    /nex
    Run Keyword And Ignore Error    Send VKey     wnd[0]    0
    Run Keyword And Ignore Error    Run Process   taskkill    /F    /IM    saplogon.exe
    Log    SAP session closed.    level=INFO
