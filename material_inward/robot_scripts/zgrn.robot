*** Settings ***
Documentation     ZGRN Print — GR Certificate download via "Download for MIP"
...
...               FLOW:
...                 1. Login to SAP, run ZGRN.
...                 2. Enter Material Document Number (MIGO number).
...                 3. Tick "Capital Goods Declaration"  -> wnd[0]/usr/chkLS_CGD
...                 4. Tick "Download for MIP"           -> wnd[0]/usr/chkLC_MIP
...                    (this makes SAP write the PDF(s) directly into
...                     C:\zgrn_print\ -- no "Print:" popup and no native
...                     "Save Print Output As" dialog is involved at all)
...                 5. Press Execute (F8).
...                 6. If a "file already exists / overwrite?" popup comes up
...                    -- either a SAP GUI popup or a native Windows
...                    "Confirm Save As" box -- it is answered YES so the
...                    existing file gets replaced. If no such popup appears,
...                    that is normal (file did not exist yet), not an error.
...                 7. Log success and report a JSON result with the list of
...                    files that appeared in the download folder.
...
...               Download folder is fixed by the ZGRN program itself:
...               C:\zgrn_print\
...
...               REWRITTEN 2026-09-03: replaces the old Print-to-PDF +
...               native Save-dialog flow (pdf_helper.py's type_save_filename)
...               with SAP's own direct-to-folder download -- removes almost
...               all of the native-dialog fragility that flow had. The one
...               native dialog left is the same-day-reprint overwrite
...               confirmation, handled by Handle Overwrite Popup below.
...
...               MULTI-BOT COORDINATION (also added 2026-09-03): login/close
...               now go through sap_coordination.resource (shared by every
...               SAP bot) instead of an unconditional taskkill on the way
...               in and out -- reuses an already-open SAP Logon process if
...               another bot has one open, registers in the shared
...               refcount, and only fully closes SAP if this was the last
...               bot still active. See that resource file for the full
...               reasoning. This is the only part of this script not
...               carried over as provided -- the print/download logic below
...               (Print ZGRN Document, Handle Overwrite Popup, etc.) is
...               otherwise unchanged.
Library           SapGuiLibrary
Library           Process
Library           OperatingSystem
Library           String
Library           DateTime
Library           Collections
Library           pdf_helper.py
Resource          sap_coordination.resource

*** Variables ***
${MIGO_NUMBER}      5000062737
${MIP_FOLDER}       C:\\zgrn_print
${session}          ${NONE}


*** Test Cases ***
Execute ZGRN Print
    [Setup]    Initialize SAP And Login
    ${result}=    Print ZGRN Document
    Log To Console    RESULT:ZGRN_PRINT_JSON:${result}
    Sleep    3s
    [Teardown]    Close SAP Session


*** Keywords ***
Initialize SAP And Login
    Evaluate    __import__('dotenv').load_dotenv(__import__('os').getenv('DOTENV_PATH', '.env'), override=True)
    ${CLIENT}=      Evaluate    __import__('os').getenv('SAP_CLIENT')
    ${CONN_NAME}=   Evaluate    __import__('os').getenv('SAP_CONNECTION_NAME')
    ${LOGON_PATH}=  Evaluate    __import__('os').getenv('SAP_LOGON_PATH')

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

    # MULTI-BOT FIX (2026-09-03): used to unconditionally taskkill
    # saplogon.exe here, every run -- see sap_coordination.resource for the
    # full reasoning on why that's no longer safe under concurrency (this
    # would kill any other bot's open window the instant a ZGRN job
    # starts, regardless of who it belonged to).
    Coordinated SAP Login    ${CLIENT}    ${CONN_NAME}    ${LOGON_PATH}    ${USERNAME}    ${PASSWORD}


Print ZGRN Document
    [Documentation]    Enters the MIGO number, ticks Capital Goods
    ...    Declaration + Download for MIP, executes, handles any
    ...    overwrite popup, and reports what landed in C:\zgrn_print\.

    # --- Snapshot of the folder BEFORE, so we can tell what SAP wrote ---
    ${files_before}=    Files In MIP Folder

    # --- Step 1: Navigate to ZGRN ---
    Run Transaction    zgrn
    Send VKey     0
    Sleep    2s
    Dismiss Any Popup

    # --- Step 2: MIGO number + both checkboxes ---
    Input Text    wnd[0]/usr/txtLS_MBLNR-LOW    ${MIGO_NUMBER}

    # Capital Goods Declaration
    Evaluate    setattr($session.findById('wnd[0]/usr/chkLS_CGD'), 'selected', True)
    Log To Console    Ticked: Capital Goods Declaration (chkLS_CGD)

    # Download for MIP -- makes SAP save the PDFs into C:\zgrn_print\
    Evaluate    setattr($session.findById('wnd[0]/usr/chkLC_MIP'), 'selected', True)
    Log To Console    Ticked: Download for MIP (chkLC_MIP)

    Set Focus    wnd[0]/usr/chkLC_MIP
    Sleep    1s

    # --- Step 3: Execute (F8) ---
    Log To Console    Executing ZGRN for MIGO ${MIGO_NUMBER} ...
    Click Element    wnd[0]/tbar[1]/btn[8]
    Sleep    3s

    # --- Step 4: Handle "already exists / overwrite?" popup, if any ---
    ${overwrite_status}=    Handle Overwrite Popup
    Log To Console    Overwrite-popup handling: ${overwrite_status}

    # Give SAP a moment to finish writing the file(s)
    Sleep    3s
    Dismiss Any Popup

    # --- Step 5: Verify what was actually written ---
    ${files_after}=    Files In MIP Folder
    ${new_files}=    Evaluate    [f for f in $files_after if f not in $files_before]
    ${new_count}=    Get Length    ${new_files}

    IF    ${new_count} > 0
        Log To Console    RESULT:ZGRN_PRINT_STATUS:SUCCESS
        Log To Console    Successfully created the print for MIGO ${MIGO_NUMBER}. New file(s) in ${MIP_FOLDER}: ${new_files}
        ${result}=    Evaluate
        ...    __import__('json').dumps({'executed': True, 'downloaded': True, 'overwrite_popup': $overwrite_status, 'folder': $MIP_FOLDER, 'new_files': $new_files, 'message': 'Successfully created the print -- ZGRN executed and file(s) downloaded for MIP.'})
    ELSE IF    '${overwrite_status}' == 'OVERWRITE_CONFIRMED'
        # File name already existed and we said Yes -- so it was replaced
        # in place, hence no "new" file name shows up in the diff.
        Log To Console    RESULT:ZGRN_PRINT_STATUS:SUCCESS_OVERWRITTEN
        Log To Console    Successfully created the print for MIGO ${MIGO_NUMBER} (existing file in ${MIP_FOLDER} was overwritten).
        ${result}=    Evaluate
        ...    __import__('json').dumps({'executed': True, 'downloaded': True, 'overwrite_popup': $overwrite_status, 'folder': $MIP_FOLDER, 'new_files': [], 'message': 'Successfully created the print -- existing file was overwritten.'})
    ELSE
        Log To Console    RESULT:ZGRN_PRINT_STATUS:NO_FILE_CREATED
        Log To Console    ZGRN executed but no new file appeared in ${MIP_FOLDER} -- check the MIGO number / document output in SAP.
        ${result}=    Evaluate
        ...    __import__('json').dumps({'executed': True, 'downloaded': False, 'overwrite_popup': $overwrite_status, 'folder': $MIP_FOLDER, 'new_files': [], 'message': 'ZGRN executed but no file was produced in the MIP folder.'})
    END
    RETURN    ${result}


Files In MIP Folder
    [Documentation]    Returns the list of file names currently in the MIP
    ...    download folder. Never fails -- returns an empty list if the
    ...    folder does not exist yet.
    ${files}=    Evaluate
    ...    __import__('os').listdir($MIP_FOLDER) if __import__('os').path.isdir($MIP_FOLDER) else []
    RETURN    ${files}


Handle Overwrite Popup
    [Documentation]    After Execute, SAP may warn that the file already
    ...    exists in C:\zgrn_print\ and ask whether to replace it. That can
    ...    surface either as a SAP GUI popup (wnd[1]) or as a native Windows
    ...    "Confirm Save As" box. Both are answered YES. Returns a status
    ...    string; never fails, because no popup at all is the normal case
    ...    when the file did not previously exist.
    FOR    ${i}    IN RANGE    8
        # --- SAP GUI popup ---
        ${sap_popup}=    Run Keyword And Return Status
        ...    Element Should Be Present    wnd[1]
        IF    ${sap_popup}
            ${text}=    Run Keyword And Ignore Error
            ...    Evaluate    $session.findById('wnd[1]').text
            Log To Console    SAP popup detected: ${text}
            # Standard SAP question popup: OPTION1 = Yes, OPTION2 = No
            ${yes}=    Run Keyword And Return Status
            ...    Evaluate    $session.findById('wnd[1]/usr/btnSPOP-OPTION1').press()
            IF    not ${yes}
                # Fallback: green tick / Continue on the popup toolbar
                Run Keyword And Ignore Error
                ...    Evaluate    $session.findById('wnd[1]/tbar[0]/btn[0]').press()
            END
            Sleep    2s
            RETURN    OVERWRITE_CONFIRMED

        END

        # --- Native Windows "Confirm Save As" box ---
        ${native}=    Evaluate
        ...    __import__('win32gui').FindWindow(None, 'Confirm Save As') != 0
        IF    ${native}
            Log To Console    Native "Confirm Save As" dialog detected -- clicking Yes.
            ${status}=    Confirm Native Overwrite
            Sleep    2s
            RETURN    ${status}
        END

        Sleep    1s
    END
    RETURN    NO_POPUP


Confirm Native Overwrite
    [Documentation]    Clicks the "Yes" button on the native Windows
    ...    "Confirm Save As" dialog. The default-focused button on that box
    ...    is "No", so a blind Enter keystroke is NOT safe -- the button is
    ...    located by caption and clicked explicitly.
    ${status}=    Evaluate    __import__('pdf_helper').confirm_overwrite_dialog()
    RETURN    ${status}


Close SAP Session
    Coordinated SAP Close
