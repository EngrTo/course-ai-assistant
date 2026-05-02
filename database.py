"""Simple JSON-based client database."""
import json
import os
import secrets
from datetime import datetime

DB_FILE = "clients_db.json"


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


def get_client_by_token(token: str) -> dict | None:
    """Look up a client by their access token."""
    db = _load_db()
    for client in db.values():
        if client.get("access_token") == token:
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
