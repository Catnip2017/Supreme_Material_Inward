"""
database/user_operations.py — User management operations.

v4 changes:
- Added: email, email_notifications_enabled, step_roles columns
- All add/update/get functions now handle these new fields

v10 changes (role-based page/tab access control overhaul):
- Added: admin_edit column (SuperAdmin-only edit vs. view-only toggle)
- role values are now 'User' / 'SuperAdmin' (renamed from 'Admin' — see
  schema_migration_v10.sql for the data migration)
- step_roles gains a new possible value: 'compliance' (Documents +
  Extracted Data + GST Approval bucket), alongside the existing
  gate_in / migo_103 / migo_105 / miro. The 'all' sentinel is retired
  for regular users now that SuperAdmin is the real "sees everything"
  tier — all/get/add/update functions below no longer default to 'all'.

v15 changes (LDAP login — see schema_migration_v15.sql):
- Added: auth_type column ('local' default, or 'ldap'). 'local' accounts
  are unchanged (bcrypt-hashed password, kept working for testing).
  'ldap' accounts store no local password at all (column is nullable now)
  -- verify_user() checks the entered password against Active Directory
  instead (services/ldap_auth.py), on every login, every time. A
  SuperAdmin creates 'ldap' rows with just a username (see add_user()) --
  role/step_roles/email are still set/managed here exactly as before,
  same as any other user; only the credential check differs.
"""

from typing import Optional
import bcrypt
import psycopg2.extras

from database.connection import get_connection
from config.logger import get_logger
from services.ldap_auth import verify_ad_user

logger = get_logger(__name__)


def hash_password(password: str) -> str:
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password.encode("utf-8"), salt).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except Exception as e:
        logger.error(f"Password verification error: {e}")
        return False


def verify_user(username: str, password: str) -> Optional[dict]:
    """
    v15: branches on auth_type. 'local' (or missing/legacy NULL, treated
    as 'local') checks the stored bcrypt hash exactly as before. 'ldap'
    skips the stored password entirely (there isn't one) and checks the
    entered password against Active Directory instead, every single
    login -- nothing about the AD result is cached or stored back into
    this table. Either way, the row must already exist here first -- this
    function never creates or looks anything up outside this table (no
    supreme_ai, no AD group membership check, just "does AD accept this
    password for this username").
    """
    try:
        with get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT * FROM users WHERE username = %s",
                    (username,)
                )
                user = cur.fetchone()
                if not user:
                    logger.warning(f"Login attempt for unknown user: {username}")
                    return None

                user = dict(user)
                auth_type = (user.get("auth_type") or "local").lower()

                if auth_type == "ldap":
                    verified = verify_ad_user(username, password)
                else:
                    verified = verify_password(password, user["password"] or "")

                if verified:
                    logger.info(f"User verified: {username} (auth_type={auth_type})")
                    return {
                        "username":   user["username"],
                        "role":       user["role"],
                        "name":       user["name"],
                        "email":      user.get("email"),
                        "step_roles": user.get("step_roles") or "",
                        "admin_edit": user.get("admin_edit", True),
                        "auth_type":  auth_type,
                    }

                logger.warning(f"Invalid password for user: {username} (auth_type={auth_type})")
                return None
    except Exception as e:
        logger.error(f"Error verifying user {username}: {e}")
        return None


def get_all_users() -> list:
    try:
        with get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT id, username, role, name, auth_type,
                           email, email_notifications_enabled, step_roles,
                           admin_edit, created_at, updated_at
                    FROM users
                    ORDER BY created_at DESC
                    """
                )
                return [dict(r) for r in cur.fetchall()]
    except Exception as e:
        logger.error(f"Error fetching users: {e}")
        return []


def get_users_for_step(step_role: str) -> list:
    """
    Get users who:
      - have email_notifications_enabled = TRUE
      - have a non-empty email
      - have step_roles matching this step OR 'all'

    Used by mail_service to send per-step notifications.
    """
    try:
        with get_connection() as conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT username, name, email, step_roles
                    FROM users
                    WHERE email_notifications_enabled = TRUE
                      AND email IS NOT NULL
                      AND email <> ''
                      AND (
                          step_roles = 'all'
                          OR step_roles ILIKE %s
                      )
                    """,
                    (f"%{step_role}%",)
                )
                return [dict(r) for r in cur.fetchall()]
    except Exception as e:
        logger.error(f"Error fetching users for step {step_role}: {e}")
        return []


def add_user(
    username: str,
    password: str,
    role: str,
    name: str,
    email: str = "",
    email_notifications_enabled: bool = False,
    step_roles: str = "",
    admin_edit: bool = True,
    auth_type: str = "local"
) -> bool:
    """
    step_roles: comma-separated subset of compliance/gate_in/migo_103/
    migo_105/miro. Ignored in practice for role='SuperAdmin' (SuperAdmin
    sees everything regardless). admin_edit only matters for SuperAdmin
    accounts — True = can edit/act everywhere + manage users, False =
    view-only everywhere.

    auth_type='ldap' (v15): password is ignored entirely (stored as NULL)
    -- a SuperAdmin creating an LDAP user only ever supplies username,
    name, role, step_roles, email. The real credential is verified
    against Active Directory at login time (verify_user() above), not
    stored here at all. name defaults to the username itself if not
    given for an LDAP row, since a SuperAdmin creating one up front may
    not know the person's display name yet -- can be edited later.
    """
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM users WHERE username = %s", (username,))
                if cur.fetchone():
                    logger.warning(f"User already exists: {username}")
                    return False

                is_ldap = (auth_type or "local").lower() == "ldap"
                password_hash = None if is_ldap else hash_password(password)

                cur.execute(
                    """
                    INSERT INTO users (
                        username, password, role, name, auth_type,
                        email, email_notifications_enabled, step_roles, admin_edit
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        username,
                        password_hash,
                        role,
                        name or username,
                        "ldap" if is_ldap else "local",
                        email or None,
                        bool(email_notifications_enabled),
                        step_roles or "",
                        bool(admin_edit),
                    )
                )
                logger.info(f"User created: {username} (auth_type={'ldap' if is_ldap else 'local'})")
                return True
    except Exception as e:
        logger.error(f"Error adding user {username}: {e}")
        return False


def update_user(
    username: str,
    password: Optional[str] = None,
    role: Optional[str] = None,
    email: Optional[str] = None,
    email_notifications_enabled: Optional[bool] = None,
    step_roles: Optional[str] = None,
    admin_edit: Optional[bool] = None,
    auth_type: Optional[str] = None,
    name: Optional[str] = None
) -> bool:
    """
    Update a user. Only fields passed (not None) will be updated.
    For password, pass plain text — it will be hashed.

    step_roles is a full replace of whatever is submitted, not a merge —
    but the edit form in User Management pre-fills every checkbox with
    the user's current roles already checked (see templates/
    user_management.html), so an admin adding a new role naturally keeps
    the existing ones checked too rather than having to know/re-type them.

    auth_type (v15): switching a row to 'ldap' clears any stored password
    hash (set NULL) regardless of whether `password` was also passed --
    an LDAP account has no local password to keep. Switching to 'local'
    does NOT set a password automatically; pass one explicitly too if
    converting an LDAP row back to local.
    """
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM users WHERE username = %s", (username,))
                if not cur.fetchone():
                    logger.warning(f"User not found for update: {username}")
                    return False

                set_parts = []
                values = []

                switching_to_ldap = auth_type is not None and auth_type.lower() == "ldap"

                if switching_to_ldap:
                    set_parts.append("password = %s")
                    values.append(None)
                elif password is not None:
                    set_parts.append("password = %s")
                    values.append(hash_password(password))
                if auth_type is not None:
                    set_parts.append("auth_type = %s")
                    values.append(auth_type.lower())
                if name is not None and name.strip():
                    set_parts.append("name = %s")
                    values.append(name.strip())
                if role is not None:
                    set_parts.append("role = %s")
                    values.append(role)
                if email is not None:
                    set_parts.append("email = %s")
                    values.append(email or None)
                if email_notifications_enabled is not None:
                    set_parts.append("email_notifications_enabled = %s")
                    values.append(bool(email_notifications_enabled))
                if step_roles is not None:
                    set_parts.append("step_roles = %s")
                    values.append(step_roles or "")
                if admin_edit is not None:
                    set_parts.append("admin_edit = %s")
                    values.append(bool(admin_edit))

                if not set_parts:
                    return True  # nothing to update

                set_parts.append("updated_at = CURRENT_TIMESTAMP")
                values.append(username)

                sql = f"UPDATE users SET {', '.join(set_parts)} WHERE username = %s"
                cur.execute(sql, values)
                logger.info(f"User updated: {username}")
                return True
    except Exception as e:
        logger.error(f"Error updating user {username}: {e}")
        return False


def delete_user(username: str) -> bool:
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM users WHERE username = %s", (username,))
                logger.info(f"User deleted: {username}")
                return True
    except Exception as e:
        logger.error(f"Error deleting user {username}: {e}")
        return False