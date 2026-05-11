"""Flask web app — Multi-tenant AI Assistant powered by Groq + Stripe."""
import os
import secrets
import stripe
from functools import wraps
from datetime import timedelta
from dotenv import load_dotenv
from flask import Flask, render_template, request, jsonify, redirect, make_response, session as flask_session
from flask_cors import CORS
from werkzeug.utils import secure_filename
from agent import load_all_indexes, ask
from ingest import ingest_all, ingest_client, get_client_dirs, INDEXES_DIR
from database import (
    init_db, create_client, get_client_by_token, get_client, update_client,
    get_client_by_email, set_client_password, verify_client_password,
    init_super_admin, verify_admin, get_admin, get_all_admins, get_all_clients,
    create_admin, delete_admin, set_reset_token, verify_reset_token, reset_password,
    log_chat_query, get_chat_stats, get_chat_stats_filtered, get_chat_history, get_daily_query_count,
    save_document_to_db, delete_document_from_db, restore_documents_from_db,
)

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", secrets.token_hex(32))
app.permanent_session_lifetime = timedelta(hours=24)
CORS(app)


@app.before_request
def make_session_permanent():
    flask_session.permanent = True


@app.errorhandler(500)
def handle_500(e):
    if request.content_type and "json" in request.content_type:
        return jsonify({"error": "Internal server error"}), 500
    return "Internal Server Error", 500


# Initialize database tables and super admin
init_db()
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "toskicve@gmail.com")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123456")
init_super_admin(ADMIN_EMAIL, ADMIN_PASSWORD)

# Stripe config
stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
STRIPE_PUBLISHABLE_KEY = os.getenv("STRIPE_PUBLISHABLE_KEY")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET")

# Gmail SMTP config
GMAIL_EMAIL = os.getenv("GMAIL_EMAIL")
GMAIL_APP_PASSWORD = os.getenv("GMAIL_APP_PASSWORD")

PLAN_PRICES = {
    "starter": 9700,         # $97/month
    "professional": 19700,   # $197/month
    "enterprise": 49700,     # $497/month
}

PLAN_LIMITS = {
    "trial": {"max_files": 1, "max_pages": 10, "max_queries_per_day": 10},
    "starter": {"max_files": 5, "max_pages": 50, "max_queries_per_day": 9999},
    "professional": {"max_files": 20, "max_pages": 200, "max_queries_per_day": 9999},
    "enterprise": {"max_files": 999, "max_pages": 9999, "max_queries_per_day": 9999},
}

# Stripe Price IDs — set via env vars or auto-created
STRIPE_PRICE_IDS = {
    "starter": os.getenv("STRIPE_PRICE_STARTER"),
    "professional": os.getenv("STRIPE_PRICE_PROFESSIONAL"),
    "enterprise": os.getenv("STRIPE_PRICE_ENTERPRISE"),
}


def get_or_create_stripe_price(plan: str) -> str:
    """Get existing Stripe Price ID or create one."""
    if STRIPE_PRICE_IDS.get(plan):
        return STRIPE_PRICE_IDS[plan]

    # Create product + price in Stripe
    product = stripe.Product.create(name=f"AI Assistant — {plan.title()} Plan")
    price = stripe.Price.create(
        product=product.id,
        unit_amount=PLAN_PRICES[plan],
        currency="usd",
        recurring={"interval": "month"},
    )
    STRIPE_PRICE_IDS[plan] = price.id
    print(f"Created Stripe Price for {plan}: {price.id}")
    return price.id

# Restore documents from database (survives Render deploys)
restore_documents_from_db()

# Build indexes at startup if not already present
if not os.path.exists(INDEXES_DIR) or not os.listdir(INDEXES_DIR):
    print("Building indexes for all clients...")
    ingest_all()

# Load all client indexes at startup (rebuild from documents if needed)
print("Loading client indexes...")
indexes = load_all_indexes()
if not indexes:
    # No indexes found — try to rebuild from any existing client documents
    client_dirs = get_client_dirs()
    if client_dirs:
        print(f"No indexes found. Rebuilding from {len(client_dirs)} client(s)...")
        for cid in client_dirs:
            try:
                ingest_client(cid)
                print(f"  Rebuilt index for: {cid}")
            except Exception as e:
                print(f"  Skip {cid}: {e}")
        indexes = load_all_indexes()
print(f"Ready! {len(indexes)} client(s) loaded.\n")


# ── Auth Helper ──────────────────────────────────────────────

def login_required(f):
    """Decorator: require any authenticated user."""
    @wraps(f)
    def decorated(*args, **kwargs):
        if not flask_session.get("user_email"):
            return redirect("/login")
        return f(*args, **kwargs)
    return decorated


def get_user_context():
    """Get the current user's roles and data."""
    email = flask_session.get("user_email")
    if not email:
        return None

    ctx = {"email": email, "is_admin": False, "is_client": False, "admin": None, "client": None}

    client = get_client_by_email(email)
    if client:
        role = client.get("role", "client")
        if role in ("admin", "super_admin"):
            ctx["is_admin"] = True
            ctx["admin"] = client  # admin data is on the same record
        if client.get("plan") and client["plan"] != "none":
            ctx["is_client"] = True
            ctx["client"] = client
    else:
        # Check via get_admin for edge cases
        admin = get_admin(email)
        if admin:
            ctx["is_admin"] = True
            ctx["admin"] = admin

    return ctx


# ── Landing Page ──────────────────────────────────────────────

@app.route("/")
def landing():
    """Public landing page with pricing."""
    user = get_user_context()
    subscribed_plan = None
    if user and user["is_client"]:
        subscribed_plan = user["client"].get("plan")
    return render_template("landing.html", subscribed_plan=subscribed_plan, logged_in=user is not None)


# ── Unified Login/Logout ─────────────────────────────────────

@app.route("/login", methods=["GET", "POST"])
def login():
    """Single login for both admins and clients."""
    if request.method == "GET":
        if flask_session.get("user_email"):
            return redirect("/dashboard")
        return render_template("login.html")

    data = request.get_json()
    email = data.get("email", "").strip()
    password = data.get("password", "")

    if not email or not password:
        return jsonify({"error": "Email and password required."}), 400

    # Single table login — check password in clients table
    client = verify_client_password(email, password)
    if client:
        flask_session["user_email"] = email
        return jsonify({"success": True})

    # Fallback: check if admin-only user (for backward compat during migration)
    admin = verify_admin(email, password)
    if admin:
        flask_session["user_email"] = email
        return jsonify({"success": True})
        return jsonify({"success": True})

    return jsonify({"error": "Invalid email or password."}), 401


@app.route("/logout")
def logout():
    flask_session.clear()
    return redirect("/")


@app.route("/set-password", methods=["GET", "POST"])
def set_password():
    """Set password after first payment (accessed via token)."""
    if request.method == "GET":
        token = request.args.get("token")
        if not token:
            return redirect("/login")
        client = get_client_by_token(token)
        if not client:
            return redirect("/login")
        if client.get("password_hash"):
            return redirect("/login")
        return render_template("set_password.html", email=client["email"], token=token)

    data = request.get_json()
    email = data.get("email", "").strip()
    password = data.get("password", "")
    token = data.get("token", "")

    client = get_client_by_token(token)
    if not client or client.get("email", "").lower() != email.lower():
        return jsonify({"error": "Invalid request."}), 403

    if len(password) < 8:
        return jsonify({"error": "Password must be at least 8 characters."}), 400

    set_client_password(email, password)
    flask_session["user_email"] = email
    return jsonify({"success": True})


# ── Free Signup + Email Verification ─────────────────────────

@app.route("/signup", methods=["GET", "POST"])
def signup():
    """Free signup — creates trial account, sends verification email."""
    if request.method == "GET":
        return render_template("signup.html")

    data = request.get_json()
    email = data.get("email", "").strip().lower()
    business_name = data.get("business_name", "").strip()
    password = data.get("password", "")

    if not email or not business_name or not password:
        return jsonify({"error": "All fields are required."}), 400
    import re
    if not re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', email):
        return jsonify({"error": "Please enter a valid email address."}), 400
    if len(password) < 8:
        return jsonify({"error": "Password must be at least 8 characters."}), 400

    existing = get_client_by_email(email)
    if existing:
        return jsonify({"error": "This email already has an account. Please log in."}), 400

    # Create trial client
    from datetime import datetime, timedelta
    try:
        client = create_client(email, business_name, "trial", "")
        trial_expires = (datetime.utcnow() + timedelta(days=7)).isoformat()
        verification_token = secrets.token_urlsafe(32)

        set_client_password(email, password)
        update_client(client["client_id"], {
            "trial_expires": trial_expires,
            "verification_token": verification_token,
            "email_verified": False,
            "status": "trial",
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        print(f"Signup error: {type(e).__name__}: {e}")
        return jsonify({"error": "Account creation failed. Please try again."}), 500

    # Send verification email (non-blocking — don't fail signup if email fails)
    try:
        verify_url = request.url_root.replace("http://", "https://") + f"verify-email?token={verification_token}"
        send_verification_email(email, business_name, verify_url)
    except Exception as e:
        print(f"Verification email error: {type(e).__name__}: {e}")

    return jsonify({"success": True, "message": "Account created! Check your email to verify."})


@app.route("/verify-email")
def verify_email():
    """Verify email via token link."""
    token = request.args.get("token")
    if not token:
        return "Invalid link.", 400

    # Find client by verification token
    from database import _get_conn, P, USE_PG
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute(f"SELECT * FROM clients WHERE verification_token = {P}", (token,))
    row = cur.fetchone()
    cur.close()
    conn.close()

    if not row:
        return "Invalid or expired verification link.", 400

    client = dict(row)
    update_client(client["client_id"], {"email_verified": True, "verification_token": None})

    # Auto-login and redirect to dashboard
    flask_session["user_email"] = client["email"]
    return redirect("/dashboard")


def send_verification_email(to_email: str, business_name: str, verify_url: str):
    """Send email verification link."""
    if not GMAIL_EMAIL or not GMAIL_APP_PASSWORD:
        print(f"[DEV] Verify link for {to_email}: {verify_url}")
        return True
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"Verify your email — {business_name} AI Assistant"
    msg["From"] = GMAIL_EMAIL
    msg["To"] = to_email

    html = f"""\
    <div style="font-family:sans-serif;max-width:500px;margin:0 auto;padding:32px;">
        <h2 style="color:#4f46e5;">Verify Your Email 📧</h2>
        <p>Welcome, {business_name}! Click below to activate your 7-day free trial:</p>
        <a href="{verify_url}" style="display:inline-block;padding:14px 28px;background:#4f46e5;color:white;border-radius:8px;text-decoration:none;font-weight:600;margin:16px 0;">Verify Email & Start Trial →</a>
        <p style="color:#666;font-size:13px;">Your trial includes: 10 queries/day, 1 file upload, 10 pages. Upgrade anytime for full access.</p>
        <p style="color:#999;font-size:12px;">If you didn't create this account, ignore this email.</p>
    </div>
    """
    msg.attach(MIMEText(html, "html"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=10) as server:
            server.login(GMAIL_EMAIL, GMAIL_APP_PASSWORD)
            server.sendmail(GMAIL_EMAIL, to_email, msg.as_string())
        return True
    except Exception as e:
        print(f"Verification email error: {e}")
        return False


# ── Forgot / Reset Password ─────────────────────────────────

def send_reset_email(to_email: str, reset_url: str):
    """Send password reset email via Gmail SMTP."""
    if not GMAIL_EMAIL or not GMAIL_APP_PASSWORD:
        print(f"[DEV] Reset link for {to_email}: {reset_url}")
        return True
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

    msg = MIMEMultipart("alternative")
    msg["Subject"] = "Reset Your Password — AI Assistant Platform"
    msg["From"] = GMAIL_EMAIL
    msg["To"] = to_email

    html = f"""\
    <div style="font-family:sans-serif;max-width:500px;margin:0 auto;padding:32px;">
        <h2>Password Reset</h2>
        <p>You requested a password reset. Click the button below to set a new password:</p>
        <a href="{reset_url}" style="display:inline-block;padding:12px 24px;background:#4f46e5;color:white;border-radius:8px;text-decoration:none;font-weight:600;margin:16px 0;">Reset Password</a>
        <p style="color:#666;font-size:13px;">This link expires in 1 hour. If you didn't request this, ignore this email.</p>
    </div>
    """
    msg.attach(MIMEText(html, "html"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=10) as server:
            server.login(GMAIL_EMAIL, GMAIL_APP_PASSWORD)
            server.sendmail(GMAIL_EMAIL, to_email, msg.as_string())
        return True
    except Exception as e:
        print(f"Email error: {e}")
        return False


def send_welcome_email(to_email: str, business_name: str, login_url: str):
    """Send welcome/onboarding email to new clients."""
    if not GMAIL_EMAIL or not GMAIL_APP_PASSWORD:
        print(f"[DEV] Welcome email for {to_email}: {login_url}")
        return True
    import smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"Welcome to AI Assistant Platform — {business_name} is live! 🚀"
    msg["From"] = GMAIL_EMAIL
    msg["To"] = to_email

    html = f"""\
    <div style="font-family:sans-serif;max-width:560px;margin:0 auto;padding:32px;background:#fff;">
        <h2 style="color:#4f46e5;">Welcome aboard, {business_name}! 🎉</h2>
        <p>Your AI chatbot is almost ready. Here's how to get started:</p>
        <div style="background:#f8fafc;border-radius:10px;padding:20px;margin:16px 0;">
            <ol style="margin:0;padding-left:20px;line-height:2;">
                <li><strong>Set your password</strong> using the link you just received</li>
                <li><strong>Upload documents</strong> (PDFs or text files) for your AI to learn from</li>
                <li><strong>Copy your widget code</strong> and paste it on your website</li>
                <li><strong>Customize</strong> your chatbot's name, color, and welcome message</li>
            </ol>
        </div>
        <a href="{login_url}" style="display:inline-block;padding:14px 28px;background:#4f46e5;color:white;border-radius:8px;text-decoration:none;font-weight:600;margin:16px 0;">Go to Dashboard →</a>
        <hr style="border:none;border-top:1px solid #eee;margin:24px 0;">
        <p style="font-size:13px;color:#666;"><strong>Quick tips:</strong></p>
        <ul style="font-size:13px;color:#666;line-height:1.8;">
            <li>The more documents you upload, the smarter your chatbot becomes</li>
            <li>Check your <strong>Usage Analytics</strong> to see what customers are asking</li>
            <li>Use the <strong>Conversation History</strong> to spot content gaps</li>
        </ul>
        <p style="font-size:12px;color:#999;margin-top:24px;">Need help? Reply to this email and we'll get back to you.</p>
    </div>
    """
    msg.attach(MIMEText(html, "html"))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=10) as server:
            server.login(GMAIL_EMAIL, GMAIL_APP_PASSWORD)
            server.sendmail(GMAIL_EMAIL, to_email, msg.as_string())
        print(f"Welcome email sent to {to_email}")
        return True
    except Exception as e:
        print(f"Welcome email error: {e}")
        return False


@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    """Request a password reset link."""
    if request.method == "GET":
        return render_template("forgot_password.html")

    data = request.get_json()
    email = data.get("email", "").strip()
    if not email:
        return jsonify({"error": "Email is required."}), 400

    token = set_reset_token(email)
    if token:
        base_url = request.url_root.replace("http://", "https://").rstrip("/")
        reset_url = f"{base_url}/reset-password?token={token}"
        send_reset_email(email, reset_url)

    # Always return success to prevent email enumeration
    return jsonify({"success": True, "message": "If that email exists, a reset link has been sent."})


@app.route("/reset-password", methods=["GET", "POST"])
def reset_password_route():
    """Reset password using a valid token."""
    if request.method == "GET":
        token = request.args.get("token")
        if not token:
            return redirect("/login")
        result = verify_reset_token(token)
        if not result:
            return render_template("forgot_password.html", error="This reset link has expired. Please request a new one.")
        return render_template("reset_password.html", token=token, email=result["email"])

    data = request.get_json()
    token = data.get("token", "")
    password = data.get("password", "")

    if len(password) < 8:
        return jsonify({"error": "Password must be at least 8 characters."}), 400

    result = verify_reset_token(token)
    if not result:
        return jsonify({"error": "Reset link expired. Request a new one."}), 400

    reset_password(result["email"], password)
    flask_session["user_email"] = result["email"]
    return jsonify({"success": True})


# ── Unified Dashboard ────────────────────────────────────────

@app.route("/dashboard")
@login_required
def dashboard():
    """Dynamic dashboard — shows features based on role."""
    user = get_user_context()
    if not user:
        return redirect("/login")

    data = {"user": user}

    if user["is_admin"]:
        all_records = list(get_all_clients().values())
        # Filter: show only actual clients (not admin-only records)
        all_clients = [c for c in all_records if c.get("plan") and c["plan"] != "none"]
        admins = list(get_all_admins().values())
        plan_prices = {"starter": 97, "professional": 197, "enterprise": 497}
        mrr = sum(plan_prices.get(c.get("plan", ""), 0) for c in all_clients if c.get("status") == "active")
        data["all_clients"] = all_clients
        data["admins"] = admins
        data["mrr"] = mrr
        data["all_verified"] = all(c.get("email_verified") for c in all_clients if c.get("plan") == "trial") if any(c.get("plan") == "trial" for c in all_clients) else True
        data["permissions"] = user["admin"].get("permissions", [])

    if user["is_client"]:
        base_url = request.url_root.replace("http://", "https://").rstrip("/")
        data["base_url"] = base_url
        client = user["client"]
        plan = client.get("plan", "starter")
        data["plan_limits"] = PLAN_LIMITS.get(plan, PLAN_LIMITS["starter"])
        data["page_count"] = int(client.get("page_count", 0) or 0)
        data["file_count"] = int(client.get("file_count", 0) or 0)
        # List uploaded files
        docs_dir = os.path.join("clients", client["client_id"], "documents")
        data["uploaded_files"] = sorted([f for f in os.listdir(docs_dir) if f.endswith((".pdf", ".txt"))]) if os.path.exists(docs_dir) else []
        # Analytics
        data["chat_stats"] = get_chat_stats(client["client_id"])
        from datetime import datetime
        data["now_weekday"] = datetime.utcnow().strftime("%a")
        # Trial info
        if plan == "trial":
            from datetime import datetime as dt
            trial_expires = client.get("trial_expires", "")
            if trial_expires:
                expires_dt = dt.fromisoformat(trial_expires)
                delta = (expires_dt - dt.utcnow()).days
                data["trial_days_left"] = max(0, delta)
            data["daily_queries_used"] = get_daily_query_count(client["client_id"])

    # Reviews (visible to both admins and clients)
    from database import get_all_reviews, get_client_review
    data["reviews"] = get_all_reviews()
    if user["is_client"]:
        data["my_review"] = get_client_review(user["client"]["client_id"])

    return render_template("dashboard.html", **data)


@app.route("/dashboard/upload", methods=["POST"])
@login_required
def dashboard_upload():
    """Handle document upload with plan limits."""
    user = get_user_context()
    if not user or not user["is_client"]:
        return jsonify({"error": "No subscription found."}), 403

    client = user["client"]
    plan = client.get("plan", "starter")
    limits = PLAN_LIMITS.get(plan, PLAN_LIMITS["starter"])

    files = request.files.getlist("files")
    if not files:
        return jsonify({"error": "No files uploaded"}), 400

    client_id = client["client_id"]
    docs_dir = os.path.join("clients", client_id, "documents")
    os.makedirs(docs_dir, exist_ok=True)

    # Count existing files
    existing_files = [f for f in os.listdir(docs_dir) if f.endswith((".pdf", ".txt"))] if os.path.exists(docs_dir) else []
    new_valid = [f for f in files if f.filename and f.filename.endswith((".pdf", ".txt"))]

    # Determine which files are replacements vs truly new
    existing_names = set(existing_files)
    new_names = [secure_filename(f.filename) for f in new_valid]
    truly_new = [n for n in new_names if n not in existing_names]

    if len(existing_files) + len(truly_new) > limits["max_files"]:
        return jsonify({"error": f"File limit exceeded. Your {plan.title()} plan allows {limits['max_files']} files. You have {len(existing_files)} already."}), 400

    # Count pages in new files
    from pypdf import PdfReader
    import math
    total_new_pages = 0
    for f in new_valid:
        if f.filename.endswith(".pdf"):
            try:
                reader = PdfReader(f)
                total_new_pages += len(reader.pages)
                f.seek(0)  # Reset file pointer after reading
            except Exception:
                total_new_pages += 1  # Count as 1 if can't read
        elif f.filename.endswith(".txt"):
            content = f.read()
            f.seek(0)  # Reset file pointer after reading
            # Estimate pages: ~3000 characters per page, minimum 1
            total_new_pages += max(1, math.ceil(len(content) / 3000))

    # For replacements, recalculate total from scratch after save
    # For page limit check, use total_new_pages against limit (we'll recalculate after)
    existing_pages = int(client.get("page_count", 0) or 0)

    # Subtract pages of files being replaced (they'll be overwritten)
    replaced_names = [n for n in new_names if n in existing_names]
    replaced_pages = 0
    for fname in replaced_names:
        fpath = os.path.join(docs_dir, fname)
        if fname.endswith(".pdf"):
            try:
                reader = PdfReader(fpath)
                replaced_pages += len(reader.pages)
            except Exception:
                replaced_pages += 1
        elif fname.endswith(".txt"):
            try:
                with open(fpath, "rb") as tf:
                    replaced_pages += max(1, math.ceil(len(tf.read()) / 3000))
            except Exception:
                replaced_pages += 1

    net_new_pages = existing_pages - replaced_pages + total_new_pages
    if net_new_pages > limits["max_pages"]:
        return jsonify({"error": f"Page limit exceeded. Your {plan.title()} plan allows {limits['max_pages']} pages. Current: {existing_pages}, adding: {total_new_pages}, replacing: {replaced_pages} pages."}), 400

    # Save files
    saved = 0
    for f in new_valid:
        filename = secure_filename(f.filename)
        filepath = os.path.join(docs_dir, filename)
        f.save(filepath)
        # Persist to database so it survives Render deploys
        with open(filepath, "rb") as saved_file:
            save_document_to_db(client_id, filename, saved_file.read())
        saved += 1

    if saved == 0:
        return jsonify({"error": "No valid files (.pdf or .txt) found"}), 400

    # Final file count after save
    final_files = [f for f in os.listdir(docs_dir) if f.endswith((".pdf", ".txt"))]

    try:
        ingest_client(client_id)
        from agent import load_all_indexes as reload_indexes
        new_indexes = reload_indexes()
        indexes.update(new_indexes)
        update_client(client_id, {"documents_uploaded": True, "page_count": net_new_pages, "file_count": len(final_files)})

        replaced_msg = f" ({len(replaced_names)} replaced)" if replaced_names else ""
        return jsonify({"success": True, "message": f"{saved} file(s) uploaded and processed! ({total_new_pages} pages){replaced_msg}"})
    except Exception as e:
        print(f"Ingest error [{client_id}]: {e}")
        return jsonify({"error": "Processing failed. We'll fix this shortly."}), 500


@app.route("/dashboard/delete-file", methods=["POST"])
@login_required
def dashboard_delete_file():
    """Delete a specific uploaded file."""
    user = get_user_context()
    if not user or not user["is_client"]:
        return jsonify({"error": "No subscription found."}), 403

    client = user["client"]
    client_id = client["client_id"]
    data = request.get_json()
    filename = data.get("filename", "")

    if not filename:
        return jsonify({"error": "No filename specified."}), 400

    docs_dir = os.path.join("clients", client_id, "documents")
    filepath = os.path.join(docs_dir, secure_filename(filename))

    if not os.path.exists(filepath):
        return jsonify({"error": "File not found."}), 404

    # Count pages being removed
    from pypdf import PdfReader
    import math
    removed_pages = 0
    if filepath.endswith(".pdf"):
        try:
            reader = PdfReader(filepath)
            removed_pages = len(reader.pages)
        except Exception:
            removed_pages = 1
    elif filepath.endswith(".txt"):
        try:
            with open(filepath, "rb") as tf:
                removed_pages = max(1, math.ceil(len(tf.read()) / 3000))
        except Exception:
            removed_pages = 1

    os.remove(filepath)
    delete_document_from_db(client_id, secure_filename(filename))

    # Recalculate counts
    remaining_files = [f for f in os.listdir(docs_dir) if f.endswith((".pdf", ".txt"))] if os.path.exists(docs_dir) else []
    existing_pages = int(client.get("page_count", 0) or 0)
    new_page_count = max(0, existing_pages - removed_pages)

    try:
        if remaining_files:
            ingest_client(client_id)
            from agent import load_all_indexes as reload_indexes
            new_indexes = reload_indexes()
            indexes.update(new_indexes)
            update_client(client_id, {"page_count": new_page_count, "file_count": len(remaining_files)})
        else:
            update_client(client_id, {"documents_uploaded": False, "page_count": 0, "file_count": 0})

        return jsonify({"success": True, "message": f"'{filename}' deleted. Freed {removed_pages} page(s)."})
    except Exception as e:
        print(f"Delete error [{client_id}]: {e}")
        return jsonify({"error": "Delete failed."}), 500


@app.route("/dashboard/branding", methods=["POST"])
@login_required
def dashboard_branding():
    """Save widget branding settings (Professional+ only)."""
    user = get_user_context()
    if not user or not user["is_client"]:
        return jsonify({"error": "No subscription found."}), 403

    client = user["client"]
    plan = client.get("plan", "starter")
    if plan == "starter":
        return jsonify({"error": "Custom branding requires Professional or Enterprise plan."}), 403

    data = request.get_json()
    primary_color = data.get("primary_color", "#4f46e5").strip()
    bot_name = data.get("bot_name", "AI Assistant").strip()[:50]
    welcome_message = data.get("welcome_message", "Hi! How can I help you today?").strip()[:200]

    # Basic color validation
    if not primary_color.startswith("#") or len(primary_color) not in (4, 7):
        return jsonify({"error": "Invalid color format. Use hex like #4f46e5"}), 400

    update_client(client["client_id"], {
        "primary_color": primary_color,
        "bot_name": bot_name,
        "welcome_message": welcome_message,
    })
    return jsonify({"success": True, "message": "Branding updated!"})


@app.route("/dashboard/generate-api-key", methods=["POST"])
@login_required
def dashboard_generate_api_key():
    """Generate API key (Enterprise only)."""
    user = get_user_context()
    if not user or not user["is_client"]:
        return jsonify({"error": "No subscription found."}), 403

    client = user["client"]
    if client.get("plan") != "enterprise":
        return jsonify({"error": "API access requires Enterprise plan."}), 403

    api_key = f"ak_{secrets.token_hex(24)}"
    update_client(client["client_id"], {"api_key": api_key})
    return jsonify({"success": True, "api_key": api_key})


# ── Public API (Enterprise) ──────────────────────────────────

@app.route("/api/v1/<client_id>/ask", methods=["POST"])
def api_ask(client_id):
    """Public API endpoint for Enterprise clients."""
    # Auth via API key
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return jsonify({"error": "Missing Authorization header. Use: Bearer ak_xxx"}), 401

    api_key = auth_header[7:]
    client = get_client(client_id)
    if not client:
        return jsonify({"error": "Client not found."}), 404
    if client.get("status") == "cancelled":
        return jsonify({"error": "Subscription cancelled. API access disabled."}), 403
    if client.get("plan") != "enterprise":
        return jsonify({"error": "API access requires Enterprise plan."}), 403
    if not client.get("api_key") or client["api_key"] != api_key:
        return jsonify({"error": "Invalid API key."}), 401
    if client.get("status") != "active":
        return jsonify({"error": "Subscription not active."}), 403

    data = request.get_json()
    question = data.get("question", "").strip() if data else ""
    if not question:
        return jsonify({"error": "Question is required."}), 400

    if client_id not in indexes:
        return jsonify({"error": "No documents indexed yet."}), 404

    answer = ask(question, client_id, indexes)
    log_chat_query(client_id, question, answer if isinstance(answer, str) else answer.get("answer", ""))
    return jsonify({"answer": answer, "client_id": client_id})


@app.route("/dashboard/add-admin", methods=["POST"])
@login_required
def dashboard_add_admin():
    """Add a new admin."""
    user = get_user_context()
    if not user or not user["is_admin"]:
        return jsonify({"error": "Permission denied."}), 403
    if "manage_admins" not in user["admin"].get("permissions", []):
        return jsonify({"error": "Permission denied."}), 403

    data = request.get_json()
    email = data.get("email", "").strip()
    permissions = data.get("permissions", [])

    if not email:
        return jsonify({"error": "Email is required."}), 400

    # Check if this email is an existing client — they keep their password
    existing_client = get_client_by_email(email)
    if existing_client:
        if existing_client.get("role") in ("admin", "super_admin"):
            return jsonify({"error": "Already an admin."}), 400
        # Promote: just update role and permissions
        from database import _get_conn, P, _encode_perms
        conn = _get_conn()
        cur = conn.cursor()
        perms = _encode_perms(permissions)
        cur.execute(f"UPDATE clients SET role = 'admin', permissions = {P} WHERE LOWER(email) = LOWER({P})",
                    (perms, email))
        conn.commit()
        cur.close()
        conn.close()
        return jsonify({"success": True, "message": "Client promoted to admin. They log in with their existing password."})

    # New email — create admin record with a temp password
    password = secrets.token_urlsafe(10)
    result = create_admin(email, password, permissions, user["email"])
    if not result:
        return jsonify({"error": "Admin already exists."}), 400
    return jsonify({"success": True, "temp_password": password, "message": "Admin created with temporary password."})


@app.route("/dashboard/remove-admin", methods=["POST"])
@login_required
def dashboard_remove_admin():
    """Remove an admin."""
    user = get_user_context()
    if not user or not user["is_admin"]:
        return jsonify({"error": "Permission denied."}), 403
    if "manage_admins" not in user["admin"].get("permissions", []):
        return jsonify({"error": "Permission denied."}), 403

    data = request.get_json()
    email = data.get("email", "").strip()
    if not delete_admin(email):
        return jsonify({"error": "Cannot remove this admin."}), 400
    return jsonify({"success": True})


@app.route("/dashboard/edit-admin", methods=["POST"])
@login_required
def dashboard_edit_admin():
    """Update an admin's permissions."""
    user = get_user_context()
    if not user or not user["is_admin"]:
        return jsonify({"error": "Permission denied."}), 403
    if "manage_admins" not in user["admin"].get("permissions", []):
        return jsonify({"error": "Permission denied."}), 403

    data = request.get_json()
    email = data.get("email", "").strip()
    permissions = data.get("permissions", [])

    if not email:
        return jsonify({"error": "Email is required."}), 400

    # Don't allow editing super_admin
    target = get_client_by_email(email)
    if not target or target.get("role") not in ("admin", "super_admin"):
        return jsonify({"error": "Admin not found."}), 404
    if target.get("role") == "super_admin":
        return jsonify({"error": "Cannot edit super admin permissions."}), 403

    from database import _get_conn, P, _encode_perms
    conn = _get_conn()
    cur = conn.cursor()
    perms = _encode_perms(permissions)
    cur.execute(f"UPDATE clients SET permissions = {P} WHERE LOWER(email) = LOWER({P})", (perms, email))
    conn.commit()
    cur.close()
    conn.close()

    return jsonify({"success": True})


# ── Reviews ───────────────────────────────────────────────────

@app.route("/dashboard/submit-review", methods=["POST"])
@login_required
def dashboard_submit_review():
    """Client submits a review."""
    user = get_user_context()
    if not user or not user["is_client"]:
        return jsonify({"error": "Only clients can submit reviews."}), 403

    client = user["client"]
    data = request.get_json()
    review = data.get("review", "").strip()

    if not review:
        return jsonify({"error": "Review cannot be empty."}), 400
    if len(review) > 1000:
        return jsonify({"error": "Review is too long (max 1000 characters)."}), 400

    from database import submit_review
    submit_review(client["client_id"], client["email"], client["business_name"], review)
    return jsonify({"success": True, "message": "Thank you for your review!"})


@app.route("/dashboard/reviews", methods=["GET"])
@login_required
def dashboard_get_reviews():
    """Get all reviews (visible to admins and clients)."""
    from database import get_all_reviews
    reviews = get_all_reviews()
    for r in reviews:
        if r.get("created_at") and not isinstance(r["created_at"], str):
            r["created_at"] = r["created_at"].strftime("%Y-%m-%d %H:%M")
    return jsonify({"reviews": reviews})


# ── Admin: Toggle Email Verified (All) ────────────────────────

@app.route("/dashboard/toggle-verified-all", methods=["POST"])
@login_required
def toggle_verified_all():
    """Toggle email_verified for all trial clients (admin only)."""
    user = get_user_context()
    if not user or not user["is_admin"]:
        return jsonify({"error": "Permission denied."}), 403

    data = request.get_json()
    verified = bool(data.get("verified", False))

    from database import _get_conn, P
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute(f"UPDATE clients SET email_verified = {P} WHERE plan = {P}", (verified, "trial"))
    conn.commit()
    cur.close()
    conn.close()

    return jsonify({"success": True, "email_verified": verified})


# ── Stripe Checkout ───────────────────────────────────────────

@app.route("/checkout", methods=["POST"])
def create_checkout():
    """Create a Stripe Checkout session."""
    data = request.get_json()
    plan = data.get("plan", "").strip()
    business_name = data.get("business_name", "").strip()
    email = data.get("email", "").strip()

    if not plan or plan not in PLAN_PRICES:
        return jsonify({"error": "Invalid plan"}), 400
    if not business_name:
        return jsonify({"error": "Business name is required"}), 400
    if not email:
        return jsonify({"error": "Email is required"}), 400

    existing = get_client_by_email(email)
    if existing:
        return jsonify({"error": f"This email already has a subscription ({existing['plan'].title()} plan). Log in to access your dashboard."}), 400

    if not stripe.api_key:
        return jsonify({"error": "Payment system not configured."}), 500

    try:
        price_id = get_or_create_stripe_price(plan)
        checkout_session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            line_items=[{"price": price_id, "quantity": 1}],
            mode="subscription",
            success_url=request.url_root.replace("http://", "https://") + "payment-success?session_id={CHECKOUT_SESSION_ID}",
            cancel_url=request.url_root.replace("http://", "https://"),
            customer_email=email,
            subscription_data={
                "metadata": {"business_name": business_name, "plan": plan},
            },
            metadata={"business_name": business_name, "plan": plan},
        )
        return jsonify({"url": checkout_session.url})
    except Exception as e:
        print(f"Stripe error: {type(e).__name__}: {e}")
        return jsonify({"error": "Payment setup failed. Please try again."}), 500


@app.route("/upgrade", methods=["POST"])
@login_required
def upgrade_plan():
    """Upgrade to a higher plan."""
    user = get_user_context()
    if not user or not user["is_client"]:
        return jsonify({"error": "No subscription found."}), 403

    client = user["client"]
    data = request.get_json()
    new_plan = data.get("plan", "").strip()

    if not new_plan or new_plan not in PLAN_PRICES:
        return jsonify({"error": "Invalid plan"}), 400

    plan_order = {"trial": 0, "starter": 1, "professional": 2, "enterprise": 3}
    if plan_order.get(new_plan, 0) <= plan_order.get(client["plan"], 0):
        return jsonify({"error": "You can only upgrade to a higher plan."}), 400

    subscription_id = client.get("stripe_subscription_id")

    try:
        if subscription_id:
            # Modify existing subscription with proration
            subscription = stripe.Subscription.retrieve(subscription_id)
            new_price_id = get_or_create_stripe_price(new_plan)

            stripe.Subscription.modify(
                subscription_id,
                items=[{
                    "id": subscription.items.data[0].id,
                    "price": new_price_id,
                }],
                proration_behavior="create_prorations",
                metadata={"plan": new_plan, "client_id": client["client_id"]},
            )

            # Update plan immediately — Stripe handles billing
            update_client(client["client_id"], {"plan": new_plan, "status": "active"})
            return jsonify({"success": True, "message": f"Upgraded to {new_plan.title()}! Prorated billing applied."})
        else:
            # No subscription ID stored — fallback to new checkout
            new_price_id = get_or_create_stripe_price(new_plan)
            checkout_session = stripe.checkout.Session.create(
                payment_method_types=["card"],
                line_items=[{"price": new_price_id, "quantity": 1}],
                mode="subscription",
                success_url=request.url_root.replace("http://", "https://") + f"upgrade-success?plan={new_plan}&client_id={client['client_id']}",
                cancel_url=request.url_root.replace("http://", "https://") + "dashboard",
                customer_email=client["email"],
                metadata={"business_name": client["business_name"], "plan": new_plan, "client_id": client["client_id"]},
            )
            return jsonify({"url": checkout_session.url})
    except Exception as e:
        print(f"Upgrade error: {type(e).__name__}: {e}")
        return jsonify({"error": "Upgrade failed."}), 500


@app.route("/upgrade-success")
def upgrade_success():
    new_plan = request.args.get("plan")
    client_id = request.args.get("client_id")
    if client_id and new_plan:
        update_client(client_id, {"plan": new_plan, "status": "active"})
    return redirect("/dashboard")


@app.route("/dashboard/analytics", methods=["GET"])
@login_required
def dashboard_analytics():
    """Return filtered chat stats as JSON."""
    user = get_user_context()
    if not user or not user["is_client"]:
        return jsonify({"error": "Unauthorized"}), 403

    client_id = user["client"]["client_id"]
    start_date = request.args.get("start")
    end_date = request.args.get("end")

    if not start_date or not end_date:
        return jsonify({"error": "start and end parameters required"}), 400

    # Basic validation (YYYY-MM-DD)
    import re
    date_pattern = re.compile(r"^\d{4}-\d{2}-\d{2}$")
    if not date_pattern.match(start_date) or not date_pattern.match(end_date):
        return jsonify({"error": "Invalid date format. Use YYYY-MM-DD."}), 400

    stats = get_chat_stats_filtered(client_id, start_date, end_date)
    return jsonify(stats)


@app.route("/dashboard/conversations", methods=["GET"])
@login_required
def dashboard_conversations():
    """Return recent conversation history as JSON."""
    user = get_user_context()
    if not user or not user["is_client"]:
        return jsonify({"error": "Unauthorized"}), 403

    client_id = user["client"]["client_id"]
    page = int(request.args.get("page", 1))
    limit = 20
    offset = (page - 1) * limit

    history = get_chat_history(client_id, limit=limit, offset=offset)
    return jsonify({"conversations": history, "page": page, "has_more": len(history) == limit})


@app.route("/cancel-subscription", methods=["POST"])
@login_required
def cancel_subscription():
    """Cancel subscription at end of billing period."""
    user = get_user_context()
    if not user or not user["is_client"]:
        return jsonify({"error": "No subscription found."}), 403

    client = user["client"]
    subscription_id = client.get("stripe_subscription_id")

    if not subscription_id:
        return jsonify({"error": "No active subscription found. Contact support."}), 400

    try:
        # Cancel at period end — client keeps access until billing cycle ends
        stripe.Subscription.modify(
            subscription_id,
            cancel_at_period_end=True,
        )
        update_client(client["client_id"], {"status": "cancelling"})
        return jsonify({"success": True, "message": "Subscription will cancel at the end of your billing period. You'll retain access until then."})
    except Exception as e:
        print(f"Cancel error: {type(e).__name__}: {e}")
        return jsonify({"error": "Cancellation failed. Contact support."}), 500


@app.route("/billing-portal", methods=["POST"])
@login_required
def billing_portal():
    """Create a Stripe Customer Portal session for managing billing."""
    user = get_user_context()
    if not user or not user["is_client"]:
        return jsonify({"error": "No subscription found."}), 403

    client = user["client"]
    customer_id = client.get("stripe_customer_id")

    if not customer_id:
        return jsonify({"error": "No billing account found. Contact support."}), 400

    try:
        session = stripe.billing_portal.Session.create(
            customer=customer_id,
            return_url=request.url_root.replace("http://", "https://") + "dashboard",
        )
        return jsonify({"url": session.url})
    except Exception as e:
        print(f"Billing portal error: {type(e).__name__}: {e}")
        return jsonify({"error": "Could not open billing portal. Contact support."}), 500


@app.route("/resubscribe", methods=["POST"])
@login_required
def resubscribe():
    """Undo cancellation if still within billing period, or create new subscription if fully cancelled."""
    user = get_user_context()
    if not user or not user["is_client"]:
        return jsonify({"error": "Unauthorized."}), 403

    client = user["client"]
    status = client.get("status", "")
    subscription_id = client.get("stripe_subscription_id")

    if status == "cancelling" and subscription_id:
        # Still within billing period — undo the cancellation
        try:
            stripe.Subscription.modify(subscription_id, cancel_at_period_end=False)
            update_client(client["client_id"], {"status": "active"})
            return jsonify({"success": True, "message": "Subscription reactivated! You won't be cancelled."})
        except Exception as e:
            print(f"Resubscribe error: {type(e).__name__}: {e}")
            return jsonify({"error": "Failed to reactivate. Contact support."}), 500

    elif status == "cancelled":
        # Fully cancelled — create a new checkout session for same plan
        plan = client.get("plan", "starter")
        try:
            price_id = STRIPE_PRICE_IDS.get(plan) or get_or_create_stripe_price(plan)
            session = stripe.checkout.Session.create(
                payment_method_types=["card"],
                line_items=[{"price": price_id, "quantity": 1}],
                mode="subscription",
                success_url=request.host_url + f"resubscribe-success?session_id={{CHECKOUT_SESSION_ID}}&client_id={client['client_id']}",
                cancel_url=request.host_url + "dashboard",
                customer_email=client["email"],
                metadata={"client_id": client["client_id"], "plan": plan},
            )
            return jsonify({"success": True, "url": session.url})
        except Exception as e:
            print(f"Resubscribe checkout error: {type(e).__name__}: {e}")
            return jsonify({"error": "Failed to create checkout. Contact support."}), 500

    else:
        return jsonify({"error": "No cancellation to undo."}), 400


@app.route("/resubscribe-success")
@login_required
def resubscribe_success():
    """Handle successful resubscription payment."""
    session_id = request.args.get("session_id")
    client_id = request.args.get("client_id")
    if not session_id or not client_id:
        return "Missing parameters.", 400

    try:
        checkout = stripe.checkout.Session.retrieve(session_id)
        stripe_customer_id = checkout.customer or ""
        stripe_subscription_id = checkout.subscription or ""
        update_client(client_id, {
            "status": "active",
            "stripe_customer_id": stripe_customer_id,
            "stripe_subscription_id": stripe_subscription_id,
        })
        return redirect("/dashboard")
    except Exception as e:
        print(f"Resubscribe success error: {type(e).__name__}: {e}")
        return redirect("/dashboard")


@app.route("/payment-success")
def payment_success():
    """Provision client after payment."""
    session_id = request.args.get("session_id")
    if not session_id:
        return "No session ID.", 400

    try:
        checkout = stripe.checkout.Session.retrieve(session_id)
        if checkout.payment_status not in ("paid", "no_payment_required") and checkout.status != "complete":
            return "Payment not confirmed. Please refresh.", 400

        email = checkout.customer_email or ""
        business_name = checkout.metadata["business_name"] if "business_name" in checkout.metadata else "Business"
        plan = checkout.metadata["plan"] if "plan" in checkout.metadata else "starter"

        # Prevent duplicates (page refresh, webhook race)
        existing = get_client_by_email(email)
        if existing:
            return redirect(f"/set-password?token={existing['access_token']}")

        client = create_client(email, business_name, plan, session_id)

        # Store Stripe customer and subscription IDs
        stripe_customer_id = checkout.customer or ""
        stripe_subscription_id = checkout.subscription or ""
        if stripe_customer_id or stripe_subscription_id:
            update_client(client["client_id"], {
                "stripe_customer_id": stripe_customer_id,
                "stripe_subscription_id": stripe_subscription_id,
            })

        print(f"Client provisioned: {client['client_id']} for {email}")

        # Send welcome email
        login_url = request.url_root.replace("http://", "https://") + "login"
        send_welcome_email(email, business_name, login_url)

        return redirect(f"/set-password?token={client['access_token']}")
    except Exception as e:
        print(f"Payment success error: {type(e).__name__}: {e}")
        return f"Error processing payment. Contact support with session: {session_id}", 500


# ── Stripe Webhook ───────────────────────────────────────────

@app.route("/webhook/stripe", methods=["POST"])
def stripe_webhook():
    payload = request.get_data()
    sig_header = request.headers.get("Stripe-Signature")

    if STRIPE_WEBHOOK_SECRET:
        try:
            event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
        except (ValueError, stripe.error.SignatureVerificationError):
            return jsonify({"error": "Invalid signature"}), 400
    else:
        event = stripe.Event.construct_from(request.get_json(), stripe.api_key)

    if event["type"] == "checkout.session.completed":
        sess = event["data"]["object"]
        metadata = sess.get("metadata", {})
        client_id = metadata.get("client_id", "")
        
        # Store Stripe customer and subscription IDs
        stripe_customer_id = sess.get("customer", "")
        stripe_subscription_id = sess.get("subscription", "")
        
        if client_id:
            # Upgrade flow — client already exists
            new_plan = metadata.get("plan", "")
            if new_plan:
                update_client(client_id, {
                    "plan": new_plan,
                    "stripe_customer_id": stripe_customer_id,
                    "stripe_subscription_id": stripe_subscription_id,
                })
                print(f"⬆ Upgraded {client_id} to {new_plan}")
        else:
            # New signup flow
            client = create_client(
                sess.get("customer_email", ""),
                metadata.get("business_name", "Business"),
                metadata.get("plan", "starter"),
                sess["id"]
            )
            update_client(client["client_id"], {
                "stripe_customer_id": stripe_customer_id,
                "stripe_subscription_id": stripe_subscription_id,
            })
            print(f"✓ New client: {client['client_id']}")

    elif event["type"] == "customer.subscription.deleted":
        sub = event["data"]["object"]
        customer_email = ""
        if hasattr(sub, "customer_email"):
            customer_email = sub.customer_email
        else:
            # Look up customer email from Stripe
            try:
                customer = stripe.Customer.retrieve(sub["customer"])
                customer_email = customer.email
            except Exception:
                pass
        if customer_email:
            client = get_client_by_email(customer_email)
            if client:
                update_client(client["client_id"], {"status": "cancelled"})
                print(f"✗ Subscription cancelled for {customer_email}")

    elif event["type"] == "invoice.payment_failed":
        invoice = event["data"]["object"]
        customer_email = invoice.get("customer_email", "")
        if customer_email:
            client = get_client_by_email(customer_email)
            if client:
                update_client(client["client_id"], {"status": "past_due"})
                print(f"⚠ Payment failed for {customer_email}")

    elif event["type"] == "customer.subscription.updated":
        sub = event["data"]["object"]
        metadata = sub.get("metadata", {})
        client_id = metadata.get("client_id", "")
        new_plan = metadata.get("plan", "")
        if client_id and new_plan:
            update_client(client_id, {"plan": new_plan})
            print(f"⬆ Subscription updated: {client_id} → {new_plan}")

    return jsonify({"status": "ok"})


# ── Chat Routes (public) ─────────────────────────────────────

@app.route("/<client_id>")
def client_chat(client_id):
    client = get_client(client_id)
    if client_id not in indexes:
        if client:
            return f"""<html><body style='font-family:sans-serif;text-align:center;padding:60px;'>
            <h2>⏳ {client['business_name']} Chatbot</h2>
            <p>Waiting for documents. The owner needs to upload content first.</p>
            </body></html>"""
        return jsonify({"error": f"Client '{client_id}' not found"}), 404
    branding = {
        "primary_color": client.get("primary_color", "#4f46e5") if client else "#4f46e5",
        "bot_name": client.get("bot_name", "AI Assistant") if client else "AI Assistant",
        "welcome_message": client.get("welcome_message", "Hi! How can I help you today?") if client else "Hi! How can I help you today?",
    }
    return render_template("index.html", client_id=client_id, branding=branding)


@app.route("/<client_id>/ask", methods=["POST"])
def ask_question(client_id):
    if client_id not in indexes:
        return jsonify({"error": f"Client '{client_id}' not found"}), 404

    # Block if subscription is cancelled
    client = get_client(client_id)
    if client and client.get("status") == "cancelled":
        return jsonify({"error": "This chatbot is no longer active. The subscription has been cancelled."}), 403

    # Enforce trial limits
    if client and client.get("plan") == "trial":
        from datetime import datetime
        if not client.get("email_verified"):
            return jsonify({"error": "Your account is pending verification. Please wait for admin approval."}), 403
        trial_expires = client.get("trial_expires", "")
        if trial_expires and datetime.utcnow() > datetime.fromisoformat(trial_expires):
            return jsonify({"error": "Your free trial has expired. Please subscribe to continue using the service."}), 403
        daily_count = get_daily_query_count(client_id)
        limit = PLAN_LIMITS["trial"]["max_queries_per_day"]
        if daily_count >= limit:
            return jsonify({"error": f"Daily query limit reached ({limit}/day on free trial). Upgrade for unlimited queries."}), 429

    data = request.get_json()
    if not data:
        return jsonify({"error": "Invalid request"}), 400

    question = data.get("question", "").strip()
    if not question:
        return jsonify({"error": "No question provided"}), 400
    if len(question) > 2000:
        return jsonify({"error": "Question too long (max 2000 chars)"}), 400

    try:
        result = ask(question, indexes[client_id], client_id)
        answer_text = result.get("answer", "") if isinstance(result, dict) else str(result)
        log_chat_query(client_id, question, answer_text)
        return jsonify(result)
    except Exception as e:
        print(f"Error [{client_id}]: {e}")
        return jsonify({"error": "Something went wrong."}), 500


@app.route("/<client_id>/embed")
def embed_code(client_id):
    if client_id not in indexes:
        return jsonify({"error": f"Client '{client_id}' not found"}), 404
    return render_template("embed.html", client_id=client_id)


if __name__ == "__main__":
    app.run(debug=True, port=5000)
