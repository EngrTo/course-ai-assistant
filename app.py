"""Flask web app — Course Q&A AI Assistant powered by Groq."""
import os
from flask import Flask, render_template, request, jsonify
from agent import load_index, ask
from ingest import build_index, load_documents

app = Flask(__name__)

# Build index at startup if not already present
if not os.path.exists("search_index.pkl"):
    print("Building search index...")
    docs = load_documents("documents")
    build_index(docs)

# Load search index once at startup
print("Loading search index...")
index = load_index()
print(f"Ready! {len(index['chunks'])} chunks loaded.\n")


@app.route("/")
def home():
    """Serve the chat interface."""
    return render_template("index.html")


@app.route("/ask", methods=["POST"])
def ask_question():
    """Handle a question from the chat UI."""
    data = request.get_json()
    if not data:
        return jsonify({"error": "Invalid request"}), 400

    question = data.get("question", "").strip()
    if not question:
        return jsonify({"error": "No question provided"}), 400

    if len(question) > 2000:
        return jsonify({"error": "Question too long (max 2000 chars)"}), 400

    try:
        result = ask(question, index)
        return jsonify(result)
    except Exception as e:
        print(f"Error: {e}")
        return jsonify({"error": "Something went wrong. Please try again."}), 500


if __name__ == "__main__":
    app.run(debug=True, port=5000)
