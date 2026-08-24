*** Settings ***
Documentation     ZGRN Print — GR Certificate Printing to PDF
...               Logs into SAP, navigates to ZGRN, enters MIGO/material
...               document number, executes, then prints to PDF via
...               "Microsoft Print to PDF" and saves with a specific
...               filename in a specific folder.
...
...               ${PRINT_TWICE} controls how many times the print/save
...               cycle runs:
...                 - FALSE (default): print/save ONCE, filename suffix _GRN
...                 - TRUE: print/save TWICE, first _GRN, second _CGD
...
...               Output folder: value of ${OUTPUT_FOLDER} below (passed in
...               by rf_runner.py as config.ZGRN_PRINT_FOLDER at runtime --
...               the path here is just the module-level default).
...               Filename format: <MIGO_NUMBER>_<DDMMYYYY>_<GRN|CGD>.pdf
Library           SapGuiLibrary
Library           Process
Library           OperatingSystem
Library           String
Library           DateTime
Library           Collections
Library           pdf_helper.py
 
*** Variables ***
${MIGO_NUMBER}      ${EMPTY}
${PRINT_TWICE}      TRUE
${OUTPUT_FOLDER}    C:\\Users\\ctn_suresh\\Agents\\zgrn_print
${session}          ${NONE}
 
 
*** Test Cases ***
Execute ZGRN Print
    [Setup]    Initialize SAP And Login
    Print ZGRN Document
    Sleep    3s
    [Teardown]    Close SAP Session
 
 
*** Keywords ***
Initialize SAP And Login
    Evaluate    __import__('dotenv').load_dotenv(__import__('os').getenv('DOTENV_PATH', '.env'), override=True)
    ${CLIENT}=      Evaluate    __import__('os').getenv('SAP_CLIENT')
    ${CONN_NAME}=   Evaluate    __import__('os').getenv('SAP_CONNECTION_NAME')
    ${SAP_LOGON}=  Evaluate    __import__('os').getenv('SAP_LOGON_PATH')
 
    ${USER_OVERRIDE}=    Evaluate    __import__('os').getenv('SAP_USER_OVERRIDE', '')
    ${PASS_OVERRIDE}=    Evaluate    __import__('os').getenv('SAP_PASS_OVERRIDE', '')
    IF    $USER_OVERRIDE != '' and $PASS_OVERRIDE != ''
        ${USERNAME}=    Set Variable    ${USER_OVERRIDE}
        ${PASSWORD}=    Set Variable    ${PASS_OVERRIDE}
        Log To Console    SAP LOGIN: using per-user credential for ${USERNAME}
    ELSE
        ${USERNAME}=    Evaluate    __import__('os').getenv('SAP_USERNAME')
        ${PASSWORD}=    Evaluate    __import__('os').getenv('SAP_PASSWORD')
        Log To Console    SAP LOGIN: using shared .env credential (${USERNAME})
    END
 
    Run Keyword And Ignore Error    Run Process    taskkill    /F    /IM    saplogon.exe    /T
    Sleep    2s
 
    Start Process   ${SAP_LOGON}
    Sleep    5s
 
    Connect To Session
    Open Connection    ${CONN_NAME}
 
    Input Text        wnd[0]/usr/txtRSYST-MANDT    ${CLIENT}
    Input Text        wnd[0]/usr/txtRSYST-BNAME    ${USERNAME}
    Input Password    wnd[0]/usr/pwdRSYST-BCODE    ${PASSWORD}
    Click Element     wnd[0]/tbar[0]/btn[0]
 
    Sleep    3s
   ${multi}=    Run Keyword And Return Status    Element Should Be Present    wnd[1]
   IF    ${multi}
       Run Keyword And Ignore Error    Select Radio Button    wnd[1]/usr/radMULTI_LOGON_OPT1
       Run Keyword And Ignore Error    Click Element          wnd[1]/tbar[0]/btn[0]
       Sleep    2s
    END
 
    Sleep    5s
    Dismiss Any Popup
 
    Maximize Window    0
    Connect To Sap Session
 
 
Connect To Sap Session
    # Same pattern as migo bots -- raw SAP GUI Scripting session handle,
    # needed for direct session.findById(...) calls that SapGuiLibrary's
    # own keywords don't cover.
    ${sess}=    Evaluate
    ...    __import__('win32com.client').client.GetObject('SAPGUI').GetScriptingEngine.Children(0).Children(0)
    Set Suite Variable    ${session}    ${sess}
 
 
Print ZGRN Document
    # --- Build filenames up front ---
    ${today}=    Get Current Date    result_format=%d%m%Y
    ${filename_grn}=    Set Variable    ${OUTPUT_FOLDER}\\${MIGO_NUMBER}_${today}_GRN
    ${filename_cgd}=    Set Variable    ${OUTPUT_FOLDER}\\${MIGO_NUMBER}_${today}_CGD
    Log To Console    Target filenames: GRN="${filename_grn}.pdf"${SPACE}|${SPACE}CGD="${filename_cgd}.pdf" (PRINT_TWICE=${PRINT_TWICE})
 
    # --- Step 1: Navigate to ZGRN ---
    Run Transaction        zgrn
    Send VKey     0
    Sleep    2s
    Dismiss Any Popup
 
    # --- Step 2: Enter MIGO number, check box, execute ---
    Select Checkbox    wnd[0]/usr/chkLS_CGD
    Input Text          wnd[0]/usr/txtLS_MBLNR-LOW    ${MIGO_NUMBER}
    Set Focus            wnd[0]/usr/chkLS_CGD
    Click Element         wnd[0]/tbar[1]/btn[8]
    Sleep    3s
    Dismiss Any Popup
 
    # --- Step 3: First print/save (always happens) -- suffix _GRN ---
    Select Print Popup Printer And Print
    type_save_filename    ${filename_grn}
    Sleep    2s
 
    ${print_twice_upper}=    Convert To Upper Case    ${PRINT_TWICE}
    IF    '${print_twice_upper}' == 'TRUE'
        # --- Step 4: Second print/save -- suffix _CGD ---
        Select Print Popup Printer And Print
        type_save_filename    ${filename_cgd}
        Sleep    2s
        Log To Console    Printed TWICE: ${filename_grn}.pdf and ${filename_cgd}.pdf
    ELSE
        Log To Console    Printed ONCE: ${filename_grn}.pdf
    END
 
 
Select Print Popup Printer And Print
    # Waits for the print-options popup, selects "Microsoft Print to PDF",
    # and presses the print button -- this triggers the native Windows
    # Save dialog that type_save_filename then handles.
    ${popup_found}=    Run Keyword And Return Status
    ...    Element Should Be Present    wnd[1]/usr/cmbSSFPP-RQPOSNAME
    IF    not ${popup_found}
        Sleep    2s
    END
    Evaluate    setattr($session.findById('wnd[1]/usr/cmbSSFPP-RQPOSNAME'), 'key', 'Microsoft Print to PDF')
    Evaluate    $session.findById('wnd[1]/usr/cmbSSFPP-RQPOSNAME').setFocus()
    Sleep    0.5s
    Evaluate    $session.findById('wnd[1]/tbar[0]/btn[86]').press()
    Sleep    1s
 
 
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
 
 
Close SAP Session
    # FIX (2026-08-21): this only ever killed saplogon.exe -- never
    # saplgpad.exe or sapgui.exe, which is what's actually running/showing
    # the SAP GUI window once a session is logged in. Same gap already
    # fixed in po_fetch.robot/migo_103.robot -- /nex + saplogon.exe alone
    # can leave the actual GUI window/process sitting there untouched.
    Log    Closing SAP session...
    Run Keyword And Ignore Error    Input Text    wnd[0]/tbar[0]/okcd    /nex
    Run Keyword And Ignore Error    Send VKey     wnd[0]    0
    Sleep    2s
    Run Keyword And Ignore Error    Run Process    taskkill    /F    /IM    saplogon.exe
    Run Keyword And Ignore Error    Run Process    taskkill    /F    /IM    saplgpad.exe
    Run Keyword And Ignore Error    Run Process    taskkill    /F    /IM    sapgui.exe
    Sleep    2s
    Run Keyword And Ignore Error    Run Process    taskkill    /F    /IM    saplogon.exe    /T
    Run Keyword And Ignore Error    Run Process    taskkill    /F    /IM    saplgpad.exe    /T
    Run Keyword And Ignore Error    Run Process    taskkill    /F    /IM    sapgui.exe    /T
    Log    SAP session closed and process terminated.    level=INFO
 
 