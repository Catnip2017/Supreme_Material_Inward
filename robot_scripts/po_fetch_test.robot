*** Settings ***
Documentation     STANDALONE TEST — PO Fetch (ME23N PO Line Item Extractor).
...               Run this file directly (no Flask app / queue needed) to see
...               exactly which fields the bot extracts per line item, and
...               whether any are coming back blank. Edit ${PO_NUMBER} below
...               to a real PO in your system before running -- ideally one
...               with multiple line items, so you can see the multi-line
...               (qty × net_price, plus TOTAL row) path exercised too.
...
...               This is read-only (ME23N display) -- it does not post or
...               change anything in SAP, so it's safe to run repeatedly.
...
...               For every line item, this logs each raw field to the
...               console as it's read (material, short_text, qty,
...               net_price, HSN/SAC), then prints a final per-field summary
...               table flagging any row where a field came back blank --
...               so you don't have to scroll through the raw JSON to spot
...               a missing value.
Library           SapGuiLibrary
Library           Process
Library           OperatingSystem
Library           String
Library           Collections
Library    sap_helpers.py

*** Variables ***
# --- EDIT this to a real PO number in your system before running.
# Pick one with 2+ line items to exercise the multi-line amount/TOTAL logic.
${PO_NUMBER}    4500001234

${ITEM_COMBO}    wnd[0]/usr/subSUB0:SAPLMEGUI:0015/subSUB3:SAPLMEVIEWS:1100/subSUB2:SAPLMEVIEWS:1200/subSUB1:SAPLMEGUI:1301/subSUB1:SAPLMEGUI:6000/cmbDYN_6000-LIST
${TABLE}    wnd[0]/usr/subSUB0:SAPLMEGUI:0015/subSUB2:SAPLMEVIEWS:1100/subSUB2:SAPLMEVIEWS:1200/subSUB1:SAPLMEGUI:1211/tblSAPLMEGUITC_1211

${F_MATERIAL}    ctxtMEPO1211-EMATN
${F_SHORTTEXT}   txtMEPO1211-TXZ01
${F_QUANTITY}    txtMEPO1211-MENGE
${F_NETPRICE}    txtMEPO1211-NETPR

${COL_MATERIAL}    4
${COL_SHORTTEXT}   5
${COL_QUANTITY}    6
${COL_NETPRICE}    10

${INDIA_TAB}      wnd[0]/usr/subSUB0:SAPLMEGUI:0015/subSUB3:SAPLMEVIEWS:1100/subSUB2:SAPLMEVIEWS:1200/subSUB1:SAPLMEGUI:1301/subSUB2:SAPLMEGUI:1303/tabsITEM_DETAIL/tabpTABIDT13
${HSN_FIELD}    wnd[0]/usr/subSUB0:SAPLMEGUI:0015/subSUB3:SAPLMEVIEWS:1100/subSUB2:SAPLMEVIEWS:1200/subSUB1:SAPLMEGUI:1301/subSUB2:SAPLMEGUI:1303/tabsITEM_DETAIL/tabpTABIDT13/ssubTABSTRIPCONTROL1SUB:SAPLMEGUI:1344/ctxtMEPO1344-STEUC
${BTN_NEXT_ITEM}  wnd[0]/usr/subSUB0:SAPLMEGUI:0015/subSUB3:SAPLMEVIEWS:1100/subSUB2:SAPLMEVIEWS:1200/subSUB1:SAPLMEGUI:1301/subSUB1:SAPLMEGUI:6000/btn%#AUTOTEXT001

${BTN_OTHER_PO}    wnd[0]/tbar[1]/btn[17]
${POPUP_PO_FIELD}  wnd[1]/usr/subSUB0:SAPLMEGUI:0003/ctxtMEPO_SELECT-EBELN
${POPUP_CONFIRM}   wnd[1]/tbar[0]/btn[0]


*** Test Cases ***
Execute PO Fetch Test
    [Setup]    Initialize SAP And Login
    ${po_data}=    Fetch PO Line Items
    Log To Console    ${\n}================ RAW RESULT ================
    Log To Console    RESULT:PO_DATA:${po_data}
    Print Field Completeness Summary    ${po_data}
    Sleep    3s
    [Teardown]    Close SAP Session


*** Keywords ***
Initialize SAP And Login
    Evaluate    __import__('dotenv').load_dotenv()
    ${CLIENT}=      Evaluate    __import__('os').getenv('SAP_CLIENT')
    ${USERNAME}=    Evaluate    __import__('os').getenv('SAP_USERNAME')
    ${PASSWORD}=    Evaluate    __import__('os').getenv('SAP_PASSWORD')
    ${CONN_NAME}=   Evaluate    __import__('os').getenv('SAP_CONNECTION_NAME')
    ${LOGON_PATH}=  Evaluate    __import__('os').getenv('SAP_LOGON_PATH')

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
    Sleep    8s
    Dismiss Any Popup

    ${multi}=    Run Keyword And Return Status    Element Should Be Present    wnd[1]
    IF    ${multi}
        Run Keyword And Ignore Error    Select Radio Button    wnd[1]/usr/radMULTI_LOGON_OPT1
        Run Keyword And Ignore Error    Click Element          wnd[1]/tbar[0]/btn[0]
        Sleep    2s
    END
    Maximize Window    0


Fetch PO Line Items
    Run Transaction    ME23N
    Sleep    3s
    Dismiss Any Popup

    Click Element    ${BTN_OTHER_PO}
    Sleep    2s
    Input Text       ${POPUP_PO_FIELD}    ${PO_NUMBER}
    Click Element    ${POPUP_CONFIRM}
    Sleep    3s
    Dismiss Any Popup

    # --------------------------------------------------------
    # STEP 1: Read all line items from the grid — logging each field
    # to the console as it's read, so a blank field is visible
    # immediately rather than only showing up in the final JSON.
    # --------------------------------------------------------
    @{items}=    Create List
    ${row_idx}=    Set Variable    0

    WHILE    True
        ${mat_path}=    Set Variable    ${TABLE}/${F_MATERIAL}\[${COL_MATERIAL},${row_idx}\]
        ${txt_path}=    Set Variable    ${TABLE}/${F_SHORTTEXT}\[${COL_SHORTTEXT},${row_idx}\]
        ${qty_path}=    Set Variable    ${TABLE}/${F_QUANTITY}\[${COL_QUANTITY},${row_idx}\]
        ${price_path}=  Set Variable    ${TABLE}/${F_NETPRICE}\[${COL_NETPRICE},${row_idx}\]

        ${mat_res}=    Run Keyword And Ignore Error    Get Value    ${mat_path}

        IF    '${mat_res}[0]' == 'FAIL'
            Log To Console    Row ${row_idx}: Get Value FAILED on material field -- treating as end of grid.
            BREAK
        END

        ${material}=    Clean SAP Value    ${mat_res}[1]

        IF    '${material}' == ''
            Log To Console    Row ${row_idx}: material blank -- skipping (assumed empty grid row).
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

        Log To Console    Row ${row_idx}: material="${material}" short_text="${short_text}" qty="${qty_raw}" net_price="${price_raw}"

        &{row_data}=    Create Dictionary
        ...    material=${material}
        ...    short_text=${short_text}
        ...    qty=${qty_raw}
        ...    net_price=${price_raw}

        Append To List    ${items}    ${row_data}
        ${row_idx}=    Evaluate    ${row_idx} + 1
        IF    ${row_idx} >= 100    BREAK
    END

    ${total_rows}=    Get Length    ${items}
    Log To Console    Found ${total_rows} line item(s) for PO ${PO_NUMBER}

    IF    ${total_rows} == 0
        Log To Console    No line items found -- check PO_NUMBER is valid and has items, or that the grid element paths still match your SAP GUI layout.
        RETURN    []
    END

    # --------------------------------------------------------
    # STEP 2: Read HSN/SAC per item via India tab combo navigation
    # --------------------------------------------------------
    Run Keyword And Ignore Error    Click Element    ${INDIA_TAB}
    Sleep    2s

    @{hsn_list}=    Create List

    FOR    ${i}    IN RANGE    ${total_rows}
        ${item_index}=    Evaluate    ${i} + 1

        ${combo_res}=    Run Keyword And Ignore Error    Set Combo Via Vbs    ${ITEM_COMBO}    ${item_index}
        Log To Console    Item ${i} combo: ${combo_res}[0]
        Sleep    1.5s

        ${hsn_res}=    Run Keyword And Ignore Error    Get Value    ${HSN_FIELD}
        Log To Console    Item ${i} HSN read: ${hsn_res}[0] = ${hsn_res}[1]

        ${hsn}=    Clean SAP Value    ${hsn_res}[1]
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
    # --------------------------------------------------------
    @{json_items}=    Create List
    ${running_total}=    Set Variable    ${0}

    FOR    ${i}    IN RANGE    ${total_rows}
        ${row}=         Get From List    ${items}    ${i}
        ${hsn}=         Get From List    ${hsn_list}    ${i}
        ${material}=    Get From Dictionary    ${row}    material
        ${short_text}=  Get From Dictionary    ${row}    short_text
        ${qty_str}=     Get From Dictionary    ${row}    qty
        ${price_str}=   Get From Dictionary    ${row}    net_price

        ${qty_clean}=    Remove String    ${qty_str}    ,
        ${price_clean}=  Remove String    ${price_str}    ,

        IF    ${total_rows} == 1
            ${line_amount}=    Set Variable    ${price_str}
        ELSE
            ${line_amount}=    Evaluate
            ...    str(round(float('${qty_clean}' or '0') * float('${price_clean}' or '0'), 2))
            ${running_total}=    Evaluate
            ...    round(${running_total} + float('${qty_clean}' or '0') * float('${price_clean}' or '0'), 2)
        END

        ${item_no}=    Evaluate    str(($i + 1) * 10)

        ${json}=    Set Variable
        ...    {"item_no":"${item_no}","material_code":"${material}","short_text":"${short_text}","qty":"${qty_str}","rate":"${price_str}","amount":"${line_amount}","hsn_sac":"${hsn}"}
        Append To List    ${json_items}    ${json}
    END

    IF    ${total_rows} > 1
        ${total_json}=    Set Variable
        ...    {"item_no":"TOTAL","material_code":"","short_text":"Total Amount","qty":"","rate":"","amount":"${running_total}","hsn_sac":""}
        Append To List    ${json_items}    ${total_json}
    END

    ${joined}=    Evaluate    ",".join($json_items)
    RETURN    [${joined}]


Print Field Completeness Summary
    [Arguments]    ${po_data_str}
    Log To Console    ${\n}================ FIELD COMPLETENESS SUMMARY ================
    ${parsed}=    Run Keyword And Ignore Error    Evaluate    __import__('json').loads('''${po_data_str}''')
    IF    '${parsed}[0]' == 'FAIL'
        Log To Console    Could not parse PO_DATA as JSON -- see raw result above.
        RETURN
    END
    ${rows}=    Set Variable    ${parsed}[1]
    ${row_count}=    Get Length    ${rows}
    IF    ${row_count} == 0
        Log To Console    No rows returned -- nothing to check.
        RETURN
    END
    FOR    ${row}    IN    @{rows}
        ${item_no}=    Get From Dictionary    ${row}    item_no
        IF    '${item_no}' == 'TOTAL'
            Log To Console    Item ${item_no}: amount=${row}[amount] (total row, other fields expected blank)
            CONTINUE
        END
        @{missing}=    Create List
        FOR    ${field}    IN    material_code    short_text    qty    rate    amount    hsn_sac
            ${val}=    Get From Dictionary    ${row}    ${field}
            IF    '${val}' == ''
                Append To List    ${missing}    ${field}
            END
        END
        ${missing_count}=    Get Length    ${missing}
        IF    ${missing_count} > 0
            Log To Console    Item ${item_no}: ⚠ MISSING FIELDS -- ${missing}
        ELSE
            Log To Console    Item ${item_no}: ✓ all fields populated (material=${row}[material_code] qty=${row}[qty] rate=${row}[rate] hsn_sac=${row}[hsn_sac])
        END
    END


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
    Run Keyword And Ignore Error    Input Text    wnd[0]/tbar[0]/okcd    /nex
    Run Keyword And Ignore Error    Send VKey     wnd[0]    0
    Sleep    2s
    Run Keyword And Ignore Error    Run Process    taskkill    /F    /IM    saplogon.exe
    Sleep    2s
    Run Keyword And Ignore Error    Run Process    taskkill    /F    /IM    saplogon.exe    /T
    Log    SAP session closed and process terminated.    level=INFO
