
"""
config/config.py — Central configuration loader.
All settings read from .env — never hardcode credentials.
"""

import os
from dotenv import load_dotenv

# FIX: load_dotenv() with no argument searches for .env starting from the
# CURRENT WORKING DIRECTORY and walking upward -- fine when the Flask app is
# launched from the project root (start_server.bat's cwd), but Windows Task
# Scheduler tasks (dms_upload_runner.py, dms_scheduler.py) don't reliably run
# with that same cwd. If this silently fails to find .env, every os.getenv()
# below falls back to its hardcoded default -- e.g. RF_SCRIPTS_PATH falls
# back to "C:\material_inward\robot_scripts" instead of the real configured
# path, silently pointing at the wrong (possibly nonexistent, possibly
# stale) robot_scripts folder with no error at all. Anchoring to this file's
# own location (config/config.py -> project root is one level up) makes
# .env resolution independent of whatever process/cwd imported this module.
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(_PROJECT_ROOT, ".env"))



class Config:
    # --- Flask ---
    SECRET_KEY: str = os.getenv("FLASK_SECRET_KEY", "change-this-in-production")
    ENV: str = os.getenv("FLASK_ENV", "production")
    HOST: str = os.getenv("SERVER_HOST", "127.0.0.1")
    PORT: int = int(os.getenv("SERVER_PORT", 5000))
    ALLOWED_ORIGIN: str = os.getenv("ALLOWED_ORIGIN", "http://localhost:5000")

    # --- Environment switch: production vs development install location ---
    # IS_PRODUCTION=true  -> use PROD_APP_ROOT below
    # IS_PRODUCTION=false -> use DEV_APP_ROOT below
    # Every environment-dependent folder this app writes to (robot scripts,
    # robot output logs, DMS staging, GST screenshot/edge-profile folders)
    # is derived from APP_ROOT below and keeps the SAME internal folder
    # layout in both prod and dev -- only the root changes. This is the
    # single switch that makes a dev install a full replica of prod.
    # Deliberately NOT included in this switch (left as single values,
    # unaffected by IS_PRODUCTION): DB_*, SAP_*, AD_* (by explicit
    # instruction -- changed by hand for now), and services/robot_lock.py's
    # lock file path (shared with the separate Password Reset app -- prod
    # and dev run on different servers so there's no collision risk either
    # way, and moving it would require hand-editing that other app too).
    IS_PRODUCTION: bool = os.getenv("IS_PRODUCTION", "true").lower() == "true"
    _PROD_APP_ROOT: str = os.getenv("PROD_APP_ROOT", r"C:\Users\ctn_suresh\Agents\material_inward_FINAL (2)\material_inward_FINAL\material_inward")
    _DEV_APP_ROOT: str = os.getenv("DEV_APP_ROOT", r"C:\material_inward")
    APP_ROOT: str = _PROD_APP_ROOT if IS_PRODUCTION else _DEV_APP_ROOT

    # --- JWT ---
    JWT_SECRET_KEY: str = os.getenv("JWT_SECRET_KEY", "change-this-in-production")
    JWT_ALGORITHM: str = os.getenv("JWT_ALGORITHM", "HS256")

    # --- PostgreSQL ---
    DB_HOST: str = os.getenv("DB_HOST", "localhost")
    DB_PORT: int = int(os.getenv("DB_PORT", 5432))
    DB_NAME: str = os.getenv("DB_NAME", "material_inward")
    DB_USER: str = os.getenv("DB_USER", "")
    DB_PASSWORD: str = os.getenv("DB_PASSWORD", "")
    # Raised from 2/10: a single /view/<id> page load alone checks out 5
    # sequential connections (history + gate-in + migo + miro + PO line items),
    # plus continuous polling from every open tab (/api/gst/status every 5s,
    # /api/queue_status every 3s) and the background RF queue worker -- with
    # 20 concurrent users the old cap of 10 left very little headroom.
    DB_MIN_CONNECTIONS: int = 5
    DB_MAX_CONNECTIONS: int = 25

    # --- SAP ---
    SAP_LOGON_PATH: str = os.getenv("SAP_LOGON_PATH", r"C:\Program Files\SAP\FrontEnd\SAPGUI\saplogon.exe")
    SAP_CONNECTION_NAME: str = os.getenv("SAP_CONNECTION_NAME", "SAP Production System")
    SAP_CLIENT: str = os.getenv("SAP_CLIENT", "400")
    SAP_USERNAME: str = os.getenv("SAP_USERNAME", "")
    SAP_PASSWORD: str = os.getenv("SAP_PASSWORD", "")

    # --- Active Directory / LDAP (login) ---
    # Same spl.com domain / server used by the Password Reset app and the
    # Ecosystem Dashboard -- confirmed with the client to be the correct
    # domain for Material Inward's own login too, not a different one.
    AD_SERVER: str = os.getenv("AD_SERVER", "192.168.203.117")
    AD_DOMAIN: str = os.getenv("AD_DOMAIN", "spl.com")

    # --- Email SMTP (outgoing) + IMAP (mail poller) — prod/dev pair ---
    # Mailbox isn't in active use yet (INTAKE_METHOD=folder), but follows
    # the same IS_PRODUCTION switch as everything else so it's ready
    # whenever intake switches to "mail". DEV_* values are blank until you
    # provide a dev mailbox -- fill them in .env when ready.
    _MAIL_PREFIX: str = "PROD_" if IS_PRODUCTION else "DEV_"
    SMTP_SERVER: str = os.getenv(_MAIL_PREFIX + "SMTP_SERVER", "smtp.office365.com")
    SMTP_PORT: int = int(os.getenv(_MAIL_PREFIX + "SMTP_PORT", 587))
    EMAIL_SENDER: str = os.getenv(_MAIL_PREFIX + "EMAIL_SENDER", "")
    EMAIL_PASSWORD: str = os.getenv(_MAIL_PREFIX + "EMAIL_PASSWORD", "")

    IMAP_SERVER: str = os.getenv(_MAIL_PREFIX + "IMAP_SERVER", "outlook.office365.com")
    IMAP_PORT: int = int(os.getenv(_MAIL_PREFIX + "IMAP_PORT", 993))
    IMAP_USERNAME: str = os.getenv(_MAIL_PREFIX + "IMAP_USERNAME", "")
    IMAP_PASSWORD: str = os.getenv(_MAIL_PREFIX + "IMAP_PASSWORD", "")
    IMAP_POLL_FOLDER: str = os.getenv("IMAP_POLL_FOLDER", "INBOX")

    # --- Step-specific Email Recipients (no fallback) ---
    GATEIN_OWNER_EMAIL: str   = os.getenv("GATEIN_OWNER_EMAIL", "")
    MIGO_103_OWNER_EMAIL: str = os.getenv("MIGO_103_OWNER_EMAIL", "")
    MIGO_105_OWNER_EMAIL: str = os.getenv("MIGO_105_OWNER_EMAIL", "")
    MIRO_OWNER_EMAIL: str     = os.getenv("MIRO_OWNER_EMAIL", "")
    ADMIN_EMAIL: str          = os.getenv("ADMIN_EMAIL", "")

    # --- WatsonX AI ---
    WATSONX_API_KEY: str    = os.getenv("WATSONX_API_KEY", "")
    WATSONX_PROJECT_ID: str = os.getenv("WATSONX_PROJECT_ID", "")
    WATSONX_URL: str        = os.getenv("WATSONX_URL", "https://us-south.ml.cloud.ibm.com")
    WATSONX_MODEL_ID: str   = os.getenv("WATSONX_MODEL_ID", "meta-llama/llama-4-maverick-17b-128e-instruct-fp8")

    # --- Robot Framework ---
    # Derived from APP_ROOT (see IS_PRODUCTION switch above) -- no longer a
    # standalone .env override, so it can never silently drift out of sync
    # with which environment is actually active.
    RF_SCRIPTS_PATH: str = os.path.join(APP_ROOT, "robot_scripts")
    RF_OUTPUT_PATH: str  = os.path.join(APP_ROOT, "logs", "rf_output")

    # --- File Handling ---
    UPLOAD_FOLDER: str           = os.getenv("UPLOAD_FOLDER", "uploads")
    DMS_STAGING_FOLDER: str      = os.path.join(APP_ROOT, "dms_staging")
    # v16: where dms_upload.robot's document-link step (Send To > Generate
    # Document link) writes {filename, Contentverse URL} rows, and where
    # services/dms_links_import.py reads them from afterward. Derived from
    # APP_ROOT like DMS_STAGING_FOLDER above -- dms_upload.robot itself now
    # reads this straight from config.config too (see its Load Environment
    # Variables keyword), instead of a separate raw .env lookup, so there's
    # exactly one place this path is computed.
    DMS_LINKS_EXCEL_PATH: str    = os.path.join(APP_ROOT, "dms_staging", "document_links.xlsx")
    UPLOAD_PROCESSED_FOLDER: str = os.path.join(os.getenv("UPLOAD_FOLDER", "uploads"), "processed")
    UPLOAD_FAILED_FOLDER: str    = os.path.join(os.getenv("UPLOAD_FOLDER", "uploads"), "failed")
    MAX_FILE_SIZE_BYTES: int     = int(os.getenv("MAX_FILE_SIZE_MB", 50)) * 1024 * 1024

    # --- GST bots (services/einvoice_bot.py, services/taxpayer_search_bot.py) ---
    # Screenshot + persistent-Edge-profile roots, also derived from
    # APP_ROOT. Each bot appends its own subfolder ("einvoice" / "taxpayer").
    GST_SCREENSHOTS_ROOT: str   = os.path.join(APP_ROOT, "gst_screenshots")
    GST_EDGE_PROFILE_ROOT: str  = os.path.join(APP_ROOT, "gst_edge_profile")

    # --- Document keyword detection ---
    # Filenames arrive as INVOICENO_<KEYWORD>.pdf (any case) -- e.g.
    # 4500012345_INV.pdf / 4500012345_EWB.pdf / 4500012345_LR.pdf.
    # These are matched as the EXACT last underscore-segment of the filename
    # (see services/folder_watcher.py _detect_doc_type), not a substring
    # search -- so keep these short and exact, not partial words.
    INVOICE_KEYWORD: str  = os.getenv("INVOICE_KEYWORD", "inv").lower()
    EWAYBILL_KEYWORD: str = os.getenv("EWAYBILL_KEYWORD", "ewb").lower()
    LR_KEYWORD: str       = os.getenv("LR_KEYWORD", "lr").lower()
    # v14: 4th document type -- miscellaneous supporting docs, pre-merged by
    # the sender into a single PDF, not run through OCR but merged into the
    # DMS-bound consolidated PDF alongside invoice/eway/lr. At most one per
    # invoice group, same as the other three.
    OTHERS_KEYWORD: str   = os.getenv("OTHERS_KEYWORD", "oth").lower()

    # --- Folder-drop intake ---
    INTAKE_METHOD: str = os.getenv("INTAKE_METHOD", "folder").lower()
    WATCH_FOLDER: str  = os.getenv("WATCH_FOLDER", r"C:\material_inward\incoming")

    # --- Phase rollout: which workflow steps are enabled ---
    # Granular flags: gate_in, migo_103, migo_105, miro, gst
    _ENABLED_STEPS_RAW: str = os.getenv("ENABLED_STEPS", "gate_in,migo_103,migo_105,miro,gst")

    @classmethod
    def is_step_enabled(cls, step: str) -> bool:
        steps = [s.strip().lower() for s in cls._ENABLED_STEPS_RAW.split(",")]
        return step.lower() in steps

    # --- In-app notifications ---
    ENABLE_INAPP_NOTIFICATIONS: bool = os.getenv("ENABLE_INAPP_NOTIFICATIONS", "false").lower() == "true"

    # --- Auto-pair tuning ---
    AUTO_PAIR_MIN_MATCHES: int       = int(os.getenv("AUTO_PAIR_MIN_MATCHES", 3))
    AUTO_PAIR_AMOUNT_TOLERANCE: float = float(os.getenv("AUTO_PAIR_AMOUNT_TOLERANCE", 5))

    # --- Upload access ---
    ALLOW_USER_UPLOAD: bool       = os.getenv("ALLOW_USER_UPLOAD", "false").lower() == "true"
    SHOW_DASHBOARD_COUNTS: bool   = os.getenv("SHOW_DASHBOARD_COUNTS", "false").lower() == "true"

    ENABLE_STEP_LOCKS: bool = os.getenv('ENABLE_STEP_LOCKS', 'true').lower() == 'true'


config = Config()

# --- Auto-create environment-dependent folders ---
# So a freshly pointed DEV_APP_ROOT (or a PROD_APP_ROOT that hasn't been
# fully unpacked yet) doesn't need every subfolder hand-built before first
# run. Non-fatal if a folder can't be created (e.g. the drive isn't
# reachable yet) -- individual callers still os.makedirs() before writing,
# this is just a best-effort head start at import time.
for _dir in (
    config.RF_OUTPUT_PATH,
    config.DMS_STAGING_FOLDER,
    os.path.join(config.GST_SCREENSHOTS_ROOT, "einvoice"),
    os.path.join(config.GST_SCREENSHOTS_ROOT, "taxpayer"),
    os.path.join(config.GST_EDGE_PROFILE_ROOT, "einvoice"),
    os.path.join(config.GST_EDGE_PROFILE_ROOT, "taxpayer"),
    os.path.join(config.APP_ROOT, "logs"),
):
    try:
        os.makedirs(_dir, exist_ok=True)
    except Exception:
        pass