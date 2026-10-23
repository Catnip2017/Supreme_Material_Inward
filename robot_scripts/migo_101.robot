*** Settings ***
Documentation     MIGO 101 — Goods Receipt against an Outbound Delivery.
...
...               Adapted from a user-supplied draft (outbond_1.robot).
...               PROCESS STRUCTURE IS DELIBERATELY IDENTICAL TO
...               migo_105.robot. Same order, same keywords, same
...               Check-then-Post handling. Only the reference document
...               differs:
...
...                 105 : ACTION = A05 (Release GR Blocked Stock)
...                       reference = material document, under :2010
...                 101 : ACTION = A01 (Goods Receipt)
...                       REFDOC = R05 (Outbound Delivery)
...                       reference = delivery number, under :2040
...                       plus header fields (FRBNR / XABLN / BKTXT) and,
...                       per matched line, Quantity tab (ERFMG / LSMNG)
...
...               CHANGES MADE WHEN ADAPTING THE DRAFT FOR PRODUCTION:
...                 1. Login/close now go through sap_bot_coordination.py
...                    (Coordinated SAP Login / Coordinated Close SAP
...                    Session) instead of the draft's own standalone
...                    taskkill+Start Process+Connect To Session logic.
...                    The draft's version could kill another bot's live
...                    SAP session under concurrency -- every other bot in
...                    this codebase already went through this same
...                    migration (see sap_bot_coordination.py). Connect To
...                    Sap Session / Handle Multi Logon Popup / Open New
...                    Sap Session (and ${OPEN_NEW_SESSION}) are removed
...                    with it -- Coordinated SAP Login handles session
...                    binding internally, and the "leave a session open
...                    for a following step" idea wasn't part of anything
...                    actually discussed for MIGO 101 (only "a print bot
...                    for which we will get the requirements later" was
...                    mentioned), so it is left out rather than guessed
...                    at against a teardown model that no longer applies.
...                 2. Batch is now PER LINE, decoded from ${ITEMS_JSON_B64}
...                    (the same matched-pairs payload the app already
...                    sends), instead of one ${BATCH_NUMBER} applied to
...                    every line. The loop itself now walks the PAYLOAD,
...                    not SAP's delivery lines -- only the matched pairs
...                    are opened, filled, and ticked; every unmatched
...                    delivery/PO line is left completely untouched.
...                    Matched to its SAP line by PO Item Number (EBELP)
...                    -- see "HOW LINES ARE MATCHED AND PROCESSED" below.
...                 3. Vendor Invoice Amount field added to the UI as a
...                    VIEW-ONLY reference value -- confirmed with the
...                    user this is not meant to be written into SAP for
...                    MIGO 101 (unlike migo_105.robot's own vendor
...                    invoice field, which does write to the same
...                    Z-extension tab, tabpOK_GOHEAD_EXT_1 /
...                    txtZIBS_AUTO_MIRO-DMBTR). An earlier version of
...                    this file copied migo_105's write-to-SAP behavior
...                    for this field; that tab is now never opened by
...                    this bot -- the value only ever flows into
...                    item_results/the DB for the record, never SAP.
...                 4. Removed a duplicate FRBNR/BKTXT re-fill block that
...                    existed right after the item-ticking loop in the
...                    draft, identical to the one a few steps earlier --
...                    looked like an accidental copy-paste leftover, not
...                    documented anywhere as intentional, and re-typing
...                    header fields immediately before Check risks an
...                    unexpected popup for no benefit. Flagging this here
...                    in case it turns out to have been intentional.
...                 5. Added (bringing 101 closer to a 103+105 hybrid, per
...                    the user's own framing): a GR/GI Slip Number header
...                    field (txtGOHEAD-XABLN), manual-entry only, same as
...                    103's own GR/GI Slip field in this UI -- no auto-
...                    population. Per-line Actual Quantity/Delivery Note
...                    Quantity typing on the Quantity tab (ERFMG/LSMNG),
...                    same fields/order as migo_103.robot. Header Text
...                    now gets the same leading-zero-off-the-Gate-In-
...                    Number trim 103 already had (app.py + the
...                    lstrip("0") backstop below, both mirroring 103's
...                    own two-layer pattern). None of this touches the
...                    matched-payload-only item loop itself (change #2
...                    above) or the view-only Vendor Invoice Amount
...                    (change #3 above).
...
...               HOW LINES ARE MATCHED AND PROCESSED: matched the same
...               way migo_103.robot already does -- by PO Item Number
...               (EBELP), the field shown as 10/20/30 in ME23N and read
...               by po_fetch.robot, which this app's own MIGO 101
...               matching table already carries per pair (labelled "PO
...               item number (SAP EBELP)"). CRITICAL, corrected after
...               review: the loop walks the PAYLOAD (${items}, the
...               matched invoice<->PO pairs decoded from
...               ITEMS_JSON_B64), NOT SAP's own delivery lines --
...               exactly mirroring how migo_103.robot's own line loop
...               ends (FOR ${i} IN RANGE ${total}, where ${total} is the
...               PAYLOAD's length, not the grid's row count). If the UI
...               shows 3 matched pairs out of a 5-line PO/delivery, ONLY
...               those 3 SAP lines are opened, filled, and ticked -- the
...               other 2 are never navigated to, never touched, and
...               never ticked, exactly like migo_103.robot. A payload
...               item whose EBELP isn't found among this delivery's SAP
...               lines is skipped and logged, never guessed at. An
...               earlier version of this file had this backwards --
...               walking every SAP line and ticking all of them, using
...               the payload only to decide what to type -- which would
...               have ticked and posted delivery lines the user never
...               matched. Fixed before this ever ran against a live
...               delivery. Sequential line position (the way
...               migo_105.robot does it) is never used here -- that only
...               works for migo_105 because it reuses migo_103's own
...               already-posted line order on the SAME document, which
...               doesn't apply to 101's UI.
...
...               MECHANICS: migo_103.robot reads EBELP from the item
...               OVERVIEW grid (tblSAPLMIGOTV_GOITEM, a scrollable table
...               control) -- a different screen area from the per-line
...               DETAIL subscreen (DETAIL_ZEILE navigation) used to open,
...               fill, and tick each matched line. No bot in this
...               codebase has ever read EBELP from that per-line DETAIL
...               subscreen, so rather than guess an unverified field path
...               there, this file reads the OVERVIEW grid ONCE at the
...               start (Extract MIGO Item Table, the same proven method
...               migo_103.robot uses, including its dynamic column-index
...               resolution -- see Resolve GOITEM Grid Columns) to build
...               a line-number -> EBELP/MATNR lookup (${item_rows}).
...               Tick All Item OK Checkboxes then loops over ${items}
...               and, for each payload item, searches ${item_rows} for
...               the matching EBELP (Find Grid Row For Item No) -- the
...               matched grid row is what tells it which single SAP line
...               number to open via DETAIL_ZEILE. Nothing else is ever
...               opened.
...
...               SANITY CHECK: once navigated to a matched line, the
...               detail screen's own MATNR is re-read and compared
...               (leading-zero-normalized) against the overview grid's
...               MATNR for that same row -- confirming SAP actually
...               landed on the line the grid said it would. Mirrors
...               migo_103.robot's own fix for this (2026-08-25): a
...               mismatch is surfaced in item_results as "VERIFY THIS
...               LINE IN SAP", not just logged, so it is visible in the
...               UI/DB rather than a silent success.
...
...               SAFETY NET: if the overview grid isn't present or
...               doesn't carry EBELP for a delivery-referenced document
...               -- the one part of this design that hasn't been
...               confirmed against a live screen -- Extract MIGO Item
...               Table comes back empty, and every payload item falls
...               back to MATERIAL CODE matching against that same (also
...               empty) grid via Find Grid Row For Material, which then
...               also finds nothing. In that case NOTHING is matched and
...               NOTHING is touched or posted: Fill MIGO 101 And Post
...               stops with a FAILED status before Check/Post rather
...               than posting an empty or wrong document -- see the
...               "matched_count == 0" guard in that keyword. A duplicate
...               PO item number within one payload is skipped and logged
...               (mirrors migo_103.robot's own duplicate-item_no guard).
...
...               FLOW:
...                 1. Log into SAP (coordinated).
...                 2. Run MIGO, dismiss popups, close the overview tree,
...                    open Header + Item Detail.
...                 3. Action = A01, RefDoc = R05.
...                 4. Enter the delivery number, press Enter. SAP's own
...                    rejection (does not exist / not released / no
...                    items found / already delivered / cannot be
...                    processed) is checked right here, before anything
...                    else is typed -- caught pre-Post, so nothing is
...                    posted and there is no duplicate-GR risk on retry.
...                 5. Header: Bill of Lading (txtGOHEAD-FRBNR), GR/GI Slip
...                    Number (txtGOHEAD-XABLN), Header Text
...                    (txtGOHEAD-BKTXT). Vendor Invoice Amount is NOT
...                    typed here -- view-only field, see change #3 above.
...                 6. LINE 1 ONLY -- WHERE tab: storage location
...                    (ctxtGOITEM-LGOBE), and remarks (txtGOITEM-SGTXT)
...                    if supplied. Mirrors migo_105 exactly: set once on
...                    line 1, not on every line.
...                 7. LOOP THE MATCHED PAYLOAD, NOT SAP'S LINES -- for
...                    each matched pair, find its SAP line by PO item
...                    number (EBELP) and open ONLY that line: sanity-
...                    check the material, type that line's Actual
...                    Quantity/Delivery Note Quantity (Quantity tab --
...                    ERFMG/LSMNG, see the mapping note in Tick All Item
...                    OK Checkboxes), set that line's batch (skip if none
...                    supplied -- SAP auto-assigns), then tick "Item OK"
...                    (chkGODYNPRO-DETAIL_TAKE). A pair whose EBELP isn't
...                    found in this delivery is skipped and logged, never
...                    touched (see above).
...                 8. Check (btn[7]) once, capture any popup.
...                 9. Post (btn[23]); if a popup appeared, confirm it and
...                    click Post a SECOND time to actually commit.
...                10. Read the material document number from the status
...                    bar.
...
...               NO MOVEMENT TYPE IS TYPED. SAP derives 101 from the
...               delivery itself.
...
...               QUANTITIES: Actual Quantity (ERFMG) and Delivery Note
...               Quantity (LSMNG) ARE typed per matched line, same as
...               migo_103.robot's own Quantity tab handling -- added so
...               a partial/adjusted receipt can be entered rather than
...               relying solely on whatever the delivery already carries.
...               See the mapping note in Tick All Item OK Checkboxes for
...               which payload key goes into which SAP field.
...
...               WHY POST IS CLICKED TWICE (and only sometimes):
...               SAP frequently answers the first Post with a validation
...               popup. Confirming that popup dismisses the dialog but
...               does NOT commit the document, so a second Post is
...               needed. When no popup appears the FIRST Post already
...               posted, and clicking again would either error or start a
...               second document -- so the second click is conditional on
...               a popup having actually appeared. Same logic as
...               migo_105.robot.
...
...               STEP LOGGING: each stage logs a numbered STEP banner, so
...               a failed run can be placed immediately instead of being
...               reconstructed from element paths.
...
...               Console result markers (read by rf_runner.py's
...               execute_migo_101_sap -- names must match exactly):
...                 RESULT:MIGO_101_JSON:<full result, diagnostic only>
...                 RESULT:MIGO101_DOC_NUMBER:<doc number | DELIVERY_NOT_VALID | MANUAL_CHECK_REQUIRED>
...                 RESULT:MIGO101_STATUS_MSG:<SAP's own message, verbatim>
...                 RESULT:ITEM_RESULTS:<json, per line>
Library           SapGuiLibrary
Library           Process
Library           OperatingSystem
Library           String
Library           DateTime
Library           Collections
Library           ${CURDIR}/sap_bot_coordination.py

*** Variables ***
${DELIVERY_NUMBER}    ${EMPTY}
${DOC_DATE}           ${EMPTY}
${POST_DATE}          ${EMPTY}
${BILL_OF_LADING}     ${EMPTY}
${GR_SLIP_NO}         ${EMPTY}
${HEADER_TEXT}        ${EMPTY}

# Storage location -- written into the WHERE tab on LINE 1 ONLY, exactly as
# migo_105.robot does it. Leave EMPTY to skip the WHERE tab entirely.
${STORAGE_LOCATION}   ${EMPTY}

# Optional per-document remark, written next to the storage location on
# line 1. Skipped when empty, same as migo_105.
${REMARKS}            ${EMPTY}

# Vendor Invoice Amount -- VIEW-ONLY reference value for MIGO 101 (see
# STEP 5b below). Unlike migo_105.robot's own vendor invoice field, this
# is NEVER typed into SAP -- confirmed with the user. Only ever carried
# through into the result/DB record for reference.
${VENDOR_INVOICE}     ${EMPTY}

# Matched line items, base64-encoded JSON, same payload shape rf_runner.py
# sends for MIGO 103/105: a list of dicts each carrying at least item_no
# (PO Item Number / EBELP), material_code, batch, qty_actual and
# qty_expected. Each item's SAP line is matched by EBELP -- same field,
# same matching idea as migo_103.robot -- with material-code matching
# kept as an automatic fallback if the EBELP lookup can't be done. See
# "HOW LINES ARE MATCHED AND PROCESSED" in the Documentation block above
# for the full reasoning.
${ITEMS_JSON_B64}     W10=

# --- MIGO item overview grid (list view) -- read ONCE per run, purely to
# look up each line's own PO Item Number (EBELP), the same field
# migo_103.robot already matches by. See "HOW LINES ARE MATCHED AND
# PROCESSED" for why this was added and Resolve GOITEM Grid Columns for how
# the column positions below are found at runtime rather than trusted
# as literals. 39/59 are migo_103.robot's own current production values
# for this exact shared grid (tblSAPLMIGOTV_GOITEM) -- used here only as
# a starting fallback if the dynamic lookup can't confirm a column,
# since it is the same underlying table on the same MIGO screen.
${GOITEM_TABLE}    wnd[0]/usr/ssubSUB_MAIN_CARRIER:SAPLMIGO:0003/subSUB_ITEMLIST:SAPLMIGO:0200/tblSAPLMIGOTV_GOITEM
${COL_GOITEM_EBELP}    39
${COL_GOITEM_MATNR}    59

# Pause after each value is typed, so SAP has settled before the next
# field is touched and before the value is read back.
${FIELD_SETTLE}       1s

# TRUE skips both Post clicks -- everything else still runs, including the
# Check button, so the whole flow can be exercised against a real delivery
# without committing a document. This exists so the Post clicks never have
# to be commented out by hand: a bot with a commented-out Post looks like
# it is working right up until someone checks SAP and finds no document.
# rf_runner.py never sets this -- it only ever runs live (DRY_RUN stays at
# its FALSE default in every real production run), so this is purely a
# manual-testing knob for a supervised run before trusting this bot.
${DRY_RUN}            FALSE

# NOT USED by the item loop anymore -- Tick All Item OK Checkboxes now
# loops over the PAYLOAD (${items}), which is inherently finite, instead
# of walking SAP's lines until SAP rejects the next number, so there is
# no unbounded/"spin forever" case left to cap. Left in place rather than
# removed in case a future change reintroduces a SAP-line walk that needs
# it again.
${MAX_ITEM_LINES}     ${100}

${session}            ${NONE}

# --- Screen paths ------------------------------------------------------------
${PATH_TREE_CLOSE_BTN}    wnd[0]/shellcont/shell/shellcont[1]/shell[0]
${DELIVERY_SUBSCREEN}     subSUB_FIRSTLINE_REFDOC:SAPLMIGO:2040/ctxtGODYNPRO-OUTBOUND_DELIVERY
${HEADER_BASE}    wnd[0]/usr/ssubSUB_MAIN_CARRIER:SAPLMIGO:0003/subSUB_HEADER:SAPLMIGO:0101/subSUB_HEADER:SAPLMIGO:0100/tabsTS_GOHEAD/tabpOK_GOHEAD_GENERAL/ssubSUB_TS_GOHEAD_GENERAL:SAPLMIGO:0110
${DETAIL_BASE}    wnd[0]/usr/ssubSUB_MAIN_CARRIER:SAPLMIGO:0003/subSUB_ITEMDETAIL:SAPLMIGO:0301/subSUB_DETAIL:SAPLMIGO:0300
${CHECK_BUTTON}   wnd[0]/tbar[1]/btn[7]
${POST_BUTTON}    wnd[0]/tbar[1]/btn[23]


*** Test Cases ***
Execute MIGO 101
    [Setup]    Initialize SAP And Login
    ${result_raw}=    Fill MIGO 101 And Post
    Log To Console    RESULT:MIGO_101_JSON:${result_raw}

    # Parsed into a real dict before indexing. A JSON string indexed with
    # [status] raises "String used with invalid index" and aborts the test
    # -- which would happen AFTER SAP has already posted, making a
    # successful run look like a failure and inviting a retry that would
    # create a duplicate goods receipt.
    ${result}=    Evaluate    __import__('json').loads($result_raw)

    # Map this bot's own status vocabulary onto the exact marker values
    # rf_runner.py's execute_migo_101_sap() expects (mirrors migo_103.robot's
    # PO_NOT_RELEASED -> MANUAL_CHECK_REQUIRED style mapping):
    #   COMPLETED             -> the real material document number
    #   FAILED                -> DELIVERY_NOT_VALID (caught before Post was
    #                            ever clicked -- covers a rejected delivery
    #                            number, the outbound-delivery field never
    #                            appearing, and a delivery with no item
    #                            lines to receive; nothing was posted in any
    #                            of these, so there is no duplicate-GR risk
    #                            on retry -- see Fill MIGO 101 And Post's
    #                            three early RETURNs)
    #   MANUAL_CHECK_REQUIRED -> unchanged (Post WAS clicked, no document
    #                            number could be read back -- retrying
    #                            without checking SAP risks a duplicate GR)
    #   DRY_RUN                -> MANUAL_CHECK_REQUIRED (never produced by
    #                            rf_runner.py in practice -- DRY_RUN stays
    #                            FALSE on every real run -- but mapped to a
    #                            safe non-success value rather than crashing
    #                            if this is ever run with it set)
    IF    '${result}[status]' == 'COMPLETED'
        ${mat_doc}=    Set Variable    ${result}[matdoc]
    ELSE IF    '${result}[status]' == 'FAILED'
        ${mat_doc}=    Set Variable    DELIVERY_NOT_VALID
    ELSE IF    '${result}[status]' == 'MANUAL_CHECK_REQUIRED'
        ${mat_doc}=    Set Variable    MANUAL_CHECK_REQUIRED
    ELSE
        ${mat_doc}=    Set Variable    MANUAL_CHECK_REQUIRED
    END
    Log To Console    RESULT:MIGO101_DOC_NUMBER:${mat_doc}
    Log To Console    RESULT:MIGO101_STATUS_MSG:${result}[message]

    Sleep    10s
    [Teardown]    Close SAP Session


*** Keywords ***
Log Step
    [Documentation]    One numbered console banner per stage. Cheap, and it
    ...    turns "the run failed somewhere in MIGO" into "it failed between
    ...    STEP 6 and STEP 7" without anyone reading element paths.
    [Arguments]    ${number}    ${text}
    Log To Console    ${EMPTY}
    Log To Console    ===== STEP ${number}: ${text} =====


Initialize SAP And Login
    # MULTI-BOT FIX: this used to carry its own standalone taskkill +
    # Start Process + Connect To Session + OPT1 multi-logon handling (see
    # the draft this file was adapted from) -- that risks killing another
    # bot's live SAP session under concurrency. Login/close now go through
    # sap_bot_coordination.py, exactly like every other bot in this
    # codebase (migo_103.robot, migo_105.robot, etc.). Coordinated SAP
    # Login also binds ${session} correctly (Bind SAP Session internally),
    # replacing the old Connect To Sap Session call.
    Evaluate    __import__('dotenv').load_dotenv(__import__('os').getenv('DOTENV_PATH', '.env'), override=True)
    ${CLIENT}=      Evaluate    __import__('os').getenv('SAP_CLIENT')
    ${CONN_NAME}=   Evaluate    __import__('os').getenv('SAP_CONNECTION_NAME')
    ${LOGON_PATH}=  Evaluate    __import__('os').getenv('SAP_LOGON_PATH')

    # v16: per-user SAP credential pass-through (LDAP users only) -- see
    # gate_in.robot for the full explanation.
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

    ${session}=    Coordinated SAP Login    ${LOGON_PATH}    ${CONN_NAME}    ${CLIENT}    ${USERNAME}    ${PASSWORD}
    Set Suite Variable    ${session}
    Log To Console    Login complete.


Get Firstline Path
    [Documentation]    The Action/RefDoc combo-box subscreen renders as
    ...    either :0003 or :0007 depending on MIGO's internal state. A
    ...    hardcoded path silently fails whenever the screen is in the
    ...    other state, so both are probed. Re-called after every combo-box
    ...    change, because setting ACTION or REFDOC can itself switch the
    ...    subscreen out from under the path just used.
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


Ensure Header And Detail Open
    [Documentation]    Header Data and Item Detail each toggle open/closed,
    ...    and the screen number shifts with the OTHER section's state.
    ...    Only the "open" button names are searched for -- finding one
    ...    means the section is CLOSED, so click it. Not finding one means
    ...    it is already open. The "close" button names are deliberately
    ...    never searched for: this only ever opens, never closes.
    ${header_open_path}=    Set Variable    ${EMPTY}
    FOR    ${scr}    IN    0002    0003    0004    0005
        ${candidate}=    Set Variable
        ...    wnd[0]/usr/ssubSUB_MAIN_CARRIER:SAPLMIGO:${scr}/subSUB_HEADER:SAPLMIGO:0102/btnOK_HEADER
        ${found}=    Run Keyword And Return Status    Element Should Be Present    ${candidate}
        IF    ${found}
            ${header_open_path}=    Set Variable    ${candidate}
            BREAK
        END
    END

    IF    '${header_open_path}' != '${EMPTY}'
        Run Keyword And Ignore Error
        ...    Evaluate    $session.findById('${header_open_path}').press()
        Sleep    1s
        Log To Console    Header data was closed -- opened it.
    ELSE
        Log To Console    Header data already open.
    END

    ${detail_open_path}=    Set Variable    ${EMPTY}
    FOR    ${scr}    IN    0002    0003    0004    0005
        ${candidate}=    Set Variable
        ...    wnd[0]/usr/ssubSUB_MAIN_CARRIER:SAPLMIGO:${scr}/subSUB_ITEMDETAIL:SAPLMIGO:0302/btnBUTTON_DETAIL
        ${found}=    Run Keyword And Return Status    Element Should Be Present    ${candidate}
        IF    ${found}
            ${detail_open_path}=    Set Variable    ${candidate}
            BREAK
        END
    END

    IF    '${detail_open_path}' != '${EMPTY}'
        Run Keyword And Ignore Error
        ...    Evaluate    $session.findById('${detail_open_path}').press()
        Sleep    1s
        Log To Console    Item detail was closed -- opened it.
    ELSE
        Log To Console    Item detail already open.
    END


Dismiss Overview Tree Sidebar
    [Documentation]    MIGO can open a document-overview tree sidebar on
    ...    this same screen, which shifts the layout underneath the paths
    ...    this bot uses. Both checks are presence guards, so this is a
    ...    no-op on any run where the sidebar never appears.
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


Fill MIGO 101 And Post
    ${delivery}=      Clean Value    ${DELIVERY_NUMBER}
    ${bol_clean}=     Clean Value    ${BILL_OF_LADING}
    ${slip_clean}=    Clean Value    ${GR_SLIP_NO}
    ${hdr_clean}=     Clean Value    ${HEADER_TEXT}
    ${storage_clean}=    Clean Value    ${STORAGE_LOCATION}
    ${rem_clean}=     Clean Value    ${REMARKS}
    ${invoice_clean}=    Clean Value    ${VENDOR_INVOICE}

    # Same decode pattern as migo_103.robot/migo_105.robot -- the ONLY
    # variable actually read is ITEMS_JSON_B64 (base64-encoded JSON list).
    ${items}=    Evaluate
    ...    __import__('json').loads(__import__('base64').b64decode('${ITEMS_JSON_B64}').decode()) if '${ITEMS_JSON_B64}' else []
    ${items_count}=    Get Length    ${items}
    Log To Console    MIGO 101 -- delivery="${delivery}" doc_date="${DOC_DATE}" post_date="${POST_DATE}" bill_of_lading="${bol_clean}" gr_slip_no="${slip_clean}" header_text="${hdr_clean}" storage_location="${storage_clean}" vendor_invoice="${invoice_clean}" matched_items=${items_count}

    # --- STEP 1: Navigate to MIGO ---
    Log Step    1    Navigate to MIGO
    Run Transaction    MIGO
    Sleep    3s
    Dismiss Any Popup
    Dismiss Overview Tree Sidebar
    Ensure Header And Detail Open

    # --- STEP 2: Action = A01 (Goods Receipt) ---
    Log Step    2    Action = A01 (Goods Receipt)
    # .key rather than a label match: labels change with the SAP logon
    # language, keys don't. setattr() rather than a direct assignment
    # because Robot's Evaluate runs expressions through Python eval(),
    # which cannot execute assignment statements.
    ${firstline}=    Get Firstline Path
    Evaluate    setattr($session.findById('${firstline}/cmbGODYNPRO-ACTION'), 'key', 'A01')
    Sleep    0.5s

    # --- STEP 3: Reference document = R05 (Outbound Delivery) ---
    # Re-resolve: setting ACTION can itself switch the subscreen between
    # :0003 and :0007, so REFDOC's container may differ from ACTION's.
    Log Step    3    Reference document = R05 (Outbound Delivery)
    ${firstline}=    Get Firstline Path
    Evaluate    setattr($session.findById('${firstline}/cmbGODYNPRO-REFDOC'), 'key', 'R05')
    Sleep    0.5s
    Dismiss Any Popup

    # --- STEP 4: Delivery number ---
    Log Step    4    Enter delivery number ${delivery}
    ${firstline}=    Get Firstline Path
    ${delivery_field}=    Set Variable    ${firstline}/${DELIVERY_SUBSCREEN}

    # Selecting R05 is what creates this field. Typing into it before it
    # renders fails in a way that looks unrelated to the real cause.
    ${field_ready}=    Run Keyword And Return Status
    ...    Wait Until Keyword Succeeds    5x    1s
    ...    Element Should Be Present    ${delivery_field}
    IF    not ${field_ready}
        Log To Console    Outbound-delivery field never appeared -- R05 did not take on this screen.
        ${res}=    Evaluate
        ...    __import__('json').dumps({'status': 'FAILED', 'matdoc': 'NOT_POSTED', 'message': 'The outbound-delivery field did not appear after selecting reference document R05. Nothing was posted.', 'item_results': []})
        RETURN    ${res}
    END

    Set Focus    ${delivery_field}
    Safe Input Text    ${delivery_field}    ${delivery}
    Send VKey    0
    Sleep    3s
    Dismiss Any Popup

    # SAP rejects a bad delivery right here. Reading the status bar now
    # surfaces SAP's own reason. Without this the bot carries on into a
    # screen that doesn't work, and the eventual error is a downstream
    # symptom that explains nothing.
    ${entry_msg}=    Get Value    wnd[0]/sbar
    Log To Console    Status bar after delivery entry: "${entry_msg}"
    ${rejected}=    Run Keyword And Return Status
    ...    Should Match Regexp    ${entry_msg}
    ...    (?i)(does\\s+not\\s+exist|not\\s+(yet\\s+)?released|no\\s+items?\\s+found|already\\s+(been\\s+)?(fully\\s+)?(delivered|processed)|cannot\\s+be\\s+processed)
    IF    ${rejected}
        Log To Console    Delivery ${delivery} rejected by SAP -- stopping before Post.
        ${res}=    Evaluate
        ...    __import__('json').dumps({'status': 'FAILED', 'matdoc': 'NOT_POSTED', 'message': $entry_msg, 'item_results': []})
        RETURN    ${res}
    END

    # --- STEP 5: Header fields ---
    Log Step    5    Header fields (Doc Date, Posting Date, Bill of Lading, GR/GI Slip, Header Text)
    # Dismiss Any Popup BETWEEN the fields, not just around them: entering
    # one header field can open a dialog on top of wnd[0], and the next
    # Input Text then hits a blocked field.
    #
    # Doc Date / Posting Date -- same fields (BLDAT/BUDAT), same shared
    # "General" header tab, same order as migo_103.robot's own header
    # step. Typed RAW, no Clean Value -- rf_runner.py's execute_migo_101_sap
    # already converts these to SAP's DD.MM.YYYY via _to_sap_date() before
    # they ever reach this script, exactly mirroring migo_103.robot (which
    # never cleans DOC_DATE/POST_DATE either -- only the free-text fields
    # below need whitespace/currency stripping).
    Set Focus    ${HEADER_BASE}/ctxtGOHEAD-BLDAT
    Safe Input Text    ${HEADER_BASE}/ctxtGOHEAD-BLDAT    ${DOC_DATE}
    Dismiss Any Popup

    Set Focus    ${HEADER_BASE}/ctxtGOHEAD-BUDAT
    Safe Input Text    ${HEADER_BASE}/ctxtGOHEAD-BUDAT    ${POST_DATE}
    Dismiss Any Popup

    Set Focus    ${HEADER_BASE}/txtGOHEAD-FRBNR
    Safe Input Text    ${HEADER_BASE}/txtGOHEAD-FRBNR    ${bol_clean}
    Dismiss Any Popup

    Set Focus    ${HEADER_BASE}/txtGOHEAD-XABLN
    Safe Input Text    ${HEADER_BASE}/txtGOHEAD-XABLN    ${slip_clean}
    Dismiss Any Popup

    Set Focus    ${HEADER_BASE}/txtGOHEAD-BKTXT
    Safe Input Text    ${HEADER_BASE}/txtGOHEAD-BKTXT    ${hdr_clean}
    Dismiss Any Popup

    Send VKey    0
    Sleep    2s
    Dismiss Any Popup

    # Read back. Cheap, and it separates "the values never landed" from
    # "the values landed but SAP rejected them at Post" -- two problems
    # that look identical from the outside.
    ${bldat_check}=    Run Keyword And Ignore Error    Get Value    ${HEADER_BASE}/ctxtGOHEAD-BLDAT
    ${budat_check}=    Run Keyword And Ignore Error    Get Value    ${HEADER_BASE}/ctxtGOHEAD-BUDAT
    ${frbnr_check}=    Run Keyword And Ignore Error    Get Value    ${HEADER_BASE}/txtGOHEAD-FRBNR
    ${xabln_check}=    Run Keyword And Ignore Error    Get Value    ${HEADER_BASE}/txtGOHEAD-XABLN
    ${bktxt_check}=    Run Keyword And Ignore Error    Get Value    ${HEADER_BASE}/txtGOHEAD-BKTXT
    Log To Console    READBACK: BLDAT="${bldat_check}[1]" BUDAT="${budat_check}[1]" FRBNR="${frbnr_check}[1]" XABLN="${xabln_check}[1]" BKTXT="${bktxt_check}[1]"

    # --- STEP 5b: Vendor Invoice Amount -- VIEW-ONLY, NEVER SENT TO SAP ---
    # CORRECTED (per explicit user confirmation): this field is a
    # reference/view-only value in the MIGO 101 UI -- shown to the user
    # for their own comparison, not something meant to be written into
    # SAP. An earlier version of this file typed it into the same custom
    # Z-extension tab migo_105.robot uses (tabpOK_GOHEAD_EXT_1 /
    # txtZIBS_AUTO_MIRO-DMBTR) purely because migo_105.robot does it for
    # its own vendor invoice field -- that was the wrong field to copy
    # for 101 specifically. Fixed: the SAP tab is never opened and
    # nothing is ever typed here for MIGO 101. The value the user saw in
    # the UI is still carried through into this result for the record --
    # ${invoice_clean} is only ever logged/returned, never sent to SAP.
    IF    '${invoice_clean}' != '${EMPTY}'
        ${invoice_status}=    Set Variable    ${invoice_clean} (view-only -- not sent to SAP)
    ELSE
        ${invoice_status}=    Set Variable    not set (no vendor invoice amount supplied)
    END

    # ── STEP 6 -- LINE 1 ONLY: Where tab, storage location + remarks ──
    # Set once on line 1, NOT on every line -- this is exactly how
    # migo_105.robot does it. MIGO applies a line-1 destination to the
    # whole document, so repeating it per line is redundant work and one
    # more chance for a tab click to land wrong mid-document.
    #
    # NOTE THE TRAILING DOT in "tabpOK_GOITEM_DESTINAT." -- part of SAP's
    # actual element name, not a typo. Dropping it gives a "Cannot find
    # Element" that looks like the tab doesn't exist.
    Log Step    6    LINE 1 ONLY -- WHERE tab: storage location "${storage_clean}"
    Safe Input Text    ${DETAIL_BASE}/txtGODYNPRO-DETAIL_ZEILE    1
    Set Focus    ${DETAIL_BASE}/txtGODYNPRO-DETAIL_ZEILE
    Send VKey    0
    Sleep    1s
    Dismiss Any Popup

    ${where_tab}=    Set Variable    ${DETAIL_BASE}/tabsTS_GOITEM/tabpOK_GOITEM_DESTINAT.
    Click Element    ${where_tab}
    Sleep    1.5s
    Dismiss Any Popup

    ${sloc_field}=    Set Variable    ${where_tab}/ssubSUB_TS_GOITEM_DESTINATION:SAPLMIGO:0325/ctxtGOITEM-LGOBE
    ${sloc_status}=    Set Variable    not set (no storage location configured)
    IF    '${storage_clean}' != '${EMPTY}'
        Safe Input Text    ${sloc_field}    ${storage_clean}
        Send VKey    0
        Sleep    0.5s
        Dismiss Any Popup

        # Read back rather than assume. SAP silently rejects a storage
        # location that isn't valid for the material or plant on the line,
        # leaving the field blank or unchanged -- without this the run
        # would post to whatever default SAP chose while reporting success.
        ${sloc_read}=    Run Keyword And Ignore Error    Get Value    ${sloc_field}
        ${sloc_actual}=    Set Variable If    '${sloc_read}[0]' == 'PASS'    ${sloc_read}[1]    ${EMPTY}
        ${sloc_actual}=    Clean Value    ${sloc_actual}
        IF    '${sloc_actual}' == '${storage_clean}'
            ${sloc_status}=    Set Variable    ${storage_clean}
            Log To Console    Storage location ${storage_clean} accepted on line 1.
        ELSE
            ${sloc_status}=    Set Variable    REJECTED -- typed "${storage_clean}", SAP shows "${sloc_actual}"
            Log To Console    WARNING: typed storage location "${storage_clean}" but SAP shows "${sloc_actual}" -- it may be invalid for this material/plant.
        END
    END

    IF    '${rem_clean}' != '${EMPTY}'
        Safe Input Text
        ...    ${where_tab}/ssubSUB_TS_GOITEM_DESTINATION:SAPLMIGO:0325/txtGOITEM-SGTXT
        ...    ${rem_clean}
        Send VKey    0
        Sleep    0.5s
        Dismiss Any Popup
        Log To Console    Remarks set on line 1: "${rem_clean}"
    END

    # ── STEP 6b -- read the item overview grid ONCE, for EBELP lookup only ──
    # Same grid migo_103.robot reads (tblSAPLMIGOTV_GOITEM), used here
    # purely to find each matched payload item's own SAP line number, via
    # its PO Item Number (EBELP) -- the same field migo_103.robot matches
    # by. See "HOW LINES ARE MATCHED AND PROCESSED" in this file's
    # Documentation block for the full reasoning.
    #
    # Never fails the run by itself: if this grid isn't present or
    # doesn't carry EBELP for a delivery-referenced document (unconfirmed
    # until a live test), ${item_rows} just comes back empty -- every
    # payload item will then fail to match (see the matched_count == 0
    # guard just below Tick All Item OK Checkboxes's call).
    Log Step    6b    Read item overview grid (EBELP lookup only)
    @{item_rows}=    Extract MIGO Item Table

    # ── STEP 7 -- LOOP THE MATCHED PAYLOAD, open only matched SAP lines ──
    # Loops over ${items} (parsed from ITEMS_JSON_B64 at the top of this
    # keyword) -- NOT over SAP's own delivery lines. For each matched
    # pair, its SAP line is found in ${item_rows} (just extracted above)
    # by EBELP -- same matching field migo_103.robot already uses,
    # falling back to material-code matching only if EBELP isn't
    # resolvable. A delivery/PO line with no matching payload entry is
    # NEVER opened, filled, or ticked -- exactly mirroring how
    # migo_103.robot's own per-item loop ends (loop over the payload,
    # skip-and-log anything not found in the grid, never walk the grid's
    # own lines looking for work to do).
    Log Step    7    LOOP THE MATCHED PAYLOAD -- open, fill, and tick only matched SAP lines
    ${item_results}    ${matched_count}=    Tick All Item OK Checkboxes    ${items}    ${item_rows}
    ${item_count}=    Get Length    ${item_results}

    # No matched pairs at all means the delivery produced nothing
    # receivable. Posting now would either fail or create an empty
    # document, so stop here where the reason is still clear.
    IF    ${item_count} == 0
        Log To Console    No matched items supplied for delivery ${delivery} -- nothing to receive, not posting.
        ${res}=    Evaluate
        ...    __import__('json').dumps({'status': 'FAILED', 'matdoc': 'NOT_POSTED', 'message': 'No matched invoice/PO items were supplied for this delivery. Nothing was posted.', 'item_results': []})
        RETURN    ${res}
    END

    # ${item_count} matched pairs were supplied, but NONE of them could
    # actually be found among this delivery's SAP lines (grid unreadable,
    # or every EBELP/material genuinely absent from it). Every line would
    # be left untouched -- posting now would create an empty document
    # while reporting success, so stop here instead, before Check/Post.
    IF    ${matched_count} == 0
        Log To Console    None of the ${item_count} matched item(s) could be located among this delivery's SAP lines -- nothing was opened or ticked. Stopping before Check/Post.
        ${res}=    Evaluate
        ...    __import__('json').dumps({'status': 'FAILED', 'matdoc': 'NOT_POSTED', 'message': 'None of the matched invoice/PO items could be located in this delivery in SAP. Nothing was posted -- verify the matching and the delivery in SAP.', 'item_results': $item_results})
        RETURN    ${res}
    END

    ${item_results_json}=    Evaluate    __import__('json').dumps($item_results)
    Log To Console    RESULT:ITEM_RESULTS:${item_results_json}

    # NOTE: the draft this file was adapted from re-typed FRBNR/BKTXT again
    # here, identically to STEP 5 above, with no comment explaining why --
    # looked like an accidental copy-paste leftover rather than a deliberate
    # "re-assert before Check" safeguard (nothing in migo_103.robot/
    # migo_105.robot does this), and re-typing header fields this late
    # risks an unexpected popup for no benefit. Removed; flagged in this
    # file's top Documentation block in case it turns out to matter.

    # --- STEP 8: Check (btn[7]) ---
    # SAP's own validation pass, separate from Post. Any popup it raises is
    # read BEFORE being confirmed -- Capture And Dismiss Popup Message both
    # reads the message and clicks confirm internally.
    # --- STEP 7b: validate everything typed, while it is still readable ---
    ${validated}=    Validate Typed Values
    ...    ${delivery}    ${DOC_DATE}    ${POST_DATE}    ${bol_clean}    ${hdr_clean}    ${sloc_status}    ${invoice_status}    ${item_results}
    Log Step    8    Check (btn[7])
    Click Element    ${CHECK_BUTTON}
    Sleep    2s
    ${check_popup_message}=    Capture And Dismiss Popup Message
    Log To Console    CHECK POPUP MESSAGE: "${check_popup_message}"

    # --- STEP 9: Post ---
    ${dry}=    Convert To Upper Case    ${DRY_RUN}
    IF    '${dry}' == 'TRUE'
        Log Step    9    DRY RUN -- Post SKIPPED, nothing committed
        Log To Console    DRY_RUN is TRUE: document filled and checked but NOT posted. Set DRY_RUN to FALSE to post for real.
        ${res}=    Evaluate
        ...    __import__('json').dumps({'status': 'DRY_RUN', 'matdoc': 'NOT_POSTED', 'message': 'DRY_RUN was TRUE -- document filled and checked but deliberately not posted.', 'item_results': $item_results, 'storage_location': $sloc_status, 'vendor_invoice': $invoice_status})
        RETURN    ${res}
    END

    Log Step    9a    Post (1st click)
    Click Element    ${POST_BUTTON}
    Sleep    3s

    # Popup handling first: a popup sitting on wnd[1] makes the status bar
    # underneath it stale or blank, so reading the bar before clearing the
    # popup tells you nothing. Confirm (not dismiss) -- see Confirm Popup.
    ${post_popup_1}=    Capture And Confirm Popup Message
    Log To Console    POST POPUP MESSAGE (1st click): "${post_popup_1}"

    # Now the status bar is readable. Whether Post gets pressed a second
    # time depends on ONE question: did SAP already create the document?
    ${status_after_1}=    Get Value    wnd[0]/sbar
    Log To Console    Status bar after 1st Post: "${status_after_1}"

    # A document number can turn up in EITHER place -- the status bar, or
    # inside the popup itself when the popup was the success confirmation.
    ${matdoc_early}=    Document Was Posted    ${status_after_1}    ${delivery}
    IF    '${matdoc_early}' == '${EMPTY}'
        ${matdoc_early}=    Document Was Posted    ${post_popup_1}    ${delivery}
    END

    ${popup1_present}=    Run Keyword And Return Status
    ...    Should Not Be Empty    ${post_popup_1}
    ${post_popup_2}=    Set Variable    ${EMPTY}

    IF    '${matdoc_early}' != '${EMPTY}'
        # SCENARIO A -- document already posted on the first click.
        # Pressing Post again here would start a SECOND goods receipt, so
        # it is skipped. This branch wins even when a popup appeared,
        # because a popup carrying a document number is a success
        # confirmation, not a validation error.
        Log To Console    Document ${matdoc_early} already posted on the 1st click -- NOT clicking Post again.
    ELSE IF    ${popup1_present}
        # SCENARIO B -- a validation popup with no document number. It has
        # been confirmed above, which closes the dialog but does NOT commit
        # the document, so Post has to be pressed again.
        Log Step    9b    Post (2nd click -- validation popup confirmed, document not yet committed)
        Click Element    ${POST_BUTTON}
        Sleep    3s
        ${post_popup_2}=    Capture And Confirm Popup Message
        Log To Console    POST POPUP MESSAGE (2nd click): "${post_popup_2}"
    ELSE
        # No popup and no document number yet -- the status bar may simply
        # be lagging. STEP 10's polling read below settles it.
        Log To Console    No popup and no document number yet after the 1st Post -- waiting for the status bar.
    END

    # --- STEP 10: Material document number ---
    Log Step    10    Read material document number
    ${status_msg}=    Read Status Bar With Retry    expected_pattern=\\d{8,}
    Log To Console    Final status bar: "${status_msg}"

    # Prefer whichever popup actually had content, most recent first, then
    # fall back to the status bar -- which is often stale or blank when a
    # popup fired over it.
    ${popup2_not_empty}=    Run Keyword And Return Status    Should Not Be Empty    ${post_popup_2}
    ${popup1_not_empty}=    Run Keyword And Return Status    Should Not Be Empty    ${post_popup_1}
    IF    ${popup2_not_empty}
        ${final_message}=    Set Variable    ${post_popup_2}
    ELSE IF    ${popup1_not_empty}
        ${final_message}=    Set Variable    ${post_popup_1}
    ELSE
        ${final_message}=    Set Variable    ${status_msg}
    END

    # Fall back to the number found right after the 1st Post if the final
    # status-bar read has since been cleared or overwritten.
    ${matdoc_final}=    Document Was Posted    ${status_msg}    ${delivery}
    IF    '${matdoc_final}' == '${EMPTY}'
        ${matdoc_final}=    Set Variable    ${matdoc_early}
    END
    @{matches}=    Create List
    IF    '${matdoc_final}' != '${EMPTY}'
        Append To List    ${matches}    ${matdoc_final}
    END
    IF    len($matches) == 0
        Log To Console    No material document number found in the status bar.
        ${res}=    Evaluate
        ...    __import__('json').dumps({'status': 'MANUAL_CHECK_REQUIRED', 'matdoc': 'MANUAL_CHECK_REQUIRED', 'message': $final_message, 'item_results': $item_results, 'storage_location': $sloc_status, 'vendor_invoice': $invoice_status})
        RETURN    ${res}
    END

    Log To Console    POSTED -- material document ${matches}[0]
    ${res}=    Evaluate
    ...    __import__('json').dumps({'status': 'COMPLETED', 'matdoc': $matches[0], 'message': $final_message, 'item_results': $item_results, 'storage_location': $sloc_status, 'vendor_invoice': $invoice_status})
    RETURN    ${res}


Resolve GOITEM Grid Columns
    [Documentation]    Finds the ACTUAL current column index of the two
    ...    fields Extract MIGO Item Table reads out of the item overview
    ...    grid (${GOITEM_TABLE}) -- PO Item Number (EBELP) and Material
    ...    (MATNR, read only for the console log) -- instead of trusting
    ...    the hardcoded ${COL_GOITEM_EBELP}/${COL_GOITEM_MATNR} defaults.
    ...    Adapted directly from migo_103_dynamic_columns.robot's keyword
    ...    of the same name -- see that file's Documentation block ("WHY
    ...    THE HARDCODED INDICES ARE A BUG" / "THE FIX" / "GENUINE
    ...    UNCERTAINTY") for the full reasoning: reads
    ...    GuiTableControl.Columns, matches each column's .Id against the
    ...    field's technical name, and returns whatever index actually
    ...    matched. Never fails the run -- a field that can't be matched
    ...    keeps its hardcoded default and logs a WARNING instead, and if
    ...    the grid itself can't be read at all (e.g. not present for a
    ...    delivery-referenced document -- the one thing about this whole
    ...    EBELP approach that hasn't been confirmed live), the caller
    ...    (Extract MIGO Item Table) treats that as "no EBELP available"
    ...    and the batch-matching loop falls back to material code
    ...    automatically -- see Tick All Item OK Checkboxes.
    ${col_count_res}=    Run Keyword And Ignore Error
    ...    Evaluate    $session.findById('${GOITEM_TABLE}').Columns.Count
    IF    '${col_count_res}[0]' != 'PASS'
        Log To Console    WARNING: could not read ${GOITEM_TABLE}.Columns.Count -- item overview grid may not be present for this delivery-referenced document. Keeping hardcoded column defaults (EBELP=${COL_GOITEM_EBELP}, MATNR=${COL_GOITEM_MATNR}); Extract MIGO Item Table will treat the grid as unavailable.
        RETURN    ${FALSE}
    END
    ${col_count}=    Set Variable    ${col_count_res}[1]
    Log To Console    ${EMPTY}
    Log To Console    ===== GOITEM grid reports ${col_count} column(s) -- Id/Title dump (read this after a supervised run to confirm the match below landed correctly) =====

    @{ids}=    Create List
    FOR    ${i}    IN RANGE    ${col_count}
        ${id_res}=      Run Keyword And Ignore Error
        ...    Evaluate    $session.findById('${GOITEM_TABLE}').Columns.ElementAt(${i}).Id
        ${title_res}=   Run Keyword And Ignore Error
        ...    Evaluate    $session.findById('${GOITEM_TABLE}').Columns.ElementAt(${i}).Title
        ${col_id}=      Set Variable If    '${id_res}[0]' == 'PASS'       ${id_res}[1]       UNKNOWN
        ${col_title}=   Set Variable If    '${title_res}[0]' == 'PASS'    ${title_res}[1]    ${EMPTY}
        Append To List    ${ids}    ${col_id}
        Log To Console    column ${i}: Id="${col_id}"  Title="${col_title}"
    END

    ${ebelp_idx}    ${ebelp_confirmed}=    Find Column Index By Id    ${ids}    GOITEM-EBELP    ${COL_GOITEM_EBELP}    PO Item Number (EBELP)
    ${matnr_idx}    ${matnr_confirmed}=    Find Column Index By Id    ${ids}    GOITEM-MATNR    ${COL_GOITEM_MATNR}    Material (MATNR)
    Set Suite Variable    ${COL_GOITEM_EBELP}    ${ebelp_idx}
    Set Suite Variable    ${COL_GOITEM_MATNR}    ${matnr_idx}
    Log To Console    Resolved column indices in use for this run: EBELP=${COL_GOITEM_EBELP} (confirmed=${ebelp_confirmed}) MATNR=${COL_GOITEM_MATNR} (confirmed=${matnr_confirmed})
    Log To Console    ${EMPTY}

    # EBELP is the ONLY column this whole feature depends on being right --
    # a wrong MATNR is just a cosmetic log issue (MATNR used everywhere
    # else in this file is read separately, straight off the detail
    # screen), but a wrong EBELP means matching a real SAP line against
    # the WRONG payload item and applying that item's batch to it -- wrong
    # data, silently, not a crash. So unlike Find Column Index By Id's own
    # per-field fallback (safe to use for MATNR), an UNCONFIRMED EBELP is
    # never trusted here even though a numeric fallback exists: falling
    # back to a guessed column position for something this consequential
    # is not an acceptable trade. Treat it exactly like the grid being
    # absent -- the caller (Extract MIGO Item Table) already knows to fall
    # back to material-code matching for every line when this returns
    # ${FALSE}.
    IF    not ${ebelp_confirmed}
        Log To Console    WARNING: EBELP column could not be confirmed by Id match (see the warning logged just above) -- refusing to trust a guessed column position for something this consequential. Treating the item overview grid as unusable; every line will use material-code matching instead.
        RETURN    ${FALSE}
    END
    RETURN    ${TRUE}


Find Column Index By Id
    [Documentation]    Searches ${ids} (a list of column Id strings, in
    ...    column-index order) for ${target_id}. Returns TWO values: the
    ...    index to use, and whether it was a genuine Id match (${TRUE})
    ...    or an unconfirmed fallback (${FALSE}) -- unlike
    ...    migo_103_dynamic_columns.robot's keyword of the same name
    ...    (which only returns the index), the caller here needs to know
    ...    WHICH kind of value it got, because Resolve GOITEM Grid
    ...    Columns treats an unconfirmed EBELP very differently from an
    ...    unconfirmed MATNR -- see that keyword.
    [Arguments]    ${ids}    ${target_id}    ${fallback}    ${label}
    ${fallback}=    Convert To Integer    ${fallback}
    ${total}=    Get Length    ${ids}
    FOR    ${i}    IN RANGE    ${total}
        ${this_id}=    Get From List    ${ids}    ${i}
        IF    '${this_id}' == '${target_id}'
            IF    ${i} != ${fallback}
                Log To Console    ${label}: DYNAMIC RESOLUTION DIFFERS from the hardcoded default -- default=${fallback}, resolved=${i} (Id "${target_id}" matched at column ${i}). Using the RESOLVED value for this run.
            ELSE
                Log To Console    ${label}: dynamic resolution matches the hardcoded default (column ${fallback}) -- no drift detected.
            END
            RETURN    ${i}    ${TRUE}
        END
    END
    Log To Console    WARNING: ${label} -- no column with Id "${target_id}" found among ${total} column(s). Falling back to the hardcoded default (column ${fallback}) -- UNCONFIRMED.
    RETURN    ${fallback}    ${FALSE}


Extract MIGO Item Table
    [Documentation]    Reads the item overview grid (list view) ONCE,
    ...    purely to build a line-number -> EBELP lookup for the per-line
    ...    loop in Tick All Item OK Checkboxes -- adapted directly from
    ...    migo_103.robot's keyword of the same name (same scrolling
    ...    logic, same scroll-verification retry, same duplicate-row
    ...    guard -- all proven there, unchanged here).
    ...
    ...    NEVER fails the run. If the grid can't be found at all (e.g.
    ...    Element Should Be Present fails for ${GOITEM_TABLE} -- the one
    ...    thing about this whole approach that's unconfirmed until a
    ...    live test against a real delivery), this returns an EMPTY
    ...    list rather than erroring, and the caller falls back to
    ...    material-code matching for every line -- see "HOW BATCH IS
    ...    MATCHED TO A SAP LINE" in this file's Documentation block.
    # Created up front, before any early exit, so EVERY return path -- the
    # three "give up and fall back" exits below, and the normal one at the
    # end -- returns a real (possibly empty) list. A bare RETURN here would
    # give back None, and @{item_rows}=    Extract MIGO Item Table at the
    # call site requires something iterable -- returning None would crash
    # this keyword's very first call, not silently fall back to it.
    @{item_rows}=    Create List

    ${grid_present}=    Run Keyword And Return Status
    ...    Element Should Be Present    ${GOITEM_TABLE}
    IF    not ${grid_present}
        Log To Console    WARNING: item overview grid (${GOITEM_TABLE}) not found -- this delivery-referenced screen may not show it, or it hasn't rendered yet. No EBELP lookup available; every line will use material-code matching instead.
        RETURN    ${item_rows}
    END

    ${resolved}=    Resolve GOITEM Grid Columns
    IF    not ${resolved}
        Log To Console    WARNING: could not resolve item overview grid columns -- no EBELP lookup available; every line will use material-code matching instead.
        RETURN    ${item_rows}
    END

    @{seen_line_nos}=    Create List
    ${row_idx}=    Set Variable    0
    ${scroll_pos}=    Set Variable    0
    ${visible_rows_res}=    Run Keyword And Ignore Error
    ...    Evaluate    $session.findById('${GOITEM_TABLE}').VisibleRowCount
    IF    '${visible_rows_res}[0]' != 'PASS'
        Log To Console    WARNING: could not read VisibleRowCount from the item overview grid -- no EBELP lookup available; every line will use material-code matching instead.
        RETURN    ${item_rows}
    END
    ${visible_rows}=    Set Variable    ${visible_rows_res}[1]

    WHILE    True
        ${row_in_view}=    Evaluate    ${row_idx} - ${scroll_pos}
        IF    ${row_in_view} >= ${visible_rows}
            ${scroll_pos}=    Set Variable    ${row_idx}
            Run Keyword And Ignore Error
            ...    Evaluate    setattr($session.findById('${GOITEM_TABLE}').verticalScrollbar, 'position', ${scroll_pos})
            Sleep    0.5s
            ${actual_pos_res}=    Run Keyword And Ignore Error
            ...    Evaluate    $session.findById('${GOITEM_TABLE}').verticalScrollbar.position
            ${scroll_confirmed}=    Run Keyword And Return Status
            ...    Should Be Equal As Integers    ${actual_pos_res}[1]    ${scroll_pos}
            IF    not ${scroll_confirmed}
                Log To Console    WARNING: item overview grid scroll to ${scroll_pos} did not land (grid reports position ${actual_pos_res}[1]) -- retrying once.
                Run Keyword And Ignore Error
                ...    Evaluate    setattr($session.findById('${GOITEM_TABLE}').verticalScrollbar, 'position', ${scroll_pos})
                Sleep    0.8s
                ${actual_pos_res2}=    Run Keyword And Ignore Error
                ...    Evaluate    $session.findById('${GOITEM_TABLE}').verticalScrollbar.position
                ${scroll_confirmed2}=    Run Keyword And Return Status
                ...    Should Be Equal As Integers    ${actual_pos_res2}[1]    ${scroll_pos}
                IF    not ${scroll_confirmed2}
                    Log To Console    WARNING: item overview grid scroll to ${scroll_pos} STILL not confirmed after retry (grid reports position ${actual_pos_res2}[1]) -- proceeding, but rows read from here on may be misaligned/duplicated.
                END
            END
        END
        ${grid_row}=    Evaluate    ${row_idx} - ${scroll_pos}

        ${zeile_path}=    Set Variable    ${GOITEM_TABLE}/btnGOITEM-ZEILE[0,${grid_row}]
        ${line_res}=    Run Keyword And Ignore Error    Get Value    ${zeile_path}
        IF    '${line_res}[0]' == 'FAIL'
            BREAK
        END
        ${line_no}=    Clean Value    ${line_res}[1]
        ${line_no_stripped}=    Replace String    ${line_no}    _    ${EMPTY}
        ${line_no_stripped}=    Replace String    ${line_no_stripped}    .    ${EMPTY}
        ${line_no_stripped}=    Strip String    ${line_no_stripped}
        IF    '${line_no_stripped}' == '${EMPTY}'
            BREAK
        END

        ${mat_path}=    Set Variable    ${GOITEM_TABLE}/ctxtGOITEM-MATNR[${COL_GOITEM_MATNR},${grid_row}]
        ${mat_res}=    Run Keyword And Ignore Error    Get Value    ${mat_path}
        ${material}=    Set Variable If    '${mat_res}[0]' == 'PASS'    ${mat_res}[1]    ${EMPTY}

        ${ebelp_path}=    Set Variable    ${GOITEM_TABLE}/ctxtGOITEM-EBELP[${COL_GOITEM_EBELP},${grid_row}]
        ${ebelp_res}=    Run Keyword And Ignore Error    Get Value    ${ebelp_path}
        ${ebelp}=    Set Variable If    '${ebelp_res}[0]' == 'PASS'    ${ebelp_res}[1]    ${EMPTY}

        ${already_seen}=    Run Keyword And Return Status
        ...    List Should Contain Value    ${seen_line_nos}    ${line_no}
        IF    ${already_seen}
            Log To Console    WARNING: item overview grid line ${line_no} already captured earlier in this extraction -- skipping duplicate row ${row_idx} (likely caused by a scroll that didn't land). Not appending.
        ELSE
            Append To List    ${seen_line_nos}    ${line_no}
            &{row_data}=    Create Dictionary
            ...    line_no=${line_no}
            ...    ebelp=${ebelp}
            ...    material=${material}
            Append To List    ${item_rows}    ${row_data}
            Log To Console    GOITEM GRID ROW ${row_idx}: line=${line_no} ebelp=${ebelp} material=${material}
        END

        ${row_idx}=    Evaluate    ${row_idx} + 1
        IF    ${row_idx} >= 100    BREAK
    END

    ${total_rows}=    Get Length    ${item_rows}
    Log To Console    Extracted ${total_rows} row(s) from the item overview grid (EBELP lookup)
    RETURN    ${item_rows}


Find Grid Row For Item No
    [Documentation]    PRIMARY matching path (see "HOW LINES ARE MATCHED
    ...    AND PROCESSED" in this file's Documentation block). Looks up
    ...    the item overview grid row (${item_rows}, extracted once by
    ...    Extract MIGO Item Table) whose PO Item Number (EBELP) matches
    ...    ${item_no} -- the same field migo_103.robot already matches
    ...    by, and the same field this file's own matched-pairs table
    ...    already labels "PO item number (SAP EBELP)". The matched row's
    ...    line_no is what tells the caller which single SAP line to
    ...    open -- this is the direction migo_103.robot's own loop
    ...    matches in (payload item -> SAP line), not the other way
    ...    round.
    ...
    ...    Returns the matched row (a dictionary with line_no/ebelp/
    ...    material) or ${NONE} if no row in the grid carries this EBELP.
    [Arguments]    ${item_no}    ${item_rows}
    ${item_no_norm}=    Evaluate    '${item_no}'.strip().lstrip('0') or '0'
    IF    '${item_no_norm}' == '0'    RETURN    ${NONE}
    FOR    ${row}    IN    @{item_rows}
        ${row_ebelp}=    Clean Value    ${row}[ebelp]
        ${row_ebelp_norm}=    Evaluate    '${row_ebelp}'.strip().lstrip('0') or '0'
        IF    '${row_ebelp_norm}' == '${item_no_norm}'
            RETURN    ${row}
        END
    END
    RETURN    ${NONE}


Find Grid Row For Material
    [Documentation]    FALLBACK matching path, used only for a payload
    ...    item whose PO Item Number (EBELP) wasn't found among this
    ...    delivery's SAP lines (see Tick All Item OK Checkboxes) -- e.g.
    ...    the item overview grid wasn't present, or genuinely didn't
    ...    carry this EBELP. Looks up the item overview grid row
    ...    (${item_rows}) whose material code matches ${material}. See
    ...    this file's Documentation block ("HOW LINES ARE MATCHED AND
    ...    PROCESSED") for the full reasoning and its limitation (this is
    ...    the weaker of the two matching strategies -- see Find Grid Row
    ...    For Item No for the primary one).
    ...
    ...    Returns the matched row (a dictionary with line_no/ebelp/
    ...    material) or ${NONE} if no row in the grid carries this
    ...    material code.
    [Arguments]    ${material}    ${item_rows}
    ${material_norm}=    Evaluate    '${material}'.strip().lstrip('0') or '0'
    IF    '${material_norm}' == '0'    RETURN    ${NONE}
    FOR    ${row}    IN    @{item_rows}
        ${row_material}=    Clean Value    ${row}[material]
        ${row_mat_norm}=    Evaluate    '${row_material}'.strip().lstrip('0') or '0'
        IF    '${row_mat_norm}' == '${material_norm}'
            RETURN    ${row}
        END
    END
    RETURN    ${NONE}


Set Batch For Line
    [Documentation]    Opens the BATCH tab on the currently-open line and
    ...    types ${batch_value} into ctxtGOITEM-CHARG.
    ...
    ...    Read back rather than assumed, for the same reason the storage
    ...    location is: SAP silently rejects a batch that does not exist
    ...    for this line's material, leaving the field blank. Without the
    ...    read-back the document posts with no batch while every step
    ...    reports success.
    ...
    ...    Never fails the run -- a rejected batch on one line is recorded
    ...    and reported, not treated as a reason to abandon a document
    ...    that is otherwise fine.
    [Arguments]    ${line_num_str}    ${batch_value}
    ${batch_tab}=      Set Variable    ${DETAIL_BASE}/tabsTS_GOITEM/tabpOK_GOITEM_BATCH
    ${batch_field}=    Set Variable    ${batch_tab}/ssubSUB_TS_GOITEM_BATCH:SAPLMIGO:0335/ctxtGOITEM-CHARG

    ${tab_ok}=    Run Keyword And Return Status    Click Element    ${batch_tab}
    Sleep    ${FIELD_SETTLE}
    Dismiss Any Popup
    IF    not ${tab_ok}
        Log To Console    WARNING: line ${line_num_str} -- BATCH tab would not open; batch NOT set.
        RETURN    BATCH TAB WOULD NOT OPEN -- NOT SET
    END

    ${typed}=    Run Keyword And Return Status
    ...    Safe Input Text    ${batch_field}    ${batch_value}
    IF    not ${typed}
        Log To Console    WARNING: line ${line_num_str} -- batch field not writable.
        RETURN    BATCH FIELD NOT WRITABLE -- NOT SET
    END

    Run Keyword And Ignore Error    Set Focus    ${batch_field}
    Send VKey    0
    Sleep    ${FIELD_SETTLE}
    Dismiss Any Popup

    ${read}=    Run Keyword And Ignore Error    Get Value    ${batch_field}
    ${actual}=    Set Variable If    '${read}[0]' == 'PASS'    ${read}[1]    ${EMPTY}
    ${actual}=    Clean Value    ${actual}

    IF    '${actual}' == '${batch_value}'
        Log To Console    Line ${line_num_str}: batch ${batch_value} accepted.
        RETURN    ${batch_value}
    END

    Log To Console    WARNING: line ${line_num_str} -- typed batch "${batch_value}" but SAP shows "${actual}". It may not exist for this line's material.
    RETURN    REJECTED -- typed "${batch_value}", SAP shows "${actual}"


Tick All Item OK Checkboxes
    [Documentation]    Loops over ${items} -- the matched invoice<->PO
    ...    pairs decoded from ITEMS_JSON_B64 -- NOT over SAP's own
    ...    delivery lines. This mirrors migo_103.robot's own per-item
    ...    loop exactly (FOR ${i} IN RANGE ${total}, where ${total} is
    ...    the PAYLOAD's length): for each payload item, its SAP line is
    ...    located in ${item_rows} (extracted once from the item overview
    ...    grid by Extract MIGO Item Table) by PO Item Number (EBELP) --
    ...    PRIMARY match, via Find Grid Row For Item No, same field
    ...    migo_103.robot matches by -- falling back to material code
    ...    (Find Grid Row For Material) only when EBELP can't place it.
    ...    ONLY the matched line is opened, filled, and ticked. A payload
    ...    item that matches no SAP line is skipped and logged -- the
    ...    corresponding delivery/PO line (if any) is never opened, never
    ...    edited, never ticked. See "HOW LINES ARE MATCHED AND
    ...    PROCESSED" in this file's Documentation block.
    ...
    ...    CORRECTED DESIGN: an earlier version of this keyword walked
    ...    every SAP line sequentially (1, 2, 3... until SAP rejected the
    ...    next number, the way migo_105.robot does) and ticked all of
    ...    them, using the payload only to decide what batch to type.
    ...    That is backwards for this feature: if the UI shows 3 matched
    ...    pairs out of a 5-line PO/delivery, only those 3 lines should
    ...    ever be touched -- the other 2 must stay completely untouched,
    ...    not just unbatched. Rewritten to loop the payload instead, the
    ...    same shape migo_103.robot already uses and ends its own line
    ...    loop with.
    ...
    ...    SANITY CHECK: after navigating to a matched line, the detail
    ...    screen's own MATNR is re-read and compared (leading-zero-
    ...    normalized) against the overview grid's MATNR for that same
    ...    row. Mirrors migo_103.robot's own fix for this (2026-08-25) --
    ...    a mismatch is recorded in item_results as "VERIFY THIS LINE IN
    ...    SAP" rather than only logged, so it surfaces in the UI/DB
    ...    instead of looking like a silent success.
    ...
    ...    Returns TWO values: per-item dicts for the result JSON
    ...    (item_no, line, material, batch, status -- so the UI/DB
    ...    records exactly which payload items were matched, which SAP
    ...    line each landed on, and which matched a batch, rather than
    ...    just "it posted"), and a count of how many payload items were
    ...    actually matched and processed (used by the caller to detect
    ...    "everything was supplied but nothing could be located in SAP"
    ...    and stop before Check/Post rather than post an empty document).
    [Arguments]    ${items}    ${item_rows}
    @{item_results}=    Create List
    @{used_item_nos}=    Create List
    @{used_line_nos}=    Create List
    ${matched_count}=    Set Variable    ${0}
    ${total}=    Get Length    ${items}

    FOR    ${i}    IN RANGE    ${total}
        ${item}=              Get From List    ${items}    ${i}
        ${target_item_no}=    Get From Dictionary    ${item}    item_no    default=${EMPTY}
        ${target_item_no}=    Clean Value    ${target_item_no}
        ${target_material}=   Get From Dictionary    ${item}    material_code    default=${EMPTY}
        ${target_material}=   Clean Value    ${target_material}
        ${target_batch}=      Get From Dictionary    ${item}    batch    default=${EMPTY}
        ${target_batch}=      Clean Value    ${target_batch}
        ${target_qty_actual}=      Get From Dictionary    ${item}    qty_actual    default=${EMPTY}
        ${target_qty_actual}=      Clean Value    ${target_qty_actual}
        ${target_qty_expected}=    Get From Dictionary    ${item}    qty_expected    default=${EMPTY}
        ${target_qty_expected}=    Clean Value    ${target_qty_expected}

        IF    $target_item_no in $used_item_nos and '${target_item_no}' != '${EMPTY}'
            Log To Console    PO item "${target_item_no}" already processed earlier in this list -- skipping duplicate.
            &{row}=    Create Dictionary
            ...    item_no=${target_item_no}    line=${EMPTY}    material=${target_material}
            ...    batch=${EMPTY}    status=duplicate item_no, skipped
            Append To List    ${item_results}    ${row}
            CONTINUE
        END

        # --- Find this item's SAP line: EBELP primary, material fallback ---
        ${matched_row}=    Find Grid Row For Item No    ${target_item_no}    ${item_rows}
        ${match_kind}=    Set Variable    PO item number (EBELP)
        IF    $matched_row == $NONE
            Log To Console    PO item "${target_item_no}" not matched by EBELP in the item overview grid -- trying material-code fallback.
            ${matched_row}=    Find Grid Row For Material    ${target_material}    ${item_rows}
            ${match_kind}=    Set Variable    material code (FALLBACK)
        END

        IF    $matched_row == $NONE
            Log To Console    WARNING: matched item (PO item "${target_item_no}", material "${target_material}") was not found among this delivery's SAP lines (checked EBELP, then material code) -- skipping. Nothing opened or touched for this item.
            &{row}=    Create Dictionary
            ...    item_no=${target_item_no}    line=${EMPTY}    material=${target_material}
            ...    batch=${EMPTY}    status=item not found in this delivery -- not touched
            Append To List    ${item_results}    ${row}
            CONTINUE
        END

        # --- Same physical SAP line claimed twice would mean two payload
        # items overwriting each other on one line -- guard against it the
        # same way migo_103.robot guards duplicate item_no's, just on the
        # other side of the match.
        ${grid_line_no}=    Clean Value    ${matched_row}[line_no]
        ${line_already_used}=    Run Keyword And Return Status
        ...    List Should Contain Value    ${used_line_nos}    ${grid_line_no}
        IF    ${line_already_used}
            Log To Console    WARNING: SAP line ${grid_line_no} was already matched to an earlier payload item this run -- skipping "${target_item_no}" to avoid double-processing the same line.
            &{row}=    Create Dictionary
            ...    item_no=${target_item_no}    line=${grid_line_no}    material=${target_material}
            ...    batch=${EMPTY}    status=SAP line ${grid_line_no} already claimed by another matched item -- skipped
            Append To List    ${item_results}    ${row}
            CONTINUE
        END

        IF    '${target_item_no}' != '${EMPTY}'
            Append To List    ${used_item_nos}    ${target_item_no}
        END
        Append To List    ${used_line_nos}    ${grid_line_no}
        ${matched_count}=    Evaluate    ${matched_count} + 1
        Log To Console    PO item "${target_item_no}" -> matched SAP grid line ${grid_line_no} via ${match_kind} (ebelp=${matched_row}[ebelp], material=${matched_row}[material])

        # --- Navigate to that one line only ---
        Safe Input Text    ${DETAIL_BASE}/txtGODYNPRO-DETAIL_ZEILE    ${grid_line_no}
        Set Focus    ${DETAIL_BASE}/txtGODYNPRO-DETAIL_ZEILE
        Send VKey    0
        Sleep    1s
        Dismiss Any Popup

        # --- Sanity check: detail-screen MATNR vs. the grid's MATNR for
        # this matched row (mirrors migo_103.robot's 2026-08-25 fix) ---
        ${matnr_res}=    Run Keyword And Ignore Error
        ...    Get Value
        ...    ${DETAIL_BASE}/tabsTS_GOITEM/tabpOK_GOITEM_MATERIAL/ssubSUB_TS_GOITEM_MATERIAL:SAPLMIGO:0301/ctxtGOITEM-MATNR
        ${matnr_mismatch}=    Set Variable    ${False}
        ${material}=    Set Variable    ${matched_row}[material]
        IF    '${matnr_res}[0]' == 'PASS'
            ${matnr_check}=    Clean Value    ${matnr_res}[1]
            Log To Console    Line ${grid_line_no} READBACK: MATNR="${matnr_check}" (grid showed "${matched_row}[material]")
            ${matnr_check_norm}=    Evaluate    '${matnr_check}'.strip().lstrip('0') or '0'
            ${matnr_grid_norm}=    Evaluate    '${matched_row}[material]'.strip().lstrip('0') or '0'
            IF    '${matnr_check_norm}' != '${matnr_grid_norm}'
                Log To Console    WARNING: navigated to line ${grid_line_no} but detail-screen MATNR doesn't match the overview grid's MATNR for this item -- flagging in item_results.
                ${matnr_mismatch}=    Set Variable    ${True}
            END
            ${material}=    Set Variable    ${matnr_check}
        ELSE
            Log To Console    Line ${grid_line_no} READBACK: MATNR field could not be read back (${matnr_res}[1])
        END

        # --- QUANTITY tab: Actual Quantity (ERFMG) + Delivery Note Quantity
        # (LSMNG) -- same fields, same tab, same order as migo_103.robot's
        # own Quantity tab block. Counter-intuitive but confirmed mapping:
        # the payload's qty_actual (UI's "Delivery Qty" column) goes into
        # ERFMG, and qty_expected (UI's "Qty" column) goes into LSMNG --
        # this exact same mapping is already used by execute_migo_101_sap
        # in rf_runner.py and by migo_103.robot, so it's kept consistent
        # here rather than "fixed" to look more intuitive.
        Click Element
        ...    ${DETAIL_BASE}/tabsTS_GOITEM/tabpOK_GOITEM_QUANTITIES
        Sleep    1s

        ${qty_base}=    Set Variable
        ...    ${DETAIL_BASE}/tabsTS_GOITEM/tabpOK_GOITEM_QUANTITIES/ssubSUB_TS_GOITEM_QUANTITIES:SAPLMIGO:0315

        Set Focus     ${qty_base}/txtGOITEM-ERFMG
        Safe Input Text    ${qty_base}/txtGOITEM-ERFMG    ${target_qty_actual}

        Set Focus     ${qty_base}/txtGOITEM-LSMNG
        Safe Input Text    ${qty_base}/txtGOITEM-LSMNG    ${target_qty_expected}

        Send VKey    0
        Sleep    1s
        Dismiss Any Popup

        # Same cheap read-back-before-Post reasoning as the MATNR/header
        # checks above.
        ${erfmg_check}=    Run Keyword And Ignore Error    Get Value    ${qty_base}/txtGOITEM-ERFMG
        ${lsmng_check}=    Run Keyword And Ignore Error    Get Value    ${qty_base}/txtGOITEM-LSMNG
        Log To Console    Line ${grid_line_no} READBACK: ERFMG(actual qty)="${erfmg_check}[1]" LSMNG(DN qty)="${lsmng_check}[1]"

        # --- BATCH tab, BEFORE Item OK ---
        # Same ordering reason as the storage location on line 1: ticking
        # Item OK and then editing the line can clear the flag on some
        # MIGO screens, so every field is written while the line is still
        # open for editing and the tick is the last thing done to it.
        ${batch_status}=    Set Variable    not supplied -- SAP auto-assigns if this material is batch-managed
        IF    '${target_batch}' != '${EMPTY}'
            ${batch_status}=    Set Batch For Line    ${grid_line_no}    ${target_batch}
        END

        # --- Tick Item OK -- last ---
        ${ticked}=    Run Keyword And Return Status
        ...    Select Checkbox
        ...    ${DETAIL_BASE}/subSUB_DETAIL_TAKE:SAPLMIGO:0304/chkGODYNPRO-DETAIL_TAKE
        Sleep    ${FIELD_SETTLE}

        IF    ${matnr_mismatch}
            ${line_status}=    Set Variable    POSTED BUT MATERIAL READBACK DID NOT MATCH THE MATCHED DELIVERY LINE -- VERIFY THIS LINE IN SAP
        ELSE IF    not ${ticked}
            ${line_status}=    Set Variable    ITEM OK COULD NOT BE TICKED -- VERIFY THIS LINE IN SAP
            Log To Console    WARNING: line ${grid_line_no} (${material}) -- Item OK checkbox could NOT be ticked.
        ELSE
            ${line_status}=    Set Variable    item OK ticked
            Log To Console    Line ${grid_line_no} (${material}): Item OK ticked.
        END

        &{row}=    Create Dictionary
        ...    item_no=${target_item_no}
        ...    line=${grid_line_no}
        ...    material=${material}
        ...    batch=${batch_status}
        ...    status=${line_status}
        Append To List    ${item_results}    ${row}
    END

    ${count}=    Get Length    ${item_results}
    Log To Console    Item OK loop finished -- ${matched_count} of ${total} matched item(s) located and processed (${count} total result row(s), including skipped/not-found).
    RETURN    ${item_results}    ${matched_count}


Validate Typed Values
    [Documentation]    Reads every value this bot typed back off the
    ...    screen and returns them as a dict, so the result JSON records
    ...    what SAP ACTUALLY HOLDS rather than what was sent.
    ...
    ...    Run immediately before Check/Post, which is the last moment the
    ...    fields are still readable -- after Post the screen resets and
    ...    the evidence is gone.
    ...
    ...    Each field is reported as typed / actual / ok, because those
    ...    three together distinguish the three outcomes that otherwise
    ...    look identical from the outside: the value landed, the value
    ...    never landed, or SAP replaced it with something else.
    ...
    ...    Storage location and per-line batch are NOT re-read here --
    ...    each was already read back at the point it was typed (the
    ...    storage-location fill for ${storage}, Set Batch For Line for
    ...    each line's batch), because those fields live on subscreens/
    ...    tabs that are no longer open by the time this keyword runs.
    ...    ${invoice} is never typed into SAP at all for MIGO 101 (view-
    ...    only field, see STEP 5b) -- it arrives here purely as the
    ...    already-built status string for the record. ${storage} and
    ...    ${invoice} both arrive here as already-resolved status
    ...    strings; this keyword just folds them into the one JSON blob
    ...    together with the header fields it DOES read fresh (delivery/
    ...    BOL/header text, all still on the visible header screen at
    ...    this point).
    [Arguments]    ${delivery}    ${doc_date}    ${post_date}    ${bol}    ${header_text}    ${storage}    ${invoice}    ${item_results}
    ${firstline}=    Get Firstline Path

    ${d_read}=    Run Keyword And Ignore Error    Get Value    ${firstline}/${DELIVERY_SUBSCREEN}
    ${d_act}=     Set Variable If    '${d_read}[0]' == 'PASS'    ${d_read}[1]    ${EMPTY}
    ${d_act}=     Clean Value    ${d_act}

    ${dd_read}=    Run Keyword And Ignore Error    Get Value    ${HEADER_BASE}/ctxtGOHEAD-BLDAT
    ${dd_act}=     Set Variable If    '${dd_read}[0]' == 'PASS'    ${dd_read}[1]    ${EMPTY}

    ${pd_read}=    Run Keyword And Ignore Error    Get Value    ${HEADER_BASE}/ctxtGOHEAD-BUDAT
    ${pd_act}=     Set Variable If    '${pd_read}[0]' == 'PASS'    ${pd_read}[1]    ${EMPTY}

    ${b_read}=    Run Keyword And Ignore Error    Get Value    ${HEADER_BASE}/txtGOHEAD-FRBNR
    ${b_act}=     Set Variable If    '${b_read}[0]' == 'PASS'    ${b_read}[1]    ${EMPTY}
    ${b_act}=     Clean Value    ${b_act}

    ${h_read}=    Run Keyword And Ignore Error    Get Value    ${HEADER_BASE}/txtGOHEAD-BKTXT
    ${h_act}=     Set Variable If    '${h_read}[0]' == 'PASS'    ${h_read}[1]    ${EMPTY}
    ${h_act}=     Clean Value    ${h_act}

    ${validated}=    Evaluate
    ...    {'delivery_number': {'typed': $delivery, 'actual': $d_act, 'ok': $delivery == $d_act}, 'doc_date': {'typed': $doc_date, 'actual': $dd_act, 'ok': $doc_date == $dd_act}, 'post_date': {'typed': $post_date, 'actual': $pd_act, 'ok': $post_date == $pd_act}, 'bill_of_lading': {'typed': $bol, 'actual': $b_act, 'ok': $bol == $b_act}, 'header_text': {'typed': $header_text, 'actual': $h_act, 'ok': $header_text == $h_act}, 'storage_location': $storage, 'vendor_invoice': $invoice, 'batch': {'per_line': [{'line': r['line'], 'material': r['material'], 'batch': r['batch']} for r in $item_results]}, 'lines': $item_results}

    ${json}=    Evaluate    __import__('json').dumps($validated)
    Log To Console    ${EMPTY}
    Log To Console    ===== TYPED VALUES VALIDATION =====
    Log To Console    RESULT:TYPED_VALUES_JSON:${json}

    @{bad}=    Evaluate    [k for k in ('delivery_number','doc_date','post_date','bill_of_lading','header_text') if not $validated[k]['ok']]
    IF    len($bad) > 0
        Log To Console    WARNING: these fields do NOT match what was typed: ${bad}. See the JSON above for typed vs actual.
    ELSE
        Log To Console    All header fields read back correctly.
    END

    RETURN    ${validated}



Read Status Bar With Retry
    [Documentation]    The status bar can lag behind the posting itself, so
    ...    it's polled rather than read once. Returns whatever it last saw
    ...    if the pattern never matches -- the caller decides what that
    ...    means.
    [Arguments]    ${expected_pattern}=\\d{8,}
    ${msg}=    Set Variable    ${EMPTY}
    FOR    ${attempt}    IN RANGE    1    6
        ${msg}=    Get Value    wnd[0]/sbar
        Log To Console    Status bar attempt ${attempt}: "${msg}"
        ${matched}=    Run Keyword And Return Status
        ...    Should Match Regexp    ${msg}    ${expected_pattern}
        IF    ${matched}    RETURN    ${msg}
        Sleep    1s
    END
    Log To Console    Status bar timed out. Last read: "${msg}"
    RETURN    ${msg}


Safe Input Text
    [Documentation]    Retries on failure. Two causes seen in practice: SAP
    ...    still repainting when the element reference was taken (a wait
    ...    fixes it), or a popup silently opened on top of wnd[0] and is
    ...    blocking the field (a wait does NOT fix it -- the popup has to
    ...    be dismissed). So: dismiss, then retry.
    [Arguments]    ${locator}    ${value}
    ${ok}=    Run Keyword And Return Status    Input Text    ${locator}    ${value}
    IF    not ${ok}
        Log To Console    Input failed on ${locator} -- dismissing any popup and retrying.
        Dismiss Any Popup
        Sleep    1s
        ${ok2}=    Run Keyword And Return Status    Input Text    ${locator}    ${value}
        IF    not ${ok2}
            Dismiss Any Popup
            Sleep    2s
            Input Text    ${locator}    ${value}
        END
    END


Clean Value
    [Documentation]    Strips whitespace and currency symbols but keeps the
    ...    full multi-word value intact. An earlier version ended with
    ...    Split String + [0] and returned only the first word, silently
    ...    truncating multi-word remarks.
    [Arguments]    ${raw_value}
    ${val}=        Convert To String    ${raw_value}
    ${cleaned}=    Strip String    ${val}
    ${cleaned}=    Replace String    ${cleaned}    ₹    ${EMPTY}
    ${cleaned}=    Replace String    ${cleaned}    $    ${EMPTY}
    ${cleaned}=    Replace String    ${cleaned}    €    ${EMPTY}
    ${cleaned}=    Replace String    ${cleaned}    £    ${EMPTY}
    ${cleaned}=    Replace String    ${cleaned}    ,    ${EMPTY}
    ${cleaned}=    Strip String    ${cleaned}
    RETURN         ${cleaned}


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


Dump Element Texts
    [Documentation]    Recursively collects non-empty .Text values from a
    ...    live SAP GUI element (a COM object, not a path string) and its
    ...    children, up to 4 levels deep. Generic fallback for popups whose
    ...    structure isn't confirmed via a GUI Scripting recording.
    [Arguments]    ${element}    ${depth}=${0}
    @{collected}=    Create List
    IF    ${depth} > 4
        RETURN    ${collected}
    END

    ${text_res}=    Run Keyword And Ignore Error    Evaluate    $element.Text
    IF    '${text_res}[0]' == 'PASS'
        ${text}=    Clean Value    ${text_res}[1]
        ${text_not_empty}=    Run Keyword And Return Status    Should Not Be Empty    ${text}
        IF    ${text_not_empty}
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


Confirm Popup
    [Documentation]    Presses the CONFIRM button on the popup currently on
    ...    wnd[1] -- deliberately not the same thing as dismissing it.
    ...
    ...    Dismiss Any Popup blindly presses wnd[1]/tbar[0]/btn[0]. On a
    ...    plain message box that IS the green-tick Continue, so it happens
    ...    to work. But on a Yes/No question popup the affirmative answer
    ...    is btnSPOP-OPTION1 in the popup BODY, and SAP's default focus on
    ...    those is usually "No" -- so a blind toolbar click can answer No,
    ...    or cancel out, and the document never posts.
    ...
    ...    Order: OPTION1 (Yes) first, green-tick Continue as fallback.
    ...    btn[12] (Cancel) and OPTION2 (No) are never pressed here.
    ${yes}=    Run Keyword And Return Status
    ...    Evaluate    $session.findById('wnd[1]/usr/btnSPOP-OPTION1').press()
    IF    ${yes}
        Log To Console    Popup confirmed (Yes / OPTION1).
        Sleep    1s
        RETURN    CONFIRMED_YES
    END

    ${tick}=    Run Keyword And Return Status
    ...    Evaluate    $session.findById('wnd[1]/tbar[0]/btn[0]').press()
    IF    ${tick}
        Log To Console    Popup confirmed (green tick / Continue).
        Sleep    1s
        RETURN    CONFIRMED_CONTINUE
    END

    Log To Console    WARNING: popup present but neither Yes nor Continue could be pressed.
    RETURN    NOT_CONFIRMED


Capture And Confirm Popup Message
    [Documentation]    Same reading logic as Capture And Dismiss Popup
    ...    Message, but CONFIRMS the popup instead of blindly clicking its
    ...    default button -- see Confirm Popup for why that distinction
    ...    matters at the Post step.
    ...
    ...    Returns the popup text, or an empty string when no popup was
    ...    present. The caller uses that emptiness plus the status bar to
    ...    decide whether Post needs pressing a second time.
    ${popup_present}=    Run Keyword And Return Status    Element Should Be Present    wnd[1]
    ${message}=    Set Variable    ${EMPTY}
    IF    not ${popup_present}
        RETURN    ${message}
    END

    FOR    ${line}    IN    1    2    3    4
        ${res}=    Run Keyword And Ignore Error
        ...    Evaluate    $session.findById('wnd[1]/usr/txtSPOP-TEXT_LINE${line}').Text
        IF    '${res}[0]' == 'PASS'
            ${line_text}=    Clean Value    ${res}[1]
            ${line_not_empty}=    Run Keyword And Return Status    Should Not Be Empty    ${line_text}
            IF    ${line_not_empty}
                ${message}=    Catenate    SEPARATOR=${SPACE}    ${message}    ${line_text}
                ${message}=    Strip String    ${message}
            END
        END
    END

    ${message_is_empty}=    Run Keyword And Return Status    Should Be Empty    ${message}
    IF    ${message_is_empty}
        ${popup_root}=    Run Keyword And Ignore Error
        ...    Evaluate    $session.findById('wnd[1]')
        IF    '${popup_root}[0]' == 'PASS'
            @{all_texts}=    Dump Element Texts    ${popup_root}[1]
            @{meaningful}=    Create List
            FOR    ${t}    IN    @{all_texts}
                ${tlen}=    Get Length    ${t}
                IF    ${tlen} >= 12
                    Append To List    ${meaningful}    ${t}
                END
            END
            # CHANGED (2026-10-22): keep everything SAP shows in the popup (title, headings, messages) but drop
            # lines that repeat (e.g. "Enter Location" appearing several times).
            ${message}=    Evaluate    ' | '.join(dict.fromkeys($meaningful))
        END
    END

    Log To Console    Popup message captured: "${message}"
    Confirm Popup
    RETURN    ${message}


Document Was Posted
    [Documentation]    TRUE when a message carries a material document
    ...    number, i.e. SAP has already committed the document.
    ...
    ...    Material documents are 10 digits starting with 5 (5000077541).
    ...    The delivery number is 8 digits (84028010), so it is excluded
    ...    explicitly -- a loose digit match would treat a message that
    ...    merely mentions the delivery as proof of a successful post, and
    ...    then skip a Post that never actually happened.
    [Arguments]    ${message}    ${delivery}
    @{strict}=    Get Regexp Matches    ${message}    \\b5\\d{9}\\b
    IF    len($strict) > 0
        RETURN    ${strict}[0]
    END

    @{loose}=    Get Regexp Matches    ${message}    \\d{8,12}
    @{filtered}=    Evaluate    [m for m in $loose if m != '${delivery}']
    IF    len($filtered) > 0
        RETURN    ${filtered}[0]
    END

    RETURN    ${EMPTY}



Capture And Dismiss Popup Message
    [Documentation]    If a popup (wnd[1]) is present, reads its message
    ...    text and THEN dismisses it (clicks wnd[1]/tbar[0]/btn[0]).
    ...    Reading first is the whole point -- a popup clicked away unread
    ...    leaves a failed post with no explanation.
    ...
    ...    Tries the standard message-line fields (txtSPOP-TEXT_LINE1..4)
    ...    first, then falls back to a recursive text dump for SAP's
    ...    table-style "Display logs" popups, which don't use those fields
    ...    at all. Short strings are filtered out of the dump because they
    ...    are column headers and icon captions, not message content.
    ...
    ...    Returns an empty string when no popup was present -- and the
    ...    caller uses that emptiness to decide whether a second Post click
    ...    is needed, so this return value matters beyond logging.
    ${popup_present}=    Run Keyword And Return Status    Element Should Be Present    wnd[1]
    ${message}=    Set Variable    ${EMPTY}
    IF    ${popup_present}
        FOR    ${line}    IN    1    2    3    4
            ${res}=    Run Keyword And Ignore Error
            ...    Evaluate    $session.findById('wnd[1]/usr/txtSPOP-TEXT_LINE${line}').Text
            IF    '${res}[0]' == 'PASS'
                ${line_text}=    Clean Value    ${res}[1]
                ${line_not_empty}=    Run Keyword And Return Status    Should Not Be Empty    ${line_text}
                IF    ${line_not_empty}
                    ${message}=    Catenate    SEPARATOR=${SPACE}    ${message}    ${line_text}
                    ${message}=    Strip String    ${message}
                END
            END
        END

        ${message_is_empty}=    Run Keyword And Return Status    Should Be Empty    ${message}
        IF    ${message_is_empty}
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
                # CHANGED (2026-10-22): keep everything SAP shows in the popup (title, headings, messages) but drop
                # lines that repeat (e.g. "Enter Location" appearing several times).
                ${message}=    Evaluate    ' | '.join(dict.fromkeys($meaningful))
            END
        END

        Log To Console    Popup message captured: "${message}"
        Run Keyword And Ignore Error    Click Element    wnd[1]/tbar[0]/btn[0]
        Sleep    1s
    END
    RETURN    ${message}


Close SAP Session
    Log Step    99    Close SAP session
    Coordinated Close SAP Session    ${session}
