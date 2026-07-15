
"""
config/config.py — Central configuration loader.
All settings read from .env — never hardcode credentials.
"""

import os
from dotenv import load_dotenv

load_dotenv()



class Config:
    # --- Flask ---
    SECRET_KEY: str = os.getenv("FLASK_SECRET_KEY", "change-this-in-production")
    ENV: str = os.getenv("FLASK_ENV", "production")
    HOST: str = os.getenv("SERVER_HOST", "127.0.0.1")
    PORT: int = int(os.getenv("SERVER_PORT", 5000))
    ALLOWED_ORIGIN: str = os.getenv("ALLOWED_ORIGIN", "http://localhost:5000")

    # --- JWT ---
    JWT_SECRET_KEY: str = os.getenv("JWT_SECRET_KEY", "change-this-in-production")
    JWT_ALGORITHM: str = os.getenv("JWT_ALGORITHM", "HS256")

    # --- PostgreSQL ---
    DB_HOST: str = os.getenv("DB_HOST", "localhost")
    DB_PORT: int = int(os.getenv("DB_PORT", 5432))
    DB_NAME: str = os.getenv("DB_NAME", "material_inward")
    DB_USER: str = os.getenv("DB_USER", "")
    DB_PASSWORD: str = os.getenv("DB_PASSWORD", "")
    DB_MIN_CONNECTIONS: int = 2
    DB_MAX_CONNECTIONS: int = 10

    # --- SAP ---
    SAP_LOGON_PATH: str = os.getenv("SAP_LOGON_PATH", r"C:\Program Files\SAP\FrontEnd\SAPGUI\saplogon.exe")
    SAP_CONNECTION_NAME: str = os.getenv("SAP_CONNECTION_NAME", "SAP Production System")
    SAP_CLIENT: str = os.getenv("SAP_CLIENT", "400")
    SAP_USERNAME: str = os.getenv("SAP_USERNAME", "")
    SAP_PASSWORD: str = os.getenv("SAP_PASSWORD", "")

    # --- Email SMTP (outgoing) ---
    SMTP_SERVER: str = os.getenv("SMTP_SERVER", "smtp.office365.com")
    SMTP_PORT: int = int(os.getenv("SMTP_PORT", 587))
    EMAIL_SENDER: str = os.getenv("EMAIL_SENDER", "")
    EMAIL_PASSWORD: str = os.getenv("EMAIL_PASSWORD", "")

    # --- Email IMAP (mail poller) ---
    IMAP_SERVER: str = os.getenv("IMAP_SERVER", "outlook.office365.com")
    IMAP_PORT: int = int(os.getenv("IMAP_PORT", 993))
    IMAP_USERNAME: str = os.getenv("IMAP_USERNAME", "")
    IMAP_PASSWORD: str = os.getenv("IMAP_PASSWORD", "")
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
    RF_SCRIPTS_PATH: str = os.getenv("RF_SCRIPTS_PATH", r"C:\material_inward\robot_scripts")
    RF_OUTPUT_PATH: str  = os.getenv("RF_OUTPUT_PATH", r"C:\material_inward\logs\rf_output")

    # --- File Handling ---
    UPLOAD_FOLDER: str           = os.getenv("UPLOAD_FOLDER", "uploads")
    UPLOAD_PROCESSED_FOLDER: str = os.path.join(os.getenv("UPLOAD_FOLDER", "uploads"), "processed")
    UPLOAD_FAILED_FOLDER: str    = os.path.join(os.getenv("UPLOAD_FOLDER", "uploads"), "failed")
    MAX_FILE_SIZE_BYTES: int     = int(os.getenv("MAX_FILE_SIZE_MB", 50)) * 1024 * 1024

    # --- Document keyword detection ---
    INVOICE_KEYWORD: str  = os.getenv("INVOICE_KEYWORD", "invoice").lower()
    EWAYBILL_KEYWORD: str = os.getenv("EWAYBILL_KEYWORD", "eway").lower()
    LR_KEYWORD: str       = os.getenv("LR_KEYWORD", "lr").lower()

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