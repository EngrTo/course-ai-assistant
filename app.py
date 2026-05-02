"""Flask web app — Multi-tenant AI Assistant powered by Groq + Stripe."""
import os
import stripe
from dotenv import load_dotenv
from flask import Flask, render_template, request, jsonify, redirect, url_for
from flask_cors import CORS
from werkzeug.utils import secure_filename
from agent import load_all_indexes, ask
from ingest import ingest_all, ingest_client, get_client_dirs, INDEXES_DIR
from database import create_client, get_client_by_token, get_client, update_client

load_dotenv()

app = Flask(__name__)
CORS(app)

# Stripe config
stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
STRIPE_PUBLISHABLE_KEY = os.getenv("STRIPE_PUBLISHABLE_KEY")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET")

PLAN_PRICES = {
    "starter": 50000,        # $500 in cents
    "professional": 150000,  # $1,500
    "enterprise": 300000,    # $3,000
}

# Build indexes at startup if not already present
if not os.path.exists(INDEXES_DIR) or not os.listdir(INDEXES_DIR):
    print("Building indexes for all clients...")
    ingest_all()

# Load all client indexes at startup
print("Loading client indexes...")
indexes = load_all_indexes()
print(f"Ready! {len(indexes)} client(s) loaded.\n")


# ── Landing Page ──────────────────────────────────────────────

@app.route("/")
def landing():
    """Public landing page with pricing."""
    return render_template("landing.html")


@app.route("/admin")
def admin():
    """Admin dashboard — list of active clients."""
    clients = list(indexes.keys())
    return render_template("home.html", clients=clients)


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

    try:
        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            line_items=[{
                "price_data": {
                    "currency": "usd",
                    "product_data": {
                        "name": f"AI Assistant — {plan.title()} Plan",
                        "description": f"Custom AI chatbot for {business_name}",
                    },
                    "unit_amount": PLAN_PRICES[plan],
                },
                "quantity": 1,
            }],
            mode="payment",
            success_url=request.host_url + "payment-success?session_id={CHECKOUT_SESSION_ID}",
            cancel_url=request.host_url,
            customer_email=email,
            metadata={
                "business_name": business_name,
                "plan": plan,
            },
        )
        return jsonify({"url": session.url})
    except Exception as e:
        print(f"Stripe error: {e}")
        return jsonify({"error": "Payment setup failed. Please try again."}), 500


@app.route("/payment-success")
def payment_success():
    """Handle successful payment — provision the client."""
    session_id = request.args.get("session_id")
    if not session_id:
        return redirect("/")

    try:
        session = stripe.checkout.Session.retrieve(session_id)
        if session.payment_status != "paid":
            return redirect("/")

        # Check if client already created for this session
        email = session.customer_email
        business_name = session.metadata.get("business_name", "Business")
        plan = session.metadata.get("plan", "starter")

        # Create client (idempotent — checks for existing)
        client = create_client(email, business_name, plan, session_id)

        return render_template("success.html",
                               token=client["access_token"],
                               email=email)
    except Exception as e:
        print(f"Payment success error: {e}")
        return redirect("/")


# ── Stripe Webhook (for production reliability) ──────────────

@app.route("/webhook/stripe", methods=["POST"])
def stripe_webhook():
    """Handle Stripe webhook events."""
    payload = request.get_data()
    sig_header = request.headers.get("Stripe-Signature")

    if STRIPE_WEBHOOK_SECRET:
        try:
            event = stripe.Webhook.construct_event(
                payload, sig_header, STRIPE_WEBHOOK_SECRET
            )
        except (ValueError, stripe.error.SignatureVerificationError):
            return jsonify({"error": "Invalid signature"}), 400
    else:
        event = stripe.Event.construct_from(
            request.get_json(), stripe.api_key
        )

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        if session.get("payment_status") == "paid":
            email = session.get("customer_email", "")
            business_name = session["metadata"].get("business_name", "Business")
            plan = session["metadata"].get("plan", "starter")
            create_client(email, business_name, plan, session["id"])
            print(f"✓ Auto-provisioned: {business_name} ({email})")

    return jsonify({"status": "ok"})


# ── Client Portal ────────────────────────────────────────────

@app.route("/portal")
def portal():
    """Client portal — upload docs and get widget code."""
    token = request.args.get("token")
    if not token:
        return "Access token required. Check your email for the portal link.", 401

    client = get_client_by_token(token)
    if not client:
        return "Invalid access token.", 403

    base_url = request.host_url.rstrip("/")
    return render_template("portal.html", client=client, base_url=base_url)


@app.route("/portal/upload", methods=["POST"])
def portal_upload():
    """Handle document upload from client portal."""
    token = request.form.get("token")
    if not token:
        return jsonify({"error": "Access token required"}), 401

    client = get_client_by_token(token)
    if not client:
        return jsonify({"error": "Invalid access token"}), 403

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

    # Re-ingest this client's documents
    try:
        ingest_client(client_id)
        # Reload this client's index
        from agent import load_all_indexes as reload_indexes
        new_indexes = reload_indexes()
        indexes.update(new_indexes)

        update_client(client_id, {"documents_uploaded": True})

        return jsonify({
            "success": True,
            "message": f"{saved} file(s) uploaded and processed. Your chatbot is ready!"
        })
    except Exception as e:
        print(f"Ingest error [{client_id}]: {e}")
        return jsonify({"error": "Files uploaded but processing failed. We'll fix this shortly."}), 500


# ── Chat Routes ──────────────────────────────────────────────

@app.route("/<client_id>")
def client_chat(client_id):
    """Serve the chat interface for a specific client."""
    if client_id not in indexes:
        return jsonify({"error": f"Client '{client_id}' not found"}), 404
    return render_template("index.html", client_id=client_id)


@app.route("/<client_id>/ask", methods=["POST"])
def ask_question(client_id):
    """Handle a question for a specific client."""
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
        return jsonify({"error": "Something went wrong. Please try again."}), 500


@app.route("/<client_id>/embed")
def embed_code(client_id):
    """Show the embed code for a client's widget."""
    if client_id not in indexes:
        return jsonify({"error": f"Client '{client_id}' not found"}), 404
    return render_template("embed.html", client_id=client_id)


if __name__ == "__main__":
    app.run(debug=True, port=5000)
