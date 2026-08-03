"""
services/mail_poller.py — Standalone IMAP inbox poller.

Run via Windows Task Scheduler every 5 minutes.
Fixes applied:
  - FIX 6: Failed OCR alert email to admin
  - FIX 7: Duplicate invoice detection before creating history record
"""

import imaplib
import email
import os
import sys
from datetime import datetime
from email.header import decode_header
from typing import Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.config import config
from config.logger import get_logger
from database.connection import init_pool
from database.db_operations import (
    create_history_record,
    save_invoice_to_db,
    save_ewaybill_to_db,
    save_lr_to_db
)
from database.gatein_operations import upsert_gatein_entry, map_ocr_to_gatein
from database.migo_operations import upsert_migo_entry, map_ocr_to_migo
from database.miro_operations import upsert_miro_entry, map_ocr_to_miro
from services.extract import process_document
from services.mail_service import send_documents_received_confirmation, _send_email

logger = get_logger(__name__)


# ============================================================
# SUBJECT PARSING
# ============================================================

def parse_subject(subject: str) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Parse InvoiceNumber_PONumber_Date from mail subject."""
    try:
        parts = subject.strip().split("_")
        invoice_number = parts[0].strip() if len(parts) > 0 else None
        po_number      = parts[1].strip() if len(parts) > 1 else None
        date_str       = parts[2].strip() if len(parts) > 2 else None
        logger.info(f"Parsed subject — Invoice: {invoice_number}, PO: {po_number}, Date: {date_str}")
        return invoice_number, po_number, date_str
    except Exception as e:
        logger.warning(f"Could not parse subject '{subject}': {e}")
        return None, None, None


def detect_doc_type(filename: str) -> Optional[str]:
    """Detect document type from filename keywords."""
    name_lower = filename.lower()
    if config.INVOICE_KEYWORD in name_lower:
        return "invoice"
    if config.EWAYBILL_KEYWORD in name_lower:
        return "ewaybill"
    if config.LR_KEYWORD in name_lower:
        return "lr"
    return None


# ============================================================
# IMAP
# ============================================================

def connect_imap() -> Optional[imaplib.IMAP4_SSL]:
    try:
        mail = imaplib.IMAP4_SSL(config.IMAP_SERVER, config.IMAP_PORT)
        mail.login(config.IMAP_USERNAME, config.IMAP_PASSWORD)
        logger.info("IMAP connection established.")
        return mail
    except Exception as e:
        logger.error(f"Failed to connect to IMAP: {e}")
        return None


def get_unread_mails(mail: imaplib.IMAP4_SSL) -> list:
    try:
        mail.select(config.IMAP_POLL_FOLDER)
        status, data = mail.search(None, "UNSEEN")
        if status != "OK" or not data[0]:
            return []
        ids = data[0].split()
        logger.info(f"Found {len(ids)} unread mail(s).")
        return ids
    except Exception as e:
        logger.error(f"Error fetching unread mails: {e}")
        return []


def mark_as_read(mail: imaplib.IMAP4_SSL, mail_id: bytes) -> None:
    try:
        mail.store(mail_id, "+FLAGS", "\\Seen")
    except Exception as e:
        logger.warning(f"Could not mark mail {mail_id} as read: {e}")


def get_sender_address(msg) -> Optional[str]:
    from_field = msg.get("From", "")
    if "<" in from_field and ">" in from_field:
        return from_field.split("<")[1].rstrip(">").strip()
    return from_field.strip() or None


# ============================================================
# FIX 7: DUPLICATE DETECTION
# ============================================================

def check_duplicate_invoice(invoice_number: str) -> Optional[int]:
    """
    Returns existing history_id if invoice already in DB, else None.
    """
    if not invoice_number:
        return None
    try:
        with __import__('database.connection', fromlist=['get_connection']).get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id FROM invoice_data WHERE invoice_number = %s LIMIT 1",
                    (invoice_number,)
                )
                row = cur.fetchone()
                return row[0] if row else None
    except Exception as e:
        logger.error(f"Duplicate check query failed: {e}")
        return None


# ============================================================
# FIX 6: FAILED OCR ALERT
# ============================================================

def send_ocr_failure_alert(subject: str, sender: Optional[str], history_id: int) -> None:
    """
    Alert the admin inbox when OCR fails so manual action can be taken.
    """
    admin_email = config.EMAIL_SENDER
    if not admin_email:
        return
    try:
        _send_email(
            to_addresses=[admin_email],
            subject=f"[ACTION REQUIRED] OCR Failed — {subject}",
            html_body=f"""
            <html><body style="font-family:Arial,sans-serif;color:#333;">
            <p style="color:#c0392b;font-weight:bold;">&#9888; Document OCR Processing Failed</p>
            <p>A mail was received but document extraction failed or returned no data.</p>
            <table style="border-collapse:collapse;margin:16px 0;">
                <tr style="background:#f5f5f5;">
                    <td style="padding:8px 16px;font-weight:bold;">Mail Subject:</td>
                    <td style="padding:8px 16px;">{subject}</td></tr>
                <tr>
                    <td style="padding:8px 16px;font-weight:bold;">Sent By:</td>
                    <td style="padding:8px 16px;">{sender or 'Unknown'}</td></tr>
                <tr style="background:#f5f5f5;">
                    <td style="padding:8px 16px;font-weight:bold;">History ID:</td>
                    <td style="padding:8px 16px;">{history_id}</td></tr>
            </table>
            <p><strong>Action required:</strong> Manually upload documents for
            History ID <strong>{history_id}</strong> via the portal,
            or ask the sender to resend.</p>
            <p>Check <code>logs/errors.log</code> for full details.</p>
            </body></html>
            """
        )
        logger.info(f"OCR failure alert sent to {admin_email} for history_id {history_id}")
    except Exception as e:
        logger.error(f"Could not send OCR failure alert: {e}")


def send_duplicate_reply(sender: str, invoice_number: str, existing_id: int) -> None:
    """Notify sender their docs are already in the system."""
    try:
        _send_email(
            to_addresses=[sender],
            subject=f"Documents Already Received — Invoice {invoice_number}",
            html_body=f"""
            <html><body style="font-family:Arial,sans-serif;color:#333;">
            <p>Dear Sir/Madam,</p>
            <p>These documents have already been received and are in our system.</p>
            <table style="border-collapse:collapse;margin:16px 0;">
                <tr><td style="padding:6px 12px;font-weight:bold;">Invoice Number:</td>
                    <td style="padding:6px 12px;">{invoice_number}</td></tr>
                <tr><td style="padding:6px 12px;font-weight:bold;">Reference ID:</td>
                    <td style="padding:6px 12px;">{existing_id}</td></tr>
            </table>
            <p>If you believe this is an error, please contact the team.</p>
            <p>Regards,<br>Material Inward Automation System</p>
            </body></html>
            """
        )
    except Exception as e:
        logger.warning(f"Could not send duplicate reply: {e}")


# ============================================================
# CORE MAIL PROCESSING
# ============================================================

def process_mail(mail: imaplib.IMAP4_SSL, mail_id: bytes) -> bool:
    """
    Process one unread mail end-to-end.
    Returns True = processed (mark as read). False = retry next cycle.
    """
    try:
        status, data = mail.fetch(mail_id, "(RFC822)")
        if status != "OK":
            logger.error(f"Failed to fetch mail {mail_id}")
            return False

        raw_email = data[0][1]
        msg = email.message_from_bytes(raw_email)

        # Decode subject
        raw_subject = msg.get("Subject", "")
        decoded_parts = decode_header(raw_subject)
        subject = ""
        for part, enc in decoded_parts:
            if isinstance(part, bytes):
                subject += part.decode(enc or "utf-8", errors="replace")
            else:
                subject += part

        logger.info(f"Processing mail — Subject: '{subject}'")

        sender = get_sender_address(msg)
        invoice_number, po_number, _ = parse_subject(subject)

        # FIX 7: Duplicate check
        if invoice_number:
            existing_id = check_duplicate_invoice(invoice_number)
            if existing_id:
                logger.warning(
                    f"Duplicate — Invoice '{invoice_number}' already in DB "
                    f"as history_id={existing_id}. Skipping."
                )
                if sender:
                    send_duplicate_reply(sender, invoice_number, existing_id)
                return True  # Mark as read — intentionally not reprocessed

        # Extract PDF attachments
        attachments = {}
        for part in msg.walk():
            if part.get_content_disposition() != "attachment":
                continue
            filename = part.get_filename()
            if not filename or not filename.lower().endswith(".pdf"):
                continue
            doc_type = detect_doc_type(filename)
            if not doc_type:
                logger.warning(f"Unrecognized attachment name: {filename}")
                continue
            payload = part.get_payload(decode=True)
            if not payload:
                continue
            attachments[doc_type] = (filename, payload)
            logger.info(f"Attachment found: {filename} → {doc_type}")

        if not attachments:
            logger.warning(f"No recognized PDF attachments in mail: '{subject}'")
            return False  # Don't mark as read — might be a wrong mail type

        # Create history record
        history_id = create_history_record(
            invoice_number=invoice_number,
            po_number=po_number,
            mail_subject=subject,
            mail_received_at=datetime.now()
        )
        if not history_id:
            logger.error("Failed to create history record.")
            return False

        logger.info(f"Created history_id={history_id} for mail: '{subject}'")

        # Save PDFs and run OCR
        extracted = {"invoice": None, "ewaybill": None, "lr": None}
        upload_dir = config.UPLOAD_FOLDER
        os.makedirs(upload_dir, exist_ok=True)
        ocr_failures = []

        for doc_type, (filename, payload) in attachments.items():
            safe_filename = f"h{history_id}_{filename}"
            file_path = os.path.join(upload_dir, safe_filename)
            with open(file_path, "wb") as f:
                f.write(payload)
            try:
                data_out = process_document(doc_type, file_path, safe_filename)
                if data_out:
                    data_out["filename"] = safe_filename
                    extracted[doc_type] = data_out
                    logger.info(f"OCR OK: {doc_type} (history_id={history_id})")
                else:
                    logger.warning(f"OCR returned no data: {doc_type}")
                    ocr_failures.append(doc_type)
            except Exception as e:
                logger.error(f"OCR exception for {doc_type}: {e}", exc_info=True)
                ocr_failures.append(doc_type)

        # Save extracted data to DB
        if extracted["invoice"]:
            save_invoice_to_db(history_id, extracted["invoice"])
        if extracted["ewaybill"]:
            save_ewaybill_to_db(history_id, extracted["ewaybill"])
        if extracted["lr"]:
            save_lr_to_db(history_id, extracted["lr"])

        # Auto-populate form tables
        inv  = extracted["invoice"]
        eway = extracted["ewaybill"]
        lr   = extracted["lr"]
        upsert_gatein_entry(history_id, map_ocr_to_gatein(inv, eway, lr))
        upsert_migo_entry(history_id, map_ocr_to_migo(inv, eway, lr))
        upsert_miro_entry(history_id, map_ocr_to_miro(inv, eway, lr))

        logger.info(f"All data saved for history_id={history_id}")

        # FIX 6: Alert admin if any OCR failed
        if ocr_failures:
            logger.warning(f"OCR failed for: {ocr_failures} on history_id={history_id}")
            send_ocr_failure_alert(subject, sender, history_id)

        # Send confirmation to sender
        if sender:
            actual_invoice = inv.get("Invoice Number") if inv else invoice_number
            send_documents_received_confirmation(
                to_address=sender,
                invoice_number=actual_invoice or invoice_number,
                po_number=po_number,
                history_id=history_id
            )

        return True

    except Exception as e:
        logger.error(f"Unexpected error processing mail {mail_id}: {e}", exc_info=True)
        return False


# ============================================================
# MAIN POLL CYCLE
# ============================================================

def run_poll_cycle() -> None:
    logger.info("=== Mail poll cycle started ===")
    try:
        init_pool()
    except Exception as e:
        logger.critical(f"DB pool init failed — aborting: {e}")
        return

    mail = connect_imap()
    if not mail:
        logger.error("IMAP connection failed — aborting.")
        return

    try:
        mail_ids = get_unread_mails(mail)
        if not mail_ids:
            logger.info("No unread mails.")
            return
        for mail_id in mail_ids:
            success = process_mail(mail, mail_id)
            if success:
                mark_as_read(mail, mail_id)
                logger.info(f"Mail {mail_id} processed and marked read.")
            else:
                logger.warning(f"Mail {mail_id} failed — will retry next cycle.")
    finally:
        try:
            mail.logout()
        except Exception:
            pass

    logger.info("=== Mail poll cycle complete ===")


if __name__ == "__main__":
    run_poll_cycle()
