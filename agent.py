"""RAG Q&A agent using Groq (free) + TF-IDF retrieval."""
import os
import pickle

from dotenv import load_dotenv
from groq import Groq
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

load_dotenv()

INDEX_FILE = "search_index.pkl"

# Conversation history (in-memory per session)
conversation_history: list[dict] = []


def load_index(index_file: str = INDEX_FILE) -> dict:
    """Load the pre-built search index."""
    if not os.path.exists(index_file):
        raise FileNotFoundError(
            f"Search index not found. Run 'python ingest.py' first."
        )
    with open(index_file, "rb") as f:
        return pickle.load(f)


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
        if similarities[idx] > 0.05:  # Minimum relevance threshold
            results.append({
                "text": chunks[idx]["text"],
                "source": chunks[idx]["source"],
                "score": float(similarities[idx]),
            })

    return results


def ask(question: str, index: dict) -> dict:
    """Ask a question — retrieves context and gets LLM answer from Groq."""
    # Retrieve relevant chunks
    relevant_chunks = retrieve(question, index)
    context = "\n\n---\n\n".join(chunk["text"] for chunk in relevant_chunks)

    # Build the prompt
    system_prompt = (
        "You are a helpful AI course assistant. Answer questions based on the "
        "provided course material. If the answer isn't in the material, say so. "
        "Be concise and helpful. Use the conversation history for context."
    )

    # Include last 5 conversation exchanges for context
    messages = [{"role": "system", "content": system_prompt}]

    # Add conversation history (last 10 messages = 5 exchanges)
    for msg in conversation_history[-10:]:
        messages.append(msg)

    # Add current question with retrieved context
    user_message = f"""Based on this course material:

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
    conversation_history.append({"role": "user", "content": question})
    conversation_history.append({"role": "assistant", "content": answer})

    # Return answer with sources
    sources = [{"source": c["source"], "score": round(c["score"], 3)} for c in relevant_chunks]

    return {"answer": answer, "sources": sources}
