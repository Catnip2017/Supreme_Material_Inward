"""
services/extract.py — WatsonX AI document OCR extraction.

Fixes applied:
- Field names now use lowercase_underscore keys matching DB columns directly
- Multi-page support — sends up to 3 pages for better coverage
- None/null/"N/A"/"-" cleanup after extraction
- HSN Details non-array handling
- Stronger prompt instructions for consistent output
- Model instance cached to avoid re-initialization per call
- Dates returned in consistent format instruction
- Amounts returned without currency symbols instruction
- "rate": The unit rate/price per item as shown in the Rate column of the invoice line item.
- "unit": The unit of measure for the line item (e.g., pc, Nos, EA, Num, kg).
- "taxable_value": The line total before tax (rate x quantity, after discount).
"""

import json
import base64
import os
import re
from typing import Optional

from config.config import config
from config.logger import get_logger

logger = get_logger(__name__)

# WatsonX AI imports
try:
    from ibm_watsonx_ai import Credentials
    from ibm_watsonx_ai.foundation_models import ModelInference
    WATSONX_AVAILABLE = True
except ImportError:
    WATSONX_AVAILABLE = False
    logger.warning("ibm_watsonx_ai not installed. OCR will return empty data.")

# Cached model instance — initialized once, reused for all calls
_model_instance = None


def _get_model() -> Optional[object]:
    """
    Return cached WatsonX model. Initialize once on first call.
    """
    global _model_instance
    if _model_instance is not None:
        return _model_instance

    if not WATSONX_AVAILABLE:
        return None
    if not config.WATSONX_API_KEY or not config.WATSONX_PROJECT_ID:
        logger.error("WatsonX credentials not configured in .env")
        return None
    try:
        credentials = Credentials(
            url=config.WATSONX_URL,
            api_key=config.WATSONX_API_KEY
        )
        _model_instance = ModelInference(
            model_id=config.WATSONX_MODEL_ID,
            credentials=credentials,
            project_id=config.WATSONX_PROJECT_ID,
            params={"max_tokens": 4000}
        )
        logger.info(f"WatsonX model initialized: {config.WATSONX_MODEL_ID}")
        return _model_instance
    except Exception as e:
        logger.error(f"Failed to initialize WatsonX model: {e}")
        return None


def _encode_pages_to_base64(file_path: str, max_pages: int = 10) -> list:
    """
    Convert up to max_pages of a PDF to PNG images encoded as base64.
    Returns list of base64 strings, one per page.
    Multi-page support ensures fields on page 2+ are captured.
    Raised from 3 -> 10 pages (per instruction) to cover longer documents;
    still a bounded cap (not unlimited) as a safety net against an
    oversized or accidentally-wrong upload driving up OCR call cost/latency.
    """
    try:
        import fitz  # PyMuPDF
        doc = fitz.open(file_path)
        pages_b64 = []
        for i in range(min(len(doc), max_pages)):
            pix = doc[i].get_pixmap(dpi=200)
            png_bytes = pix.tobytes("png")
            pages_b64.append(base64.standard_b64encode(png_bytes).decode("utf-8"))
        doc.close()
        logger.info(f"Encoded {len(pages_b64)} page(s) from {os.path.basename(file_path)}")
        return pages_b64
    except ImportError:
        logger.warning("PyMuPDF not installed — falling back to raw PDF bytes.")
        try:
            with open(file_path, "rb") as f:
                return [base64.standard_b64encode(f.read()).decode("utf-8")]
        except Exception as e:
            logger.error(f"Failed to encode PDF {file_path}: {e}")
            return []
    except Exception as e:
        logger.error(f"Failed to convert PDF to images {file_path}: {e}")
        return []


def _clean_value(value) -> str:
    """
    Normalize extracted values:
    - None, null, "None", "null", "N/A", "n/a", "-", "NA", "Not available",
      "Not found", "Not applicable" → empty string
    - Strip leading/trailing whitespace
    - Keep everything else as-is (don't strip currency — that's rf_runner's job)
    """
    if value is None:
        return ""
    val = str(value).strip()
    empty_values = {
        "none", "null", "n/a", "na", "-", "--", "not available",
        "not found", "not applicable", "unknown", "nil", ""
    }
    if val.lower() in empty_values:
        return ""
    return val


def _clean_extracted(data: dict) -> dict:
    """
    Recursively clean all string values in extracted dict.
    Handles nested dicts and lists (for HSN Details).
    """
    if not isinstance(data, dict):
        return data
    cleaned = {}
    for key, value in data.items():
        if isinstance(value, list):
            cleaned[key] = [
                _clean_extracted(item) if isinstance(item, dict)
                else _clean_value(item)
                for item in value
            ]
        elif isinstance(value, dict):
            cleaned[key] = _clean_extracted(value)
        else:
            cleaned[key] = _clean_value(value)
    return cleaned


def _normalize_date(date_str: str) -> str:
    """
    Normalize various date formats to YYYY-MM-DD for HTML date inputs.
    Handles: 25-Jul-25, 25/07/2025, 25-07-2025, 25.07.2025, 25-Jul-2025 etc.
    Returns empty string if parsing fails.
    
    2-digit year rule: years 00-30 → 2000-2030, years 31-99 → 1931-1999
    But we clamp to reasonable range: if parsed year < 2020, add 2000.
    """
    if not date_str:
        return ""

    from datetime import datetime

    formats = [
        "%d-%b-%y",    # 25-Jul-25
        "%d-%b-%Y",    # 25-Jul-2025
        "%d/%m/%Y",    # 25/07/2025
        "%d-%m-%Y",    # 25-07-2025
        "%d.%m.%Y",    # 25.07.2025
        "%Y-%m-%d",    # Already correct
        "%d/%m/%y",    # 25/07/25
        "%d-%m-%y",    # 25-07-25
        "%d|%m|%y",    # 25|07|25 (handwritten separator)
        "%d|%m|%Y",    # 25|07|2025
        "%b %d, %Y",   # Jul 25, 2025
        "%d %b %Y",    # 25 Jul 2025
        "%d %b %y",    # 25 Jul 25
    ]

    clean = date_str.strip()
    for fmt in formats:
        try:
            dt = datetime.strptime(clean, fmt)
            # Fix ambiguous 2-digit years
            # If parsed year looks wrong (before 2020), it's likely a 2-digit year
            # interpreted as 19xx — add 2000 to correct it
            if dt.year < 2020:
                dt = dt.replace(year=dt.year + 2000)
            # Sanity check — don't return future dates more than 1 year out
            from datetime import date
            if dt.year > date.today().year + 1:
                dt = dt.replace(year=dt.year - 100)
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            continue

    logger.warning(f"Could not parse date: {date_str}")
    return date_str


def _normalize_dates_in_dict(data: dict, date_fields: list) -> dict:
    """Normalize specific date fields in a dict to YYYY-MM-DD."""
    for field in date_fields:
        if field in data and data[field]:
            data[field] = _normalize_date(data[field])
    return data


# ============================================================
# GSTIN OCR-error correction and IRN cleanup (client-requested accuracy
# improvement for photographed/landscape invoices).
#
# Ground rule (explicit client instruction): NEVER blank a value just
# because it fails a validation check. The Extracted Data tab already has
# a client-side red-highlight for exactly this case
# (_validateGstinField()/_checkIrnLength() in extracted_data.html) --
# their whole job is to tell the reviewer "this needs a second look
# against the paper document." If this module blanked the value instead,
# the reviewer would see nothing at all and lose the one clue (whatever
# OCR did manage to read) that helps them fix it by hand.
#
# So these functions only ever REPAIR a value when the fix is
# deterministic, or strip a redundant label the model echoed back --
# they never discard a value outright. GSTIN's 15 character positions are
# fixed by statute (always digit/digit/letter*5/digit*4/letter/alnum/
# literal-Z/alnum), so a letter sitting where only a digit is possible
# (or vice versa) is an unambiguous OCR misread, not a guess. If a value
# still doesn't validate after correction, it's returned unchanged --
# the client-side check will flag it exactly as before.
# ============================================================

# All current Indian state/UT GST codes (01-38) plus the two special
# codes (97 = Other Territory, 99 = Centre/foreign).
_VALID_GSTIN_STATE_CODES = {f"{i:02d}" for i in range(1, 39)} | {"97", "99"}


def _correct_gstin_ocr_errors(gstin: str) -> str:
    """
    Fix single-character OCR confusions in a 15-character GSTIN candidate,
    based on what each fixed position is allowed to contain. Assumes
    `gstin` is already exactly 15 characters -- callers check length first.
    """
    chars = list(gstin)
    digit_confusions = {'O': '0', 'I': '1', 'L': '1', 'S': '5', 'B': '8', 'G': '6'}
    alpha_confusions = {'0': 'O', '1': 'I', '8': 'B', '5': 'S', '6': 'G'}

    for i in (0, 1):                      # state code -- must be digits
        if chars[i] in digit_confusions:
            chars[i] = digit_confusions[chars[i]]
    for i in range(2, 7):                 # PAN letters -- must be alpha
        if chars[i] in alpha_confusions:
            chars[i] = alpha_confusions[chars[i]]
    for i in range(7, 11):                # PAN digits -- must be digits
        if chars[i] in digit_confusions:
            chars[i] = digit_confusions[chars[i]]
    if chars[11] in alpha_confusions:      # PAN's last char -- must be alpha
        chars[11] = alpha_confusions[chars[11]]
    if chars[13] != 'Z' and chars[13] in {'2', '5', '7', 'S', 'z'}:
        chars[13] = 'Z'                   # 14th char is always literal "Z"

    return ''.join(chars)


def _attempt_gstin_correction(raw: str) -> str:
    """
    Try to repair common OCR character confusions in a GSTIN. Returns the
    corrected value if it becomes structurally valid; otherwise returns
    the original (only whitespace/hyphen-cleaned) value UNCHANGED -- never
    blanks it. The existing client-side format+checksum check picks up
    whatever this function couldn't fix.
    """
    if not raw:
        return raw

    cleaned = re.sub(r'[\s\-]', '', raw.strip().upper())
    if len(cleaned) != 15:
        return raw  # wrong length isn't something character-swapping can fix

    corrected = _correct_gstin_ocr_errors(cleaned)

    looks_valid = (
        corrected[0:2].isdigit() and corrected[0:2] in _VALID_GSTIN_STATE_CODES
        and corrected[2:7].isalpha()
        and corrected[7:11].isdigit()
        and corrected[11].isalpha()
    )
    return corrected if looks_valid else raw


def _clean_irn(raw: str) -> str:
    """
    Strip a redundant "IRN:"/"IRN No:" label or stray whitespace the model
    sometimes echoes back into the value itself. Never blanks based on
    length -- the client-side _checkIrnLength() already flags a
    non-64-character IRN for review, so this only ever tidies the string,
    it doesn't judge it.
    """
    if not raw:
        return raw
    cleaned = re.sub(r'(?i)^\s*irn\s*(no\.?)?\s*[:\-]?\s*', '', raw.strip())
    return re.sub(r'\s+', '', cleaned)


def _build_prompt(doc_type: str) -> str:
    """
    Build extraction prompt. Keys use lowercase_underscore to match DB columns.
    Instructions ensure consistent output format.
    """
    base_instructions = """
Rules:
- Return ONLY valid JSON. No text before or after.
- If a field is not visible or not applicable, use empty string "".
- Do NOT use null, None, N/A, or any placeholder text.
- Return all dates in DD/MM/YYYY format.
- Return amounts as numbers only — no currency symbols, no commas.
- Return quantities as numbers only — no units.
- CRITICAL — every field value must be a SINGLE LINE with no actual line
  break/newline character inside it. If a field's text wraps across
  multiple printed lines on the document (a multi-line address is the
  most common case), join those lines into one string separated by a
  comma and a space -- never insert a literal line break between them.
  A raw line break inside a JSON string value is invalid JSON and makes
  your ENTIRE response fail to parse, discarding every field you
  extracted, not just that one -- so this rule matters even for fields
  that seem unimportant.
"""

    prompts = {
        "invoice": base_instructions + """
Extract these fields from the invoice:

This document may be a photograph of a physical paper invoice rather than a
clean digital scan -- expect possible perspective skew, shadows, glare,
folds, and lower legibility toward the edges. If a specific character or
value cannot be read with reasonable confidence because of image quality,
return empty string "" for that field rather than guessing a
plausible-looking value.

The invoice may also carry handwriting, rubber stamps, signatures, or
"received/verified" notes physically overlapping the printed text (for
example a stamp crossing through the IRN line, or a handwritten reference
number near the header). Only extract values that are part of the
ORIGINALLY PRINTED invoice content. Ignore any handwritten numbers, dates,
initials, stamps, or notes layered on top of or near a printed field --
they are not invoice data, even when they sit directly over a field you
are asked to extract.

Header block mapping -- invoices label the three parties differently
depending on template. Map whichever labels appear to these fields:
- "seller_name"/"seller_address"/"seller_gstin": the party issuing the
  invoice -- labeled "Supplier", "Seller", or "From".
- "buyer_name"/"buyer_address"/"buyer_gstin": the party being billed --
  labeled "Recipient", "Billed to", "Buyer", or "Bill To".
- "ship_to_name"/"ship_to_address": the delivery party -- labeled
  "Consignee", "Shipped to", "Ship To", or "Delivery Address". This is
  often the same company as the Recipient/Buyer, but extract it from
  whichever block is actually labeled as the shipping/consignee party,
  not the billing party, even when the two blocks show identical details.

Important field notes:
- "invoice_number": Use the value labeled "Invoice No", "Invoice Number", "Bill No", or similar. On a dense/landscape layout this label often sits in a small top-right box next to Invoice Date/Due Date/Internal Ref No -- read the whole box, not just the first line, since it can wrap across two lines. FALLBACK ONLY: if there is no usable/legible Invoice Number field anywhere on the document, but there IS an "Outbound Delivery No", "Delivery No", or "ODN" label, use that value instead. Never use the Outbound Delivery Number when a proper Invoice Number is present and readable -- it is strictly a fallback for when the real Invoice Number cannot be found, not a preference.
- "invoice_date": Use the FIRST date that appears at the top of the invoice — typically labeled "Date", "Invoice Date", or "Dated". Do not use delivery date, dispatch date, or due date. On a landscape layout it commonly sits in the same small header box as Invoice Number, immediately below or beside it -- read that whole box carefully rather than picking the first date-like text seen anywhere on the page (which is often a due date or removal/preparation date printed elsewhere).
- "po_number": A numeric purchase order number typically starting with 4 or 6 followed by 9 digits (e.g. 4500012345 or 6300001343). Check ALL pages — it may appear on page 2 in a receiving/gate entry form under "Purchase Order No." or "Purchase Order No". Also check "Our Order No", "Buyer Order No", "Customer PO Ref". If only a text reference like "TELE BY..." appears or genuinely not found, return empty string.
- "buyer_address"/"seller_address"/"ship_to_address": these almost always span MULTIPLE LINES (street, village/area, city, state, PIN code, country) -- read every line belonging to that address block and join them into ONE combined string separated by ", " (comma and space), e.g. "VILLAGE: AMDOSHI / WANGANI, WAI-ROAD, TALUKA-ROHA, RAIGAD, MAHARASHTRA 402106", not just the first word or first line. Do NOT join with an actual line break -- comma-and-space only, per the SINGLE LINE rule above. On a landscape layout, address blocks are often squeezed into a narrow column and wrap across 3-4 short lines -- make sure you have captured the full block down to the PIN code before moving on, rather than stopping after the first line.
- "buyer_gstin"/"seller_gstin": always exactly 15 characters (2-digit state code, 10-character PAN, 1-digit entity code, the letter "Z", 1 checksum character) -- if what you've read is shorter or longer than 15 characters, re-examine that region of the image for a missed or extra character before returning it.
- "company_pan": always exactly 10 characters, format 5 letters + 4 digits + 1 letter (e.g. AAACE1713F) -- note that this PAN is also embedded inside the Seller GSTIN as characters 3-12, so if the two are visible together you can cross-check one against the other.
- "hsn_sac" (also applies to hsn_details[].hsn_sac): a numeric HSN/SAC code, commonly 4, 6, or 8 digits -- do not confuse it with Material Code (which is always 8 characters and starts with 18/20/21/23, see below) or with a batch/lot number printed in the same row.
- "grand_total": Some invoices show two separate total blocks -- an initial total (e.g. "Total Invoice Value") and a final total after deductions (e.g. "Total Net Invoice Value", "Net Payable", after a "Less: Advance Received" or similar line). When both appear, "grand_total" is always the FINAL net payable figure -- the one after any deduction line, not the subtotal above it. If there is only one total block, use that.
- "material_code" (top-level field notes, applies to hsn_details[].material_code too): The vendor or buyer internal item identifier — look for labels
  like "Item Code", "Product Code", "Part No", "SAP Code", "Material Code",
  "Art. No", "Cat. No", or any alphanumeric code column separate from HSN/SAC.
  These material codes are always exactly 8 characters long and start with
  one of "18", "20", "21", or "23" (e.g. 18040021, 20115032, 21987744,
  23004410) -- use this shape to help distinguish it from other nearby
  codes/numbers of a different length, and to sanity-check a read: if the
  code you found is not 8 characters or does not start with one of those
  four prefixes, double-check you have not picked up a different column
  (HSN/SAC, batch no, etc.) before returning it. Return empty string if not
  found.
- "rate": The unit rate/price per item as shown in the Rate column.
- "unit": The unit of measure for the line item (e.g., pc, Nos, EA, Num, kg).
- "taxable_value": The line total before tax (rate x quantity, after discount).
- "irn": Look anywhere on the page for a line labeled "IRN" (also seen as "IRN No",
  "IRN #", "IRN:", or "e-Invoice IRN") — the value immediately following that label
  is the IRN. It has no fixed position on the page (it may be near the top by the
  QR code, or further down near the totals/signature area) — search the whole
  page, not just the header. Do not confuse it with "Ack No" — that is a shorter,
  separate acknowledgement number, not the IRN. Return empty string if no line
  labeled IRN is found.

Line-item table columns can appear in varying order and density. Read each
row left to right and match each value to its column header exactly as
printed on this document, rather than assuming a fixed position -- do not
let a value in one column bleed into a neighboring one just because they
sit close together (this is especially easy to get wrong on tightly-packed
tables with Freight/Insurance/Others/Discount columns sitting between Rate
and Taxable Value). Where possible, sanity-check that Taxable Value plus
the tax amounts roughly equals the row's Total -- if a read produces a
number that clearly fails this check, re-examine that cell rather than
returning the first guess.

═══════════════════════════════════════════
SELF-VERIFICATION (run before returning JSON)
═══════════════════════════════════════════
Before returning your answer, check:
1. buyer_gstin and seller_gstin, if not empty, are exactly 15 characters.
2. irn, if not empty, is exactly 64 characters -- if what you read is
   shorter, re-scan the page for the rest of the value before giving up;
   only return a value under 64 characters if you are confident that is
   genuinely the entire printed IRN.
3. invoice_date and po_number contain digits, not placeholder text.
4. grand_total is the FINAL net payable figure if two total blocks exist,
   not the pre-deduction subtotal.
5. Every hsn_details row's taxable_value plus its tax amounts roughly
   equals its total -- if not, re-check that row's columns for bleed
   before finalizing.
6. No field value contains an actual line break -- a multi-line address
   or any other wrapped field must be one single-line string joined with
   ", " (comma and space), never a raw newline. A single field with a
   literal line break in it will break your entire JSON response.
Correct any field that fails these checks before returning the JSON.

{
  "invoice_number": "",
  "invoice_date": "",
  "po_number": "",
  "irn": "",
  "buyer_name": "",
  "buyer_address": "",
  "buyer_gstin": "",
  "ship_to_name": "",
  "ship_to_address": "",
  "ship_to_state": "",
  "ship_to_code": "",
  "bill_to_state": "",
  "bill_to_code": "",
  "seller_name": "",
  "seller_address": "",
  "seller_gstin": "",
  "company_pan": "",
  "payment_terms": "",
  "amount_in_words": "",
  "total_taxable_amount": "",
  "cgst_rate": "",
  "cgst_amount": "",
  "sgst_rate": "",
  "sgst_amount": "",
  "igst_rate": "",
  "igst_amount": "",
  "total_tax_amount": "",
  "total_amount": "",
  "grand_total": "",
  "hsn_details": [
    {
        "material_code": "",
      "hsn_sac": "",
      "description": "",
      "quantity": "",
      "unit": "",
      "rate": "",
      "taxable_value": "",
      "cgst_rate": "",
      "cgst_amount": "",
      "sgst_rate": "",
      "sgst_amount": "",
      "igst_rate": "",
      "igst_amount": "",
      "total": ""
    }
  ]
}
""",
        "ewaybill": base_instructions + """
Extract these fields from the E-way Bill:

Important notes:
- "generated_date": Use the FIRST date shown on the document, labeled "Generated Date" or "Date".
- "po_number": Look for purchase order number — may appear as "PO No", "Purchase Order", or near the invoice reference. Typically 10 digits starting with 4 or 6. Return empty string if not found.

{
  "ewaybill_number": "",
  "generated_date": "",
  "validity_date": "",
  "invoice_number": "",
  "invoice_date": "",
  "po_number": "",
  "dispatch_from": "",
  "dispatch_to": "",
  "transport_mode": "",
  "vehicle_number": "",
  "transporter_name": "",
  "transporter_gstin": "",
  "transport_doc_no": "",
  "transport_doc_date": ""
}
""",
        "lr": base_instructions + """
Extract these fields from the Lorry Receipt:

Important notes:
- "lr_date": Use the FIRST date at the top of the lorry receipt. Dates may be handwritten — read carefully.
- "lr_number": The consignment/lorry receipt number, usually at the top. May be labeled "No.", "LR No", "GR No", or "Consignment No", or just printed prominently. IMPORTANT: Lorry receipts often also print an unrelated "Invoice No." somewhere on the same page — do NOT return that value here under any circumstances. Only return a number found next to an LR/GR/Consignment label, or if truly unlabeled, the number in the position where an LR number normally appears (top area, near the LR date). If you cannot confidently tell the LR number apart from an invoice number on this document, return an empty string rather than guessing.

{
  "lr_number": "",
  "lr_date": "",
  "consignor_name": "",
  "consignee_name": "",
  "vehicle_number": "",
  "material_description": "",
  "quantity": "",
  "weight": "",
  "delivery_address": "",
  "from_location": "",
  "to_location": "",
  "transporter_name": "",
  "freight_amount": ""
}
"""
    }
    return prompts.get(doc_type, "")


def _build_messages(pages_b64: list, prompt: str) -> list:
    """
    Build the WatsonX chat messages payload.
    Sends all pages as images followed by the extraction prompt.
    """
    content = []
    for i, page_b64 in enumerate(pages_b64):
        content.append({
            "type": "image_url",
            "image_url": {
                "url": f"data:image/png;base64,{page_b64}"
            }
        })
    content.append({
        "type": "text",
        "text": prompt
    })
    return [{"role": "user", "content": content}]


def _sanitize_json_control_chars(text: str) -> str:
    """
    Defensive safety net (not a substitute for the prompt's SINGLE LINE
    rule, but a backstop for when the model doesn't follow it): replace
    raw control characters (newline, carriage return, tab) found INSIDE a
    JSON string value with a single space. A literal, unescaped control
    character inside a string is invalid per the JSON spec and makes
    json.loads() fail on the ENTIRE response -- this is exactly what was
    observed when the model joined a multi-line address with an actual
    line break instead of a comma. Only whitespace control characters
    between quotes are touched; nothing outside a string, and no other
    character, is ever modified -- so this cannot change what value was
    extracted, only make an otherwise-unparseable response parseable.
    """
    out = []
    in_string = False
    escape = False
    for ch in text:
        if in_string:
            if escape:
                out.append(ch)
                escape = False
            elif ch == '\\':
                out.append(ch)
                escape = True
            elif ch == '"':
                in_string = False
                out.append(ch)
            elif ch in ('\n', '\r', '\t'):
                out.append(' ')
            else:
                out.append(ch)
        else:
            if ch == '"':
                in_string = True
            out.append(ch)
    return ''.join(out)


def _parse_response(raw_text: str, doc_type: str) -> Optional[dict]:
    """
    Parse WatsonX response text into a dict.
    Handles markdown code block wrapping.
    """
    text = raw_text.strip()

    # Strip markdown code blocks
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0].strip()
    elif "```" in text:
        text = text.split("```")[1].split("```")[0].strip()

    # Safety net: repair raw line breaks/tabs left inside string values
    # before attempting to parse -- see _sanitize_json_control_chars().
    text = _sanitize_json_control_chars(text)

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as e:
        # Try to extract JSON object from text if surrounded by other content
        match = re.search(r'\{[\s\S]*\}', text)
        if match:
            try:
                parsed = json.loads(match.group())
            except Exception:
                logger.error(f"JSON parse failed for {doc_type}: {e}")
                return None
        else:
            logger.error(f"No JSON found in response for {doc_type}: {e}")
            return None

    # Ensure HSN details is always a list
    if doc_type == "invoice" and "hsn_details" in parsed:
        if not isinstance(parsed["hsn_details"], list):
            # WatsonX returned a string or null — wrap or reset
            parsed["hsn_details"] = []

    return parsed


def process_document(doc_type: str, file_path: str, filename: str) -> Optional[dict]:
    """
    Main OCR function. Sends PDF pages to WatsonX and returns extracted data.

    Args:
        doc_type:  'invoice', 'ewaybill', or 'lr'
        file_path: Absolute path to the PDF file
        filename:  Original filename (stored in DB)

    Returns:
        dict of extracted fields with DB-compatible keys, or None on failure
    """
    logger.info(f"Starting OCR for {doc_type}: {filename}")

    model = _get_model()
    if not model:
        logger.error("WatsonX model not available. Cannot process document.")
        return None

    # Encode up to 10 pages
    pages_b64 = _encode_pages_to_base64(file_path, max_pages=10)
    if not pages_b64:
        logger.error(f"Failed to encode PDF pages for {filename}")
        return None

    prompt = _build_prompt(doc_type)
    if not prompt:
        logger.error(f"No prompt defined for doc_type: {doc_type}")
        return None

    try:
        messages = _build_messages(pages_b64, prompt)
        response = model.chat(messages=messages)
        raw_text = response["choices"][0]["message"]["content"]

        logger.debug(f"WatsonX raw response for {doc_type}: {raw_text[:500]}")

        extracted = _parse_response(raw_text, doc_type)
        if not extracted:
            return None

        # Clean all values — remove None/"N/A"/null etc
        extracted = _clean_extracted(extracted)

        # Normalize date fields to YYYY-MM-DD for HTML date inputs
        date_fields_map = {
            "invoice":  ["invoice_date"],
            "ewaybill": ["generated_date", "validity_date", "invoice_date", "transport_doc_date"],
            "lr":       ["lr_date"],
        }
        date_fields = date_fields_map.get(doc_type, [])
        if date_fields:
            extracted = _normalize_dates_in_dict(extracted, date_fields)

        # Server-side GSTIN OCR-error correction and IRN cleanup (client
        # requested: never blank a value that fails validation -- only ever
        # repair it deterministically, or tidy it up, and otherwise leave it
        # exactly as extracted so the existing client-side red-highlight
        # checks (_validateGstinField()/_checkIrnLength() in
        # extracted_data.html) can flag it for the reviewer as before).
        if doc_type == "invoice":
            if extracted.get("buyer_gstin"):
                extracted["buyer_gstin"] = _attempt_gstin_correction(extracted["buyer_gstin"])
            if extracted.get("seller_gstin"):
                extracted["seller_gstin"] = _attempt_gstin_correction(extracted["seller_gstin"])
            if extracted.get("irn"):
                extracted["irn"] = _clean_irn(extracted["irn"])
        elif doc_type == "ewaybill":
            if extracted.get("transporter_gstin"):
                extracted["transporter_gstin"] = _attempt_gstin_correction(extracted["transporter_gstin"])

        # Always set filename
        extracted["filename"] = filename

        logger.info(f"OCR successful for {doc_type}: {filename} — {len(extracted)} fields extracted")
        return extracted

    except Exception as e:
        logger.error(f"WatsonX API error for {doc_type} '{filename}': {e}", exc_info=True)
        return None