"""Flask web app — Multi-tenant AI Assistant powered by Groq."""
import os
from flask import Flask, render_template, request, jsonify
from agent import load_all_indexes, ask
from ingest import ingest_all, get_client_dirs, INDEXES_DIR

app = Flask(__name__)

# Build indexes at startup if not already present
if not os.path.exists(INDEXES_DIR) or not os.listdir(INDEXES_DIR):
    print("Building indexes for all clients...")
    ingest_all()

# Load all client indexes at startup
print("Loading client indexes...")
indexes = load_all_indexes()
print(f"Ready! {len(indexes)} client(s) loaded.\n")


@app.route("/")
def home():
    """Show list of active clients."""
    clients = list(indexes.keys())
    return render_template("home.html", clients=clients)


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


if __name__ == "__main__":
    app.run(debug=True, port=5000)
