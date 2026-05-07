"""Database for clients and admins — PostgreSQL on Render, SQLite locally."""
import os
import json
import secrets
import sqlite3
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash

DATABASE_URL = os.getenv("DATABASE_URL")
USE_PG = bool(DATABASE_URL and DATABASE_URL.startswith("postgres"))

if USE_PG:
    import psycopg2
    from psycopg2.extras import RealDictCursor

SQLITE_PATH = "app_database.db"

# Placeholder: %s for PostgreSQL, ? for SQLite
P = "%s" if USE_PG else "?"


def _get_conn():
    if USE_PG:
        return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    conn = sqlite3.connect(SQLITE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _row(row):
    """Convert a row to dict, parsing permissions JSON for SQLite."""
    if row is None:
        return None
    d = dict(row)
    # SQLite stores permissions as JSON string
    if not USE_PG and "permissions" in d and isinstance(d["permissions"], str):
        try:
            d["permissions"] = json.loads(d["permissions"])
        except (json.JSONDecodeError, TypeError):
            d["permissions"] = []
    # SQLite stores documents_uploaded as 0/1
    if "documents_uploaded" in d:
        d["documents_uploaded"] = bool(d["documents_uploaded"])
    return d


def _encode_perms(permissions: list) -> any:
    """Encode permissions for storage."""
    if USE_PG:
        return permissions
    return json.dumps(permissions)


def init_db():
    """Create tables if they don't exist."""
    conn = _get_conn()
    cur = conn.cursor()
    if USE_PG:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS clients (
                client_id TEXT PRIMARY KEY,
                email TEXT NOT NULL,
                business_name TEXT NOT NULL,
                plan TEXT NOT NULL DEFAULT 'starter',
                access_token TEXT,
                password_hash TEXT,
                stripe_session_id TEXT,
                status TEXT DEFAULT 'active',
                created_at TEXT,
                documents_uploaded BOOLEAN DEFAULT FALSE,
                reset_token TEXT,
                reset_expires FLOAT,
                page_count INTEGER DEFAULT 0,
                file_count INTEGER DEFAULT 0,
                stripe_customer_id TEXT,
                stripe_subscription_id TEXT,
                primary_color TEXT DEFAULT '#4f46e5',
                bot_name TEXT DEFAULT 'AI Assistant',
                welcome_message TEXT DEFAULT 'Hi! How can I help you today?',
                api_key TEXT,
                role TEXT DEFAULT 'client',
                permissions TEXT[] DEFAULT '{}'
            );
        """)
        # Add columns if missing (for existing databases)
        cur.execute("ALTER TABLE clients ADD COLUMN IF NOT EXISTS page_count INTEGER DEFAULT 0")
        cur.execute("ALTER TABLE clients ADD COLUMN IF NOT EXISTS file_count INTEGER DEFAULT 0")
        cur.execute("ALTER TABLE clients ADD COLUMN IF NOT EXISTS stripe_customer_id TEXT")
        cur.execute("ALTER TABLE clients ADD COLUMN IF NOT EXISTS stripe_subscription_id TEXT")
        cur.execute("ALTER TABLE clients ADD COLUMN IF NOT EXISTS primary_color TEXT DEFAULT '#4f46e5'")
        cur.execute("ALTER TABLE clients ADD COLUMN IF NOT EXISTS bot_name TEXT DEFAULT 'AI Assistant'")
        cur.execute("ALTER TABLE clients ADD COLUMN IF NOT EXISTS welcome_message TEXT DEFAULT 'Hi! How can I help you today?'")
        cur.execute("ALTER TABLE clients ADD COLUMN IF NOT EXISTS api_key TEXT")
        cur.execute("ALTER TABLE clients ADD COLUMN IF NOT EXISTS email_verified BOOLEAN DEFAULT FALSE")
        cur.execute("ALTER TABLE clients ADD COLUMN IF NOT EXISTS verification_token TEXT")
        cur.execute("ALTER TABLE clients ADD COLUMN IF NOT EXISTS trial_expires TEXT")
        cur.execute("ALTER TABLE clients ADD COLUMN IF NOT EXISTS role TEXT DEFAULT 'client'")
        cur.execute("ALTER TABLE clients ADD COLUMN IF NOT EXISTS permissions TEXT[] DEFAULT '{}'")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS chat_logs (
                id SERIAL PRIMARY KEY,
                client_id TEXT NOT NULL,
                question TEXT NOT NULL,
                answer TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT NOW()
            );
        """)
        cur.execute("ALTER TABLE chat_logs ADD COLUMN IF NOT EXISTS answer TEXT DEFAULT ''")
        cur.execute("""
            CREATE TABLE IF NOT EXISTS client_documents (
                id SERIAL PRIMARY KEY,
                client_id TEXT NOT NULL,
                filename TEXT NOT NULL,
                content BYTEA NOT NULL,
                uploaded_at TIMESTAMP DEFAULT NOW(),
                UNIQUE(client_id, filename)
            );
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS client_reviews (
                id SERIAL PRIMARY KEY,
                client_id TEXT NOT NULL,
                email TEXT NOT NULL,
                business_name TEXT NOT NULL,
                review TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT NOW()
            );
        """)
        # Migrate existing admins table data into clients (one-time migration)
        cur.execute("SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'admins')")
        admins_exists = cur.fetchone()
        if admins_exists and dict(admins_exists).get("exists", False):
            cur.execute("SELECT * FROM admins")
            admin_rows = cur.fetchall()
            for row in admin_rows:
                a = dict(row)
                # Check if this admin email already exists in clients
                cur.execute(f"SELECT 1 FROM clients WHERE LOWER(email) = LOWER({P})", (a["email"],))
                if cur.fetchone():
                    # Update existing client with admin role
                    cur.execute(f"UPDATE clients SET role = {P}, permissions = {P} WHERE LOWER(email) = LOWER({P})",
                                (a["role"], a.get("permissions", []), a["email"]))
                else:
                    # Create a new client record for admin-only users
                    cur.execute(f"""
                        INSERT INTO clients (client_id, email, business_name, plan, password_hash, status, created_at, role, permissions)
                        VALUES ({P}, {P}, {P}, {P}, {P}, {P}, {P}, {P}, {P})
                    """, (a["email"].split("@")[0], a["email"], "Admin", "none", a["password_hash"],
                          "active", a.get("created_at", ""), a["role"], a.get("permissions", [])))
            conn.commit()
            cur.execute("DROP TABLE admins")
            conn.commit()
    else:
        cur.execute("""CREATE TABLE IF NOT EXISTS clients (
            client_id TEXT PRIMARY KEY,
            email TEXT NOT NULL,
            business_name TEXT NOT NULL,
            plan TEXT NOT NULL DEFAULT 'starter',
            access_token TEXT,
            password_hash TEXT,
            stripe_session_id TEXT,
            status TEXT DEFAULT 'active',
            created_at TEXT,
            documents_uploaded INTEGER DEFAULT 0,
            reset_token TEXT,
            reset_expires REAL,
            page_count INTEGER DEFAULT 0,
            file_count INTEGER DEFAULT 0,
            stripe_customer_id TEXT,
            stripe_subscription_id TEXT,
            primary_color TEXT DEFAULT '#4f46e5',
            bot_name TEXT DEFAULT 'AI Assistant',
            welcome_message TEXT DEFAULT 'Hi! How can I help you today?',
            api_key TEXT,
            role TEXT DEFAULT 'client',
            permissions TEXT DEFAULT '[]'
        )""")
        cur.execute("""CREATE TABLE IF NOT EXISTS chat_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id TEXT NOT NULL,
            question TEXT NOT NULL,
            answer TEXT DEFAULT '',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )""")
        cur.execute("""CREATE TABLE IF NOT EXISTS client_reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id TEXT NOT NULL,
            email TEXT NOT NULL,
            business_name TEXT NOT NULL,
            review TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )""")
        # Add columns if missing (for existing SQLite databases)
        try:
            cur.execute("ALTER TABLE clients ADD COLUMN role TEXT DEFAULT 'client'")
        except Exception:
            pass
        try:
            cur.execute("ALTER TABLE clients ADD COLUMN permissions TEXT DEFAULT '[]'")
        except Exception:
            pass
    conn.commit()
    cur.close()
    conn.close()


# ── Client Database ──────────────────────────────────────────

def create_client(email: str, business_name: str, plan: str, stripe_session_id: str) -> dict:
    """Create a new client after successful payment."""
    conn = _get_conn()
    cur = conn.cursor()

    client_id = business_name.lower().strip()
    client_id = "".join(c if c.isalnum() else "-" for c in client_id)
    client_id = "-".join(part for part in client_id.split("-") if part)

    base_id = client_id
    counter = 1
    while True:
        cur.execute(f"SELECT 1 FROM clients WHERE client_id = {P}", (client_id,))
        if not cur.fetchone():
            break
        client_id = f"{base_id}-{counter}"
        counter += 1

    access_token = secrets.token_urlsafe(32)
    now = datetime.now().isoformat()

    cur.execute(f"""
        INSERT INTO clients (client_id, email, business_name, plan, access_token, password_hash, stripe_session_id, status, created_at, documents_uploaded)
        VALUES ({P}, {P}, {P}, {P}, {P}, {P}, {P}, {P}, {P}, {P})
    """, (client_id, email, business_name, plan, access_token, None, stripe_session_id, "active", now, False if USE_PG else 0))
    conn.commit()
    cur.close()
    conn.close()

    # Create client folders
    docs_dir = os.path.join("clients", client_id, "documents")
    os.makedirs(docs_dir, exist_ok=True)

    config_path = os.path.join("clients", client_id, "config.txt")
    if not os.path.exists(config_path):
        with open(config_path, "w") as f:
            f.write(f"name={business_name} AI Assistant\n")
            f.write(f"system_prompt=You are a helpful AI assistant for {business_name}. "
                    f"Answer questions based on the provided material. If the answer "
                    f"isn't in the material, say so. Be concise and helpful.\n")

    return {
        "client_id": client_id,
        "email": email,
        "business_name": business_name,
        "plan": plan,
        "access_token": access_token,
        "password_hash": None,
        "stripe_session_id": stripe_session_id,
        "status": "active",
        "created_at": now,
        "documents_uploaded": False,
    }


def set_client_password(email: str, password: str) -> bool:
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute(f"UPDATE clients SET password_hash = {P} WHERE LOWER(email) = LOWER({P})",
                (generate_password_hash(password), email))
    updated = cur.rowcount > 0
    conn.commit()
    cur.close()
    conn.close()
    return updated


def verify_client_password(email: str, password: str) -> dict | None:
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute(f"SELECT * FROM clients WHERE LOWER(email) = LOWER({P})", (email,))
    client = cur.fetchone()
    cur.close()
    conn.close()
    client = _row(client)
    if client and client.get("password_hash") and check_password_hash(client["password_hash"], password):
        return client
    return None


def get_client_by_token(token: str) -> dict | None:
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute(f"SELECT * FROM clients WHERE access_token = {P}", (token,))
    client = cur.fetchone()
    cur.close()
    conn.close()
    return _row(client)


def get_client_by_email(email: str) -> dict | None:
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute(f"SELECT * FROM clients WHERE LOWER(email) = LOWER({P})", (email,))
    client = cur.fetchone()
    cur.close()
    conn.close()
    return _row(client)


def get_client(client_id: str) -> dict | None:
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute(f"SELECT * FROM clients WHERE client_id = {P}", (client_id,))
    client = cur.fetchone()
    cur.close()
    conn.close()
    return _row(client)


def update_client(client_id: str, updates: dict):
    if not updates:
        return
    conn = _get_conn()
    cur = conn.cursor()
    set_clause = ", ".join(f"{k} = {P}" for k in updates.keys())
    values = list(updates.values()) + [client_id]
    cur.execute(f"UPDATE clients SET {set_clause} WHERE client_id = {P}", values)
    conn.commit()
    cur.close()
    conn.close()


def get_all_clients() -> dict:
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM clients ORDER BY created_at DESC")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return {_row(r)["client_id"]: _row(r) for r in rows}


# ── Reset Token Functions ────────────────────────────────────

def set_reset_token(email: str) -> str | None:
    token = secrets.token_urlsafe(32)
    expires = datetime.now().timestamp() + 3600

    conn = _get_conn()
    cur = conn.cursor()

    cur.execute(f"UPDATE clients SET reset_token = {P}, reset_expires = {P} WHERE LOWER(email) = LOWER({P})",
                (token, expires, email))
    if cur.rowcount > 0:
        conn.commit()
        cur.close()
        conn.close()
        return token

    conn.commit()
    cur.close()
    conn.close()
    return None


def verify_reset_token(token: str) -> dict | None:
    now = datetime.now().timestamp()
    conn = _get_conn()
    cur = conn.cursor()

    cur.execute(f"SELECT email FROM clients WHERE reset_token = {P} AND reset_expires > {P}", (token, now))
    row = cur.fetchone()
    if row:
        cur.close()
        conn.close()
        return {"email": dict(row)["email"], "type": "client"}

    cur.close()
    conn.close()
    return None


def reset_password(email: str, new_password: str) -> bool:
    hashed = generate_password_hash(new_password)
    conn = _get_conn()
    cur = conn.cursor()

    cur.execute(f"UPDATE clients SET password_hash = {P}, reset_token = NULL, reset_expires = NULL WHERE LOWER(email) = LOWER({P})",
                (hashed, email))
    if cur.rowcount > 0:
        conn.commit()
        cur.close()
        conn.close()
        return True

    conn.commit()
    cur.close()
    conn.close()
    return False


# ── Admin Database ───────────────────────────────────────────

def init_super_admin(email: str, password: str):
    """Ensure super admin exists in clients table."""
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute(f"SELECT 1 FROM clients WHERE LOWER(email) = LOWER({P}) AND role = 'super_admin'", (email,))
    if cur.fetchone():
        cur.close()
        conn.close()
        return
    # Check if email already exists as a client
    cur.execute(f"SELECT 1 FROM clients WHERE LOWER(email) = LOWER({P})", (email,))
    if cur.fetchone():
        # Promote to super_admin
        perms = _encode_perms(["manage_admins", "view_clients", "manage_clients", "view_analytics"])
        cur.execute(f"UPDATE clients SET role = 'super_admin', permissions = {P}, password_hash = {P} WHERE LOWER(email) = LOWER({P})",
                    (perms, generate_password_hash(password), email))
    else:
        # Create new record for super admin
        perms = _encode_perms(["manage_admins", "view_clients", "manage_clients", "view_analytics"])
        client_id = email.split("@")[0].lower()
        client_id = "".join(c if c.isalnum() else "-" for c in client_id)
        now = datetime.now().isoformat()
        cur.execute(f"""
            INSERT INTO clients (client_id, email, business_name, plan, password_hash, status, created_at, role, permissions)
            VALUES ({P}, {P}, {P}, {P}, {P}, {P}, {P}, {P}, {P})
        """, (client_id, email.lower(), "Admin", "none", generate_password_hash(password),
              "active", now, "super_admin", perms))
    conn.commit()
    cur.close()
    conn.close()


def create_admin(email: str, password: str, permissions: list, created_by: str) -> dict | None:
    """Grant admin role. If email exists, promote. If not, create new record."""
    conn = _get_conn()
    cur = conn.cursor()
    perms = _encode_perms(permissions)
    now = datetime.now().isoformat()

    # Check if already an admin
    cur.execute(f"SELECT role FROM clients WHERE LOWER(email) = LOWER({P})", (email,))
    row = cur.fetchone()
    if row:
        r = dict(row)
        if r.get("role") in ("admin", "super_admin"):
            cur.close()
            conn.close()
            return None  # Already an admin
        # Promote existing client
        cur.execute(f"UPDATE clients SET role = 'admin', permissions = {P} WHERE LOWER(email) = LOWER({P})",
                    (perms, email))
        # If a password was given and they don't have one, set it
        if password:
            cur.execute(f"SELECT password_hash FROM clients WHERE LOWER(email) = LOWER({P})", (email,))
            ph_row = cur.fetchone()
            if not ph_row or not dict(ph_row).get("password_hash"):
                cur.execute(f"UPDATE clients SET password_hash = {P} WHERE LOWER(email) = LOWER({P})",
                            (generate_password_hash(password), email))
    else:
        # Create a new record
        client_id = email.split("@")[0].lower()
        client_id = "".join(c if c.isalnum() else "-" for c in client_id)
        # Ensure unique client_id
        base_id = client_id
        counter = 1
        while True:
            cur.execute(f"SELECT 1 FROM clients WHERE client_id = {P}", (client_id,))
            if not cur.fetchone():
                break
            client_id = f"{base_id}-{counter}"
            counter += 1
        cur.execute(f"""
            INSERT INTO clients (client_id, email, business_name, plan, password_hash, status, created_at, role, permissions)
            VALUES ({P}, {P}, {P}, {P}, {P}, {P}, {P}, {P}, {P})
        """, (client_id, email.lower(), "Admin", "none", generate_password_hash(password),
              "active", now, "admin", perms))

    conn.commit()
    cur.close()
    conn.close()
    return {"email": email.lower(), "role": "admin", "permissions": permissions, "created_by": created_by, "created_at": now}


def verify_admin(email: str, password: str) -> dict | None:
    """Verify admin credentials (checks clients table where role is admin/super_admin)."""
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute(f"SELECT * FROM clients WHERE LOWER(email) = LOWER({P}) AND role IN ('admin', 'super_admin')", (email,))
    admin = cur.fetchone()
    cur.close()
    conn.close()
    admin = _row(admin)
    if admin and admin.get("password_hash") and check_password_hash(admin["password_hash"], password):
        return admin
    return None


def get_admin(email: str) -> dict | None:
    """Get admin record if user has admin/super_admin role."""
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute(f"SELECT * FROM clients WHERE LOWER(email) = LOWER({P}) AND role IN ('admin', 'super_admin')", (email,))
    admin = cur.fetchone()
    cur.close()
    conn.close()
    return _row(admin)


def get_all_admins() -> dict:
    """Get all users with admin or super_admin role."""
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM clients WHERE role IN ('admin', 'super_admin') ORDER BY created_at")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return {_row(r)["email"]: _row(r) for r in rows}


def delete_admin(email: str) -> bool:
    """Remove admin role (demote back to client), never delete the record."""
    conn = _get_conn()
    cur = conn.cursor()
    # Never demote super_admin
    cur.execute(f"SELECT role FROM clients WHERE LOWER(email) = LOWER({P})", (email,))
    row = cur.fetchone()
    if not row or dict(row).get("role") == "super_admin":
        cur.close()
        conn.close()
        return False
    perms = _encode_perms([])
    cur.execute(f"UPDATE clients SET role = 'client', permissions = {P} WHERE LOWER(email) = LOWER({P})", (perms, email))
    conn.commit()
    cur.close()
    conn.close()
    return True


# ── Chat Logs (Analytics) ────────────────────────────────────

def log_chat_query(client_id: str, question: str, answer: str = ""):
    """Log a chat question and answer for analytics."""
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute(
        f"INSERT INTO chat_logs (client_id, question, answer, created_at) VALUES ({P}, {P}, {P}, {P})",
        (client_id, question[:500], answer[:2000], datetime.utcnow().isoformat())
    )
    conn.commit()
    cur.close()
    conn.close()


def get_chat_history(client_id: str, limit: int = 50, offset: int = 0) -> list:
    """Get recent conversation history for a client."""
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute(
        f"SELECT id, question, answer, created_at FROM chat_logs WHERE client_id = {P} ORDER BY created_at DESC LIMIT {P} OFFSET {P}",
        (client_id, limit, offset)
    )
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [dict(r) for r in rows]


def get_chat_stats(client_id: str) -> dict:
    """Get query counts for a client: today, this month, total."""
    conn = _get_conn()
    cur = conn.cursor()
    now = datetime.utcnow()
    today_str = now.strftime("%Y-%m-%d")
    month_str = now.strftime("%Y-%m")

    # Total queries
    cur.execute(f"SELECT COUNT(*) as cnt FROM chat_logs WHERE client_id = {P}", (client_id,))
    row = cur.fetchone()
    total = dict(row)["cnt"] if row else 0

    # Today
    if USE_PG:
        cur.execute(f"SELECT COUNT(*) as cnt FROM chat_logs WHERE client_id = {P} AND created_at::text LIKE {P}", (client_id, f"{today_str}%"))
    else:
        cur.execute(f"SELECT COUNT(*) as cnt FROM chat_logs WHERE client_id = {P} AND created_at LIKE {P}", (client_id, f"{today_str}%"))
    row = cur.fetchone()
    today = dict(row)["cnt"] if row else 0

    # This month
    if USE_PG:
        cur.execute(f"SELECT COUNT(*) as cnt FROM chat_logs WHERE client_id = {P} AND created_at::text LIKE {P}", (client_id, f"{month_str}%"))
    else:
        cur.execute(f"SELECT COUNT(*) as cnt FROM chat_logs WHERE client_id = {P} AND created_at LIKE {P}", (client_id, f"{month_str}%"))
    row = cur.fetchone()
    this_month = dict(row)["cnt"] if row else 0

    cur.close()
    conn.close()
    return {"total": total, "today": today, "this_month": this_month}


def get_chat_stats_filtered(client_id: str, start_date: str, end_date: str) -> dict:
    """Get query count for a client between start_date and end_date (YYYY-MM-DD)."""
    conn = _get_conn()
    cur = conn.cursor()

    if USE_PG:
        cur.execute(
            f"SELECT COUNT(*) as cnt FROM chat_logs WHERE client_id = {P} AND created_at >= {P}::timestamp AND created_at < ({P}::date + 1)::timestamp",
            (client_id, start_date, end_date),
        )
    else:
        cur.execute(
            f"SELECT COUNT(*) as cnt FROM chat_logs WHERE client_id = {P} AND created_at >= {P} AND created_at < date({P}, '+1 day')",
            (client_id, start_date, end_date),
        )
    row = cur.fetchone()
    count = dict(row)["cnt"] if row else 0

    cur.close()
    conn.close()
    return {"count": count, "start_date": start_date, "end_date": end_date}


def get_daily_query_count(client_id: str) -> int:
    """Get today's query count for a client."""
    conn = _get_conn()
    cur = conn.cursor()
    today_str = datetime.utcnow().strftime("%Y-%m-%d")
    if USE_PG:
        cur.execute(f"SELECT COUNT(*) as cnt FROM chat_logs WHERE client_id = {P} AND created_at::text LIKE {P}", (client_id, f"{today_str}%"))
    else:
        cur.execute(f"SELECT COUNT(*) as cnt FROM chat_logs WHERE client_id = {P} AND created_at LIKE {P}", (client_id, f"{today_str}%"))
    row = cur.fetchone()
    count = dict(row)["cnt"] if row else 0
    cur.close()
    conn.close()
    return count


# ── Document Storage (persists across deploys) ───────────────

def save_document_to_db(client_id: str, filename: str, content: bytes):
    """Save or replace a document in the database."""
    if not USE_PG:
        return  # Only needed for Render/PostgreSQL
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute(f"""
        INSERT INTO client_documents (client_id, filename, content)
        VALUES ({P}, {P}, {P})
        ON CONFLICT (client_id, filename) DO UPDATE SET content = EXCLUDED.content, uploaded_at = NOW()
    """, (client_id, filename, content))
    conn.commit()
    cur.close()
    conn.close()


def delete_document_from_db(client_id: str, filename: str):
    """Delete a document from the database."""
    if not USE_PG:
        return
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute(f"DELETE FROM client_documents WHERE client_id = {P} AND filename = {P}", (client_id, filename))
    conn.commit()
    cur.close()
    conn.close()


def restore_documents_from_db():
    """Restore all client documents from DB to filesystem. Called on startup."""
    if not USE_PG:
        return
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("SELECT client_id, filename, content FROM client_documents")
    rows = cur.fetchall()
    cur.close()
    conn.close()

    restored = 0
    for row in rows:
        r = dict(row)
        docs_dir = os.path.join("clients", r["client_id"], "documents")
        os.makedirs(docs_dir, exist_ok=True)
        filepath = os.path.join(docs_dir, r["filename"])
        if not os.path.exists(filepath):
            with open(filepath, "wb") as f:
                f.write(bytes(r["content"]))
            restored += 1

    if restored:
        print(f"Restored {restored} document(s) from database.")


# ── Reviews ──────────────────────────────────────────────────

def submit_review(client_id: str, email: str, business_name: str, review: str):
    """Save a client review."""
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute(f"""
        INSERT INTO client_reviews (client_id, email, business_name, review)
        VALUES ({P}, {P}, {P}, {P})
    """, (client_id, email, business_name, review))
    conn.commit()
    cur.close()
    conn.close()


def get_all_reviews():
    """Get all reviews, newest first."""
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM client_reviews ORDER BY created_at DESC")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [dict(r) for r in rows]


def get_client_review(client_id: str):
    """Get review by a specific client (latest one)."""
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute(f"SELECT * FROM client_reviews WHERE client_id = {P} ORDER BY created_at DESC LIMIT 1", (client_id,))
    row = cur.fetchone()
    cur.close()
    conn.close()
    return dict(row) if row else None
