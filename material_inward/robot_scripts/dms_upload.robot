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
# v16: where each uploaded document's Contentverse sharing link gets
# appended -- services/dms_links_import.py reads this back into the app's
# own DB (dms_document_links table) afterward. Loaded from .env below.
${DMS_LINKS_EXCEL_PATH}   ${EMPTY}

*** Keywords ***

Load Environment Variables
    ${env_path}=    Join Path    ${EXECDIR}    .env
    Evaluate    __import__('dotenv').load_dotenv(r'''${env_path}''')
    ${USERNAME}=    Evaluate    __import__('os').getenv('CV_USERNAME')
    ${PASSWORD}=    Evaluate    __import__('os').getenv('CV_PASSWORD')
    ${LINKS_PATH}=    Evaluate    __import__('os').getenv('DMS_LINKS_EXCEL_PATH')
    Should Not Be Empty    ${USERNAME}
    Should Not Be Empty    ${PASSWORD}
    Should Not Be Empty    ${LINKS_PATH}
    Set Suite Variable    ${USERNAME}
    Set Suite Variable    ${PASSWORD}
    Set Suite Variable    ${DMS_LINKS_EXCEL_PATH}    ${LINKS_PATH}
    # FIX: DMS_PENDING_UPLOAD_FOLDER/DMS_UPLOADED_ARCHIVE_FOLDER were pure
    # hardcoded literals in the *** Variables *** table above, completely
    # disconnected from config.DMS_STAGING_FOLDER (the single source of
    # truth every Python-side path -- dms_upload_runner.py, folder_watcher.py,
    # the staging/consolidation step -- already reads from .env). Two
    # independent copies of the same folder path is exactly how this file
    # can silently drift out of sync with where the app is actually staging
    # files, if .env's DMS_STAGING_FOLDER is ever changed without also
    # hand-editing this file. Now mirrors the DMS_LINKS_EXCEL_PATH pattern
    # immediately above: .env wins when set, hardcoded literal is only the
    # fallback if DMS_STAGING_FOLDER is missing from .env entirely.
    ${STAGING_FOLDER}=    Evaluate
    ...    __import__('os').getenv('DMS_STAGING_FOLDER', r'''${DMS_PENDING_UPLOAD_FOLDER}''')
    Set Suite Variable    ${DMS_PENDING_UPLOAD_FOLDER}    ${STAGING_FOLDER}
    Set Suite Variable    ${DMS_UPLOADED_ARCHIVE_FOLDER}    ${STAGING_FOLDER}\\uploaded
    Create Directory    ${DMS_UPLOADED_ARCHIVE_FOLDER}
    # Tracks every Contentverse document link already claimed by a file
    # earlier in this run -- see Copy Generated Link And Close Popup's
    # anti-mislink fix below.
    @{SEEN_DOC_LINKS}=    Create List
    Set Suite Variable    @{SEEN_DOC_LINKS}

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
    # v19: ported from a colleague's working copy of this bot after ours hit
    # a real failure at the 2026 year rollover (empty "2026" tree node, no
    # expand icon yet -> the old hard Click Element aborted the whole batch).
    # Their version is more robust in three ways, not just error-tolerant:
    # (a) waits for the folder link itself to be rendered before touching
    # it, (b) targets the expand icon with a tag-agnostic
    # preceding-sibling::*[1] instead of assuming it's always an <ins>
    # element (Contentverse renders none at all for a genuinely childless
    # node, and may use a different tag otherwise), and (c) falls back to a
    # JS DOM search across the common jsTree toggle selectors if the plain
    # Selenium click can't find/click it. None of these hard-fail if
    # there's truly nothing to expand -- they just no-op -- but they have a
    # real chance of actually succeeding first instead of always giving up.
    Wait Until Element Is Visible
    ...    xpath=//a[text()='${folder_name}']    10s

    ${already_open}=    Run Keyword And Return Status
    ...    Page Should Contain Element
    ...    xpath=//a[text()='${folder_name}']/ancestor::li[1][contains(@class,'jstree-open')]

    IF    not ${already_open}
        ${node_element}=    Get WebElement
        ...    xpath=//a[text()='${folder_name}']
        Mouse Over    ${node_element}
        Sleep    1s

        ${arrow_clicked}=    Run Keyword And Return Status
        ...    Click Element
        ...    xpath=//a[text()='${folder_name}']/preceding-sibling::*[1]

        IF    not ${arrow_clicked}
            Execute Javascript
            ...    var els = document.querySelectorAll('a');
            ...    for(var i=0; i<els.length; i++){
            ...        if(els[i].textContent.trim() === '${folder_name}'){
            ...            var li = els[i].closest('li');
            ...            if(li){
            ...                var arrow = li.querySelector('.jstree-ocl, ins, i');
            ...                if(arrow){ arrow.click(); }
            ...            }
            ...            break;
            ...        }
            ...    }
        END
        Sleep    2s
    END

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

# ── v16: document-link generation ──────────────────────────────────────────
# Ported from the originally dropped dms_bot/dms_bot.robot (Send To >
# Generate Document link > copy > save to Excel). dms_upload.robot's own
# upload/index steps above never generated a link at all until now.

Click View On Document Created Popup
    ${popup_found}=    Run Keyword And Return Status
    ...    Wait Until Element Is Visible
    ...    xpath=//*[contains(text(),'Document created successfully')]    10s

    IF    ${popup_found}
        Log    🟢 Document created popup found — clicking Navigate
        Click Element    xpath=//button[normalize-space()='Navigate']
        Sleep    10s
    ELSE
        Log    ⚠️ Document created popup not found
    END

Select Document Row By Name
    [Arguments]    ${report_name}
    Wait Until Element Is Visible
    ...    xpath=//*[normalize-space(text())='${report_name}']    10s
    Sleep    1s

    ${status}=    Execute Javascript
    ...    var target = null;
    ...    var candidates = document.querySelectorAll('a, span, td, div');
    ...    for (var i=0; i<candidates.length; i++){
    ...        if (candidates[i].textContent.trim() === '${report_name}'){
    ...            target = candidates[i];
    ...            break;
    ...        }
    ...    }
    ...    if(!target) return 'ROW_NOT_FOUND';
    ...    var row = target.closest('tr');
    ...    if(!row) return 'TR_NOT_FOUND';
    ...    var checkbox = row.querySelector('input[type="checkbox"]');
    ...    if(!checkbox){
    ...        var firstCell = row.querySelector('td');
    ...        if(firstCell){ checkbox = firstCell.querySelector('input, span, div'); }
    ...    }
    ...    if(!checkbox) return 'CHECKBOX_NOT_FOUND';
    ...    if(checkbox.tagName === 'INPUT' && !checkbox.checked){
    ...        checkbox.click();
    ...    } else if (checkbox.tagName !== 'INPUT') {
    ...        checkbox.click();
    ...    }
    ...    return 'OK';

    Log To Console    ☑️ Select row status for '${report_name}': ${status}
    Should Be Equal As Strings    ${status}    OK
    ...    msg=Could not select checkbox for document '${report_name}' — got status: ${status}
    Sleep    1s

Deselect Document Row By Name
    [Arguments]    ${report_name}
    ${status}=    Execute Javascript
    ...    var target = null;
    ...    var candidates = document.querySelectorAll('a, span, td, div');
    ...    for (var i=0; i<candidates.length; i++){
    ...        if (candidates[i].textContent.trim() === '${report_name}'){
    ...            target = candidates[i];
    ...            break;
    ...        }
    ...    }
    ...    if(!target) return 'ROW_NOT_FOUND';
    ...    var row = target.closest('tr');
    ...    if(!row) return 'TR_NOT_FOUND';
    ...    var checkbox = row.querySelector('input[type="checkbox"]');
    ...    if(!checkbox){
    ...        var firstCell = row.querySelector('td');
    ...        if(firstCell){ checkbox = firstCell.querySelector('input, span, div'); }
    ...    }
    ...    if(!checkbox) return 'CHECKBOX_NOT_FOUND';
    ...    if(checkbox.tagName === 'INPUT' && checkbox.checked){
    ...        checkbox.click();
    ...    } else if (checkbox.tagName !== 'INPUT') {
    ...        checkbox.click();
    ...    }
    ...    return 'OK';

    Log To Console    ☐ Deselect row status for '${report_name}': ${status}
    Sleep    1s

Close Any Open Context Menu
    Execute Javascript
    ...    document.body.click();
    Sleep    1s
    Press Keys    NONE    ESCAPE
    Sleep    1s

Right Click Document Row And Open Send To
    [Arguments]    ${report_name}

    # Clean up any leftover menu from the previous file before starting
    Close Any Open Context Menu

    ${locator}=    Set Variable
    ...    xpath=//*[normalize-space(text())='${report_name}']

    Wait Until Element Is Visible    ${locator}    10s
    Scroll Element Into View    ${locator}
    ${row_element}=    Get WebElement    ${locator}

    # Attempt 1: Selenium's native context menu (real right-click)
    Open Context Menu    ${row_element}
    Sleep    2s

    # Tag the visible "Send To" element with a unique id we control
    ${tag_result}=    Execute Javascript
    ...    var candidates = document.querySelectorAll('*');
    ...    var target = null;
    ...    for (var i=0; i<candidates.length; i++){
    ...        var txt = candidates[i].textContent.trim();
    ...        if (txt === 'Send To' && candidates[i].offsetParent !== null){
    ...            target = candidates[i];
    ...            break;
    ...        }
    ...    }
    ...    if(!target) return 'NOT_FOUND';
    ...    var el = target;
    ...    var safeguard = 0;
    ...    while (el && safeguard < 6){
    ...        var rect = el.getBoundingClientRect();
    ...        if (rect.width > 0 && rect.height > 0){
    ...            break;
    ...        }
    ...        el = el.parentElement;
    ...        safeguard++;
    ...    }
    ...    if(!el) return 'NOT_FOUND';
    ...    el.id = 'cv_send_to_target';
    ...    return 'TAGGED';

    Log To Console    📤 Send To tag result: ${tag_result}

    # Attempt 2 (fallback): if native right-click didn't open the menu, dispatch contextmenu event via JS
    IF    '${tag_result}' == 'NOT_FOUND'
        Log To Console    ⚠️ Native right-click didn't reveal 'Send To', retrying with JS contextmenu event
        Execute Javascript
        ...    var target = null;
        ...    var candidates = document.querySelectorAll('*');
        ...    for (var i=0; i<candidates.length; i++){
        ...        if (candidates[i].textContent.trim() === '${report_name}' && candidates[i].offsetParent !== null){
        ...            target = candidates[i];
        ...        }
        ...    }
        ...    if(target){
        ...        var rect = target.getBoundingClientRect();
        ...        var evt = new MouseEvent('contextmenu', {
        ...            bubbles: true, cancelable: true, view: window,
        ...            clientX: rect.left + 5, clientY: rect.top + 5, button: 2
        ...        });
        ...        target.dispatchEvent(evt);
        ...    }
        Sleep    2s

        ${tag_result}=    Execute Javascript
        ...    var candidates = document.querySelectorAll('*');
        ...    var target = null;
        ...    for (var i=0; i<candidates.length; i++){
        ...        var txt = candidates[i].textContent.trim();
        ...        if (txt === 'Send To' && candidates[i].offsetParent !== null){
        ...            target = candidates[i];
        ...            break;
        ...        }
        ...    }
        ...    if(!target) return 'NOT_FOUND';
        ...    var el = target;
        ...    var safeguard = 0;
        ...    while (el && safeguard < 6){
        ...        var rect = el.getBoundingClientRect();
        ...        if (rect.width > 0 && rect.height > 0){
        ...            break;
        ...        }
        ...        el = el.parentElement;
        ...        safeguard++;
        ...    }
        ...    if(!el) return 'NOT_FOUND';
        ...    el.id = 'cv_send_to_target';
        ...    return 'TAGGED';

        Log To Console    📤 Send To tag result (retry): ${tag_result}
    END

    Should Be Equal As Strings    ${tag_result}    TAGGED
    ...    msg=Could not find a visible 'Send To' menu item in the context menu for '${report_name}'

    # Skip real mouse hover entirely (Edge WebDriver DPI/coordinate bug causes
    # MoveTargetOutOfBoundsException regardless of element size/position).
    # Instead, find "Generate Document link" directly in the DOM (even while
    # hidden) and force every ancestor's CSS to make it visible — no hover needed.
    ${force_result}=    Execute Javascript
    ...    var candidates = document.querySelectorAll('*');
    ...    var genLink = null;
    ...    for (var i=0; i<candidates.length; i++){
    ...        if (candidates[i].textContent.trim() === 'Generate Document link'
    ...            && candidates[i].children.length === 0){
    ...            genLink = candidates[i];
    ...            break;
    ...        }
    ...    }
    ...    if(!genLink) return 'GENLINK_NOT_FOUND';
    ...    var el = genLink;
    ...    var count = 0;
    ...    while (el && count < 8){
    ...        el.style.setProperty('display', 'block', 'important');
    ...        el.style.setProperty('visibility', 'visible', 'important');
    ...        el.style.setProperty('opacity', '1', 'important');
    ...        el.style.setProperty('max-height', 'none', 'important');
    ...        el.classList.remove('hidden');
    ...        el.classList.remove('hide');
    ...        el.classList.remove('collapsed');
    ...        el = el.parentElement;
    ...        count++;
    ...    }
    ...    genLink.id = 'cv_gen_doc_link_target';
    ...    return 'FORCED';

    Log To Console    🔓 Force-show Generate Document link result: ${force_result}

    IF    '${force_result}' == 'GENLINK_NOT_FOUND'
        Capture Page Screenshot    send_to_failure_${report_name}.png
        ${html}=    Execute Javascript    return document.body.innerHTML;
        Create File    ${EXECDIR}/send_to_failure_${report_name}.html    ${html}
        Log    ⚠️ Dumped page HTML/screenshot for debugging: send_to_failure_${report_name}
    END

    Should Be Equal As Strings    ${force_result}    FORCED
    ...    msg=Could not find/force-show 'Generate Document link' for '${report_name}'
    Sleep    1s

Click Generate Document Link
    ${click_status}=    Execute Javascript
    ...    var el = document.getElementById('cv_gen_doc_link_target');
    ...    if(!el) return 'NOT_FOUND';
    ...    el.click();
    ...    return 'CLICKED';
    Log To Console    🔗 Generate Document link click status: ${click_status}
    Should Be Equal As Strings    ${click_status}    CLICKED
    ...    msg=Could not click 'Generate Document link'
    Sleep    2s

Copy Generated Link And Close Popup
    Wait Until Element Is Visible
    ...    xpath=//*[contains(text(),'openDocumentLogin')] | //input[contains(@value,'openDocumentLogin')]    10s

    ${all_links}=    Execute Javascript
    ...    var results = [];
    ...    var inputs = document.querySelectorAll('input, textarea');
    ...    for (var i=0; i<inputs.length; i++){
    ...        if (inputs[i].value && inputs[i].value.indexOf('openDocumentLogin') > -1){
    ...            results.push(inputs[i].value);
    ...        }
    ...    }
    ...    var els = document.querySelectorAll('div, span, p, td');
    ...    for (var j=0; j<els.length; j++){
    ...        if (els[j].textContent && els[j].textContent.indexOf('openDocumentLogin') > -1){
    ...            results.push(els[j].textContent.trim());
    ...        }
    ...    }
    ...    return results;

    # FIX: production confirmed a real mislink -- h38's generated link
    # opened a DIFFERENT file's invoice (from the same batch run) instead
    # of its own. Root cause: this used to grab "the first element on the
    # WHOLE page containing openDocumentLogin", with no scoping to the
    # popup that was just opened for THIS file. When Contentverse hides
    # (rather than removes) a previous file's link element between
    # iterations of Process All Document Links, that stale element can
    # still match first, silently returning an earlier file's link as if
    # it were this file's. Fixed without touching Contentverse's own DOM
    # (safer than trying to delete/blank stale nodes): collect every
    # matching value on the page, then only accept the first one NOT
    # already claimed earlier in this same run (tracked in the suite-level
    # @{SEEN_DOC_LINKS} list, initialized in Load Environment Variables).
    ${doc_link}=    Set Variable    NOT_FOUND
    FOR    ${candidate}    IN    @{all_links}
        ${already_seen}=    Run Keyword And Return Status
        ...    List Should Contain Value    ${SEEN_DOC_LINKS}    ${candidate}
        IF    not ${already_seen}
            ${doc_link}=    Set Variable    ${candidate}
            Append To List    ${SEEN_DOC_LINKS}    ${candidate}
            Exit For Loop
        END
    END
    Should Not Be Equal As Strings    ${doc_link}    NOT_FOUND
    ...    msg=Could not find a NEW (not already claimed by an earlier file this run) document link — every openDocumentLogin value on the page was already used for a previous file.

    Log To Console    🔗 Generated Link: ${doc_link}

    ${ok_clicked}=    Run Keyword And Return Status
    ...    Click Element    xpath=//button[normalize-space()='Ok']

    IF    not ${ok_clicked}
        Log To Console    ⚠️ Native click on 'Ok' failed — trying JS click
        Execute Javascript
        ...    var buttons = document.querySelectorAll('button');
        ...    for (var i=0; i<buttons.length; i++){
        ...        if (buttons[i].textContent.trim() === 'Ok' && buttons[i].offsetParent !== null){
        ...            buttons[i].click();
        ...            break;
        ...        }
        ...    }
    END
    Sleep    2s

    [Return]    ${doc_link}

Save File Name And Link To Excel
    [Arguments]    ${file_name}    ${doc_link}
    ${result}=    Run Process    python
    ...    ${CURDIR}${/}dms_excel_writer.py
    ...    ${file_name}    ${doc_link}    ${DMS_LINKS_EXCEL_PATH}
    ...    stdout=PIPE    stderr=PIPE
    Log    STDOUT: ${result.stdout}
    Log    STDERR: ${result.stderr}
    Log To Console    📊 Saved to Excel: ${file_name} → ${doc_link}

Generate And Save Document Link
    [Arguments]    ${excel_file_name}
    # FIX: ${excel_file_name} carries the h{id}_ prefix (guaranteed-unique,
    # used only for matching this row back to a history record in
    # services/dms_links_import.py). Everything that has to match what's
    # actually indexed/visible inside Contentverse -- the row lookup, the
    # right-click target -- must use the STRIPPED name instead, since that
    # (not the prefixed one) is what Index Each File typed into
    # Contentverse's ReportName field. Only the Excel write uses the full,
    # prefixed name.
    ${display_name}=    Evaluate    __import__('re').sub(r'^h\d+_', '', '''${excel_file_name}''')
    Select Document Row By Name    ${display_name}
    Right Click Document Row And Open Send To    ${display_name}
    Click Generate Document Link
    ${doc_link}=    Copy Generated Link And Close Popup
    Save File Name And Link To Excel    ${excel_file_name}    ${doc_link}
    Deselect Document Row By Name    ${display_name}

Process All Document Links
    [Arguments]    @{report_names}
    FOR    ${report_name}    IN    @{report_names}
        Log To Console    ▶️ Processing link for: ${report_name}
        Generate And Save Document Link    ${report_name}
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

    # Step 4a: Create YEAR folder under MIP Docs if missing.
    # v19: switched from the old Create Folder If Not Exists (checks for ANY
    # element anywhere on the page with this exact text) to the
    # MIP-Docs-scoped Create Subfolder If Not Exists -- same fix source as
    # Expand Folder Node above. The unscoped version is a latent risk any
    # time the current year (or any folder name) could also appear elsewhere
    # on the page -- breadcrumb, another folder, a label -- since it could
    # click the wrong element and desync the page from what Expand Folder
    # Node expects next. Scoping to MIP Docs as the parent removes that
    # ambiguity, matching how the month folder one step below has always
    # been handled.
    Create Subfolder If Not Exists
    ...    xpath=//*[contains(text(),'MIP Docs')]    MIP Docs    ${year_folder}

    # Step 4b: Open year folder (scoped to MIP Docs for the same reason)
    Open Subfolder By Name    MIP Docs    ${year_folder}

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

        # Build report names BEFORE moving files out of the staging folder --
        # Move All Files To Uploaded Archive below empties this directory.
        # ${report_names} stores the FULL h{id}_-prefixed name (used later
        # for the Excel/DB-matching write in Generate And Save Document
        # Link). ${display_name} -- the prefix stripped off -- is what
        # actually gets typed into Contentverse's ReportName field here,
        # so the client only ever sees invoice_vendorcode_date.
        @{report_names}=    Create List
        FOR    ${invoice}    IN    @{pending_files}
            ${report_name}=    Evaluate    __import__('os').path.splitext('${invoice}')[0]
            ${display_name}=    Evaluate    __import__('re').sub(r'^h\d+_', '', '''${report_name}''')
            Index Each File    ${display_name}
            Append To List    ${report_names}    ${report_name}
        END
        Move All Files To Uploaded Archive

        # Popup appears once, after all files are indexed -> click Navigate
        # to return to folder view before generating links.
        Click View On Document Created Popup

        # v16: for every uploaded file, generate its Contentverse sharing
        # link and append it to DMS_LINKS_EXCEL_PATH (services/
        # dms_links_import.py picks these rows up afterward).
        Process All Document Links    @{report_names}

        Log To Console    RESULT:DMS_UPLOAD_STATUS:SUCCESS
    END
