# Course Q&A AI Agent

AI-powered chatbot that answers student questions based on course materials. Built with LangChain, ChromaDB, OpenAI, and Flask.

## Quick Start

### 1. Set up environment

```bash
cd course-qa-agent
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Add your OpenAI API key

```bash
cp .env.example .env
# Edit .env and replace sk-your-key-here with your actual key
```

> Get a key at: https://platform.openai.com/api-keys

### 3. Add course documents

Place `.pdf` or `.txt` files in the `documents/` folder. A sample course is included.

### 4. Ingest documents

```bash
python ingest.py
```

This reads your documents, splits them into chunks, creates embeddings, and stores them in ChromaDB.

### 5. Start the app

```bash
python app.py
```

Open http://localhost:5000 in your browser and start asking questions!

## Project Structure

```
course-qa-agent/
├── .env.example        # API key template
├── .gitignore          # Files to exclude from git
├── requirements.txt    # Python dependencies
├── ingest.py           # Document ingestion → vector DB
├── agent.py            # RAG agent logic
├── app.py              # Flask web server
├── documents/          # Put course PDFs/text files here
│   └── sample-course.txt
├── chroma_db/          # Vector database (auto-created)
└── templates/
    └── index.html      # Chat UI
```

## How It Works

1. **Ingest:** Documents are split into chunks and converted to embeddings (numerical representations)
2. **Store:** Embeddings are stored in ChromaDB (a vector database)
3. **Query:** When a user asks a question, it's converted to an embedding and matched against stored chunks
4. **Answer:** The top matching chunks + the question are sent to GPT-4o-mini, which generates an accurate answer

## Costs

- **OpenAI API:** ~$0.01-0.05 per conversation (GPT-4o-mini is very cheap)
- **Embeddings:** ~$0.001 per document ingestion
- **Hosting:** Free locally, $5-7/mo on Railway or Render

## Customization

- Change the LLM model in `agent.py` (line with `model=`)
- Adjust chunk size in `ingest.py` for longer/shorter context
- Modify the UI in `templates/index.html`
- Add system prompts to customize the agent's personality
