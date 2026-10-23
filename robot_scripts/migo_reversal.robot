*** Settings ***
Documentation     MIGO Reversal Bot — Cancellation (A03), single pass, + Post
...
...               1. Login to SAP (credentials: per-user override if this
...                  job was submitted by an LDAP user, else the shared
...                  .env account -- same pattern as migo_103.robot/
...                  migo_105.robot/miro.robot, since a reversal is an
...                  attributable SAP posting, not a read-only lookup).
...               2. Run Transaction MIGO, action A03 (Cancellation), enter
...                  Material Document number, enter.
...               3. Header text is directly editable on this same A03
...                  screen -- prefix it with "0" if non-empty
...                  (123 -> 0123), enter. No separate A12 pass needed.
...               4. Tick "Item OK" for the current line, then advance with
...                  the Next Item button (btnOK_NEXT_ITEM) and tick again --
...                  repeats until the line number stops advancing, so 1
...                  item iterates once, 10 items iterate ten times, no
...                  hardcoded row count needed.
...               5. Check (tbar[1]/btn[7]) then Post (tbar[1]/btn[23]) --
...                  once, at the end, not mid-flow. CONFIRMED 2026-08-21:
...                  Post is LIVE, not a dry run -- clicking Reverse from
...                  the app performs a real, final reversal in SAP, same
...                  as MIGO 103/105/MIRO already do. (The version this was
...                  based on had a documentation comment claiming Post was
...                  disabled/dry-run -- it was not; the code always
...                  clicked it. That mismatch is fixed here, not the
...                  behavior.)
...               6. Status bar message captured and logged after every
...                  step (matdoc entry, header text, post) so failures
...                  ("not present" / "no cancellation" / "not released")
...                  are always visible in the log. Read via the raw
...                  session object (session.findById('wnd[0]/sbar').Text)
...                  rather than the Get Value keyword, which was silently
...                  failing on this element type and returning "".
...               7. FIX (2026-08-21): the final result used to be built by
...                  hand-gluing SAP's raw status-bar text into a
...                  {"status":...} string literal -- the same bug class
...                  that corrupted migo_103.robot's data earlier this
...                  session (a literal quote character, e.g. an inch mark,
...                  inside a SAP message breaks hand-built "JSON" that was
...                  never actually serialized as JSON). Now built via
...                  Evaluate + json.dumps, the same fix already applied to
...                  migo_103.robot.
...               8. FIX (2026-08-21): a failure message on the FINAL post
...                  status was detected (Check For Failure Message) but
...                  never actually changed the result -- the keyword
...                  always returned status:"COMPLETED" regardless, even
...                  when SAP's own status bar said e.g. "not released" or
...                  "already reversed". Now returns status:"FAILED" (with
...                  the SAP message attached) when that check trips,
...                  mirroring how migo_103.robot/gate_in.robot already
...                  distinguish a real completion from one that only
...                  LOOKS like it reached the end of the script.
...               9. FIX (2026-08-21): the reversal's own new SAP document
...                  number was never extracted from the post status text --
...                  only the raw status string was returned. Added the same
...                  digit-pattern extraction migo_103.robot already uses,
...                  so the new reversal document number can actually be
...                  stored (schema_migration_v27's
...                  migo_103_reversal_doc_number column).
...
...               MULTI-BOT COORDINATION (integrated, same pattern as
...               po_fetch.robot/migo_103.robot): this bot may run
...               concurrently with other bots against the same SAP
...               production system. Each bot increments a shared counter
...               file on start and decrements it on finish. SAP processes
...               are only actually killed when the counter reaches 0 --
...               i.e. when EVERY bot that registered has finished. A bot
...               finishing early just closes its own session tab (/nex)
...               and leaves the shared processes running for the others.
...               The login+session-identification sequence is serialized
...               under the same lock as the counter, so two bots can never
...               open connections at the same instant and race each other
...               for "which connection is mine" -- see Connect To Sap
...               Session below.
...
...               Counter file: C:\temp\sap_bot_refcount.txt
...               Lock (mutex) dir: C:\temp\sap_bot_refcount.lock
Library           SapGuiLibrary
Library           Process
Library           OperatingSystem
Library           String
Library           Collections
Library           ${CURDIR}/sap_bot_coordination.py

*** Variables ***
# --- Business input ----------------------------------------------------------
${MATERIAL_DOC_NUMBER}          ${EMPTY}


# --- Live SAP GUI Scripting session object, bound after login ---------------
${session}                 ${NONE}
${PATH_TREE_CLOSE_BTN}     wnd[0]/shellcont/shell/shellcont[1]/shell[0]

# --- Multi-bot coordination (MIGRATED 2026-09-03) --------------------------
# Login/close now go through sap_bot_coordination.py -- this file no
# longer keeps its own separate refcount file path constants.

*** Test Cases ***
Execute MIGO Reversal
    [Setup]    Initialize SAP And Login
    ${result}=    Reverse Material Document
    Log To Console    RESULT:REVERSAL_STATUS:${result}
    Sleep    5s
    [Teardown]    Close SAP Session

*** Keywords ***
Initialize SAP And Login
    # FIX (2026-08-21): credentials used to be plain hardcoded local
    # variables meant for CLI -v args (matching the friend's standalone
    # test file). Swapped for the same .env + per-user-override pattern
    # migo_103.robot/migo_105.robot/miro.robot already use -- a reversal
    # is an attributable SAP posting, same class of action as those, not
    # a shared read-only lookup like po_fetch.
    Evaluate    __import__('dotenv').load_dotenv(__import__('os').getenv('DOTENV_PATH', '.env'), override=True)
    ${CLIENT}=      Evaluate    __import__('os').getenv('SAP_CLIENT')
    ${CONN_NAME}=   Evaluate    __import__('os').getenv('SAP_CONNECTION_NAME')
    ${LOGON_PATH}=  Evaluate    __import__('os').getenv('SAP_LOGON_PATH')

    ${USER_OVERRIDE}=    Evaluate    __import__('os').getenv('SAP_USER_OVERRIDE', '')
    ${PASS_OVERRIDE}=    Evaluate    __import__('os').getenv('SAP_PASS_OVERRIDE', '')
    IF    $USER_OVERRIDE != '' and $PASS_OVERRIDE != ''
        ${SAP_USERNAME}=    Set Variable    ${USER_OVERRIDE}
        ${SAP_PASSWORD}=    Set Variable    ${PASS_OVERRIDE}
        Log To Console    SAP LOGIN: using per-user credential for ${SAP_USERNAME}
    ELSE
        ${SAP_USERNAME}=    Evaluate    __import__('os').getenv('SAP_USERNAME')
        ${SAP_PASSWORD}=    Evaluate    __import__('os').getenv('SAP_PASSWORD')
        Log To Console    SAP LOGIN: using shared .env credential (${SAP_USERNAME})
    END

    ${session}=    Coordinated SAP Login    ${LOGON_PATH}    ${CONN_NAME}    ${CLIENT}    ${SAP_USERNAME}    ${SAP_PASSWORD}
    Set Suite Variable    ${session}


Get Firstline Path
    # The Action combo box subscreen can render as either :0003 or :0007
    # depending on MIGO's current internal state -- a hardcoded path
    # silently fails to find the element whenever the screen happens to be
    # in the other state. Probe both, use whichever actually exists now.
    ${path_0003}=    Set Variable    wnd[0]/usr/ssubSUB_MAIN_CARRIER:SAPLMIGO:0003/subSUB_FIRSTLINE:SAPLMIGO:0011
    ${path_0007}=    Set Variable    wnd[0]/usr/ssubSUB_MAIN_CARRIER:SAPLMIGO:0007/subSUB_FIRSTLINE:SAPLMIGO:0011

    ${found_0003}=    Run Keyword And Return Status    Element Should Be Present    ${path_0003}
    IF    ${found_0003}
        RETURN    ${path_0003}
    END

    ${found_0007}=    Run Keyword And Return Status    Element Should Be Present    ${path_0007}
    IF    ${found_0007}
        RETURN    ${path_0007}
    END

    Fail    Neither SAPLMIGO:0003 nor SAPLMIGO:0007 firstline subscreen found -- MIGO screen state unrecognized.


Get Screen Number From Path
    [Arguments]    ${path}
    # Extracts the leading SAPLMIGO:XXXX screen number so header/item-detail
    # paths (which share the same screen number as the firstline path) can
    # be built dynamically instead of hardcoding :0003.
    @{matches}=    Get Regexp Matches    ${path}    SAPLMIGO:(\\d+)    1
    RETURN    ${matches}[0]


Dismiss Overview Tree Sidebar
    # A document-overview tree sidebar can pop up on the MIGO screen and
    # block element access underneath it. Defensive by construction -- no-op
    # if the sidebar never appears.
    ${hide_overview_status}=    Run Keyword And Return Status
    ...    SapGuiLibrary.Element Should Be Present    wnd[0]/tbar[1]/btn[21]
    IF    ${hide_overview_status}
        Evaluate    $session.findById('wnd[0]').maximize()
        Evaluate    $session.findById('wnd[0]/tbar[1]/btn[21]').press()
        Sleep    2s
    END

    ${tree_close_status}=    Run Keyword And Return Status
    ...    SapGuiLibrary.Element Should Be Present    ${PATH_TREE_CLOSE_BTN}
    IF    ${tree_close_status}
        Evaluate    $session.findById('${PATH_TREE_CLOSE_BTN}').pressButton('OK_CLOSE')
        Sleep    2s
    END


Reverse Material Document
    Run Transaction    MIGO
    Sleep    3s
    Dismiss Any Popup
    Dismiss Overview Tree Sidebar

    # ------------------------------------------------------------------
    # STEP 1: Action A03 (Cancellation) + Material Document number
    # ------------------------------------------------------------------
    ${firstline}=    Get Firstline Path

    # setattr() used instead of a direct `.key = 'A03'` assignment because
    # Robot's Evaluate runs expressions through Python eval(), which cannot
    # execute assignment statements -- setattr() is a function call, so
    # it's a valid expression instead.
    Evaluate    setattr($session.findById('${firstline}/cmbGODYNPRO-ACTION'), 'key', 'A03')
    Sleep    0.5s

    # Re-resolve firstline path -- setting ACTION can itself switch the
    # subscreen between :0003/:0007, so MAT_DOC's container may differ
    # from what ACTION was just found under.
    ${firstline}=    Get Firstline Path
    ${matdoc_field}=    Set Variable
    ...    ${firstline}/subSUB_FIRSTLINE_REFDOC:SAPLMIGO:2010/txtGODYNPRO-MAT_DOC

    Set Focus    ${matdoc_field}
    Safe Input Text    ${matdoc_field}    ${MATERIAL_DOC_NUMBER}
    Send VKey    0
    Sleep    3s
    Dismiss Any Popup

    ${status_1}=    Read Status Bar With Retry
    Log To Console    STATUS AFTER MATDOC ENTRY (A03): ${status_1}
    ${failed_1}=    Check For Failure Message    ${status_1}
    IF    ${failed_1}
        ${result_json}=    Evaluate
        ...    __import__('json').dumps({"status": "FAILED", "step": "matdoc_entry", "message": $status_1})
        RETURN    ${result_json}
    END

    # ------------------------------------------------------------------
    # STEP 2: Header text — prefix with "0" if non-empty (123 -> 0123).
    # Editable directly on this A03 screen -- no separate A12 pass needed.
    # ------------------------------------------------------------------
    ${screen_no}=    Get Screen Number From Path    ${firstline}
    ${header_field}=    Set Variable
    ...    wnd[0]/usr/ssubSUB_MAIN_CARRIER:SAPLMIGO:${screen_no}/subSUB_HEADER:SAPLMIGO:0101/subSUB_HEADER:SAPLMIGO:0100/tabsTS_GOHEAD/tabpOK_GOHEAD_GENERAL/ssubSUB_TS_GOHEAD_GENERAL:SAPLMIGO:0110/txtGOHEAD-BKTXT

    ${existing_header_raw}=    Run Keyword And Ignore Error    Get Value    ${header_field}
    ${existing_header_text}=    Set Variable    ${EMPTY}
    IF    '${existing_header_raw}[0]' == 'PASS'
        ${existing_header_text}=    Clean Value    ${existing_header_raw}[1]
    END
    Log To Console    EXISTING HEADER TEXT (before prefix): "${existing_header_text}"

    ${status_2}=    Set Variable    ${EMPTY}
    IF    '${existing_header_text}' == '${EMPTY}'
        Log To Console    Header text is blank -- nothing to prefix, skipping this step.
    ELSE
        ${padded_header_text}=    Catenate    SEPARATOR=    0    ${existing_header_text}
        Safe Input Text    ${header_field}    ${padded_header_text}
        Set Focus    ${header_field}
        ${caret_pos}=    Get Length    ${padded_header_text}
        Evaluate    setattr($session.findById('${header_field}'), 'caretPosition', ${caret_pos})
        Send VKey    0
        Sleep    2s
        Dismiss Any Popup

        ${status_2}=    Read Status Bar With Retry
        Log To Console    STATUS AFTER HEADER TEXT ENTRY: ${status_2}
        Check For Failure Message    ${status_2}
    END

    # ------------------------------------------------------------------
    # STEP 3: Tick "Item OK" for every line — starts on whichever line is
    # already active (SAP defaults to line 1), ticks it, then advances
    # with the Next Item button and ticks again. Stops the moment the
    # line number stops changing after Next is pressed -- so 1 item
    # iterates once, 10 items iterate ten times, no hardcoded row count.
    # ------------------------------------------------------------------
    ${screen_no}=    Get Screen Number From Path    ${firstline}
    ${det_base}=    Set Variable
    ...    wnd[0]/usr/ssubSUB_MAIN_CARRIER:SAPLMIGO:${screen_no}/subSUB_ITEMDETAIL:SAPLMIGO:0301/subSUB_DETAIL:SAPLMIGO:0300

    ${items_selected}=    Tick All Item OK Lines    ${det_base}
    Log To Console    Total items ticked "Item OK": ${items_selected}

    # ------------------------------------------------------------------
    # STEP 4: Check, then Post — once, after every item is ticked.
    # CONFIRMED 2026-08-21: Post is intentionally LIVE (see Documentation
    # note 5 above) -- this is a real, final reversal in SAP.
    # ------------------------------------------------------------------
    Sleep    1s
    Dismiss Any Popup
    Click Element    wnd[0]/tbar[1]/btn[7]
    Sleep    1s
    ${check_popup_message}=    Capture And Dismiss Popup Message
    Log To Console    CHECK POPUP MESSAGE: "${check_popup_message}"
    Click Element    wnd[0]/tbar[1]/btn[23]
    # FIX (2026-08-21): same status-bar-read race condition already
    # confirmed in production for migo_105.robot (history_id=340, job
    # 1310) -- SAP can genuinely take longer than a ~5s poll window to
    # finish posting under real load, making a real success look like a
    # failure. Widened the wait before the first read here specifically
    # (Read Status Bar With Retry's own retry window is widened below too).
    Sleep    5s
    Dismiss Any Popup

    ${status_3}=    Read Status Bar With Retry
    Log To Console    STATUS AFTER POSTING: ${status_3}

    # The status bar can come back empty for a "Check" result even though
    # a popup (e.g. "document already reversed") did fire -- prefer the
    # popup text in that case, since it's often the only place the actual
    # message showed up.
    IF    '${status_3}' == '${EMPTY}' and '${check_popup_message}' != '${EMPTY}'
        ${status_3}=    Set Variable    ${check_popup_message}
    END
    ${failed_3}=    Check For Failure Message    ${status_3}

    # ADDED (2026-08-21): extract the new reversal document number from
    # the post status text, same digit-pattern extraction migo_103.robot
    # already uses for its own material document number -- without this,
    # the reversal's own new SAP document number was never captured
    # anywhere, so schema_migration_v27's migo_103_reversal_doc_number
    # column had nothing real to store.
    @{doc_matches}=    Get Regexp Matches    ${status_3}    \\d{8,12}
    ${reversal_doc_number}=    Set Variable    ${EMPTY}
    IF    len($doc_matches) > 0
        ${reversal_doc_number}=    Set Variable    ${doc_matches}[0]
    END

    # FIX (2026-08-21): a failure message on this FINAL post status used
    # to be detected but never actually changed the result -- the keyword
    # always returned status:"COMPLETED" regardless. Now genuinely
    # reflects what SAP said.
    IF    ${failed_3}
        ${result_json}=    Evaluate
        ...    __import__('json').dumps({"status": "FAILED", "step": "post", "matdoc": $MATERIAL_DOC_NUMBER, "items_selected": $items_selected, "matdoc_entry_status": $status_1, "header_text_status": $status_2, "post_status": $status_3})
        RETURN    ${result_json}
    END

    ${result_json}=    Evaluate
    ...    __import__('json').dumps({"status": "COMPLETED", "matdoc": $MATERIAL_DOC_NUMBER, "reversal_doc_number": $reversal_doc_number, "items_selected": $items_selected, "matdoc_entry_status": $status_1, "header_text_status": $status_2, "post_status": $status_3})
    RETURN    ${result_json}


Tick All Item OK Lines
    [Documentation]    Ticks "Item OK" on the currently active line, then
    ...    presses the Next Item button (btnOK_NEXT_ITEM) to advance and
    ...    ticks again. Reads the "Line" field before and after pressing
    ...    Next -- if it hasn't changed (or the button isn't present at
    ...    all, e.g. a single-item document), that's the end of the item
    ...    table and the loop stops.
    [Arguments]    ${det_base}

    ${zeile_field}=       Set Variable    ${det_base}/txtGODYNPRO-DETAIL_ZEILE
    ${ok_checkbox}=       Set Variable    ${det_base}/subSUB_DETAIL_TAKE:SAPLMIGO:0304/chkGODYNPRO-DETAIL_TAKE
    ${next_item_btn}=     Set Variable    ${det_base}/btnOK_NEXT_ITEM

    ${item_count}=    Set Variable    0

    WHILE    True
        ${line_before_raw}=    Run Keyword And Ignore Error    Get Value    ${zeile_field}
        ${line_before}=    Set Variable    ${EMPTY}
        IF    '${line_before_raw}[0]' == 'PASS'
            ${line_before}=    Clean Value    ${line_before_raw}[1]
        END

        Select Checkbox    ${ok_checkbox}
        Sleep    0.5s
        ${item_count}=    Evaluate    ${item_count} + 1
        Log To Console    Item OK ticked for line "${line_before}" (item #${item_count})

        ${next_present}=    Run Keyword And Return Status    Element Should Be Present    ${next_item_btn}
        IF    not ${next_present}
            Log To Console    Next Item button not present -- single-item document, stopping.
            BREAK
        END

        Click Element    ${next_item_btn}
        Sleep    1s
        Dismiss Any Popup

        ${line_after_raw}=    Run Keyword And Ignore Error    Get Value    ${zeile_field}
        ${line_after}=    Set Variable    ${EMPTY}
        IF    '${line_after_raw}[0]' == 'PASS'
            ${line_after}=    Clean Value    ${line_after_raw}[1]
        END
        Log To Console    Line check: before=${line_before} after=${line_after}

        IF    '${line_after}' == '${EMPTY}' or '${line_after}' == '${line_before}'
            Log To Console    No further items after line "${line_before}" -- stopping.
            BREAK
        END

        IF    ${item_count} >= 100    BREAK
    END

    RETURN    ${item_count}


Read Status Bar With Retry
    [Arguments]    ${expected_pattern}=.+
    ${msg}=    Set Variable    ${EMPTY}
    # FIX (2026-08-21): widened from 5 attempts (~5s total) to 10 attempts
    # with a slightly longer sleep (~15s total) -- same status-bar-read
    # race condition already confirmed in production for migo_105.robot
    # (history_id=340, job 1310): SAP can genuinely take longer than ~5s
    # to finish posting under real load, and a reversal is higher-stakes
    # to misreport as failed than most other steps (schema_migration_v27's
    # policy permanently blocks MIGO 105/MIRO once migo_103_reversed=1, so
    # a false failure here is more costly to sort out than most).
    FOR    ${attempt}    IN RANGE    1    11
        # Read via the raw session object rather than the Get Value
        # keyword -- Get Value was silently failing on this element type
        # (GuiStatusbar), swallowed by Run Keyword And Ignore Error, so
        # ${msg} never left ${EMPTY} regardless of what SAP actually showed.
        ${res}=    Run Keyword And Ignore Error
        ...    Evaluate    $session.findById('wnd[0]/sbar').Text
        IF    '${res}[0]' == 'PASS'
            ${msg}=    Clean Value    ${res}[1]
        END
        Log To Console    Status bar attempt ${attempt}: "${msg}" (read status: ${res}[0])
        IF    '${msg}' != '${EMPTY}'
            RETURN    ${msg}
        END
        Sleep    1.5s
    END
    Log To Console    Status bar check timed out -- last read: "${msg}"
    RETURN    ${msg}


Check For Failure Message
    [Arguments]    ${message}
    ${is_failure}=    Run Keyword And Return Status
    ...    Should Match Regexp    ${message}    (?i).*(not present|does not exist|no cancellation|not released|already cancel|already revers|deficit).*
    IF    ${is_failure}
        Log To Console    !! REVERSAL FAILURE DETECTED: ${message}
    END
    RETURN    ${is_failure}


Safe Input Text
    # Retries once on failure -- covers the "Property text can not be set"
    # AttributeError seen when the SAP GUI COM element reference goes stale
    # mid-navigation.
    [Arguments]    ${locator}    ${value}
    ${status}=    Run Keyword And Return Status    Input Text    ${locator}    ${value}
    IF    not ${status}
        Log    Input Text failed on first attempt for ${locator} -- retrying once after a short pause.    level=WARN
        Sleep    1s
        Input Text    ${locator}    ${value}
    END


Clean Value
    [Arguments]    ${raw_value}
    ${val}=        Convert To String    ${raw_value}
    ${cleaned}=    Strip String    ${val}
    RETURN         ${cleaned}


Dump Element Texts
    [Documentation]    Recursively collects any non-empty .Text values from
    ...    the given live SAP GUI element (a COM object reference, not a
    ...    path string) and its children, up to 4 levels deep. Generic
    ...    fallback for popups whose exact structure (table vs. plain
    ...    fields) isn't confirmed via a GUI Scripting recording.
    [Arguments]    ${element}    ${depth}=${0}
    @{collected}=    Create List
    IF    ${depth} > 4
        RETURN    ${collected}
    END

    ${text_res}=    Run Keyword And Ignore Error    Evaluate    $element.Text
    IF    '${text_res}[0]' == 'PASS'
        ${text}=    Clean Value    ${text_res}[1]
        IF    '${text}' != '${EMPTY}'
            Append To List    ${collected}    ${text}
        END
    END

    ${count_res}=    Run Keyword And Ignore Error    Evaluate    $element.Children.Count
    IF    '${count_res}[0]' == 'PASS'
        ${child_count}=    Convert To Integer    ${count_res}[1]
        FOR    ${i}    IN RANGE    ${child_count}
            ${child}=    Evaluate    $element.Children.ElementAt(${i})
            @{child_texts}=    Dump Element Texts    ${child}    ${depth + 1}
            FOR    ${t}    IN    @{child_texts}
                Append To List    ${collected}    ${t}
            END
        END
    END

    RETURN    ${collected}


Capture And Dismiss Popup Message
    [Documentation]    If a popup (wnd[1]) is present, reads its message
    ...    text before dismissing it. Tries the standard single/multi-line
    ...    popup fields (txtSPOP-TEXT_LINE1..4) first. Some Check results
    ...    instead open a table-based "Display logs" popup (Typ/Item/
    ...    Message text/Long columns, e.g. "Deficit of PU GR blocked
    ...    stock..."), which doesn't use those fields at all -- for that
    ...    case, falls back to Dump Element Texts on the whole popup,
    ...    filtered to drop short UI chrome (column headers, icon
    ...    captions) and keep the actual message content.
    ${popup_present}=    Run Keyword And Return Status    Element Should Be Present    wnd[1]
    ${message}=    Set Variable    ${EMPTY}
    IF    ${popup_present}
        FOR    ${line}    IN    1    2    3    4
            ${res}=    Run Keyword And Ignore Error
            ...    Evaluate    $session.findById('wnd[1]/usr/txtSPOP-TEXT_LINE${line}').Text
            IF    '${res}[0]' == 'PASS'
                ${line_text}=    Clean Value    ${res}[1]
                IF    '${line_text}' != '${EMPTY}'
                    ${message}=    Catenate    SEPARATOR=${SPACE}    ${message}    ${line_text}
                    ${message}=    Strip String    ${message}
                END
            END
        END

        IF    '${message}' == '${EMPTY}'
            ${popup_root}=    Run Keyword And Ignore Error
            ...    Evaluate    $session.findById('wnd[1]')
            IF    '${popup_root}[0]' == 'PASS'
                @{all_texts}=    Dump Element Texts    ${popup_root}[1]
                Log To Console    Popup table-dump candidates: @{all_texts}
                @{meaningful}=    Create List
                FOR    ${t}    IN    @{all_texts}
                    ${tlen}=    Get Length    ${t}
                    IF    ${tlen} >= 12
                        Append To List    ${meaningful}    ${t}
                    END
                END
                ${message}=    Catenate    SEPARATOR=${SPACE}|${SPACE}    @{meaningful}
            END
        END

        Log To Console    Popup message captured: "${message}"
        Run Keyword And Ignore Error    Click Element    wnd[1]/tbar[0]/btn[0]
        Sleep    1s
    END
    RETURN    ${message}


Dismiss Any Popup
    ${p1}=    Run Keyword And Return Status    Element Should Be Present    wnd[1]
    IF    ${p1}
        Run Keyword And Ignore Error    Click Element    wnd[1]/tbar[0]/btn[0]
        Sleep    1s
    END
    ${p2}=    Run Keyword And Return Status    Element Should Be Present    wnd[2]
    IF    ${p2}
        Run Keyword And Ignore Error    Click Element    wnd[2]/tbar[0]/btn[0]
        Sleep    1s
    END


# Acquire Refcount Lock / Release Refcount Lock / Register Bot Start /
# Register Bot End -- REMOVED (2026-09-03). This bot used to carry its
# own standalone copy of the multi-bot coordination logic (including
# the same destructive OPT1 multi-logon handling fixed above in
# Initialize SAP And Login). Login/close now go entirely through
# sap_bot_coordination.py, shared by every bot in this codebase.


Close SAP Session
    Coordinated Close SAP Session    ${session}
