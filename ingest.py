"""Ingest documents into a TF-IDF search index for RAG."""
import os
import pickle
import sys

from pypdf import PdfReader
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

DOCS_DIR = "documents"
INDEX_FILE = "search_index.pkl"


def load_documents(docs_dir: str) -> list[dict]:
    """Load all .txt and .pdf files, split into chunks."""
    chunks = []

    if not os.path.exists(docs_dir):
        print(f"Error: '{docs_dir}' directory not found.")
        sys.exit(1)

    files = [f for f in os.listdir(docs_dir) if f.endswith((".txt", ".pdf"))]
    if not files:
        print(f"No .txt or .pdf files found in '{docs_dir}/'")
        sys.exit(1)

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


def build_index(chunks: list[dict], index_file: str = INDEX_FILE):
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

    with open(index_file, "wb") as f:
        pickle.dump(index, f)

    return index


def ingest():
    """Main ingestion pipeline."""
    print("\n1. Loading documents...")
    chunks = load_documents(DOCS_DIR)
    print(f"   Loaded {len(chunks)} chunks\n")

    print("2. Building search index...")
    build_index(chunks)
    print(f"   Index saved to '{INDEX_FILE}'\n")

    print("Done! Run: python app.py")


if __name__ == "__main__":
    ingest()
