"""
services/mail_service.py — Outgoing email notifications.

v4 changes:
- Step-specific recipient envs: MIGO_103_OWNER_EMAIL, MIGO_105_OWNER_EMAIL
- ADMIN_EMAIL for OCR failures
- Each notification also fans out to per-user recipients (filtered by
  email_notifications_enabled + step_roles)
- No fallback chain — empty env = no email
"""

import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import List, Optional

from config.config import config
from config.logger import get_logger
from database.user_operations import get_users_for_step

logger = get_logger(__name__)


def _send_email(
    to_addresses: List[str],
    subject: str,
    html_body: str,
    cc_addresses: Optional[List[str]] = None
) -> bool:
    if not config.EMAIL_SENDER or not config.EMAIL_PASSWORD:
        logger.warning("Email sender credentials not configured. Skipping email.")
        return False

    to_addresses = list({a for a in to_addresses if a})  # dedupe
    if not to_addresses:
        logger.info(f"No valid recipients for email '{subject}'. Skipping.")
        return False

    try:
        msg = MIMEMultipart("alternative")
        msg["From"]    = config.EMAIL_SENDER
        msg["To"]      = ", ".join(to_addresses)
        msg["Subject"] = subject
        if cc_addresses:
            msg["Cc"] = ", ".join([a for a in cc_addresses if a])

        msg.attach(MIMEText(html_body, "html"))

        with smtplib.SMTP(config.SMTP_SERVER, config.SMTP_PORT) as server:
            server.ehlo()
            server.starttls()
            server.login(config.EMAIL_SENDER, config.EMAIL_PASSWORD)
            all_recipients = to_addresses + (cc_addresses or [])
            server.sendmail(config.EMAIL_SENDER, all_recipients, msg.as_string())

        logger.info(f"Email sent — Subject: '{subject}' | To: {to_addresses}")
        return True

    except smtplib.SMTPException as e:
        logger.error(f"SMTP error sending email '{subject}': {e}")
        return False
    except Exception as e:
        logger.error(f"Unexpected error sending email '{subject}': {e}")
        return False


def _collect_recipients(env_email: str, step_role: str) -> List[str]:
    """
    Build recipient list from:
      - env-configured step owner email (if non-empty)
      - per-user emails matching step_role + email_notifications_enabled
    """
    recipients = []
    if env_email:
        recipients.append(env_email)

    try:
        users = get_users_for_step(step_role)
        for u in users:
            if u.get("email"):
                recipients.append(u["email"])
    except Exception as e:
        logger.warning(f"Could not fetch user-level recipients for {step_role}: {e}")

    return list({r for r in recipients if r})


# ============================================================
# DOCUMENTS RECEIVED CONFIRMATION (sender of original mail)
# ============================================================

def send_documents_received_confirmation(
    to_address: str,
    invoice_number: Optional[str],
    po_number: Optional[str],
    history_id: int
) -> bool:
    subject = f"Documents Received & Processed — Invoice {invoice_number or 'N/A'}"
    body = f"""
    <html><body style="font-family: Arial, sans-serif; color: #333;">
    <p>Dear Sir/Madam,</p>
    <p>We have received your documents and they have been successfully processed by our system.</p>
    <table style="border-collapse: collapse; margin: 16px 0;">
        <tr><td style="padding: 6px 12px; font-weight: bold;">Invoice Number:</td>
            <td style="padding: 6px 12px;">{invoice_number or 'Not extracted'}</td></tr>
        <tr><td style="padding: 6px 12px; font-weight: bold;">PO Number:</td>
            <td style="padding: 6px 12px;">{po_number or 'Not extracted'}</td></tr>
        <tr><td style="padding: 6px 12px; font-weight: bold;">Reference ID:</td>
            <td style="padding: 6px 12px;">{history_id}</td></tr>
    </table>
    <p>The documents are now visible in the Material Inward portal and are pending verification.</p>
    <p>Regards,<br><strong>Material Inward Automation System</strong></p>
    </body></html>
    """
    return _send_email([to_address], subject, body)


# ============================================================
# APPROVAL — notify Gate In team
# ============================================================

def send_approval_notification(
    history_id: int,
    invoice_number: Optional[str],
    approved_by: str
) -> bool:
    subject = f"Documents Approved — Ready for Gate In | Invoice {invoice_number or history_id}"
    body = f"""
    <html><body style="font-family:Arial,sans-serif;color:#333;">
    <p>Dear Gate In Team,</p>
    <p>Documents for the below shipment have been verified and approved.
    Please proceed with Gate In posting.</p>
    <table style="border-collapse:collapse;margin:16px 0;">
        <tr style="background:#f5f5f5;">
            <td style="padding:8px 16px;font-weight:bold;">Invoice Number:</td>
            <td style="padding:8px 16px;">{invoice_number or 'N/A'}</td></tr>
        <tr><td style="padding:8px 16px;font-weight:bold;">Approved By:</td>
            <td style="padding:8px 16px;">{approved_by}</td></tr>
        <tr style="background:#f5f5f5;">
            <td style="padding:8px 16px;font-weight:bold;">Reference ID:</td>
            <td style="padding:8px 16px;">{history_id}</td></tr>
    </table>
    <p>Regards,<br><strong>Material Inward Automation System</strong></p>
    </body></html>
    """
    recipients = _collect_recipients(config.GATEIN_OWNER_EMAIL, "gate_in")
    if not recipients:
        logger.info("No Gate In recipients configured. Approval email skipped.")
        return False
    return _send_email(recipients, subject, body)


# ============================================================
# GATE IN COMPLETED
# ============================================================

def send_gate_in_notification(
    gate_in_number: str,
    history_id: int,
    invoice_number: Optional[str] = None,
    po_number: Optional[str] = None
) -> bool:
    subject = f"Gate In Completed — GIN: {gate_in_number}"
    body = f"""
    <html><body style="font-family: Arial, sans-serif; color: #333;">
    <p>Dear Team,</p>
    <p>Gate In has been successfully completed in SAP.</p>
    <table style="border-collapse: collapse; margin: 16px 0;">
        <tr style="background: #f5f5f5;">
            <td style="padding: 8px 16px; font-weight: bold;">Gate In Number (GIN):</td>
            <td style="padding: 8px 16px; color: #1a7a3c; font-weight: bold; font-size: 16px;">
                {gate_in_number}
            </td>
        </tr>
        <tr>
            <td style="padding: 8px 16px; font-weight: bold;">Invoice Number:</td>
            <td style="padding: 8px 16px;">{invoice_number or 'N/A'}</td>
        </tr>
        <tr style="background: #f5f5f5;">
            <td style="padding: 8px 16px; font-weight: bold;">PO Number:</td>
            <td style="padding: 8px 16px;">{po_number or 'N/A'}</td>
        </tr>
        <tr>
            <td style="padding: 8px 16px; font-weight: bold;">Reference ID:</td>
            <td style="padding: 8px 16px;">{history_id}</td>
        </tr>
    </table>
    <p><strong>MIGO 103 Team:</strong> Please log in to the portal to proceed with MIGO 103 posting.</p>
    <p>Regards,<br><strong>Material Inward Automation System</strong></p>
    </body></html>
    """
    recipients = list(set(
        _collect_recipients(config.GATEIN_OWNER_EMAIL, "gate_in")
        + _collect_recipients(config.MIGO_103_OWNER_EMAIL, "migo_103")
    ))
    if not recipients:
        logger.info("No Gate In/MIGO 103 recipients configured. Email skipped.")
        return False
    return _send_email(recipients, subject, body)


# ============================================================
# MIGO 103 COMPLETED
# ============================================================

def send_migo_103_notification(
    material_doc_number: str,
    history_id: int,
    invoice_number: Optional[str] = None
) -> bool:
    subject = f"MIGO 103 Completed — Material Doc: {material_doc_number}"
    body = f"""
    <html><body style="font-family: Arial, sans-serif; color: #333;">
    <p>Dear Team,</p>
    <p>MIGO 103 (GR into Blocked Stock) has been successfully posted in SAP.</p>
    <table style="border-collapse: collapse; margin: 16px 0;">
        <tr style="background: #f5f5f5;">
            <td style="padding: 8px 16px; font-weight: bold;">Material Document Number:</td>
            <td style="padding: 8px 16px; color: #1a4a7a; font-weight: bold; font-size: 16px;">
                {material_doc_number}
            </td>
        </tr>
        <tr>
            <td style="padding: 8px 16px; font-weight: bold;">Invoice Number:</td>
            <td style="padding: 8px 16px;">{invoice_number or 'N/A'}</td>
        </tr>
        <tr style="background: #f5f5f5;">
            <td style="padding: 8px 16px; font-weight: bold;">Reference ID:</td>
            <td style="padding: 8px 16px;">{history_id}</td>
        </tr>
    </table>
    <p>You can now proceed with <strong>MIGO 105</strong> posting from the portal.</p>
    <p>Regards,<br><strong>Material Inward Automation System</strong></p>
    </body></html>
    """
    recipients = list(set(
        _collect_recipients(config.MIGO_103_OWNER_EMAIL, "migo_103")
        + _collect_recipients(config.MIGO_105_OWNER_EMAIL, "migo_105")
    ))
    if not recipients:
        logger.info("No MIGO 103/105 recipients configured. Email skipped.")
        return False
    return _send_email(recipients, subject, body)


# ============================================================
# MIGO 105 COMPLETED
# ============================================================

def send_migo_105_notification(
    history_id: int,
    invoice_number: Optional[str] = None,
    migo_105_doc: Optional[str] = None
) -> bool:
    subject = f"MIGO 105 Complete — Material Released | History {history_id}"
    body = f"""
    <html><body style="font-family:Arial,sans-serif;color:#333;">
    <p>Dear Team,</p>
    <p>MIGO 105 (Release from Blocked Stock) has been successfully posted in SAP.</p>
    <table style="border-collapse:collapse;margin:16px 0;">
        <tr style="background:#f5f5f5;">
            <td style="padding:8px 16px;font-weight:bold;">Invoice Number:</td>
            <td style="padding:8px 16px;">{invoice_number or 'N/A'}</td>
        </tr>
        <tr>
            <td style="padding:8px 16px;font-weight:bold;">MIGO 105 Document:</td>
            <td style="padding:8px 16px;">{migo_105_doc or 'N/A'}</td>
        </tr>
        <tr style="background:#f5f5f5;">
            <td style="padding:8px 16px;font-weight:bold;">Reference ID:</td>
            <td style="padding:8px 16px;">{history_id}</td>
        </tr>
    </table>
    <p>You can now proceed with <strong>MIRO</strong> posting from the portal.</p>
    <p>Regards,<br><strong>Material Inward Automation System</strong></p>
    </body></html>
    """
    recipients = list(set(
        _collect_recipients(config.MIGO_105_OWNER_EMAIL, "migo_105")
        + _collect_recipients(config.MIRO_OWNER_EMAIL, "miro")
    ))
    if not recipients:
        logger.info("No MIGO 105/MIRO recipients configured. Email skipped.")
        return False
    return _send_email(recipients, subject, body)

# ============================================================
# MIRO COMPLETED
# ============================================================

def send_miro_completion_notification(
    history_id: int,
    invoice_number: Optional[str] = None,
    po_number: Optional[str] = None,
    fi_doc_number: str = None
) -> bool:
    subject = f"MIRO Posted — Material Inward Complete | Invoice {invoice_number or history_id}"
    body = f"""
    <html><body style="font-family: Arial, sans-serif; color: #333;">
    <p>Dear Team,</p>
    <p>MIRO has been successfully posted in SAP. The material inward process is now <strong>complete</strong>.</p>
    <table style="border-collapse: collapse; margin: 16px 0;">
        <tr style="background: #f5f5f5;">
            <td style="padding: 8px 16px; font-weight: bold;">Invoice Number:</td>
            <td style="padding: 8px 16px;">{invoice_number or 'N/A'}</td>
        </tr>
        <tr>
            <td style="padding: 8px 16px; font-weight: bold;">PO Number:</td>
            <td style="padding: 8px 16px;">{po_number or 'N/A'}</td>
        </tr>
        <tr style="background: #f5f5f5;">
            <td style="padding: 8px 16px; font-weight: bold;">Reference ID:</td>
            <td style="padding: 8px 16px;">{history_id}</td>
        </tr>
        <tr>
            <td style="padding: 8px 16px; font-weight: bold;">FI Document Number:</td>
            <td style="padding: 8px 16px;">{fi_doc_number or 'N/A'}</td>
        </tr>
    </table>
    <p>Regards,<br><strong>Material Inward Automation System</strong></p>
    </body></html>
    """
    recipients = list(set(
        _collect_recipients(config.MIRO_OWNER_EMAIL, "miro")
        + _collect_recipients(config.MIGO_103_OWNER_EMAIL, "migo_103")
        + _collect_recipients(config.MIGO_105_OWNER_EMAIL, "migo_105")
        + _collect_recipients(config.GATEIN_OWNER_EMAIL, "gate_in")
    ))
    if not recipients:
        logger.info("No completion email recipients configured. Email skipped.")
        return False
    return _send_email(recipients, subject, body)


# ============================================================
# OCR FAILURE — admin only
# ============================================================

def send_ocr_failure_alert(
    history_id: int,
    invoice_number: Optional[str] = None,
    error_detail: Optional[str] = None
) -> bool:
    if not config.ADMIN_EMAIL:
        logger.info("ADMIN_EMAIL not configured — OCR failure alert skipped.")
        return False

    subject = f"[ACTION REQUIRED] OCR Failed — Reference ID {history_id}"
    body = f"""
    <html><body style="font-family:Arial,sans-serif;color:#333;">
    <p style="color:#c0392b;font-weight:bold;">&#9888; Document OCR Processing Failed</p>
    <p>An incoming document batch could not be processed by OCR.</p>
    <table style="border-collapse:collapse;margin:16px 0;">
        <tr style="background:#f5f5f5;">
            <td style="padding:8px 16px;font-weight:bold;">Reference ID:</td>
            <td style="padding:8px 16px;">{history_id}</td></tr>
        <tr><td style="padding:8px 16px;font-weight:bold;">Invoice Number:</td>
            <td style="padding:8px 16px;">{invoice_number or 'Not extracted'}</td></tr>
        <tr style="background:#f5f5f5;">
            <td style="padding:8px 16px;font-weight:bold;">Error:</td>
            <td style="padding:8px 16px;font-family:monospace;">{error_detail or 'See application logs'}</td></tr>
    </table>
    <p><strong>Action:</strong> Please investigate via the portal. Failed documents are stored in the failed/ folder
    and can be re-run via the "Re-run OCR" button on the record.</p>
    </body></html>
    """
    return _send_email([config.ADMIN_EMAIL], subject, body)