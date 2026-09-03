*** Settings ***
Documentation       SAP MIGO - Attach Invoice Document URL via GOS Toolbox (MIGO 103 link bot)
...                 Recreates the recorded VBS flow:
...                 Login -> MIGO -> enter material doc -> GOS Toolbox
...                 -> Create URL -> title "Invoice_<day>_<month>" -> link -> confirm
...                 Single-file version: no custom Python library, everything
...                 that SapGuiLibrary does not expose (pressContextButton,
...                 selectContextMenuItem, sendVKey, caretPosition, setFocus)
...                 is done via BuiltIn "Evaluate" talking to the same live
...                 SAP GUI Scripting session through win32com.
...                 Requires: pip install pywin32
...
...                 Called by services/rf_runner.py's execute_migo103_link_sap()
...                 as its own separate RF-queue job, after MIGO 103 posting has
...                 already succeeded -- so a failure here never affects whether
...                 the MIGO 103 goods receipt itself succeeded. Variables
...                 MATERIAL_DOC_NUMBER / DOCUMENT_LINK are passed in via
...                 --variable (overriding the sample defaults below); the
...                 RESULT:MIGO103_LINK_STATUS:SUCCESS marker at the end of
...                 test case 04 is how rf_runner.py confirms the link was
...                 actually attached, on top of Robot Framework's own exit code.
 
Library             Process
Library             SapGuiLibrary
Library             BuiltIn
Library             OperatingSystem
Library             DateTime
Library             String
Library             Collections
Resource            sap_coordination.resource

Suite Setup         Initialize Logging And Environment
Suite Teardown      Finalize Logging
 
*** Variables ***
# ---------- FIXED VALUES (overridden from the shared .env at runtime -- see
# Load Environment Variables below; these are only the fallback if .env
# doesn't define them) ----------
${SAP_LOGON_PATH}              C:\\Program Files\\SAP\\FrontEnd\\SAPGUI\\saplogon.exe
#${SAP_CONNECTION_NAME}         01 SAP Producation System
${SAP_CONNECTION_NAME}         02 SAP Quality System
${LOGS_FOLDER}                 %{USERPROFILE}\\Agent logs\\migo invoice logs
${LOG_FILE}                    ${EMPTY}
${FIRST_LOGIN}                 ${TRUE}
${session}                     ${NONE}
# MULTI-BOT FIX (2026-09-03): flags whichever of Close SAP On Error /
# "05 Logout" actually runs first, so the other one becomes a no-op --
# see the comment on Close SAP On Error below for why this guard is
# needed now that closing decrements a SHARED counter (Coordinated SAP
# Close) instead of just running an idempotent taskkill.
${_sap_closed}                 ${FALSE}
 
# ---------- MIGO (sample defaults -- rf_runner.py always overrides both via
# --variable when this is run from the app; only used if run standalone) ----------
${MIGO_TCODE}                  MIGO
${MATERIAL_DOC_NUMBER}         5000034577
${DOCUMENT_LINK}                http://192.168.203.92:8080/CVWeb/openDocumentLogin?serverName=SPL&roomName=DMS&documentId=458338
 
# ---------- ELEMENT PATHS ----------
${PATH_MAT_DOC}                 wnd[0]/usr/ssubSUB_MAIN_CARRIER:SAPLMIGO:0003/subSUB_FIRSTLINE:SAPLMIGO:0011/subSUB_FIRSTLINE_REFDOC:SAPLMIGO:2010/txtGODYNPRO-MAT_DOC
${PATH_GOS_SHELL}                wnd[0]/titl/shellcont/shell
${PATH_URL_TITLE}                wnd[1]/usr/txtDOCUMENT_TITLE
${PATH_URL_ADDRESS}             wnd[1]/usr/txtURL
${PATH_URL_CONFIRM_BTN}          wnd[1]/tbar[0]/btn[0]
${PATH_MIGO_ACTION}         wnd[0]/usr/ssubSUB_MAIN_CARRIER:SAPLMIGO:0003/subSUB_FIRSTLINE:SAPLMIGO:0011/subSUB_FIRSTLINE_ACTION:SAPLMIGO:1010/ctxtGODYNPRO-ACTION
${PATH_MIGO_REFDOC}         wnd[0]/usr/ssubSUB_MAIN_CARRIER:SAPLMIGO:0003/subSUB_FIRSTLINE:SAPLMIGO:0011/subSUB_FIRSTLINE_REFDOC:SAPLMIGO:1010/ctxtGODYNPRO-REFDOC
${PATH_ACTION_COMBO}            wnd[0]/usr/ssubSUB_MAIN_CARRIER:SAPLMIGO:0003/subSUB_FIRSTLINE:SAPLMIGO:0011/cmbGODYNPRO-ACTION
${PATH_TREE_CLOSE_BTN}          wnd[0]/shellcont/shell/shellcont[1]/shell[0]
 
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
    Append To File    ${LOG_FILE}    BOT EXECUTION LOG - MIGO103 INVOICE LINK\n
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
    # Builds "Invoice_<day>_<month>" with NO leading zeros, e.g. Invoice_29_7
    ${day}=      Evaluate    __import__('datetime').datetime.now().day
    ${month}=    Evaluate    __import__('datetime').datetime.now().month
    ${title}=    Set Variable    Invoice_${day}_${month}
    RETURN    ${title}
 
SAP Login Steps
    [Arguments]    ${log_context}
    # MULTI-BOT FIX (2026-09-03): used to unconditionally taskkill
    # saplogon.exe/saplgpad.exe here (guarded only by ${FIRST_LOGIN}, which
    # just skipped the kill on this suite's own very first login -- it
    # still killed any OTHER bot's SAP on every subsequent run) and then
    # bind its own session via the buggy Children(0).Children(0) lookup
    # (always the FIRST connection on the machine). Coordinated SAP Login
    # replaces both: it reuses an already-open SAP Logon instead of killing
    # it, and binds ${session} to the connection THIS login just created
    # (Bind SAP Session). ${FIRST_LOGIN} is no longer needed -- Coordinated
    # SAP Login already decides reuse-vs-fresh-start on its own.
    Coordinated SAP Login    ${CLIENT}    ${SAP_CONNECTION_NAME}    ${SAP_LOGON_PATH}    ${USERNAME}    ${PASSWORD}
    Write Log    INFO    ${log_context}    Opened connection: ${SAP_CONNECTION_NAME}

Close SAP On Error
    [Arguments]    ${log_context}    ${error}
    Write Log    ERROR    ${log_context}    ${error}
    # MULTI-BOT FIX (2026-09-03): this suite has TWO close paths -- this one
    # (called from an EXCEPT block partway through) and "05 Logout" at the
    # end, which Robot Framework still runs even after an earlier test case
    # in this suite has failed (there's no [Teardown]/exitonfailure tying
    # them together). The old unconditional taskkill was harmless to run
    # twice; Coordinated SAP Close is NOT, because it decrements a counter
    # SHARED with every other SAP bot -- so ${_sap_closed} ensures only
    # whichever of these two actually runs FIRST does the real close.
    IF    not ${_sap_closed}
        Set Suite Variable    ${_sap_closed}    ${TRUE}
        Run Keyword And Ignore Error    SapGuiLibrary.Run Transaction    /nex
        Sleep    1s
        Coordinated SAP Close
    END


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
# NAVIGATE TO MIGO
# ═══════════════════════════════════════════════════════════════
 
02 Navigate To MIGO
    Write Log    INFO    MIGO    Navigating to ${MIGO_TCODE}
    TRY
        SapGuiLibrary.Run Transaction    ${MIGO_TCODE}
        Sleep    2s
        Write Log    SUCCESS    MIGO    Navigated to ${MIGO_TCODE}
    EXCEPT    AS    ${error}
        Close SAP On Error    MIGO    Navigation failed: ${error}
        Fail    MIGO Navigation Failed: ${error}
    END
 
# ═══════════════════════════════════════════════════════════════
# ENTER MATERIAL DOCUMENT NUMBER
# ═══════════════════════════════════════════════════════════════
 
03 Enter Material Document Number
    Write Log    INFO    MIGO    Entering material document number: ${MATERIAL_DOC_NUMBER}
    TRY
        ${hide_overview_status}=    Run Keyword And Return Status    SapGuiLibrary.Element Should Be Present    wnd[0]/tbar[1]/btn[21]
        IF    ${hide_overview_status}
            Evaluate    $session.findById('wnd[0]').maximize()
            Evaluate    $session.findById('wnd[0]/tbar[1]/btn[21]').press()
            Sleep    2s
        END
 
        ${tree_close_status}=    Run Keyword And Return Status    SapGuiLibrary.Element Should Be Present    ${PATH_TREE_CLOSE_BTN}
        IF    ${tree_close_status}
            Evaluate    $session.findById('${PATH_TREE_CLOSE_BTN}').pressButton('OK_CLOSE')
            Sleep    2s
        END
 
        Evaluate    $session.findById('${PATH_ACTION_COMBO}').setFocus()
        Evaluate    setattr($session.findById('${PATH_ACTION_COMBO}'), 'key', 'A12')
        Sleep    1s
 
        SapGuiLibrary.Input Text    ${PATH_MAT_DOC}    ${MATERIAL_DOC_NUMBER}
        Sleep    2s
        Evaluate    setattr($session.findById('${PATH_MAT_DOC}'), 'caretPosition', 10)
        Evaluate    $session.findById('wnd[0]').sendVKey(0)
        Sleep    2s
        Write Log    SUCCESS    MIGO    Material document ${MATERIAL_DOC_NUMBER} loaded
    EXCEPT    AS    ${error}
        Close SAP On Error    MIGO    Material doc entry failed: ${error}
        Fail    Material Document Entry Failed: ${error}
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
        Sleep    2s
        SapGuiLibrary.Input Text    ${PATH_URL_ADDRESS}    ${DOCUMENT_LINK}
        Sleep    2s
        Evaluate    $session.findById('${PATH_URL_ADDRESS}').setFocus()
        Evaluate    setattr($session.findById('${PATH_URL_ADDRESS}'), 'caretPosition', 5)
        Evaluate    $session.findById('${PATH_URL_CONFIRM_BTN}').press()
        Sleep    2s
        Evaluate    $session.findById('wnd[0]/tbar[0]/btn[11]').press()
        Write Log    SUCCESS    GOS URL    Document URL "${doc_title}" created and attached
        # INTEGRATION: consumed by services/rf_runner.py's
        # execute_migo103_link_sap() via _extract_marked_value() -- confirms
        # the link was actually attached, on top of Robot Framework's own
        # exit code. Only reached if every Evaluate/Input Text call above
        # completed without raising into the EXCEPT branch below.
        Log To Console    RESULT:MIGO103_LINK_STATUS:SUCCESS
    EXCEPT    AS    ${error}
        Close SAP On Error    GOS URL    URL creation failed: ${error}
        Fail    GOS URL Creation Failed: ${error}
    END
 
# ═══════════════════════════════════════════════════════════════
# LOGOUT
# ═══════════════════════════════════════════════════════════════
 
05 Logout
    IF    not ${_sap_closed}
        Set Suite Variable    ${_sap_closed}    ${TRUE}
        Write Log    INFO    Logout    Closing SAP session
        TRY
            Coordinated SAP Close
            Write Log    SUCCESS    Logout    SAP session closed
        EXCEPT    AS    ${error}
            Write Log    ERROR    Logout    ${error}
        END
    ELSE
        Write Log    INFO    Logout    SAP session was already closed by an earlier error handler -- skipping.
    END
 