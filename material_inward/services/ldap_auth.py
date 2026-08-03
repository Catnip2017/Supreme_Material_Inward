"""
services/ldap_auth.py — Active Directory / LDAP credential verification.

v14: Material Inward's own login, added this session. Same pattern as the
Password Reset app (verify_ad_password in that repo's app.py) and the
Ecosystem Dashboard (backend/auth.py's verify_ad_user) -- same AD_SERVER /
AD_DOMAIN (spl.com, confirmed with the client to be the correct domain for
this app too, not a different one), same LDAPS bind-as-auth approach: a
successful bind IS the proof the password is correct, nothing else is
checked or stored from AD itself.

This module deliberately does NOT touch the client's `supreme_ai` Postgres
database at all -- per client decision, Material Inward's user list is
its own local table (see database/user_operations.py), seeded once from a
one-time historical import and grown from then on by a SuperAdmin creating
username-only rows directly in this app. LDAP is used here purely to
verify a password against AD; it is not a source of who's allowed to log
in -- that's entirely gated by whether a row exists in this app's own
`users` table.
"""

import ssl
from typing import Optional

from ldap3 import Server, Connection, NONE, SYNC, SIMPLE, Tls

from config.config import config
from config.logger import get_logger

logger = get_logger(__name__)


def verify_ad_user(username: str, plain_password: str) -> bool:
    """
    Returns True if username+password are valid against Active Directory
    via LDAPS (port 636, implied by use_ssl=True). A successful bind is
    the entire check -- no group membership, OU, or other AD attribute is
    inspected. Never logs the password itself.
    """
    if not username or not plain_password:
        return False
    try:
        tls_configuration = Tls(validate=ssl.CERT_NONE, version=ssl.PROTOCOL_TLSv1_2)
        server = Server(
            config.AD_SERVER,
            use_ssl=True,
            tls=tls_configuration,
            get_info=NONE,
            connect_timeout=5
        )
        user_credential = f"{username}@{config.AD_DOMAIN}"
        conn = Connection(
            server,
            user=user_credential,
            password=plain_password,
            client_strategy=SYNC,
            authentication=SIMPLE,
            receive_timeout=5
        )
        if conn.bind():
            conn.unbind()
            logger.info(f"[LDAP] Verified: {username}")
            return True
        logger.warning(f"[LDAP] Rejected: {username} — {conn.result.get('description', 'invalid credentials')}")
        return False
    except Exception as e:
        logger.error(f"[LDAP] Error verifying {username}: {e}")
        return False
