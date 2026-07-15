*** Settings ***
Documentation     Update Gate In entry with PO — TCODE: zgatein_update
...               Enter the gate in sequence number and execute.
...               SAP automatically links the PO from MIGO 103 to the gate in record.
...               Variables: GATE_IN_NUMBER, HISTORY_ID
Library           SapGuiLibrary
Library           Process
Library           OperatingSystem
Library           String

*** Variables ***
${GATE_IN_NUMBER}       ${EMPTY}
${PO_NUMBER}            ${EMPTY}
${TCODE}                zgatein_update
${HISTORY_ID}           ${EMPTY}

*** Test Cases ***
Update Gate In PO
    [Setup]    Initialize SAP And Login
    ${status}=    Enter Gate In And Execute
    Log To Console    RESULT:GATEIN_UPDATE_STATUS:${status}
    Sleep    3s
    [Teardown]    Close SAP Session

*** Keywords ***
Initialize SAP And Login
    ${env_path}=    Join Path    ${EXECDIR}    .env
    Evaluate    __import__('dotenv').load_dotenv(r'''${env_path}''')

    ${CLIENT}=      Evaluate    __import__('os').getenv('SAP_CLIENT')
    ${USERNAME}=    Evaluate    __import__('os').getenv('SAP_USERNAME')
    ${PASSWORD}=    Evaluate    __import__('os').getenv('SAP_PASSWORD')
    ${CONN_NAME}=   Evaluate    __import__('os').getenv('SAP_CONNECTION_NAME')
    ${LOGON_PATH}=  Evaluate    __import__('os').getenv('SAP_LOGON_PATH')

    Should Not Be Empty    ${LOGON_PATH}    SAP_LOGON_PATH not found in .env
    Should Not Be Empty    ${CONN_NAME}     SAP_CONNECTION_NAME not found in .env
    Should Not Be Empty    ${CLIENT}        SAP_CLIENT not found in .env
    Should Not Be Empty    ${USERNAME}      SAP_USERNAME not found in .env
    Should Not Be Empty    ${PASSWORD}      SAP_PASSWORD not found in .env

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
    Sleep    3s

    ${login_popup}=    Run Keyword And Return Status    Element Should Be Present    wnd[1]
    IF    ${login_popup}
        Run Keyword And Ignore Error    Select Radio Button    wnd[1]/usr/radMULTI_LOGON_OPT2
        Run Keyword And Ignore Error    Click Element          wnd[1]/tbar[0]/btn[0]
        Sleep    2s
    END

    Sleep    5s
    Dismiss Any Popup
    Maximize Window    0

Enter Gate In And Execute
    # Navigate to tcode
    SapGuiLibrary.Run Transaction     ${TCODE}
    #Send VKey     wnd[0]    0
    Sleep    2s
    Dismiss Any Popup

    # Wait for the zgatein_update screen to load
    Wait Until Keyword Succeeds    10s    1s    Element Should Be Present    wnd[0]/usr/ctxtZSEQ

    # Enter the gate in sequence number
    Set Focus     wnd[0]/usr/ctxtZSEQ
    SapGuiLibrary.Input Text     wnd[0]/usr/ctxtZSEQ       ${GATE_IN_NUMBER}
    Set Focus     wnd[0]/usr/ctxtZSEQ
    Sleep    1s
      # Click Execute
    SapGuiLibrary.Click Element     wnd[0]/tbar[1]/btn[8]
    Sleep     3s
     ${session}=    Evaluate
    ...    __import__('win32com.client', fromlist=['client']).GetObject("SAPGUI").GetScriptingEngine.Children(0).Children(0)
    ${ns}=    Create Dictionary    session=${session}
    # Get the grid object
    ${grid}=    Evaluate
    ...    session.findById("wnd[0]/usr/cntlGRID1/shellcont/shell/shellcont[1]/shell")
    ...    namespace=${ns}
    ${gns}=    Create Dictionary    grid=${grid}
    # Modify the cell — row 0, column ZEBELN, value from PO_NUMBER
    Evaluate    grid.modifyCell(0, "ZEBELN", "${PO_NUMBER}")    namespace=${gns}
    Sleep    1s
    Log To Console    ✅ Cell ZEBELN row 0 set to ${PO_NUMBER}
    # Update the grid cell
    #SapGuiLibrary.Select Cell    wnd[0]/usr/cntlGRID1/shellcont/shell/shellcont[1]/shell    0    ZEBELN
    #SapGuiLibrary.Modify Cell    wnd[0]/usr/cntlGRID1/shellcont/shell/shellcont[1]/shell    0    ZEBELN    12345678
    # Set current column (if required)
    #SAP Set Current Cell Column    wnd[0]/usr/cntlGRID1/shellcont/shell/shellcont[1]/shell    ZEBELN
    # Click Save
    SapGuiLibrary.Click Element    wnd[0]/tbar[0]/btn[11]
    # Execute (F8) — SAP auto-links the PO from MIGO 103
    SapGuiLibrary.Click Element    wnd[0]/tbar[1]/btn[8]
    Sleep    3s
    Dismiss Any Popup

    # Check status bar
    ${status_msg}=    Get Value    wnd[0]/sbar
    Log    zgatein_update status: "${status_msg}"    level=INFO
    Log To Console    Status bar: ${status_msg}

    # Check for known error keywords first
    ${is_error}=    Run Keyword And Return Status
    ...    Should Match Regexp    ${status_msg}    (?i)(error|not found|authorization|invalid|failed|no authorization)
    IF    ${is_error}
        Log    SAP returned error: ${status_msg}    level=ERROR
        Log To Console    RESULT:GATEIN_UPDATE_STATUS:MANUAL_CHECK_REQUIRED
        RETURN    MANUAL_CHECK_REQUIRED
    END

    # Check for success keywords
    @{matches}=    Get Regexp Matches    ${status_msg}    (?i)(success|saved|updated|document|posted|changed)
    IF    len($matches) > 0
        RETURN    SUCCESS
    ELSE IF    '${status_msg}' != ''
        # Status bar has content but no known keyword — log and treat as success
        # Review the message and add the keyword to the success pattern above
        Log    Unknown status bar message (treating as SUCCESS — verify manually): ${status_msg}    level=WARN
        RETURN    SUCCESS
    ELSE
        Log To Console    RESULT:GATEIN_UPDATE_STATUS:MANUAL_CHECK_REQUIRED
        RETURN    MANUAL_CHECK_REQUIRED
    END

Dismiss Any Popup
    ${present}=    Run Keyword And Return Status    Element Should Be Present    wnd[1]
    IF    ${present}
        Run Keyword And Ignore Error    Click Element    wnd[1]/tbar[0]/btn[0]
    END

Close SAP Session
    Run Keyword And Ignore Error    Input Text    wnd[0]/tbar[0]/okcd    /nex
    Run Keyword And Ignore Error    Send VKey     wnd[0]    0
    Sleep    2s
    Run Keyword And Ignore Error    Run Process    taskkill    /F    /IM    saplogon.exe
    Sleep    2s
    Run Keyword And Ignore Error    Run Process    taskkill    /F    /IM    saplogon.exe    /T
