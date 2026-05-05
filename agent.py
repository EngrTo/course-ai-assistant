"""RAG Q&A agent using Groq (free) + TF-IDF retrieval (multi-tenant)."""
import os
import pickle

from dotenv import load_dotenv
from groq import Groq
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

load_dotenv()

INDEXES_DIR = "indexes"

# Conversation history per client (in-memory)
conversation_histories: dict[str, list[dict]] = {}


def load_all_indexes() -> dict[str, dict]:
    """Load all client indexes at startup."""
    indexes = {}
    if not os.path.exists(INDEXES_DIR):
        return indexes

    for filename in os.listdir(INDEXES_DIR):
        if filename.endswith(".pkl"):
            client_id = filename[:-4]  # Remove .pkl
            filepath = os.path.join(INDEXES_DIR, filename)
            with open(filepath, "rb") as f:
                indexes[client_id] = pickle.load(f)
            print(f"  Loaded: {client_id} ({len(indexes[client_id]['chunks'])} chunks)")

    return indexes


def retrieve(query: str, index: dict, top_k: int = 4) -> list[dict]:
    """Find the most relevant document chunks for a query."""
    vectorizer: TfidfVectorizer = index["vectorizer"]
    tfidf_matrix = index["tfidf_matrix"]
    chunks = index["chunks"]

    # Transform query using same vectorizer
    query_vec = vectorizer.transform([query])

    # Calculate cosine similarity
    similarities = cosine_similarity(query_vec, tfidf_matrix).flatten()

    # Get top-k most similar chunks
    top_indices = similarities.argsort()[-top_k:][::-1]

    results = []
    for idx in top_indices:
        if similarities[idx] > 0.01:  # Low threshold to catch misspellings
            results.append({
                "text": chunks[idx]["text"],
                "source": chunks[idx]["source"],
                "score": float(similarities[idx]),
            })

    return results


def get_client_config(client_id: str) -> dict:
    """Load client-specific config (name, system prompt, etc.)."""
    config_file = os.path.join("clients", client_id, "config.txt")
    config = {
        "name": client_id.replace("-", " ").title(),
        "system_prompt": (
            "You are a helpful AI assistant. Answer questions based on the "
            "provided material. If the user misspells a word or uses abbreviations, "
            "try to understand what they mean and find the closest match in the material. "
            "For example, if they ask about 'guatenberg' they likely mean 'Gutenberg'. "
            "Only say you can't find information if there's truly nothing related in the material. "
            "Be concise and helpful."
        ),
    }
    if os.path.exists(config_file):
        with open(config_file, "r") as f:
            for line in f:
                if "=" in line:
                    key, value = line.strip().split("=", 1)
                    config[key.strip()] = value.strip()
    return config


def ask(question: str, index: dict, client_id: str) -> dict:
    """Ask a question — retrieves context and gets LLM answer from Groq."""
    # Retrieve relevant chunks
    relevant_chunks = retrieve(question, index)

    # Fallback: if no relevant chunks found (misspelling, abbreviation, etc.),
    # provide some context from the first few chunks so the LLM can try to help
    if not relevant_chunks:
        fallback_chunks = index["chunks"][:6]
        relevant_chunks = [{"text": c["text"], "source": c["source"], "score": 0.0} for c in fallback_chunks]

    context = "\n\n---\n\n".join(chunk["text"] for chunk in relevant_chunks)

    # Get client config
    config = get_client_config(client_id)

    # Get or create conversation history for this client
    if client_id not in conversation_histories:
        conversation_histories[client_id] = []
    history = conversation_histories[client_id]

    # Build the prompt
    messages = [{"role": "system", "content": config["system_prompt"]}]

    # Add conversation history (last 10 messages = 5 exchanges)
    for msg in history[-10:]:
        messages.append(msg)

    # Add current question with retrieved context
    user_message = f"""Based on this material:

{context}

Question: {question}"""

    messages.append({"role": "user", "content": user_message})

    # Call Groq API (free, fast)
    client = Groq()
    response = client.chat.completions.create(
        model="llama-3.1-8b-instant",
        messages=messages,
        temperature=0.3,
        max_tokens=1024,
    )

    answer = response.choices[0].message.content

    # Save to conversation history
    history.append({"role": "user", "content": question})
    history.append({"role": "assistant", "content": answer})

    # Return answer with sources
    sources = [{"source": c["source"], "score": round(c["score"], 3)} for c in relevant_chunks]

    return {"answer": answer, "sources": sources}
