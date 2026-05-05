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
                api_key TEXT
            );
            CREATE TABLE IF NOT EXISTS admins (
                email TEXT PRIMARY KEY,
                password_hash TEXT NOT NULL,
                role TEXT DEFAULT 'admin',
                permissions TEXT[],
                created_by TEXT,
                created_at TEXT,
                reset_token TEXT,
                reset_expires FLOAT
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
            api_key TEXT
        )""")
        cur.execute("""CREATE TABLE IF NOT EXISTS admins (
            email TEXT PRIMARY KEY,
            password_hash TEXT NOT NULL,
            role TEXT DEFAULT 'admin',
            permissions TEXT,
            created_by TEXT,
            created_at TEXT,
            reset_token TEXT,
            reset_expires REAL
        )""")
        cur.execute("""CREATE TABLE IF NOT EXISTS chat_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id TEXT NOT NULL,
            question TEXT NOT NULL,
            answer TEXT DEFAULT '',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )""")
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

    cur.execute(f"UPDATE admins SET reset_token = {P}, reset_expires = {P} WHERE LOWER(email) = LOWER({P})",
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

    cur.execute(f"SELECT email FROM admins WHERE reset_token = {P} AND reset_expires > {P}", (token, now))
    row = cur.fetchone()
    if row:
        cur.close()
        conn.close()
        return {"email": dict(row)["email"], "type": "admin"}

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

    cur.execute(f"UPDATE admins SET password_hash = {P}, reset_token = NULL, reset_expires = NULL WHERE LOWER(email) = LOWER({P})",
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
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) as cnt FROM admins")
    row = cur.fetchone()
    count = dict(row)["cnt"]
    if count == 0:
        perms = _encode_perms(["manage_admins", "view_clients", "manage_clients", "view_analytics"])
        cur.execute(f"""
            INSERT INTO admins (email, password_hash, role, permissions, created_at)
            VALUES (LOWER({P}), {P}, 'super_admin', {P}, {P})
        """, (email, generate_password_hash(password), perms, datetime.now().isoformat()))
        conn.commit()
    cur.close()
    conn.close()


def create_admin(email: str, password: str, permissions: list, created_by: str) -> dict | None:
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute(f"SELECT 1 FROM admins WHERE email = LOWER({P})", (email,))
    if cur.fetchone():
        cur.close()
        conn.close()
        return None

    perms = _encode_perms(permissions)
    now = datetime.now().isoformat()
    cur.execute(f"""
        INSERT INTO admins (email, password_hash, role, permissions, created_by, created_at)
        VALUES (LOWER({P}), {P}, 'admin', {P}, {P}, {P})
    """, (email, generate_password_hash(password), perms, created_by, now))
    conn.commit()
    cur.close()
    conn.close()
    return {"email": email.lower(), "role": "admin", "permissions": permissions, "created_by": created_by, "created_at": now}


def verify_admin(email: str, password: str) -> dict | None:
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute(f"SELECT * FROM admins WHERE email = LOWER({P})", (email,))
    admin = cur.fetchone()
    cur.close()
    conn.close()
    admin = _row(admin)
    if admin and check_password_hash(admin["password_hash"], password):
        return admin
    return None


def get_admin(email: str) -> dict | None:
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute(f"SELECT * FROM admins WHERE email = LOWER({P})", (email,))
    admin = cur.fetchone()
    cur.close()
    conn.close()
    return _row(admin)


def get_all_admins() -> dict:
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM admins ORDER BY created_at")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return {_row(r)["email"]: _row(r) for r in rows}


def delete_admin(email: str) -> bool:
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute(f"DELETE FROM admins WHERE email = LOWER({P}) AND role != 'super_admin'", (email,))
    deleted = cur.rowcount > 0
    conn.commit()
    cur.close()
    conn.close()
    return deleted


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
