"""JSON-based database for clients and admins with password auth."""
import json
import os
import secrets
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash

DB_FILE = "clients_db.json"
ADMIN_DB_FILE = "admins_db.json"


# ── Client Database ──────────────────────────────────────────

def _load_db() -> dict:
    if os.path.exists(DB_FILE):
        with open(DB_FILE, "r") as f:
            return json.load(f)
    return {}


def _save_db(db: dict):
    with open(DB_FILE, "w") as f:
        json.dump(db, f, indent=2)


def create_client(email: str, business_name: str, plan: str, stripe_session_id: str) -> dict:
    """Create a new client after successful payment."""
    db = _load_db()

    # Generate a URL-safe client ID from business name
    client_id = business_name.lower().strip()
    client_id = "".join(c if c.isalnum() else "-" for c in client_id)
    client_id = "-".join(part for part in client_id.split("-") if part)

    # Handle duplicate IDs
    base_id = client_id
    counter = 1
    while client_id in db:
        client_id = f"{base_id}-{counter}"
        counter += 1

    # Generate access token for the client portal
    access_token = secrets.token_urlsafe(32)

    client = {
        "client_id": client_id,
        "email": email,
        "business_name": business_name,
        "plan": plan,
        "access_token": access_token,
        "password_hash": None,  # Set when client creates password
        "stripe_session_id": stripe_session_id,
        "status": "active",
        "created_at": datetime.now().isoformat(),
        "documents_uploaded": False,
    }

    db[client_id] = client
    _save_db(db)

    # Create client folders
    docs_dir = os.path.join("clients", client_id, "documents")
    os.makedirs(docs_dir, exist_ok=True)

    # Create default config
    config_path = os.path.join("clients", client_id, "config.txt")
    with open(config_path, "w") as f:
        f.write(f"name={business_name} AI Assistant\n")
        f.write(f"system_prompt=You are a helpful AI assistant for {business_name}. "
                f"Answer questions based on the provided material. If the answer "
                f"isn't in the material, say so. Be concise and helpful.\n")

    return client


def set_client_password(email: str, password: str) -> bool:
    """Set/update a client's password."""
    db = _load_db()
    for client in db.values():
        if client.get("email", "").lower() == email.lower():
            client["password_hash"] = generate_password_hash(password)
            _save_db(db)
            return True
    return False


def verify_client_password(email: str, password: str) -> dict | None:
    """Verify client credentials. Returns client dict or None."""
    db = _load_db()
    for client in db.values():
        if client.get("email", "").lower() == email.lower():
            if client.get("password_hash") and check_password_hash(client["password_hash"], password):
                return client
    return None


def get_client_by_token(token: str) -> dict | None:
    """Look up a client by their access token."""
    db = _load_db()
    for client in db.values():
        if client.get("access_token") == token:
            return client
    return None


def get_client_by_email(email: str) -> dict | None:
    """Look up a client by their email."""
    db = _load_db()
    for client in db.values():
        if client.get("email", "").lower() == email.lower():
            return client
    return None


def get_client(client_id: str) -> dict | None:
    """Get a client by ID."""
    db = _load_db()
    return db.get(client_id)


def update_client(client_id: str, updates: dict):
    """Update client fields."""
    db = _load_db()
    if client_id in db:
        db[client_id].update(updates)
        _save_db(db)


def get_all_clients() -> dict:
    """Get all clients."""
    return _load_db()


def set_reset_token(email: str) -> str | None:
    """Generate a password reset token (valid 1 hour). Works for clients and admins."""
    import secrets as _secrets
    token = _secrets.token_urlsafe(32)
    expires = (datetime.now().timestamp()) + 3600  # 1 hour

    # Try client
    db = _load_db()
    for client in db.values():
        if client.get("email", "").lower() == email.lower():
            client["reset_token"] = token
            client["reset_expires"] = expires
            _save_db(db)
            return token

    # Try admin
    admin_db = _load_admin_db()
    admin = admin_db.get(email.lower())
    if admin:
        admin["reset_token"] = token
        admin["reset_expires"] = expires
        _save_admin_db(admin_db)
        return token

    return None


def verify_reset_token(token: str) -> dict | None:
    """Verify a reset token. Returns {email, type} or None if invalid/expired."""
    now = datetime.now().timestamp()

    db = _load_db()
    for client in db.values():
        if client.get("reset_token") == token and client.get("reset_expires", 0) > now:
            return {"email": client["email"], "type": "client"}

    admin_db = _load_admin_db()
    for admin in admin_db.values():
        if admin.get("reset_token") == token and admin.get("reset_expires", 0) > now:
            return {"email": admin["email"], "type": "admin"}

    return None


def reset_password(email: str, new_password: str) -> bool:
    """Reset password for a client or admin and clear the token."""
    # Try client
    db = _load_db()
    for client in db.values():
        if client.get("email", "").lower() == email.lower():
            client["password_hash"] = generate_password_hash(new_password)
            client.pop("reset_token", None)
            client.pop("reset_expires", None)
            _save_db(db)
            return True

    # Try admin
    admin_db = _load_admin_db()
    admin = admin_db.get(email.lower())
    if admin:
        admin["password_hash"] = generate_password_hash(new_password)
        admin.pop("reset_token", None)
        admin.pop("reset_expires", None)
        _save_admin_db(admin_db)
        return True

    return False


# ── Admin Database ───────────────────────────────────────────

def _load_admin_db() -> dict:
    if os.path.exists(ADMIN_DB_FILE):
        with open(ADMIN_DB_FILE, "r") as f:
            return json.load(f)
    return {}


def _save_admin_db(db: dict):
    with open(ADMIN_DB_FILE, "w") as f:
        json.dump(db, f, indent=2)


def init_super_admin(email: str, password: str):
    """Initialize the super admin if no admins exist."""
    db = _load_admin_db()
    if not db:
        db[email.lower()] = {
            "email": email,
            "password_hash": generate_password_hash(password),
            "role": "super_admin",
            "permissions": ["manage_admins", "view_clients", "manage_clients", "view_analytics"],
            "created_at": datetime.now().isoformat(),
        }
        _save_admin_db(db)


def create_admin(email: str, password: str, permissions: list, created_by: str) -> dict | None:
    """Create a new admin. Returns admin dict or None if exists."""
    db = _load_admin_db()
    if email.lower() in db:
        return None

    admin = {
        "email": email,
        "password_hash": generate_password_hash(password),
        "role": "admin",
        "permissions": permissions,
        "created_by": created_by,
        "created_at": datetime.now().isoformat(),
    }
    db[email.lower()] = admin
    _save_admin_db(db)
    return admin


def verify_admin(email: str, password: str) -> dict | None:
    """Verify admin credentials. Returns admin dict or None."""
    db = _load_admin_db()
    admin = db.get(email.lower())
    if admin and check_password_hash(admin["password_hash"], password):
        return admin
    return None


def get_admin(email: str) -> dict | None:
    """Get admin by email."""
    db = _load_admin_db()
    return db.get(email.lower())


def get_all_admins() -> dict:
    """Get all admins."""
    return _load_admin_db()


def delete_admin(email: str) -> bool:
    """Delete an admin (cannot delete super_admin)."""
    db = _load_admin_db()
    admin = db.get(email.lower())
    if admin and admin["role"] != "super_admin":
        del db[email.lower()]
        _save_admin_db(db)
        return True
    return False
