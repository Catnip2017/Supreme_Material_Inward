# *** Settings ***
# Documentation     MIRO SAP Automation — TCODE: ZMM35
# ...               Fixes applied:
# ...               - No date range filter on search screen (handles multi-day gaps)
# ...               - Dynamic TDS row deletion (count rows first, delete all except row 1)
# ...               - Generic popup dismissal after every major action
# ...               - Status bar retry after posting
# ...               - Data cleaning on all input values
# Library           SapGuiLibrary
# Library           Process
# Library           OperatingSystem
# Library           String
# Library           DateTime
# Library           Collections

# *** Variables ***
# # Passed from rf_runner.py
# ${MATERIAL_DOC_NUMBER}  ${EMPTY}    # From MIGO 103 DB — used to find the right row in list
# ${POSTING_DATE}         ${EMPTY}    # Today's date — filled on MIRO document page only
# ${REFERENCE_NUMBER}     ${EMPTY}    # Invoice number (bill number)
# ${INVOICE_DATE}         ${EMPTY}    # Original invoice date
# ${PO_NUMBER}            ${EMPTY}    # Purchase order number

# *** Test Cases ***
# Execute MIRO
#     [Setup]    Initialize SAP and Login
#     Execute MIRO Flow
#     [Teardown]    Close SAP Session

# *** Keywords ***
# Initialize SAP and Login
#     Evaluate    __import__('dotenv').load_dotenv()
#     ${CLIENT}=      Evaluate    __import__('os').getenv('SAP_CLIENT')
#     ${USERNAME}=    Evaluate    __import__('os').getenv('SAP_USERNAME')
#     ${PASSWORD}=    Evaluate    __import__('os').getenv('SAP_PASSWORD')
#     ${CONN_NAME}=   Evaluate    __import__('os').getenv('SAP_CONNECTION_NAME')
#     ${LOGON_PATH}=  Evaluate    __import__('os').getenv('SAP_LOGON_PATH')

#     Run Keyword And Ignore Error    Run Process    taskkill    /F    /IM    saplogon.exe
#     Sleep    2s
#     Start Process    ${LOGON_PATH}
#     Sleep    5s

#     Connect To Session
#     Open Connection    ${CONN_NAME}
#     Input Text      wnd[0]/usr/txtRSYST-MANDT    ${CLIENT}
#     Input Text      wnd[0]/usr/txtRSYST-BNAME    ${USERNAME}
#     Input Password  wnd[0]/usr/pwdRSYST-BCODE    ${PASSWORD}
#     Click Element   wnd[0]/tbar[0]/btn[0]
#     Sleep    5s

#     ${status}=    Run Keyword And Return Status    Element Should Be Present    wnd[1]
#     IF    ${status}
#         Select Radio Button    wnd[1]/usr/radMULTI_LOGON_OPT1
#         Click Element    wnd[1]/tbar[0]/btn[0]
#         Sleep    3s
#     END
#     Maximize Window    wnd[0]
#     Log    SAP login successful    level=INFO


# Clean Value
#     [Arguments]    ${raw_value}
#     ${cleaned}=    Strip String    ${raw_value}
#     ${cleaned}=    Replace String    ${cleaned}    ₹    ${EMPTY}
#     ${cleaned}=    Replace String    ${cleaned}    $    ${EMPTY}
#     ${cleaned}=    Replace String    ${cleaned}    €    ${EMPTY}
#     ${cleaned}=    Replace String    ${cleaned}    £    ${EMPTY}
#     ${cleaned}=    Replace String    ${cleaned}    ,    ${EMPTY}
#     ${parts}=      Split String    ${cleaned}    ${SPACE}    1
#     ${cleaned}=    Set Variable    ${parts}[0]
#     RETURN    ${cleaned}


# Dismiss Any Popup
#     ${popup1}=    Run Keyword And Return Status    Element Should Be Present    wnd[1]
#     IF    ${popup1}
#         Log    Popup on wnd[1] — dismissing    level=WARN
#         Run Keyword And Ignore Error    Click Element    wnd[1]/tbar[0]/btn[0]
#         Sleep    1s
#     END
#     ${popup2}=    Run Keyword And Return Status    Element Should Be Present    wnd[2]
#     IF    ${popup2}
#         Log    Popup on wnd[2] — dismissing    level=WARN
#         Run Keyword And Ignore Error    Click Element    wnd[2]/tbar[0]/btn[0]
#         Sleep    1s
#     END


# Read Status Bar With Retry
#     [Arguments]    ${expected_pattern}=\\d{4,}
#     ${msg}=    Set Variable    ${EMPTY}
#     FOR    ${attempt}    IN RANGE    1    6
#         ${msg}=    Get Value    wnd[0]/sbar
#         Log    Status bar attempt ${attempt}: "${msg}"    level=INFO
#         ${matched}=    Run Keyword And Return Status
#         ...    Should Match Regexp    ${msg}    ${expected_pattern}
#         IF    ${matched}    RETURN    ${msg}
#         Sleep    1s
#     END
#     Log    Status bar check timed out. Last: "${msg}"    level=WARN
#     RETURN    ${msg}


# Delete All TDS Rows Except First
#     [Documentation]
#     ...    Dynamically counts rows in the Withholding Tax table.
#     ...    Deletes all rows except row 1 (194Q).
#     ...    Deletes from bottom up to avoid index shifting.
#     ...    Handles any number of rows — not hardcoded to 4.
#     ...
#     ...    TODO: Replace the row count and row selection element IDs
#     ...    below with actual IDs from your SAP system.
#     ...    Record the GUI script while on the Withholding Tax tab to get them.

#     # TODO: Get the row count from the Withholding Tax table grid
#     # Example: ${row_count}=    Get Row Count    wnd[0]/usr/<withholding_tax_grid_id>
#     # For now using a placeholder — replace with actual element ID
#     ${row_count}=    Set Variable    4    # TODO: Replace with dynamic count

#     Log    Withholding Tax rows found: ${row_count}    level=INFO

#     IF    ${row_count} <= 1
#         Log    Only 1 or 0 TDS rows — nothing to delete    level=INFO
#         RETURN
#     END

#     # Delete from bottom row up to row 2 (keep row 1 = 194Q)
#     FOR    ${row_idx}    IN RANGE    ${row_count}    1    -1
#         Log    Deleting TDS row ${row_idx}    level=INFO
#         # TODO: Click on row ${row_idx} to select it
#         # Example: Click Element    wnd[0]/usr/<grid_id>/rows[${row_idx - 1}]
#         # Then press Delete:
#         # TODO: Send VKey    wnd[0]    82   (or use the delete toolbar button)
#         Sleep    0.5s
#         Dismiss Any Popup
#     END

#     Log    TDS cleanup complete — only row 1 (194Q) remains    level=INFO


# Execute MIRO Flow
#     [Documentation]
#     ...    Full MIRO sequence:
#     ...    ZMM35 → Select Parked Docs → Execute (no date filter) →
#     ...    Find row by material doc number → Fill posting date + reference →
#     ...    Simulate → Withholding Tax → Delete rows except 194Q →
#     ...    Simulate again → Post

#     # STEP 1: Navigate to ZMM35
#     Input Text    wnd[0]/tbar[0]/okcd    ZMM35
#     Send VKey     wnd[0]    0
#     Sleep    2s
#     Dismiss Any Popup
#     Log    Navigated to ZMM35    level=INFO

#     # STEP 2: Select Parked Documents radio button
#     # No date filter — MIGO 105 may have happened days ago
#     # TODO: Replace element ID with actual ID from your SAP system
#     # Example: Select Radio Button    wnd[0]/usr/subSEL:SAPLZMM35:0100/radXSELECT-3
#     Sleep    1s
#     Log    Parked Documents selected    level=INFO

#     # STEP 3: Execute — loads full parked doc list with NO date restriction
#     # TODO: Click Execute button
#     # Example: Click Element    wnd[0]/tbar[1]/btn[8]
#     Sleep    3s
#     Dismiss Any Popup
#     Log    Execute clicked — parked document list loaded    level=INFO

#     # STEP 4: Find and click the row matching MATERIAL_DOC_NUMBER
#     # The material doc number column in the list identifies the exact document.
#     # TODO: Implement row search in the ALV grid
#     # Approach:
#     #   - Use SapGuiLibrary grid functions to find the row where
#     #     the "Material D" column value = ${MATERIAL_DOC_NUMBER}
#     #   - Double-click that row to open the MIRO document
#     # Example (adjust column index after checking your SAP grid):
#     # ${row}=    Find Table Row    wnd[0]/usr/<grid_id>    ${MATERIAL_DOC_NUMBER}
#     # Double Click Element    wnd[0]/usr/<grid_id>/rows[${row}]
#     Sleep    2s
#     Dismiss Any Popup
#     Log    Opened MIRO document for MatDoc: ${MATERIAL_DOC_NUMBER}    level=INFO

#     # STEP 5: Fill Posting Date (always today) and Reference (invoice number)
#     ${ref_clean}=    Clean Value    ${REFERENCE_NUMBER}

#     # TODO: Input posting date
#     # Example: Input Text    wnd[0]/usr/subHEADER:SAPLMR07M:0700/ctxtRBKPV-BUDAT    ${POSTING_DATE}

#     # TODO: Input reference number (invoice number)
#     # Example: Input Text    wnd[0]/usr/subHEADER:SAPLMR07M:0700/ctxtRBKPV-XBLNR    ${ref_clean}

#     Sleep    1s
#     Log    Posting date and reference filled    level=INFO

#     # STEP 6: Click Simulate (first time)
#     # TODO: Click Simulate button
#     # Example: Click Element    wnd[0]/tbar[0]/btn[1]
#     Sleep    3s
#     Dismiss Any Popup
#     Log    First Simulate complete    level=INFO

#     # STEP 7: Navigate to Withholding Tax tab
#     # TODO: Click Withholding Tax tab
#     # Example: Click Element    wnd[0]/usr/tabsTAXTAB/tabpTAX
#     Sleep    2s
#     Dismiss Any Popup
#     Log    On Withholding Tax tab    level=INFO

#     # STEP 8: Dynamic TDS row deletion — keeps only row 1 (194Q)
#     Delete All TDS Rows Except First
#     Sleep    1s

#     # STEP 9: Click Simulate again (second time — verify after TDS cleanup)
#     # TODO: Click Simulate button
#     Sleep    3s
#     Dismiss Any Popup
#     Log    Second Simulate complete    level=INFO

#     # STEP 10: Click Post
#     # TODO: Click Post button
#     # Example: Click Element    wnd[0]/tbar[0]/btn[11]
#     Sleep    3s
#     Dismiss Any Popup

#     # Read status bar to confirm posting
#     ${status_msg}=    Read Status Bar With Retry    expected_pattern=.{5,}
#     Log    MIRO post status: "${status_msg}"    level=INFO
#     Log    MIRO posted successfully.    level=INFO


# Close SAP Session
#     Run Keyword And Ignore Error    Input Text    wnd[0]/tbar[0]/okcd    /nex
#     Run Keyword And Ignore Error    Send VKey     wnd[0]    0
#     Run Keyword And Ignore Error    Run Process   taskkill    /F    /IM    saplogon.exe
#     Log    SAP session closed.    level=INFO


*** Settings ***
Documentation     MIRO SAP Automation — TCODE: ZMM35
...               Steps:
...               1. SAP Login with credentials
...               2. Enter ZMM35 tcode
...               3. Set document date: From = 3 days ago, To = today
...               4. Select Parked Documents radio button and Execute
...               5. Dynamically select each document and verify Reference Number
...               6. Change Posting Date to current date
...               7. Go to Withholding Tax tab — remove second TDS row entirely
...               8. Simulate → Post
Library           SapGuiLibrary
Library           Process
Library           OperatingSystem
Library           String
Library           DateTime
Library           Collections
Library           SapGuiLibrary    screenshots_on_failure=False
*** Variables ***
 
${POSTING_DATE}           ${EMPTY}
${DATE_FROM}              ${EMPTY}
${DATE_TO}                ${EMPTY}
${CLIENT}                 ${EMPTY}
${USERNAME}               ${EMPTY}
${PASSWORD}               ${EMPTY}
${CONN_NAME}              ${EMPTY}
# Passed from rf_runner.py
${MATERIAL_DOC_NUMBER}    ${EMPTY}    # From MIGO 103 DB — used to find the right row in list
${REFERENCE_NUMBER}       ${EMPTY}    # Invoice number — verified against document after 105
${PO_NUMBER}              ${EMPTY}    # Purchase order number (optional filter)
 
# These are computed at runtime — do not set manually
${POSTING_DATE}           ${EMPTY}    # Filled dynamically = today
${DATE_FROM}              ${EMPTY}    # Filled dynamically = today minus 3 days
${DATE_TO}                ${EMPTY}    # Filled dynamically = today
 
*** Test Cases ***
Execute MIRO
    [Setup]    Initialize SAP and Login
    Execute MIRO Flow
    [Teardown]    Close SAP Session
    Debug Find Delete Button
 
*** Keywords ***
 
# ─────────────────────────────────────────────
# SAP LOGIN
# ─────────────────────────────────────────────
 
Initialize SAP and Login
    [Documentation]    Loads .env credentials, launches SAP Logon, logs in, handles multi-logon popup.
    ${env_path}=    Join Path    ${EXECDIR}    .env
    Evaluate    __import__('dotenv').load_dotenv(r'''${env_path}''')
    ${CLIENT}=      Evaluate    __import__('os').getenv('SAP_CLIENT')
    ${USERNAME}=    Evaluate    __import__('os').getenv('SAP_USERNAME')
    ${PASSWORD}=    Evaluate    __import__('os').getenv('SAP_PASSWORD')
    ${CONN_NAME}=   Evaluate    __import__('os').getenv('SAP_CONNECTION_NAME')
 
    # Kill any existing SAP processes
    Run Keyword And Ignore Error    Run Process    taskkill    /F    /IM    saplogon.exe
    Run Keyword And Ignore Error    Run Process    taskkill    /F    /IM    saplgpad.exe
    Sleep    2s
 
    # Launch SAP — hardcoded path, no spaces issue
    Start Process    C:\\Program Files\\SAP\\FrontEnd\\SAPGUI\\saplogon.exe
    Sleep    5s
 
    # Connect to SAP GUI session
    SapGuiLibrary.Connect To Session
    SapGuiLibrary.Open Connection    ${CONN_NAME}
 
    # Fill credentials
    SapGuiLibrary.Input Text        wnd[0]/usr/txtRSYST-MANDT    ${CLIENT}
    SapGuiLibrary.Input Text        wnd[0]/usr/txtRSYST-BNAME    ${USERNAME}
    SapGuiLibrary.Input Password    wnd[0]/usr/pwdRSYST-BCODE    ${PASSWORD}
    SapGuiLibrary.Click Element     wnd[0]/tbar[0]/btn[0]
    Sleep    5s
 
    Log    SAP login successful    level=INFO
 
 
# ─────────────────────────────────────────────
# UTILITY KEYWORDS
# ─────────────────────────────────────────────
 
Clean Value
    [Documentation]    Strips currency symbols, commas, and leading/trailing spaces from a value.
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
    [Documentation]    Checks for popups on wnd[1] and wnd[2] and dismisses them with Enter.
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
    [Documentation]    Polls the SAP status bar up to 5 times looking for a pattern match.
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
 
 
Get Today And Date From
    [Documentation]
    ...    Computes:
    ...      POSTING_DATE / DATE_TO = today in DD.MM.YYYY (SAP format)
    ...      DATE_FROM              = today minus 3 days in DD.MM.YYYY
    ${now}=           Get Current Date    result_format=%d.%m.%Y
    ${three_ago}=     Get Current Date    increment=-7 days    result_format=%d.%m.%Y
    Set Suite Variable    ${POSTING_DATE}    ${now}
    Set Suite Variable    ${DATE_TO}         ${now}
    Set Suite Variable    ${DATE_FROM}       ${three_ago}
    Log    Posting/To date: ${now}    level=INFO
    Log    From date (7 days ago): ${three_ago}    level=INFO
 
 
 
# ─────────────────────────────────────────────
# TDS / WITHHOLDING TAX CLEANUP
# ─────────────────────────────────────────────
 
Delete Second TDS Row
    [Documentation]
    ...    Navigates to the Withholding Tax tab and removes ALL TDS rows except the first (index 0).
 
    Log    Navigating to Withholding Tax tab    level=INFO
 
    Click Element    wnd[0]/usr/subHEADER_AND_ITEMS:SAPLMR1M:6005/tabsHEADER/tabpHEADER_WT
    Sleep    2s
    Dismiss Any Popup
 
    ${tbl_path}=    Set Variable
    ...    wnd[0]/usr/subHEADER_AND_ITEMS:SAPLMR1M:6005/tabsHEADER/tabpHEADER_WT/ssubHEADER_SCREEN:SAPLFDCB:0080/subSUB_WT:SAPLFWTD:0120/tblSAPLFWTDWT_DIALOG
 
    ${engine}=    Evaluate
    ...    win32com.client.GetObject('SAPGUI').GetScriptingEngine.Children(0).Children(0)
    ...    modules=win32com.client
 
    FOR    ${row_idx}    IN RANGE    1    10
 
        ${cell_path}=    Set Variable
        ...    ${tbl_path}/ctxtACWT_ITEM-WT_WITHCD[1,${row_idx}]
 
        ${row_exists}=    Run Keyword And Return Status
        ...    Element Should Be Present    ${cell_path}
 
        IF    not ${row_exists}
            Log    No more rows at index ${row_idx} — done    level=INFO
            BREAK
        END
 
        ${cell_value}=    Get Value    ${cell_path}
 
        IF    '${cell_value}' == ''
            Log    Row ${row_idx} is empty — skipping    level=INFO
            CONTINUE
        END
 
        Log    Row ${row_idx} has value '${cell_value}' — deleting via keyboard    level=WARN
 
        # Focus the cell
        Set Focus    ${cell_path}
        Sleep    0.5s
 
        # Send Backspace using pyautogui (bypasses SapGuiLibrary VKey issues)
        Evaluate    __import__('pyautogui').hotkey('backspace')    modules=pyautogui
        Sleep    0.5s
        Dismiss Any Popup
 
        # Confirm cleared
        ${new_value}=    Get Value    ${cell_path}
        IF    '${new_value}' == ''
            Log    Row ${row_idx} cleared successfully    level=INFO
        ELSE
            Log    Backspace failed — trying Delete key    level=WARN
            Set Focus    ${cell_path}
            Sleep    0.3s
            Evaluate    __import__('pyautogui').hotkey('delete')    modules=pyautogui
            Sleep    0.5s
            Dismiss Any Popup
        END
 
    END
 
    # Safety check — row 0 must still have value
    ${row0_value}=    Get Value
    ...    ${tbl_path}/ctxtACWT_ITEM-WT_WITHCD[1,0]
 
    IF    '${row0_value}' == ''
        Fail    ERROR: Row 0 (194Q) was cleared! Aborting.
    END
 
    Log    TDS cleanup done — row 0 (${row0_value}) intact    level=INFO
 
# ─────────────────────────────────────────────
# DEBUG — RUN ONCE TO FIND DELETE BUTTON ID
# ─────────────────────────────────────────────
 
 
 
Debug Find Delete Button
    [Documentation]    Run this ONCE to find the correct delete button ID
 
    Click Element    wnd[0]/usr/subHEADER_AND_ITEMS:SAPLMR1M:6005/tabsHEADER/tabpHEADER_WT
    Sleep    2s
 
    ${engine}=    Evaluate
    ...    win32com.client.GetObject('SAPGUI').GetScriptingEngine.Children(0).Children(0)
    ...    modules=win32com.client
 
    # Get all children of the WT subscreen to find buttons
    ${sub}=    Evaluate
    ...    $engine.findById('wnd[0]/usr/subHEADER_AND_ITEMS:SAPLMR1M:6005/tabsHEADER/tabpHEADER_WT/ssubHEADER_SCREEN:SAPLFDCB:0080/subSUB_WT:SAPLFWTD:0120')
 
    ${count}=    Evaluate    $sub.Children.Count
 
    Log    Total children in subSUB_WT: ${count}    level=WARN
 
    FOR    ${i}    IN RANGE    0    ${count}
        ${child}=    Evaluate    $sub.Children.ElementAt(${i})
        ${child_id}=    Evaluate    $child.Id
        ${child_type}=    Evaluate    $child.Type
        ${child_text}=    Evaluate    $child.Text
        Log    [${i}] ID=${child_id} | Type=${child_type} | Text=${child_text}    level=WARN
    END
# ─────────────────────────────────────────────
# MAIN FLOW
# ─────────────────────────────────────────────
 
Execute MIRO Flow
    [Documentation]
    ...    Full MIRO sequence:
    ...    1. Compute dates (today, today-3)
    ...    2. ZMM35 → set date range → Parked Documents → Execute
    ...    3. Dynamically iterate documents → match Reference Number
    ...    4. Fill Posting Date (today) on matched document
    ...    5. First Simulate
    ...    6. Withholding Tax tab → delete second TDS row
    ...    7. Second Simulate → verify
    ...    8. Post → confirm status bar
 
    # ── Compute all dates upfront ──
    Get Today And Date From
 
    # ── STEP 1: Navigate to ZMM35 ──
     SapGuiLibrary.Run Transaction    ZMM35
   
    Dismiss Any Popup
    Log    Navigated to ZMM35    level=INFO
 
    # ── STEP 2: Set Document Date range (From = 7 days ago, To = today) ──
    # First date field = "Document Date From"
    # TODO: Confirm element IDs by recording a GUI script on this selection screen
#Input Text    wnd[0]/usr/ctxtS_BLDAT-LOW    ${DATE_FROM}
    # Second date field = "Document Date To"
#Input Text    wnd[0]/usr/ctxtS_BLDAT-HIGH    ${DATE_TO}
    Sleep    1s
    Log    Date range set: ${DATE_FROM} to ${DATE_TO}    level=INFO
 
    # ── STEP 3: Select Parked Documents radio button ──
    # Radio option 3 typically = Parked Documents in ZMM35
    # TODO: Confirm radio button ID from your SAP system
    Select Radio Button    wnd[0]/usr/radP_PARK
    Sleep    1s
   
    Log    Parked Documents radio selected    level=INFO
 
    # ── STEP 4: Execute — loads parked document list ──
    Click Element    wnd[0]/tbar[1]/btn[8]
    Sleep    4s
    Dismiss Any Popup
    Log    Execute clicked — parked document list loaded    level=INFO
 
 
 
    # ── STEP 5: Iterate through grid rows and match by Reference Number ──
   
 
    # ── DIAGNOSTIC: print all columns and rows ──
    ${grid}=    Evaluate
    ...    __import__('win32com.client').client.GetObject('SAPGUI').GetScriptingEngine.Children(0).Children(0).findById('wnd[0]/usr/cntlGRID1/shellcont/shell/shellcont[1]/shell')
 
    ${rows}=    Evaluate    $grid.RowCount
    ${cols}=    Evaluate    $grid.ColumnCount
    Log    ROWS=${rows} COLS=${cols}    level=WARN
 
    # Print all column names
    FOR    ${c}    IN RANGE    0    ${cols}
        ${col}=    Evaluate    $grid.ColumnOrder[${c}]
        Log    COLUMN: ${col}    level=WARN
    END
 
    # Print first 3 rows all columns
    # ── STEP 5: Open matching document from grid ──
   # ── Iterate rows and match by MATERIAL_DOC_NUMBER ──
    FOR    ${r}    IN RANGE    0    ${rows}
        ${doc_val}=    Evaluate    $grid.GetCellValue(${r}, 'BELNR')
        ${doc_val}=    Strip String    ${doc_val}
        ${doc_clean}=  Strip String    ${MATERIAL_DOC_NUMBER}
 
        IF    '${doc_val}' == '${doc_clean}'
            Log    Match found at row ${r} by MATERIAL_DOC_NUMBER    level=WARN
            Evaluate    $grid.setCurrentCell(${r}, 'BELNR')
            Sleep    0.5s
            Evaluate    $grid.doubleClick(${r}, 'BELNR')
            Sleep    3s
            Dismiss Any Popup
 
            ${still_on_list}=    Run Keyword And Return Status
            ...    Element Should Be Present
            ...    wnd[0]/usr/cntlGRID1/shellcont/shell/shellcont[1]/shell
 
            IF    ${still_on_list}
                Log    Double-click failed — trying Enter    level=WARN
                SapGuiLibrary.Send VKey    wnd[0]    0
                Sleep    3s
                Dismiss Any Popup
            END
 
            ${still_on_list}=    Run Keyword And Return Status
            ...    Element Should Be Present
            ...    wnd[0]/usr/cntlGRID1/shellcont/shell/shellcont[1]/shell
            IF    ${still_on_list}
                Fail    Could not open MIRO document at row ${r}.
            END
 
            Log    MIRO document opened successfully    level=INFO
            BREAK
        END
    END
 
 
 
    # ── Set Posting Date — now inside the open document ──
    # ── Set Posting Date — confirmed field ID ──
    Sleep    2s
    Dismiss Any Popup
 
    SapGuiLibrary.Input Text
    ...    wnd[0]/usr/subHEADER_AND_ITEMS:SAPLMR1M:6005/tabsHEADER/tabpHEADER_TOTAL/ssubHEADER_SCREEN:SAPLFDCB:0010/ctxtINVFO-BUDAT
    ...    ${POSTING_DATE}
   
    Sleep    1s
    Log    Posting date set to: ${POSTING_DATE}    level=INFO
 
    # ── STEP 7: First Simulate ──
    # Simulate button is typically F9 (VKey 9) or toolbar btn[1] in MIRO
    # TODO: Confirm Simulate button element ID
    Click Element    wnd[0]/tbar[0]/btn[1]
    Sleep    4s
    Dismiss Any Popup
    Log    First Simulate complete    level=INFO
 
    # ── STEP 8: Navigate to Withholding Tax tab and delete second TDS row ──
    Delete Second TDS Row
    Sleep    1s
 
    # ── STEP 9: Second Simulate — verify accounting entries are correct ──
    Click Element    wnd[0]/tbar[0]/btn[1]
    Sleep    4s
    Dismiss Any Popup
    Log    Second Simulate complete — entries verified    level=INFO
 
    # Check status bar after simulate — should not show an error
    ${sim_status}=    Read Status Bar With Retry    expected_pattern=.{3,}
    Log    Simulate status: "${sim_status}"    level=INFO
    ${is_error}=    Run Keyword And Return Status
    ...    Should Match Regexp    ${sim_status}    (?i)(error|fehler|E\\s)
    IF    ${is_error}
        Fail    Simulate returned an error: "${sim_status}". Aborting post.
    END
 
    # ── STEP 10: Post the document ──
    # Post button is typically the Save/Post button: tbar[0]/btn[11] or Ctrl+S
    # TODO: Confirm Post button element ID
    #Click Element    wnd[0]/tbar[0]/btn[11]
    Sleep    5s
    Dismiss Any Popup
 
    # ── STEP 11: Read and validate status bar for document number ──
    ${status_msg}=    Read Status Bar With Retry    expected_pattern=\\d{9,}
    Log    MIRO post status: "${status_msg}"    level=INFO
 
    # Status bar on success typically contains the new FI document number (9+ digits)
    ${posted_ok}=    Run Keyword And Return Status
    ...    Should Match Regexp    ${status_msg}    \\d{9,}
    IF    ${posted_ok}
        Log    MIRO posted successfully. FI Document: "${status_msg}"    level=INFO
        Log To Console    RESULT:FI_DOC_NUMBER:${status_msg}
    ELSE
        Log    Post may have failed — status bar: "${status_msg}"    level=WARN
        # Uncomment to hard-fail:
        # Fail    MIRO post did not return a document number. Status: "${status_msg}"
    END
 
 
# ─────────────────────────────────────────────
# TEARDOWN
# ─────────────────────────────────────────────
 
#Close SAP Session
    [Documentation]    Gracefully exits SAP and kills the process.
    Run Keyword And Ignore Error    Input Text    wnd[0]/tbar[0]/okcd    /nex
    Run Keyword And Ignore Error    Send VKey     wnd[0]    0
    Sleep    2s
    Run Keyword And Ignore Error    Run Process    taskkill    /F    /IM    saplogon.exe    shell=True
    Log    SAP session closed.    level=INFO