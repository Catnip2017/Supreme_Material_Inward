*** Settings ***
Documentation     PO Fetch — SAP ME23N PO Line Items Extractor
...               Enters PO number, reads all line items from the grid,
...               navigates to India tab per item for HSN/SAC.
...               Amount logic:
...                 - Multiple lines: amount per line = qty x net_price, total = sum
...                 - Single line: qty = quantity, amount = net_price as-is
...               Outputs: RESULT:PO_DATA:<json_array>
Library           SapGuiLibrary
Library           Process
Library           OperatingSystem
Library           String
Library           Collections
Library    sap_helpers.py
*** Variables ***
${PO_NUMBER}     ${EMPTY}
${ITEM_COMBO}    wnd[0]/usr/subSUB0:SAPLMEGUI:0015/subSUB3:SAPLMEVIEWS:1100/subSUB2:SAPLMEVIEWS:1200/subSUB1:SAPLMEGUI:1301/subSUB1:SAPLMEGUI:6000/cmbDYN_6000-LIST
# ============================================================
# CONFIRMED PATHS FROM GUI RECORDING
# ============================================================
${TABLE}    wnd[0]/usr/subSUB0:SAPLMEGUI:0015/subSUB2:SAPLMEVIEWS:1100/subSUB2:SAPLMEVIEWS:1200/subSUB1:SAPLMEGUI:1211/tblSAPLMEGUITC_1211
 
# Field names (confirmed from recording)
${F_MATERIAL}    ctxtMEPO1211-EMATN
${F_SHORTTEXT}   txtMEPO1211-TXZ01
${F_QUANTITY}    txtMEPO1211-MENGE
${F_NETPRICE}    txtMEPO1211-NETPR
${F_UOM}    ctxtMEPO1211-MEINS
 
# Column indices (confirmed from recording)
${COL_MATERIAL}    4
${COL_SHORTTEXT}   5
${COL_QUANTITY}    6
${COL_NETPRICE}    10
${COL_UOM}    7
 
# India tab and HSN/SAC (confirmed from recording)
${INDIA_TAB}      wnd[0]/usr/subSUB0:SAPLMEGUI:0015/subSUB3:SAPLMEVIEWS:1100/subSUB2:SAPLMEVIEWS:1200/subSUB1:SAPLMEGUI:1301/subSUB2:SAPLMEGUI:1303/tabsITEM_DETAIL/tabpTABIDT13
${HSN_FIELD}    wnd[0]/usr/subSUB0:SAPLMEGUI:0015/subSUB3:SAPLMEVIEWS:1100/subSUB2:SAPLMEVIEWS:1200/subSUB1:SAPLMEGUI:1301/subSUB2:SAPLMEGUI:1303/tabsITEM_DETAIL/tabpTABIDT13/ssubTABSTRIPCONTROL1SUB:SAPLMEGUI:1344/ctxtMEPO1344-STEUC
${BTN_NEXT_ITEM}  wnd[0]/usr/subSUB0:SAPLMEGUI:0015/subSUB3:SAPLMEVIEWS:1100/subSUB2:SAPLMEVIEWS:1200/subSUB1:SAPLMEGUI:1301/subSUB1:SAPLMEGUI:6000/btn%#AUTOTEXT001
 
# PO number entry via Other Purchase Order button
${BTN_OTHER_PO}    wnd[0]/tbar[1]/btn[17]
${POPUP_PO_FIELD}  wnd[1]/usr/subSUB0:SAPLMEGUI:0003/ctxtMEPO_SELECT-EBELN
${POPUP_CONFIRM}   wnd[1]/tbar[0]/btn[0]
 
 
# Delivery tab and Open Quantity (confirmed from recording)
${DELIVERY_TAB}      wnd[0]/usr/subSUB0:SAPLMEGUI:0015/subSUB3:SAPLMEVIEWS:1100/subSUB2:SAPLMEVIEWS:1200/subSUB1:SAPLMEGUI:1301/subSUB2:SAPLMEGUI:1303/tabsITEM_DETAIL/tabpTABIDT6
${OPEN_QTY_TABLE}    wnd[0]/usr/subSUB0:SAPLMEGUI:0015/subSUB3:SAPLMEVIEWS:1100/subSUB2:SAPLMEVIEWS:1200/subSUB1:SAPLMEGUI:1301/subSUB2:SAPLMEGUI:1303/tabsITEM_DETAIL/tabpTABIDT6/ssubTABSTRIPCONTROL1SUB:SAPLMEGUI:1320/tblSAPLMEGUITC_1320
${F_OPENQTY}          txtMEPO1320-OBMNG
${COL_OPENQTY}        10
${BTN_NEXT_ITEM_OQ}    wnd[0]/usr/subSUB0:SAPLMEGUI:0015/subSUB3:SAPLMEVIEWS:1100/subSUB2:SAPLMEVIEWS:1200/subSUB1:SAPLMEGUI:1301/subSUB1:SAPLMEGUI:6000/btn%#AUTOTEXT002
 
*** Test Cases ***
Execute PO Fetch
    [Setup]    Initialize SAP And Login
    ${po_data}=    Fetch PO Line Items
    Log To Console    RESULT:PO_DATA:${po_data}
    Sleep    3s
    [Teardown]    Close SAP Session
 
 
*** Keywords ***
Initialize SAP And Login
    Evaluate    __import__('dotenv').load_dotenv(__import__('os').getenv('DOTENV_PATH', '.env'), override=True)
    ${CLIENT}=      Evaluate    __import__('os').getenv('SAP_CLIENT')
    ${CONN_NAME}=   Evaluate    __import__('os').getenv('SAP_CONNECTION_NAME')
    ${LOGON_PATH}=  Evaluate    __import__('os').getenv('SAP_LOGON_PATH')
 
    # v16: PO Fetch (ME23N line-item read) ALWAYS uses the shared spl_rpa
    # .env account below -- unlike gate_in/migo_103/migo_105/miro/
    # zgatein_update, this bot never checks for a per-user SAP_USER_OVERRIDE/
    # SAP_PASS_OVERRIDE credential. It's a read-only PO lookup feeding
    # MIGO's line items, not an attributable posting, so rf_runner.py
    # deliberately never passes an override into this bot's environment --
    # by client decision, not an oversight. See .env for the full comment.
    ${USERNAME}=    Evaluate    __import__('os').getenv('SAP_USERNAME')
    ${PASSWORD}=    Evaluate    __import__('os').getenv('SAP_PASSWORD')
 
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
 
    # FIX: same reordering + OPT1->OPT2 fix as gate_in.robot/migo_103.robot/
    # migo_105.robot -- see migo_103.robot's comment for the full
    # explanation. Generic Dismiss Any Popup used to run first and would
    # already close the multi-logon dialog with SAP's default choice
    # before this specific OPT2 selection could run.
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
 
 
Fetch PO Line Items
    # Navigate to ME23N
    Run Transaction    ME23N
    Sleep    3s
    Dismiss Any Popup
 
    # Enter PO number via Other Purchase Order button
    Click Element    ${BTN_OTHER_PO}
    Sleep    2s
    Input Text       ${POPUP_PO_FIELD}    ${PO_NUMBER}
    Click Element    ${POPUP_CONFIRM}
    Sleep    3s
    Dismiss Any Popup
 
    # --------------------------------------------------------
    # STEP 1: Read all line items from the grid
    # --------------------------------------------------------
    @{items}=    Create List
    ${row_idx}=    Set Variable    0
 
    WHILE    True
        # Build full element paths as strings first — avoids RF interpreting [col,row] as list index
        ${mat_path}=    Set Variable    ${TABLE}/${F_MATERIAL}\[${COL_MATERIAL},${row_idx}\]
        ${txt_path}=    Set Variable    ${TABLE}/${F_SHORTTEXT}\[${COL_SHORTTEXT},${row_idx}\]
        ${qty_path}=    Set Variable    ${TABLE}/${F_QUANTITY}\[${COL_QUANTITY},${row_idx}\]
        ${price_path}=  Set Variable    ${TABLE}/${F_NETPRICE}\[${COL_NETPRICE},${row_idx}\]
        ${uom_path}=    Set Variable    ${TABLE}/${F_UOM}\[${COL_UOM},${row_idx}\]
 
        # Try to read material — FAIL means no more rows
        ${mat_res}=    Run Keyword And Ignore Error    Get Value    ${mat_path}
 
        IF    '${mat_res}[0]' == 'FAIL'    BREAK
 
        ${material}=    Clean SAP Value    ${mat_res}[1]
 
        # Skip blank rows
        IF    '${material}' == ''
            ${row_idx}=    Evaluate    ${row_idx} + 1
            IF    ${row_idx} > 100    BREAK
            CONTINUE
        END
 
        ${txt_res}=      Run Keyword And Ignore Error    Get Value    ${txt_path}
        ${short_text}=   Clean SAP Value    ${txt_res}[1]
 
        ${qty_res}=      Run Keyword And Ignore Error    Get Value    ${qty_path}
        ${qty_raw}=      Clean SAP Value    ${qty_res}[1]
 
        ${price_res}=    Run Keyword And Ignore Error    Get Value    ${price_path}
        ${price_raw}=    Clean SAP Value    ${price_res}[1]
 
        ${uom_res}=      Run Keyword And Ignore Error    Get Value    ${uom_path}
        ${uom_raw}=      Clean SAP Value    ${uom_res}[1]
 
 
        # Diagnostic: log every value read for this row, per field, before
        # it goes anywhere else -- makes it obvious in the console/log
        # whether SAP actually returned something for material/qty (vs.
        # them arriving blank at the SAP-read step itself) versus getting
        # lost further down the pipeline (rf_runner parsing, DB save, etc).
        Log To Console    Row ${row_idx} READ: material="${material}" short_text="${short_text}" qty="${qty_raw}" net_price="${price_raw}"
        #qty="${qty_raw}"
 
        &{row_data}=    Create Dictionary
        ...    material=${material}
        ...    short_text=${short_text}
        ...    net_price=${price_raw}
        ...    uom=${uom_raw}
        ...    qty=${qty_raw}
 
        Append To List    ${items}    ${row_data}
        ${row_idx}=    Evaluate    ${row_idx} + 1
        IF    ${row_idx} >= 100    BREAK
    END
 
    ${total_rows}=    Get Length    ${items}
    Log To Console    Found ${total_rows} line item(s) for PO ${PO_NUMBER}
 
    IF    ${total_rows} == 0
        RETURN    []
    END
 
    # --------------------------------------------------------
    # STEP 2: Read HSN/SAC from India tab per item
    # Click India tab once, then use down arrow for each next item
    # --------------------------------------------------------
    Run Keyword And Ignore Error    Click Element    ${INDIA_TAB}
    Sleep    2s
 
    # @{hsn_list}=    Create List
 
    #     FOR    ${i}    IN RANGE    ${total_rows}
 
    #         # Always ensure India tab is active before reading
    #         Run Keyword And Ignore Error    Click Element    ${INDIA_TAB}
    #         Sleep    0.5s
 
    #         ${hsn_res}=    Run Keyword And Ignore Error    Get Value    ${HSN_FIELD}
    #         ${hsn}=        Clean SAP Value    ${hsn_res}[1]
 
    #         Append To List    ${hsn_list}    ${hsn}
    #         Log To Console    Item ${i} HSN/SAC: ${hsn}
 
    #        IF    ${i} < ${total_rows} - 1
    #         ${next_row}=    Evaluate    ${i} + 1
    #         ${row_path}=    Set Variable    ${TABLE}/rows[${next_row}]
    #         Run Keyword And Ignore Error    Click Element    ${row_path}
    #         Sleep    1s
    #     END
 
    #     END
 
# --------------------------------------------------------
# --------------------------------------------------------
# --------------------------------------------------------
 # --------------------------------------------------------
   # --------------------------------------------------------
    # --------------------------------------------------------
    # STEP 2b: Read Open Quantity — click Delivery tab once,
    # then press next-item button between reads
    # --------------------------------------------------------
    # --------------------------------------------------------
    # STEP 2b: Read Open Quantity — click Delivery tab once,
    # then press next-item button between reads
    # --------------------------------------------------------
    @{open_qty_list}=    Create List
 
    # Make sure we're back on item 1 before starting this pass
    Run Keyword And Ignore Error    Set Combo Via Vbs    ${ITEM_COMBO}    1
    Sleep    1s
    Run Keyword And Ignore Error    Click Element    ${DELIVERY_TAB}
    Sleep    1s
 
    FOR    ${i}    IN RANGE    ${total_rows}
        ${oq_path}=    Set Variable    ${OPEN_QTY_TABLE}/${F_OPENQTY}\[${COL_OPENQTY},0\]
        ${oq_res}=    Run Keyword And Ignore Error    Get Value    ${oq_path}
        Log To Console    Item ${i} Open Qty read: ${oq_res}[0] = ${oq_res}[1]
        IF    '${oq_res}[0]' == 'PASS'
            ${open_qty}=    Clean SAP Value    ${oq_res}[1]
        ELSE
            ${open_qty}=    Set Variable    ${EMPTY}
        END
        Append To List    ${open_qty_list}    ${open_qty}
        Log To Console    Item ${i} Open Quantity: ${open_qty}
 
        IF    ${i} < ${total_rows} - 1
            Run Keyword And Ignore Error    Click Element    ${BTN_NEXT_ITEM_OQ}
            Sleep    3s
        END
    END
 
    ${oq_count}=    Get Length    ${open_qty_list}
    WHILE    ${oq_count} < ${total_rows}
        Append To List    ${open_qty_list}    ${EMPTY}
        ${oq_count}=    Evaluate    ${oq_count} + 1
    END
 
    @{hsn_list}=    Create List
 
    FOR    ${i}    IN RANGE    ${total_rows}
        ${item_index}=    Evaluate    ${i} + 1
        ${combo_res}=    Run Keyword And Ignore Error    Set Combo Via Vbs    ${ITEM_COMBO}    ${item_index}
        Log To Console    Item ${i} combo: ${combo_res}[0]
        Sleep    1.5s
 
        Run Keyword And Ignore Error    Click Element    ${INDIA_TAB}
        Sleep    1s
 
        ${hsn_res}=    Run Keyword And Ignore Error    Get Value    ${HSN_FIELD}
        Log To Console    Item ${i} HSN read: ${hsn_res}[0] = ${hsn_res}[1]
        IF    '${hsn_res}[0]' == 'PASS'
            ${hsn}=    Clean SAP Value    ${hsn_res}[1]
        ELSE
            ${hsn}=    Set Variable    ${EMPTY}
        END
        Append To List    ${hsn_list}    ${hsn}
 
        ${hsn_res}=    Run Keyword And Ignore Error    Get Value    ${HSN_FIELD}
        Log To Console    Item ${i} HSN read: ${hsn_res}[0] = ${hsn_res}[1]
        IF    '${hsn_res}[0]' == 'PASS'
            ${hsn}=    Clean SAP Value    ${hsn_res}[1]
        ELSE
            ${hsn}=    Set Variable    ${EMPTY}
        END
        Append To List    ${hsn_list}    ${hsn}
        Log To Console    Item ${i} HSN/SAC: ${hsn}
    END
 
    ${hsn_count}=    Get Length    ${hsn_list}
    WHILE    ${hsn_count} < ${total_rows}
        Append To List    ${hsn_list}    ${EMPTY}
        ${hsn_count}=    Evaluate    ${hsn_count} + 1
    END
    # --------------------------------------------------------
    # STEP 3: Calculate amounts and build JSON output
    # Single row  → amount = net_price as-is
    # Multiple rows → amount per row = qty × net_price
    #                 append TOTAL row at end
    # --------------------------------------------------------
    @{json_items}=    Create List
    ${running_total}=    Set Variable    ${0}
 
    FOR    ${i}    IN RANGE    ${total_rows}
        ${row}=         Get From List    ${items}    ${i}
        ${hsn}=         Get From List    ${hsn_list}    ${i}
        ${open_qty}=    Get From List    ${open_qty_list}    ${i}
        ${material}=    Get From Dictionary    ${row}    material
        ${short_text}=  Get From Dictionary    ${row}    short_text
        ${qty_str}=     Get From Dictionary    ${row}    qty
        ${price_str}=   Get From Dictionary    ${row}    net_price
        ${uom}=         Get From Dictionary    ${row}    uom
 
        ...
 
   
        # Remove commas from numbers (SAP formats: 1,234.56)
        ${qty_clean}=    Remove String    ${qty_str}    ,
        ${price_clean}=  Remove String    ${price_str}    ,
 
        IF    ${total_rows} == 1
            # Single line — amount is net price as-is
            ${line_amount}=    Set Variable    ${price_str}
        ELSE
            # Multiple lines — line amount = qty × net_price
            ${line_amount}=    Evaluate
            ...    str(round(float('${qty_clean}' or '0') * float('${price_clean}' or '0'), 2))
            ${running_total}=    Evaluate
            ...    round(${running_total} + float('${qty_clean}' or '0') * float('${price_clean}' or '0'), 2)
        END
 
        ${item_no}=    Evaluate    str(($i + 1) * 10)
 
        # Diagnostic: log the final per-item values right before they're
        # written into the JSON that RESULT:PO_DATA: carries out of this
        # script -- this is the last point inside the robot where these
        # values are still individual variables, not yet a JSON blob.
        Log To Console    Item ${item_no} FINAL: material_code="${material}" short_text="${short_text}" uom="${uom}" qty="${qty_str}" rate="${price_str}" amount="${line_amount}" hsn_sac="${hsn}" "open_qty":"${open_qty}"
 
          ${json}=    Set Variable
        ...    {"item_no":"${item_no}","material_code":"${material}","short_text":"${short_text}","uom":"${uom}","rate":"${price_str}","qty":"${qty_str}","amount":"${line_amount}","hsn_sac":"${hsn}","open_qty":"${open_qty}"}
        Append To List    ${json_items}    ${json}
    END
    #"qty":"${qty_str}",
 
    # Append total row for multi-line POs
    IF    ${total_rows} > 1
        ${total_json}=    Set Variable
        ...    {"item_no":"TOTAL","material_code":"","short_text":"Total Amount","uom":"","rate":"","qty":"","amount":"${running_total}","hsn_sac":"","open_qty":""}
        Append To List    ${json_items}    ${total_json}
    END
    #"qty":"",
 
    ${joined}=    Evaluate    ",".join($json_items)
    RETURN    [${joined}]
 
 
Clean SAP Value
    [Arguments]    ${raw}
    ${val}=    Convert To String    ${raw}
    ${val}=    Strip String    ${val}
    ${val}=    Replace String    ${val}    "    '
    ${val}=    Replace String    ${val}    \\    /
    IF    '${val}' == 'None' or '${val}' == 'null'
        RETURN    ${EMPTY}
    END
    RETURN    ${val}
 
 
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
 
 
Close SAP Session
    # Log    PO fetch finished. Session kept open.
    # RETURN
 
    Run Keyword And Ignore Error    Input Text    wnd[0]/tbar[0]/okcd    /nex
    Run Keyword And Ignore Error    Send VKey     wnd[0]    0
    Sleep    2s
    Run Keyword And Ignore Error    Run Process    taskkill    /F    /IM    saplogon.exe
    Sleep    2s
    Run Keyword And Ignore Error    Run Process    taskkill    /F    /IM    saplogon.exe    /T
    Log    SAP session closed and process terminated.    level=INFO
 