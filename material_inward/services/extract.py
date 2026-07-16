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
"""

    prompts = {
        "invoice": base_instructions + """
Extract these fields from the invoice:

Important field notes:
- "invoice_date": Use the FIRST date that appears at the top of the invoice — typically labeled "Date", "Invoice Date", or "Dated". Do not use delivery date, dispatch date, or due date.
- "po_number": A numeric purchase order number typically starting with 4 or 6 followed by 9 digits (e.g. 4500012345 or 6300001343). Check ALL pages — it may appear on page 2 in a receiving/gate entry form under "Purchase Order No." or "Purchase Order No". Also check "Our Order No", "Buyer Order No". If only a text reference like "TELE BY..." appears or genuinely not found, return empty string.
- "grand_total": The final payable amount including all taxes — the largest total amount on the invoice.
-"material_code": The vendor or buyer internal item identifier — look for labels
  like "Item Code", "Product Code", "Part No", "SAP Code", "Material Code",
  "Art. No", "Cat. No", or any alphanumeric code column separate from HSN/SAC.
  Return empty string if not found.
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
- "lr_number": The consignment/lorry receipt number, usually at the top. May be labeled "No.", "LR No", or just printed prominently.

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

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as e:
        # Try to extract JSON object from text if surrounded by other content
        import re
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

        # Always set filename
        extracted["filename"] = filename

        logger.info(f"OCR successful for {doc_type}: {filename} — {len(extracted)} fields extracted")
        return extracted

    except Exception as e:
        logger.error(f"WatsonX API error for {doc_type} '{filename}': {e}", exc_info=True)
        return None