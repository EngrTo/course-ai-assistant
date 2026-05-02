"""Ingest documents into TF-IDF search indexes for RAG (multi-tenant)."""
import os
import pickle
import sys

from pypdf import PdfReader
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

CLIENTS_DIR = "clients"
INDEXES_DIR = "indexes"


def load_documents(docs_dir: str) -> list[dict]:
    """Load all .txt and .pdf files, split into chunks."""
    chunks = []

    if not os.path.exists(docs_dir):
        print(f"Error: '{docs_dir}' directory not found.")
        return chunks

    files = [f for f in os.listdir(docs_dir) if f.endswith((".txt", ".pdf"))]
    if not files:
        print(f"No .txt or .pdf files found in '{docs_dir}/'")
        return chunks

    for filename in files:
        filepath = os.path.join(docs_dir, filename)
        print(f"  Loading: {filename}")

        try:
            if filename.endswith(".pdf"):
                reader = PdfReader(filepath)
                text = "\n".join(page.extract_text() or "" for page in reader.pages)
            else:
                with open(filepath, "r", encoding="utf-8") as f:
                    text = f.read()
        except Exception as e:
            print(f"  Warning: Failed to load {filename}: {e}")
            continue

        # Split into chunks by paragraphs (double newlines)
        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]

        # Merge small paragraphs, split large ones
        current_chunk = ""
        for para in paragraphs:
            if len(current_chunk) + len(para) < 800:
                current_chunk += "\n\n" + para if current_chunk else para
            else:
                if current_chunk:
                    chunks.append({"text": current_chunk, "source": filename})
                current_chunk = para
        if current_chunk:
            chunks.append({"text": current_chunk, "source": filename})

    return chunks


def build_index(chunks: list[dict], index_file: str):
    """Build TF-IDF index from document chunks and save to disk."""
    texts = [chunk["text"] for chunk in chunks]

    vectorizer = TfidfVectorizer(
        stop_words="english",
        max_features=5000,
        ngram_range=(1, 2),
    )
    tfidf_matrix = vectorizer.fit_transform(texts)

    index = {
        "chunks": chunks,
        "vectorizer": vectorizer,
        "tfidf_matrix": tfidf_matrix,
    }

    os.makedirs(os.path.dirname(index_file), exist_ok=True)
    with open(index_file, "wb") as f:
        pickle.dump(index, f)

    return index


def get_client_dirs() -> list[str]:
    """Get list of client directory names."""
    if not os.path.exists(CLIENTS_DIR):
        return []
    return [d for d in os.listdir(CLIENTS_DIR)
            if os.path.isdir(os.path.join(CLIENTS_DIR, d))]


def ingest_client(client_id: str):
    """Ingest documents for a single client."""
    docs_dir = os.path.join(CLIENTS_DIR, client_id, "documents")
    index_file = os.path.join(INDEXES_DIR, f"{client_id}.pkl")

    print(f"\n--- Ingesting: {client_id} ---")
    print(f"  Documents: {docs_dir}")

    chunks = load_documents(docs_dir)
    if not chunks:
        print(f"  Skipping {client_id} — no documents found.")
        return

    print(f"  Loaded {len(chunks)} chunks")
    build_index(chunks, index_file)
    print(f"  Index saved: {index_file}")


def ingest_all():
    """Ingest documents for all clients."""
    clients = get_client_dirs()
    if not clients:
        print(f"No client folders found in '{CLIENTS_DIR}/'")
        print(f"Create folders like: {CLIENTS_DIR}/school/documents/")
        sys.exit(1)

    os.makedirs(INDEXES_DIR, exist_ok=True)

    print(f"Found {len(clients)} client(s): {', '.join(clients)}")
    for client_id in clients:
        ingest_client(client_id)

    print(f"\nDone! All indexes saved to '{INDEXES_DIR}/'")
    print("Run: python app.py")


if __name__ == "__main__":
    ingest_all()
