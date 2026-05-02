"""Flask web app — Multi-tenant AI Assistant powered by Groq + Stripe."""
import os
import secrets
import stripe
from functools import wraps
from dotenv import load_dotenv
from flask import Flask, render_template, request, jsonify, redirect, make_response, session as flask_session
from flask_cors import CORS
from werkzeug.utils import secure_filename
from agent import load_all_indexes, ask
from ingest import ingest_all, ingest_client, get_client_dirs, INDEXES_DIR
from database import (
    create_client, get_client_by_token, get_client, update_client,
    get_client_by_email, set_client_password, verify_client_password,
    init_super_admin, verify_admin, get_admin, get_all_admins, get_all_clients,
    create_admin, delete_admin, set_reset_token, verify_reset_token, reset_password,
)

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", secrets.token_hex(32))
CORS(app)

# Initialize super admin on first run
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

# Build indexes at startup if not already present
if not os.path.exists(INDEXES_DIR) or not os.listdir(INDEXES_DIR):
    print("Building indexes for all clients...")
    ingest_all()

# Load all client indexes at startup
print("Loading client indexes...")
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

    admin = get_admin(email)
    if admin:
        ctx["is_admin"] = True
        ctx["admin"] = admin

    client = get_client_by_email(email)
    if client:
        ctx["is_client"] = True
        ctx["client"] = client

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

    # Try admin login
    admin = verify_admin(email, password)
    if admin:
        flask_session["user_email"] = email
        return jsonify({"success": True})

    # Try client login
    client = verify_client_password(email, password)
    if client:
        flask_session["user_email"] = email
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
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(GMAIL_EMAIL, GMAIL_APP_PASSWORD)
            server.sendmail(GMAIL_EMAIL, to_email, msg.as_string())
        return True
    except Exception as e:
        print(f"Email error: {e}")
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
        all_clients = list(get_all_clients().values())
        admins = list(get_all_admins().values())
        plan_prices = {"starter": 97, "professional": 197, "enterprise": 497}
        mrr = sum(plan_prices.get(c.get("plan", ""), 0) for c in all_clients if c.get("status") == "active")
        data["all_clients"] = all_clients
        data["admins"] = admins
        data["mrr"] = mrr
        data["permissions"] = user["admin"].get("permissions", [])

    if user["is_client"]:
        base_url = request.url_root.replace("http://", "https://").rstrip("/")
        data["base_url"] = base_url

    return render_template("dashboard.html", **data)


@app.route("/dashboard/upload", methods=["POST"])
@login_required
def dashboard_upload():
    """Handle document upload."""
    user = get_user_context()
    if not user or not user["is_client"]:
        return jsonify({"error": "No subscription found."}), 403

    client = user["client"]
    files = request.files.getlist("files")
    if not files:
        return jsonify({"error": "No files uploaded"}), 400

    client_id = client["client_id"]
    docs_dir = os.path.join("clients", client_id, "documents")
    os.makedirs(docs_dir, exist_ok=True)

    saved = 0
    for f in files:
        if f.filename and f.filename.endswith((".pdf", ".txt")):
            filename = secure_filename(f.filename)
            f.save(os.path.join(docs_dir, filename))
            saved += 1

    if saved == 0:
        return jsonify({"error": "No valid files (.pdf or .txt) found"}), 400

    try:
        ingest_client(client_id)
        from agent import load_all_indexes as reload_indexes
        new_indexes = reload_indexes()
        indexes.update(new_indexes)
        update_client(client_id, {"documents_uploaded": True})
        return jsonify({"success": True, "message": f"{saved} file(s) uploaded and processed!"})
    except Exception as e:
        print(f"Ingest error [{client_id}]: {e}")
        return jsonify({"error": "Processing failed. We'll fix this shortly."}), 500


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
    password = data.get("password", "")
    permissions = data.get("permissions", [])

    if not email or not password:
        return jsonify({"error": "Email and password required."}), 400
    if len(password) < 8:
        return jsonify({"error": "Password must be at least 8 characters."}), 400

    result = create_admin(email, password, permissions, user["email"])
    if not result:
        return jsonify({"error": "Admin already exists."}), 400
    return jsonify({"success": True})


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
        checkout_session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            line_items=[{
                "price_data": {
                    "currency": "usd",
                    "product_data": {
                        "name": f"AI Assistant — {plan.title()} Plan",
                        "description": f"Custom AI chatbot for {business_name}",
                    },
                    "unit_amount": PLAN_PRICES[plan],
                    "recurring": {"interval": "month"},
                },
                "quantity": 1,
            }],
            mode="subscription",
            success_url=request.url_root.replace("http://", "https://") + "payment-success?session_id={CHECKOUT_SESSION_ID}",
            cancel_url=request.url_root.replace("http://", "https://"),
            customer_email=email,
            subscription_data={"metadata": {"business_name": business_name, "plan": plan}},
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

    plan_order = {"starter": 1, "professional": 2, "enterprise": 3}
    if plan_order.get(new_plan, 0) <= plan_order.get(client["plan"], 0):
        return jsonify({"error": "You can only upgrade to a higher plan."}), 400

    try:
        checkout_session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            line_items=[{
                "price_data": {
                    "currency": "usd",
                    "product_data": {
                        "name": f"AI Assistant — {new_plan.title()} Plan (Upgrade)",
                    },
                    "unit_amount": PLAN_PRICES[new_plan],
                    "recurring": {"interval": "month"},
                },
                "quantity": 1,
            }],
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
        update_client(client_id, {"plan": new_plan})
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

        client = create_client(email, business_name, plan, session_id)
        print(f"Client provisioned: {client['client_id']} for {email}")
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
        create_client(sess.get("customer_email", ""), metadata.get("business_name", "Business"), metadata.get("plan", "starter"), sess["id"])

    elif event["type"] == "customer.subscription.deleted":
        print(f"✗ Subscription cancelled")

    elif event["type"] == "invoice.payment_failed":
        print(f"⚠ Payment failed")

    return jsonify({"status": "ok"})


# ── Chat Routes (public) ─────────────────────────────────────

@app.route("/<client_id>")
def client_chat(client_id):
    if client_id not in indexes:
        client = get_client(client_id)
        if client:
            return f"""<html><body style='font-family:sans-serif;text-align:center;padding:60px;'>
            <h2>⏳ {client['business_name']} Chatbot</h2>
            <p>Waiting for documents. The owner needs to upload content first.</p>
            </body></html>"""
        return jsonify({"error": f"Client '{client_id}' not found"}), 404
    return render_template("index.html", client_id=client_id)


@app.route("/<client_id>/ask", methods=["POST"])
def ask_question(client_id):
    if client_id not in indexes:
        return jsonify({"error": f"Client '{client_id}' not found"}), 404

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
