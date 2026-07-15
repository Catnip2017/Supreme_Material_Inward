*** Settings ***
Documentation    Upload consolidated PDFs from DMS_STAGING_FOLDER into the
...              Contentverse DMS portal (Material Inward Process > MIP Docs >
...              {year} > {month}), then archive the uploaded files.
...
...              Called by services/dms_scheduler.py (nightly Windows Task
...              Scheduler run) after PDFs have been consolidated + staged.
...              It is NOT run through the RF queue (no SAP interaction).
...
...              Folders (mapped to this app's config, see config/config.py):
...                  DMS_PENDING_UPLOAD_FOLDER  = config.DMS_STAGING_FOLDER
...                                               (where dms_scheduler.py
...                                               writes h{id}_consolidated.pdf)
...                  DMS_UPLOADED_ARCHIVE_FOLDER = DMS_STAGING_FOLDER\uploaded
...                                               (uploaded PDFs moved here)
...
...              Credentials: CV_USERNAME / CV_PASSWORD read from .env.
...
...              Output marker (parsed by the caller):
...                  RESULT:DMS_UPLOAD_STATUS:SUCCESS
...                  RESULT:DMS_UPLOAD_STATUS:FAILED
Library    SeleniumLibrary
Library    OperatingSystem
Library    DateTime
Library    Process
Suite Setup       Load Environment Variables
Suite Teardown    Close Browser
Library    Collections

*** Variables ***
${URL}        http://192.168.203.92:8080/CVWeb/cvLgn
${BROWSER}    edge
${USERNAME}   ${EMPTY}
${PASSWORD}   ${EMPTY}
# Source: consolidated PDFs waiting to be uploaded (app's DMS staging folder)
${DMS_PENDING_UPLOAD_FOLDER}     C:\material_inward\dms_staging
# Destination: PDFs already uploaded, archived here after indexing
${DMS_UPLOADED_ARCHIVE_FOLDER}   C:\material_inward\dms_staging\uploaded

*** Keywords ***

Load Environment Variables
    ${env_path}=    Join Path    ${EXECDIR}    .env
    Evaluate    __import__('dotenv').load_dotenv(r'''${env_path}''')
    ${USERNAME}=    Evaluate    __import__('os').getenv('CV_USERNAME')
    ${PASSWORD}=    Evaluate    __import__('os').getenv('CV_PASSWORD')
    Should Not Be Empty    ${USERNAME}
    Should Not Be Empty    ${PASSWORD}
    Set Suite Variable    ${USERNAME}
    Set Suite Variable    ${PASSWORD}
    Create Directory    ${DMS_UPLOADED_ARCHIVE_FOLDER}

Open Login Page
    Open Browser    ${URL}    ${BROWSER}
    Maximize Browser Window
    Sleep    8s

Handle YES Popup
    Log    🔔 Session popup detected — clicking YES
    ${yes_found}=    Run Keyword And Return Status
    ...    Wait Until Element Is Visible
    ...    xpath=//button[normalize-space()='YES']    3s
    Run Keyword If    ${yes_found}
    ...    Click Element    xpath=//button[normalize-space()='YES']
    ...    ELSE    Execute Javascript
    ...    var allElements = document.querySelectorAll('button, input[type=button], a');
    ...    for(var i=0; i<allElements.length; i++){
    ...        if(allElements[i].textContent.trim().toUpperCase() === 'YES'){
    ...            allElements[i].click();
    ...            break;
    ...        }
    ...    }
    Sleep    2s
    Log    ✅ YES clicked — session popup closed

Login To Contentverse
    Execute Javascript
    ...    var inputs = document.querySelectorAll('input');
    ...    for(var i=0; i<inputs.length; i++){
    ...        if(inputs[i].placeholder && inputs[i].placeholder.toLowerCase().includes('user')){
    ...            inputs[i].value = '${USERNAME}';
    ...            inputs[i].dispatchEvent(new Event('input', {bubbles:true}));
    ...            inputs[i].dispatchEvent(new Event('change', {bubbles:true}));
    ...        }
    ...    }
    Sleep    1s
    Execute Javascript
    ...    var inputs = document.querySelectorAll('input');
    ...    for(var i=0; i<inputs.length; i++){
    ...        if(inputs[i].placeholder && inputs[i].placeholder.toLowerCase().includes('password')){
    ...            inputs[i].value = '${PASSWORD}';
    ...            inputs[i].dispatchEvent(new Event('input', {bubbles:true}));
    ...            inputs[i].dispatchEvent(new Event('change', {bubbles:true}));
    ...        }
    ...    }
    Sleep    1s
    Execute Javascript
    ...    var inputs = document.querySelectorAll('input');
    ...    for(var i=0; i<inputs.length; i++){
    ...        if(inputs[i].placeholder && inputs[i].placeholder.toLowerCase().includes('room')){
    ...            inputs[i].value = 'SPL.DMS';
    ...            inputs[i].dispatchEvent(new Event('input', {bubbles:true}));
    ...            inputs[i].dispatchEvent(new Event('change', {bubbles:true}));
    ...            inputs[i].dispatchEvent(new Event('keyup', {bubbles:true}));
    ...        }
    ...    }
    Sleep    3s
    ${dropdown_visible}=    Run Keyword And Return Status
    ...    Wait Until Element Is Visible    xpath=//*[contains(text(),'SPL.DMS')]    5s
    Run Keyword If    ${dropdown_visible}
    ...    Click Element    xpath=//*[contains(text(),'SPL.DMS')]
    Sleep    1s
    Execute Javascript
    ...    var buttons = document.querySelectorAll('button');
    ...    for(var i=0; i<buttons.length; i++){
    ...        if(buttons[i].textContent.trim().toUpperCase().includes('LOG IN')){
    ...            buttons[i].click();
    ...            break;
    ...        }
    ...    }
    FOR    ${i}    IN RANGE    10
        Sleep    1s
        ${popup_found}=    Run Keyword And Return Status
        ...    Page Should Contain    already active
        Run Keyword If    ${popup_found}    Handle YES Popup
        Run Keyword If    ${popup_found}    Exit For Loop
    END
    Sleep    5s

Get Current Month Folder Name
    ${date}=    Get Current Date    result_format=%Y-%b
    [Return]    ${date}

Get Current Year Folder Name
    ${year}=    Get Current Date    result_format=%Y
    [Return]    ${year}

Expand Material Inward Process
    Wait Until Element Is Visible
    ...    xpath=//*[contains(text(),'Material Inward Process')]    10s
    Sleep    1s
    ${mip_element}=    Get WebElement
    ...    xpath=//*[contains(text(),'Material Inward Process')]
    Mouse Over    ${mip_element}
    Sleep    1s
    ${plus_clicked}=    Run Keyword And Return Status
    ...    Click Element
    ...    xpath=//*[contains(text(),'Material Inward Process')]/preceding-sibling::*[1]
    Run Keyword Unless    ${plus_clicked}
    ...    Execute Javascript
    ...    var els = document.querySelectorAll('*');
    ...    for(var i=0; i<els.length; i++){
    ...        if(els[i].innerText && els[i].innerText.trim() === 'Material Inward Process'){
    ...            var parent = els[i].parentElement;
    ...            if(parent && parent.firstElementChild){
    ...                parent.firstElementChild.click();
    ...            }
    ...            break;
    ...        }
    ...    }
    Sleep    3s
    ${mip_visible}=    Run Keyword And Return Status
    ...    Wait Until Element Is Visible
    ...    xpath=//*[contains(text(),'MIP Docs')]    5s
    Run Keyword Unless    ${mip_visible}
    ...    Execute Javascript
    ...    var els = document.querySelectorAll('*');
    ...    for(var i=0; i<els.length; i++){
    ...        if(els[i].innerText && els[i].innerText.trim() === 'Material Inward Process'){
    ...            els[i].click();
    ...            break;
    ...        }
    ...    }
    Sleep    3s
    Log    ✅ Expand done

Expand MIP Docs
    Wait Until Element Is Visible
    ...    xpath=//*[contains(text(),'MIP Docs')]    10s
    Sleep    1s
    ${mip_docs_element}=    Get WebElement
    ...    xpath=//*[contains(text(),'MIP Docs')]
    Mouse Over    ${mip_docs_element}
    Sleep    1s
    Run Keyword And Ignore Error
    ...    Click Element
    ...    xpath=//*[contains(text(),'MIP Docs')]/preceding-sibling::*[1]
    Sleep    2s

Right Click Folder Node
    [Arguments]    ${node_locator}
    Wait Until Element Is Visible    ${node_locator}    10s
    Sleep    1s
    ${node_element}=    Get WebElement    ${node_locator}
    Open Context Menu    ${node_element}
    Sleep    2s

Click Create Folder From Menu
    Sleep    1s
    ${exists}=    Execute Javascript
    ...    return document.getElementById('createNodeAnchorMobile') != null;
    Log To Console    Exists=${exists}
    Execute Javascript
    ...    document.getElementById('createNodeAnchorMobile').click();

Type Folder Name And Click Ok
    [Arguments]    ${folder_name}
    Sleep    2s
    Execute Javascript
    ...    var inputs = document.querySelectorAll('input[type="text"], input:not([type])');
    ...    for(var i=0; i<inputs.length; i++){
    ...        if(inputs[i].offsetParent !== null && inputs[i].value === ''){
    ...            inputs[i].value = '${folder_name}';
    ...            inputs[i].dispatchEvent(new Event('input', {bubbles:true}));
    ...            inputs[i].dispatchEvent(new Event('change', {bubbles:true}));
    ...            break;
    ...        }
    ...    }
    Sleep    1s
    Press Keys    NONE    TAB
    Sleep    1s
    Press Keys    NONE    RETURN

Check If Folder Already Exists
    [Arguments]    ${folder_name}
    ${exists}=    Run Keyword And Return Status
    ...    Wait Until Element Is Visible
    ...    xpath=//*[text()='${folder_name}']    3s
    [Return]    ${exists}

Create Folder If Not Exists
    [Arguments]    ${parent_locator}    ${folder_name}
    ${folder_exists}=    Check If Folder Already Exists    ${folder_name}
    Run Keyword If    ${folder_exists}
    ...    Log    ⚠️ Folder '${folder_name}' already exists. Skipping.
    ...    ELSE    Run Keywords
    ...    Right Click Folder Node    ${parent_locator}
    ...    AND    Click Create Folder From Menu
    ...    AND    Type Folder Name And Click Ok    ${folder_name}

Check If Subfolder Already Exists
    [Arguments]    ${parent_name}    ${folder_name}
    Expand Folder Node    ${parent_name}
    ${exists}=    Run Keyword And Return Status
    ...    Wait Until Element Is Visible
    ...    xpath=//a[text()='${parent_name}']/parent::li//a[text()='${folder_name}']    3s
    [Return]    ${exists}

Create Subfolder If Not Exists
    [Arguments]    ${parent_locator}    ${parent_name}    ${folder_name}
    ${folder_exists}=    Check If Subfolder Already Exists    ${parent_name}    ${folder_name}
    Run Keyword If    ${folder_exists}
    ...    Log    ⚠️ Folder '${folder_name}' already exists under '${parent_name}'. Skipping.
    ...    ELSE    Run Keywords
    ...    Right Click Folder Node    ${parent_locator}
    ...    AND    Click Create Folder From Menu
    ...    AND    Type Folder Name And Click Ok    ${folder_name}

Open Subfolder By Name
    [Arguments]    ${parent_name}    ${folder_name}
    Expand Folder Node    ${parent_name}
    ${locator}=    Set Variable
    ...    xpath=//a[text()='${parent_name}']/parent::li//a[text()='${folder_name}']
    Wait Until Element Is Visible    ${locator}    10s
    Click Element    ${locator}
    Sleep    2s

Get List Of Pending Upload Files
    ${files}=    List Files In Directory    ${DMS_PENDING_UPLOAD_FOLDER}    *.pdf    absolute=False
    [Return]    ${files}

Open Folder By Name
    [Arguments]    ${folder_name}
    Wait Until Element Is Visible    xpath=//*[text()='${folder_name}']    10s
    Click Element    xpath=//*[text()='${folder_name}']
    Sleep    2s

Check If Invoice Already Exists
    [Arguments]    ${invoice_name}
    ${exists}=    Run Keyword And Return Status
    ...    Wait Until Element Is Visible
    ...    xpath=//td[normalize-space(text())='${invoice_name}'] | //*[@class='docName' and normalize-space(text())='${invoice_name}'] | //span[normalize-space(text())='${invoice_name}']    5s
    [Return]    ${exists}

Expand Folder Node
    [Arguments]    ${folder_name}
    ${already_open}=    Run Keyword And Return Status
    ...    Page Should Contain Element
    ...    xpath=//a[text()='${folder_name}']/ancestor::li[1][contains(@class,'jstree-open')]
    Run Keyword Unless    ${already_open}
    ...    Run Keywords
    ...    Click Element    xpath=//a[text()='${folder_name}']/preceding-sibling::ins[1]
    ...    AND    Sleep    2s

Click New Document Tab
    Execute Javascript
    ...    var els = document.querySelectorAll('a, li, button, span');
    ...    for(var i=0; i<els.length; i++){
    ...        var txt = els[i].textContent.trim().toUpperCase();
    ...        if((txt.includes('BATCH') || txt.includes('SCANNING') || txt.includes('IMPORT'))
    ...            && els[i].offsetParent !== null){
    ...            els[i].click();
    ...            break;
    ...        }
    ...    }
    Sleep    3s

Close Document Viewer
    Debug Dump Document Viewer HTML
    ${info}=    Execute Javascript
    ...    var candidates = document.querySelectorAll('span, a, button, i, div, ins');
    ...    var target = null;
    ...    for (var i=0; i<candidates.length; i++){
    ...        var el = candidates[i];
    ...        var txt = (el.textContent || '').trim();
    ...        var cls = (el.className || '').toLowerCase();
    ...        var title = (el.title || '').toLowerCase();
    ...        if ((txt === '×' || cls.indexOf('close') > -1 || title.indexOf('close') > -1) && el.offsetParent !== null){
    ...            target = el; break;
    ...        }
    ...    }
    ...    if(!target) return 'NONE';
    ...    var desc = target.tagName + ' | class=' + target.className + ' | title=' + target.title + ' | text=' + target.textContent.trim();
    ...    target.click();
    ...    return desc;
    Log    🔍 Close-button click target: ${info}
    Sleep    2s

Reopen Month Folder After Upload
    [Arguments]    ${year_folder}    ${month_folder}
    Sleep    5s
    Click Documents Tab
    Sleep    2s
    Capture Page Screenshot    after_documents_click.png
    Open Subfolder By Name    ${year_folder}    ${month_folder}

Debug Dump Document Viewer HTML
    ${html}=    Execute Javascript    return document.body.innerHTML;
    Create File    ${EXECDIR}/document_viewer_debug.html    ${html}
    Log    ⚠️ Could not find a close button automatically — dumped HTML to document_viewer_debug.html for review.

Click Documents Tab
    ${info}=    Execute Javascript
    ...    var matches = [];
    ...    var els = document.querySelectorAll('*');
    ...    for (var i=0;i<els.length;i++){
    ...        if (els[i].textContent.trim() === 'Documents' && els[i].children.length === 0){
    ...            matches.push(els[i]);
    ...        }
    ...    }
    ...    var target = null;
    ...    for (var j=0;j<matches.length;j++){
    ...        if (matches[j].offsetParent !== null){ target = matches[j]; break; }
    ...    }
    ...    if(!target && matches.length > 0){ target = matches[0]; }
    ...    if(!target) return 'NO MATCH FOUND';
    ...    var r = target.getBoundingClientRect();
    ...    var desc = 'tag=' + target.tagName + ' visible=' + (target.offsetParent !== null)
    ...        + ' rect=(' + Math.round(r.top) + ',' + Math.round(r.left) + ',' + Math.round(r.width) + ',' + Math.round(r.height) + ')'
    ...        + ' html=' + target.outerHTML.substring(0, 200);
    ...    target.click();
    ...    return desc;
    Log    🔍 Documents click target: ${info}

Move Invoice To Uploaded Archive
    [Arguments]    ${invoice}
    Create Directory    ${DMS_UPLOADED_ARCHIVE_FOLDER}
    Move File    ${DMS_PENDING_UPLOAD_FOLDER}\\${invoice}    ${DMS_UPLOADED_ARCHIVE_FOLDER}\\${invoice}
    Log    📦 Moved ${invoice} → ${DMS_UPLOADED_ARCHIVE_FOLDER}

Click Batch Upload Icon
    Execute Javascript
    ...    var els = document.querySelectorAll('a, button, span, ins, i, div');
    ...    for(var i=0; i<els.length; i++){
    ...        var cls = (els[i].className || '').toLowerCase();
    ...        var title = (els[i].title || '').toLowerCase();
    ...        if((cls.includes('batch') || cls.includes('scan') || cls.includes('upload')
    ...            || title.includes('batch') || title.includes('scan') || title.includes('upload'))
    ...            && els[i].offsetParent !== null){
    ...            els[i].click();
    ...            break;
    ...        }
    ...    }
    Sleep    3s
    Capture Page Screenshot    after_batch_icon_click.png

Close Right Panel
    Execute Javascript
    ...    var els = document.querySelectorAll('button, span, a, div');
    ...    for(var i=0; i<els.length; i++){
    ...        var txt = els[i].textContent.trim();
    ...        var cls = (els[i].className || '').toLowerCase();
    ...        if((txt === '×' || txt === 'x' || txt === 'X' || cls.includes('close'))
    ...            && els[i].offsetParent !== null){
    ...            els[i].click();
    ...            break;
    ...        }
    ...    }
    Sleep    5s

Double Click Uploading Folder
    Double Click Element    xpath=//span[text()='Uploading'] | //div[text()='Uploading'] | //p[text()='Uploading']
    Sleep    2s

Click Upload Button In Batch Screen
    Execute Javascript
    ...    var els = document.querySelectorAll('button, a, span, div');
    ...    for(var i=0; i<els.length; i++){
    ...        var txt = els[i].textContent.trim().toUpperCase();
    ...        if(txt === 'UPLOAD' && els[i].offsetParent !== null){
    ...            els[i].click();
    ...            break;
    ...        }
    ...    }
    Sleep    2s
    Capture Page Screenshot    after_upload_button_click.png

Type Path And Press Enter In File Dialog
    Sleep    1s
    ${result}=    Run Process    python
    ...    ${CURDIR}${/}file_dialog.py
    ...    ${DMS_PENDING_UPLOAD_FOLDER}
    ...    stdout=PIPE    stderr=PIPE
    Log    STDOUT: ${result.stdout}
    Log    STDERR: ${result.stderr}
    Log To Console    STDOUT: ${result.stdout}
    Log To Console    STDERR: ${result.stderr}
    Sleep    3s
    Capture Page Screenshot    after_path_enter.png

Close Upload Success Popup
    Execute Javascript
    ...    var els = document.querySelectorAll('button, span, a, div');
    ...    for(var i=0; i<els.length; i++){
    ...        var txt = els[i].textContent.trim();
    ...        var cls = (els[i].className || '').toLowerCase();
    ...        if((txt === '×' || txt === 'X' || cls.includes('close'))
    ...            && els[i].offsetParent !== null){
    ...            els[i].click();
    ...            break;
    ...        }
    ...    }
    Sleep    2s
    Press Keys    NONE    SPACE
    Sleep    2s
    Capture Page Screenshot    after_popup_close.png

Select All And Index Files
    Sleep    1s
    Execute Javascript
    ...    var els = document.querySelectorAll('button, a, span, div');
    ...    for(var i=0; i<els.length; i++){
    ...        var txt = els[i].textContent.trim().toUpperCase();
    ...        if(txt === 'SELECT ALL' && els[i].offsetParent !== null){
    ...            els[i].click();
    ...            break;
    ...        }
    ...    }
    Sleep    2s
    Capture Page Screenshot    after_select_all.png
    Execute Javascript
    ...    var checkboxes = document.querySelectorAll('input[type="checkbox"]');
    ...    for(var i=0; i<checkboxes.length; i++){
    ...        var label = document.querySelector('label[for="' + checkboxes[i].id + '"]');
    ...        if(label && label.textContent.trim().toUpperCase().includes('DELETE ON INDEX')){
    ...            if(!checkboxes[i].checked){ checkboxes[i].click(); }
    ...            break;
    ...        }
    ...    }
    Sleep    1s
    Log To Console    ✅ Delete On Index checked
    Capture Page Screenshot    after_delete_checkbox.png
    Execute Javascript
    ...    var els = document.querySelectorAll('button, a, input[type="button"]');
    ...    for(var i=0; i<els.length; i++){
    ...        var txt = els[i].textContent.trim().toUpperCase();
    ...        if(txt === 'INDEX' && els[i].offsetParent !== null){
    ...            els[i].click();
    ...            break;
    ...        }
    ...    }
    Sleep    3s
    Capture Page Screenshot    after_index_click.png

Index Each File
    [Arguments]    ${report_name}
    Sleep    2s
    Execute Javascript
    ...    var selects = document.querySelectorAll('select');
    ...    for(var i=0; i<selects.length; i++){
    ...        var opts = selects[i].options;
    ...        for(var j=0; j<opts.length; j++){
    ...            if(opts[j].text.trim() === 'CVReports'){
    ...                selects[i].selectedIndex = j;
    ...                selects[i].dispatchEvent(new Event('change', {bubbles:true}));
    ...                break;
    ...            }
    ...        }
    ...    }
    Sleep    2s
    Wait Until Element Is Visible
    ...    xpath=//*[contains(text(),'ReportName')]/ancestor::tr[1]//input    10s
    ${input_el}=    Get WebElement
    ...    xpath=//*[contains(text(),'ReportName')]/ancestor::tr[1]//input
    Clear Element Text    ${input_el}
    Input Text    ${input_el}    ${report_name}
    Sleep    1s
    Execute Javascript
    ...    var checkboxes = document.querySelectorAll('input[type="checkbox"]');
    ...    for(var i=0; i<checkboxes.length; i++){
    ...        var label = document.querySelector('label[for="' + checkboxes[i].id + '"]');
    ...        if(label && label.textContent.trim().toUpperCase().includes('RETAIN')){
    ...            if(!checkboxes[i].checked){ checkboxes[i].click(); }
    ...            break;
    ...        }
    ...    }
    Sleep    1s
    Log To Console    ✅ Retain checked for ${report_name}
    Wait Until Element Is Visible    xpath=//button[normalize-space()='Create']    10s
    Execute Javascript
    ...    var els = document.querySelectorAll('button');
    ...    for(var i=0; i<els.length; i++){
    ...        if(els[i].textContent.trim() === 'Create' && els[i].offsetParent !== null){
    ...            els[i].click(); break;
    ...        }
    ...    }
    Sleep    5s
    Capture Page Screenshot    after_create_${report_name}.png
    Log To Console    ✅ Created document: ${report_name}

Move All Files To Uploaded Archive
    Create Directory    ${DMS_UPLOADED_ARCHIVE_FOLDER}
    ${files}=    List Files In Directory    ${DMS_PENDING_UPLOAD_FOLDER}    *.pdf    absolute=True
    Log To Console    📁 Files to move: ${files}
    FOR    ${file}    IN    @{files}
        ${filename}=    Evaluate    __import__('os').path.basename(r'${file}')
        ${dest}=    Set Variable    ${DMS_UPLOADED_ARCHIVE_FOLDER}\\${filename}
        Run Process    cmd    /c    move    /Y    ${file}    ${dest}
        Log To Console    📦 Moved ${filename} → ${DMS_UPLOADED_ARCHIVE_FOLDER}
    END

*** Test Cases ***
Upload Consolidated PDFs To DMS Portal
    # Step 1: Login
    Open Login Page
    Login To Contentverse

    # Step 2: Get year and month folder names
    ${year_folder}=     Get Current Year Folder Name
    ${month_folder}=    Get Current Month Folder Name
    Log    📁 Year folder: ${year_folder}
    Log    📁 Month folder: ${month_folder}

    # Step 3: Expand tree
    Expand Material Inward Process
    Expand MIP Docs

    # Step 4a: Create YEAR folder under MIP Docs if missing
    Create Folder If Not Exists    xpath=//*[contains(text(),'MIP Docs')]    ${year_folder}

    # Step 4b: Open year folder
    Open Folder By Name    ${year_folder}

    # Step 4c: Create MONTH folder inside year folder if missing
    Create Subfolder If Not Exists
    ...    xpath=//*[text()='${year_folder}']    ${year_folder}    ${month_folder}

    # Step 5: Open month folder, then bulk-upload everything pending
    Open Subfolder By Name    ${year_folder}    ${month_folder}

    ${pending_files}=    Get List Of Pending Upload Files
    ${file_count}=    Get Length    ${pending_files}
    Log To Console    📄 Pending files found: ${file_count}

    IF    ${file_count} == 0
        Log To Console    ℹ️ No pending files in ${DMS_PENDING_UPLOAD_FOLDER} — nothing to upload.
        Log To Console    RESULT:DMS_UPLOAD_STATUS:SUCCESS
    ELSE
        Click Batch Upload Icon
        Close Right Panel
        Double Click Uploading Folder
        Click Upload Button In Batch Screen
        Type Path And Press Enter In File Dialog
        Close Upload Success Popup

        Select All And Index Files
        ${pending_files}=    Get List Of Pending Upload Files
        FOR    ${invoice}    IN    @{pending_files}
            ${report_name}=    Evaluate    __import__('os').path.splitext('${invoice}')[0]
            Index Each File    ${report_name}
        END
        Move All Files To Uploaded Archive
        Log To Console    RESULT:DMS_UPLOAD_STATUS:SUCCESS
    END
