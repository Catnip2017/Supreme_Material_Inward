*** Settings ***
Documentation       SAP ZMM35 - Find Material Document In Grid & Attach Invoice URL via GOS Toolbox (MIRO link bot)
...                 Recreates the recorded VBS flow:
...                 Login -> ZMM35 -> select POST radio -> Execute
...                 -> search grid column MBLNR for material doc number
...                 -> click matching row's BELNR column
...                 -> GOS Toolbox -> Create URL -> title "Invoice_<day>_<month>"
...                 -> link -> confirm
...                 Single-file version: no custom Python library. Anything
...                 SapGuiLibrary does not expose (grid cell search,
...                 pressContextButton, selectContextMenuItem, sendVKey,
...                 caretPosition, setFocus) is done via BuiltIn "Evaluate"
...                 talking to the same live SAP GUI Scripting session
...                 through win32com.
...                 Requires: pip install pywin32
...
...                 Called by services/rf_runner.py's execute_miro_link_sap()
...                 as its own separate RF-queue job, after MIRO posting has
...                 already succeeded -- a failure here never affects whether
...                 MIRO itself succeeded. Searches ZMM35's grid by the SAME
...                 material_doc_number the MIGO 103 link bot used (per
...                 client instruction), so it can never post the same link
...                 twice against the wrong row. MATERIAL_DOC_NUMBER /
...                 DOCUMENT_LINK are passed in via --variable;
...                 RESULT:MIRO_LINK_STATUS:SUCCESS at the end of test case 04
...                 is how rf_runner.py confirms the link was actually
...                 attached, on top of Robot Framework's own exit code.

Library             Process
Library             SapGuiLibrary
Library             BuiltIn
Library             OperatingSystem
Library             DateTime
Library             String
Library             Collections

Suite Setup         Initialize Logging And Environment
Suite Teardown      Finalize Logging

*** Variables ***
# ---------- FIXED VALUES (overridden from the shared .env at runtime -- see
# Load Environment Variables below; these are only the fallback if .env
# doesn't define them) ----------
${SAP_LOGON_PATH}              C:\\Program Files\\SAP\\FrontEnd\\SAPGUI\\saplogon.exe
#${SAP_CONNECTION_NAME}         01 SAP Producation System
${SAP_CONNECTION_NAME}         02 SAP Quality System
${LOGS_FOLDER}                 %{USERPROFILE}\\Agent logs\\zmm35 invoice logs
${LOG_FILE}                    ${EMPTY}
${FIRST_LOGIN}                 ${TRUE}
${session}                     ${NONE}
${grid}                        ${NONE}

# ---------- ZMM35 (sample defaults -- rf_runner.py always overrides both via
# --variable when this is run from the app; only used if run standalone) ----------
${ZMM35_TCODE}                  ZMM35
${MATERIAL_DOC_NUMBER}          5000006274
${DOCUMENT_LINK}                https://192.168.203.92:8080/CVWeb/openDocumentLogin?serverName=SPL&roomName=DMS&documentId=458338

# ---------- ELEMENT PATHS ----------
${PATH_PARK_RADIO}              wnd[0]/usr/radP_PARK
${PATH_EXECUTE_BTN}             wnd[0]/tbar[1]/btn[8]
${PATH_GRID}                    wnd[0]/usr/cntlGRID1/shellcont/shell/shellcont[1]/shell
${PATH_GOS_SHELL}                wnd[0]/titl/shellcont/shell
${PATH_URL_TITLE}                wnd[1]/usr/txtDOCUMENT_TITLE
${PATH_URL_ADDRESS}             wnd[1]/usr/txtURL
${PATH_URL_CONFIRM_BTN}          wnd[1]/tbar[0]/btn[0]

# ---------- LOADED FROM .env ----------
${CLIENT}                      ${EMPTY}
${USERNAME}                    ${EMPTY}
${PASSWORD}                    ${EMPTY}


*** Keywords ***

Initialize Logging And Environment
    Run Keyword And Ignore Error    Create Directory    ${LOGS_FOLDER}
    ${timestamp}=       Get Current Date    result_format=%Y-%m-%d_%H-%M-%S
    ${log_path}=        Join Path    ${LOGS_FOLDER}    bot_execution_${timestamp}.log
    Set Suite Variable  ${LOG_FILE}    ${log_path}
    ${start_time}=      Get Current Date    result_format=%Y-%m-%d %H:%M:%S
    Append To File    ${LOG_FILE}    ═══════════════════════════════════════════════════════════════\n
    Append To File    ${LOG_FILE}    BOT EXECUTION LOG - ZMM35 INVOICE LINK (MIRO)\n
    Append To File    ${LOG_FILE}    ═══════════════════════════════════════════════════════════════\n
    Append To File    ${LOG_FILE}    Start Time: ${start_time}\n
    Append To File    ${LOG_FILE}    ═══════════════════════════════════════════════════════════════\n\n
    Load Environment Variables

Finalize Logging
    ${end_time}=    Get Current Date    result_format=%Y-%m-%d %H:%M:%S
    Append To File    ${LOG_FILE}    \n═══════════════════════════════════════════════════════════════\n
    Append To File    ${LOG_FILE}    EXECUTION SUMMARY\n
    Append To File    ${LOG_FILE}    ═══════════════════════════════════════════════════════════════\n
    Append To File    ${LOG_FILE}    End Time: ${end_time}\n
    Append To File    ${LOG_FILE}    Final Status: ${SUITE_STATUS}\n
    Run Keyword If    '${SUITE_STATUS}' == 'FAIL'    Append To File    ${LOG_FILE}    Error Details: ${SUITE_MESSAGE}\n
    Append To File    ${LOG_FILE}    ═══════════════════════════════════════════════════════════════\n
    Log To Console    \n✓ Custom log saved to: ${LOG_FILE}

Write Log
    [Arguments]    ${level}    ${test_case}    ${message}
    ${timestamp}=     Get Current Date    result_format=%Y-%m-%d %H:%M:%S
    ${log_entry}=     Set Variable    [${timestamp}] [${level}] ${test_case}: ${message}\n
    Append To File    ${LOG_FILE}    ${log_entry}
    Log To Console    ${log_entry}

Load Environment Variables
    ${env_path}=    Join Path    ${EXECDIR}    .env
    Evaluate    __import__('dotenv').load_dotenv(r'''${env_path}''')
    ${CLIENT}=      Evaluate    __import__('os').getenv('SAP_CLIENT')
    ${USERNAME}=    Evaluate    __import__('os').getenv('SAP_USERNAME')
    ${PASSWORD}=    Evaluate    __import__('os').getenv('SAP_PASSWORD')
    Set Suite Variable    ${CLIENT}
    Set Suite Variable    ${USERNAME}
    Set Suite Variable    ${PASSWORD}

    # INTEGRATION: pick up SAP_CONNECTION_NAME / SAP_LOGON_PATH from the
    # shared .env if present, so this bot follows the same QA/Production
    # switch as migo_103.robot / migo_105.robot / miro.robot instead of the
    # hardcoded "02 SAP Quality System" default above staying stuck on QA
    # after everything else has been pointed at Production. Falls back to
    # the hardcoded defaults only if .env doesn't define them.
    ${CONN_NAME}=    Evaluate    __import__('os').getenv('SAP_CONNECTION_NAME', '')
    ${LOGON_PATH}=   Evaluate    __import__('os').getenv('SAP_LOGON_PATH', '')
    IF    $CONN_NAME != ''
        Set Suite Variable    ${SAP_CONNECTION_NAME}    ${CONN_NAME}
    END
    IF    $LOGON_PATH != ''
        Set Suite Variable    ${SAP_LOGON_PATH}    ${LOGON_PATH}
    END

    # INTEGRATION (v16 parity with migo_103.robot/migo_105.robot/miro.robot):
    # per-user SAP credential override for LDAP-authenticated users --
    # rf_runner.py's _sap_credential_env() passes these via the subprocess
    # environment (never via .env). Falls back to the shared .env
    # SAP_USERNAME/SAP_PASSWORD above when not present, which is the common
    # case for test accounts and today's default behavior.
    ${USER_OVERRIDE}=    Evaluate    __import__('os').getenv('SAP_USER_OVERRIDE', '')
    ${PASS_OVERRIDE}=    Evaluate    __import__('os').getenv('SAP_PASS_OVERRIDE', '')
    IF    $USER_OVERRIDE != '' and $PASS_OVERRIDE != ''
        Set Suite Variable    ${USERNAME}    ${USER_OVERRIDE}
        Set Suite Variable    ${PASSWORD}    ${PASS_OVERRIDE}
        Write Log    INFO    Environment Setup    Using per-user SAP credential override for ${USER_OVERRIDE}
    ELSE
        Write Log    INFO    Environment Setup    Environment variables loaded successfully
    END

Get Invoice Title
    # Builds "Invoice_<day>_<month>" with NO leading zeros, e.g. Invoice_30_7
    ${day}=      Evaluate    __import__('datetime').datetime.now().day
    ${month}=    Evaluate    __import__('datetime').datetime.now().month
    ${title}=    Set Variable    Invoice_${day}_${month}
    RETURN    ${title}

Connect To Sap Session
    # Attaches to the SAME live SAP GUI Scripting session that
    # SapGuiLibrary already opened and logged into.
    ${session}=    Evaluate
    ...    __import__('win32com.client').client.GetObject('SAPGUI').GetScriptingEngine.Children(0).Children(0)
    Set Suite Variable    ${session}
    Write Log    INFO    SAP Session    Attached to live SAP GUI scripting session

SAP Login Steps
    [Arguments]    ${log_context}
    IF    not ${FIRST_LOGIN}
        Run Keyword And Ignore Error    Run Process    taskkill    /F    /IM    saplogon.exe
        Run Keyword And Ignore Error    Run Process    taskkill    /F    /IM    saplgpad.exe
        Sleep    2s
    END
    Set Suite Variable    ${FIRST_LOGIN}    ${FALSE}
    Start Process    ${SAP_LOGON_PATH}
    Sleep    15s
    Write Log    INFO    ${log_context}    SAP Logon Pad started
    Wait Until Keyword Succeeds    5x    3s    SapGuiLibrary.Connect To Session
    Wait Until Keyword Succeeds    5x    3s    SapGuiLibrary.Open Connection    ${SAP_CONNECTION_NAME}
    Write Log    INFO    ${log_context}    Opened connection: ${SAP_CONNECTION_NAME}
    SapGuiLibrary.Input Text        wnd[0]/usr/txtRSYST-MANDT    ${CLIENT}
    SapGuiLibrary.Input Text        wnd[0]/usr/txtRSYST-BNAME    ${USERNAME}
    SapGuiLibrary.Input Password    wnd[0]/usr/pwdRSYST-BCODE    ${PASSWORD}
    SapGuiLibrary.Click Element     wnd[0]/tbar[0]/btn[0]
    Sleep    5s
    ${status}=    Run Keyword And Return Status    SapGuiLibrary.Element Should Be Present    wnd[1]
    IF    ${status}
        SapGuiLibrary.Select Radio Button    wnd[1]/usr/radMULTI_LOGON_OPT1
        SapGuiLibrary.Click Element          wnd[1]/tbar[0]/btn[0]
        Sleep    3s
    END
    Connect To Sap Session
    Evaluate    $session.findById('wnd[0]').maximize()

Close SAP On Error
    [Arguments]    ${log_context}    ${error}
    Write Log    ERROR    ${log_context}    ${error}
    Run Keyword And Ignore Error    SapGuiLibrary.Run Transaction    /nex
    Sleep    1s
    Run Keyword And Ignore Error    Run Process    taskkill    /F    /IM    saplogon.exe
    Run Keyword And Ignore Error    Run Process    taskkill    /F    /IM    saplgpad.exe
    Run Keyword And Ignore Error    Run Process    taskkill    /F    /IM    sapgui.exe


*** Test Cases ***

# ═══════════════════════════════════════════════════════════════
# SAP LOGIN
# ═══════════════════════════════════════════════════════════════

01 Start SAP Logon and Login
    Write Log    INFO    SAP Login    Starting SAP logon process
    TRY
        SAP Login Steps    SAP Login
        Write Log    SUCCESS    SAP Login    Successfully logged into SAP with user: ${USERNAME}
    EXCEPT    AS    ${error}
        Close SAP On Error    SAP Login    SAP Login Failed: ${error}
        Fail    SAP Login Failed: ${error}
    END

# ═══════════════════════════════════════════════════════════════
# NAVIGATE TO ZMM35, SELECT POST, EXECUTE
# ═══════════════════════════════════════════════════════════════

02 Navigate To ZMM35 And Execute
    Write Log    INFO    ZMM35    Navigating to ${ZMM35_TCODE}
    TRY
        SapGuiLibrary.Run Transaction    ${ZMM35_TCODE}
        Sleep    2s
        SapGuiLibrary.Select Radio Button    ${PATH_PARK_RADIO}
        Evaluate    $session.findById('${PATH_PARK_RADIO}').setFocus()
        SapGuiLibrary.Click Element    ${PATH_EXECUTE_BTN}
        Sleep    10s
        Write Log    SUCCESS    ZMM35    Executed report with PARK option
    EXCEPT    AS    ${error}
        Close SAP On Error    ZMM35    Navigation/Execute failed: ${error}
        Fail    ZMM35 Navigation/Execute Failed: ${error}
    END

# ═══════════════════════════════════════════════════════════════
# FIND MATERIAL DOCUMENT IN GRID AND CLICK BELNR COLUMN
# ═══════════════════════════════════════════════════════════════

03 Find Material Document In Grid And Click
    Write Log    INFO    ZMM35 Grid    Searching for material document: ${MATERIAL_DOC_NUMBER}
    TRY
        ${grid}=    Evaluate    $session.findById('${PATH_GRID}')
        Set Suite Variable    ${grid}

        # Point the grid at the MBLNR (material doc) column before searching
        Evaluate    $grid.setCurrentCell(-1, 'MBLNR')
        Evaluate    $grid.selectColumn('MBLNR')
        # Scan every row's MBLNR cell; return the matching row index, or -1 if none found
        ${row_count}=    Evaluate    int($grid.RowCount)
        ${found_row}=    Set Variable    ${-1}
        FOR    ${i}    IN RANGE    ${row_count}
            ${cell_value}=    Evaluate    $grid.GetCellValue(${i}, 'MBLNR').strip()
            IF    '${cell_value}' == '${MATERIAL_DOC_NUMBER}'
                ${found_row}=    Set Variable    ${i}
                Exit For Loop
            END
        END

        IF    ${found_row} == -1
            Write Log    ERROR    ZMM35 Grid    Material document ${MATERIAL_DOC_NUMBER} not found in grid
            Close SAP On Error    ZMM35 Grid    Material document ${MATERIAL_DOC_NUMBER} not found
            Fail    Material Document ${MATERIAL_DOC_NUMBER} Not Found In Grid
        END

        # Row found - select the whole row, then click the BELNR cell in that row
        Evaluate    setattr($grid, 'currentCellColumn', '')
        Evaluate    setattr($grid, 'selectedRows', '${found_row}')
        Evaluate    $grid.setCurrentCell(${found_row}, 'BELNR')
        Sleep    2s
        Evaluate    $grid.clickCurrentCell()
        Sleep    2s
        Write Log    SUCCESS    ZMM35 Grid    Found material doc ${MATERIAL_DOC_NUMBER} at row ${found_row} and clicked BELNR column
    EXCEPT    AS    ${error}
        Close SAP On Error    ZMM35 Grid    Grid search/click failed: ${error}
        Fail    ZMM35 Grid Search Failed: ${error}
    END

# ═══════════════════════════════════════════════════════════════
# CREATE GOS DOCUMENT URL (Invoice link)
# ═══════════════════════════════════════════════════════════════

04 Create Invoice URL Via GOS Toolbox
    ${doc_title}=    Get Invoice Title
    Write Log    INFO    GOS URL    Creating document URL with title: ${doc_title}
    TRY
        Evaluate    $session.findById('${PATH_GOS_SHELL}').pressContextButton('%GOS_TOOLBOX')
        Evaluate    $session.findById('${PATH_GOS_SHELL}').selectContextMenuItem('%GOS_URL_CREA')
        Sleep    1s
        SapGuiLibrary.Input Text    ${PATH_URL_TITLE}      ${doc_title}
        SapGuiLibrary.Input Text    ${PATH_URL_ADDRESS}    ${DOCUMENT_LINK}
        Evaluate    $session.findById('${PATH_URL_ADDRESS}').setFocus()
        Evaluate    setattr($session.findById('${PATH_URL_ADDRESS}'), 'caretPosition', 6)
        Evaluate    $session.findById('${PATH_URL_CONFIRM_BTN}').press()
        Sleep    2s
        Evaluate    $session.findById('wnd[0]/tbar[0]/btn[11]').press()
        Write Log    SUCCESS    GOS URL    Document URL "${doc_title}" created and attached
        # INTEGRATION: consumed by services/rf_runner.py's
        # execute_miro_link_sap() via _extract_marked_value() -- confirms the
        # link was actually attached, on top of Robot Framework's own exit
        # code. Only reached if every Evaluate/Input Text call above
        # completed without raising into the EXCEPT branch below.
        Log To Console    RESULT:MIRO_LINK_STATUS:SUCCESS
    EXCEPT    AS    ${error}
        Close SAP On Error    GOS URL    URL creation failed: ${error}
        Fail    GOS URL Creation Failed: ${error}
    END

# ═══════════════════════════════════════════════════════════════
# LOGOUT
# ═══════════════════════════════════════════════════════════════

05 Logout
    Write Log    INFO    Logout    Closing SAP session
    TRY
        SapGuiLibrary.Run Transaction    /nex
        Sleep    2s
        Write Log    SUCCESS    Logout    SAP session closed
    EXCEPT    AS    ${error}
        Write Log    ERROR    Logout    ${error}
    END
    Run Keyword And Ignore Error    Run Process    taskkill    /F    /IM    saplogon.exe
    Run Keyword And Ignore Error    Run Process    taskkill    /F    /IM    saplgpad.exe
    Run Keyword And Ignore Error    Run Process    taskkill    /F    /IM    sapgui.exe
    Write Log    INFO    Logout    SAP processes terminated
