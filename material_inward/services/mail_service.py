"""
services/mail_service.py — Outgoing email notifications via SMTP/Outlook.
All notification functions are here. Add recipient emails to .env when available.
"""

import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import List, Optional

from config.config import config
from config.logger import get_logger

logger = get_logger(__name__)


def _send_email(
    to_addresses: List[str],
    subject: str,
    html_body: str,
    cc_addresses: Optional[List[str]] = None
) -> bool:
    """
    Internal helper — sends an HTML email via SMTP.
    Returns True on success, False on failure.
    """
    if not config.EMAIL_SENDER or not config.EMAIL_PASSWORD:
        logger.warning("Email sender credentials not configured. Skipping email.")
        return False

    to_addresses = [a for a in to_addresses if a]
    if not to_addresses:
        logger.warning("No valid recipients. Skipping email.")
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


# ============================================================
# NOTIFICATION: Documents received (sent after mail poller OCR)
# ============================================================

def send_documents_received_confirmation(
    to_address: str,
    invoice_number: Optional[str],
    po_number: Optional[str],
    history_id: int
) -> bool:
    """
    Confirmation mail sent back to the plant person after documents are processed.
    """
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
    <p>The documents are now visible in the Material Inward portal and will be processed shortly.</p>
    <p>Regards,<br><strong>Material Inward Automation System</strong></p>
    </body></html>
    """
    return _send_email([to_address], subject, body)


# ============================================================
# NOTIFICATION: Gate In completed
# ============================================================

def send_gate_in_notification(
    gate_in_number: str,
    history_id: int,
    invoice_number: Optional[str] = None,
    po_number: Optional[str] = None
) -> bool:
    """
    Sent to Gate In owner AND MIGO owner after Gate In RF completes.
    Gate In person gets confirmation. MIGO person gets signal to proceed.
    """
    subject = f"Gate In Completed — GIN: {gate_in_number}"
    body = f"""
    <html><body style="font-family: Arial, sans-serif; color: #333;">
    <p>Dear Team,</p>
    <p>Gate In has been successfully completed in SAP. Please find the details below:</p>
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
    <p><strong>MIGO Team:</strong> Gate In is complete. Please log in to the portal to proceed with MIGO posting.</p>
    <p>Regards,<br><strong>Material Inward Automation System</strong></p>
    </body></html>
    """
    recipients = [r for r in [config.GATEIN_OWNER_EMAIL, config.MIGO_OWNER_EMAIL] if r]
    if not recipients:
        logger.warning("No Gate In / MIGO owner emails configured. Email not sent.")
        return False
    return _send_email(recipients, subject, body)


# ============================================================
# NOTIFICATION: MIGO 103 completed
# ============================================================

def send_migo_103_notification(
    material_doc_number: str,
    history_id: int,
    invoice_number: Optional[str] = None
) -> bool:
    """
    Sent after MIGO 103 RF completes. Informs team that 105 can proceed.
    """
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
    recipients = [r for r in [config.MIGO_OWNER_EMAIL] if r]
    if not recipients:
        logger.warning("No MIGO owner email configured. Email not sent.")
        return False
    return _send_email(recipients, subject, body)


def send_migo_105_notification(
    history_id: int,
    invoice_number: Optional[str] = None
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
            <td style="padding:8px 16px;font-weight:bold;">Reference ID:</td>
            <td style="padding:8px 16px;">{history_id}</td>
        </tr>
    </table>
    <p>You can now proceed with <strong>MIRO</strong> posting from the portal.</p>
    <p>Regards,<br><strong>Material Inward Automation System</strong></p>
    </body></html>
    """
    recipients = [r for r in [config.MIGO_OWNER_EMAIL] if r]
    if not recipients:
        logger.warning("No MIGO owner email configured.")
        return False
    return _send_email(recipients, subject, body)

# ============================================================
# NOTIFICATION: MIRO completed
# ============================================================

def send_miro_completion_notification(
    history_id: int,
    invoice_number: Optional[str] = None,
    po_number: Optional[str] = None
) -> bool:
    """
    Sent after MIRO RF completes. Signals end of material inward process.
    """
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
    </table>
    <p>Regards,<br><strong>Material Inward Automation System</strong></p>
    </body></html>
    """
    recipients = [r for r in [config.MIRO_OWNER_EMAIL, config.MIGO_OWNER_EMAIL] if r]
    if not recipients:
        logger.warning("No MIRO owner email configured. Email not sent.")
        return False
    return _send_email(recipients, subject, body)
