# Ask Your Data - Text-to-SQL Chatbot with RAG

[![CI](https://github.com/prachichoudhary2004/Ask-Your-Data/actions/workflows/ci.yml/badge.svg)](https://github.com/prachichoudhary2004/Ask-Your-Data/actions/workflows/ci.yml)

A powerful chatbot that converts natural language questions into SQL queries using Retrieval-Augmented Generation (RAG). Ask questions in plain English and get structured database results instantly.

## 🌟 Features

- **Natural Language Processing**: Convert everyday questions into precise SQL queries
- **RAG Architecture**: Retrieves relevant schema information to generate accurate queries
- **Real-time Validation**: SQL syntax validation before execution
- **Web Interface**: Clean and intuitive Streamlit UI
- **API Access**: FastAPI backend for programmatic access
- **Query History**: Keep track of your previous queries
- **Export Results**: Download query results as CSV files

## 🏗️ Architecture

The RAG Loop in Action:

```mermaid
flowchart TD
    A[👤 User Question] --> B[📋 Retrieve Schema Info]
    B --> C[🤖 Generate SQL for LLM]
    C --> D[✅ Validate + Execute]
    D --> E[📊 Return Results]
    E --> A
```

## 🚀 Quick Start

### Prerequisites

- Python 3.13+
- [uv](https://docs.astral.sh/uv/) package manager
- SQLite database
- [Groq API key](https://console.groq.com/) (free tier available)

### Installation

1. **Clone the repository**

   ```bash
   git clone https://github.com/prachichoudhary2004/Ask-Your-Data.git
   cd Ask-Your-Data
   ```

2. **Install dependencies**

   ```bash
   uv sync
   ```

3. **Set up environment variables**

   Copy the example file and fill in your values:

   ```bash
   cp .env-example .env
   ```

   Edit `.env`:

   ```env
   # Database
   DATABASE_PATH=data/xpto_empresa.db

   # Vector store
   VECTOR_COLLECTION=xpto_database_embeddings
   VECTOR_STORE_DIR=data/vector_store
   TOP_K=5

   # Embedding & rerank models (downloaded automatically)
   EMBED_MODEL=sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2
   RERANK_MODEL=cross-encoder/ms-marco-MiniLM-L-6-v2

   # LLM — powered by Groq
   LLM_MODEL=llama-3.3-70b-versatile
   LLM_API_KEY=your_groq_api_key_here
   LLM_TEMPERATURE=0

   # Disable ChromaDB telemetry
   ANONYMIZED_TELEMETRY=False
   ```

4. **Create the sample database** (optional — skip if you have your own `.db`)

   ```bash
   uv run python scripts/create_xpto_db.py
   ```

5. **Ingest the database into the vector store**

   ```bash
   uv run python ingest_pipeline.py
   ```

### Running the Application

#### Option 1: Web Interface (Streamlit)

```bash
uv run streamlit run ui.py
```

Access the web interface at: `http://localhost:8501`

#### Option 2: API Server (FastAPI)

```bash
uv run python api.py
```

API documentation available at: `http://localhost:8000/docs`

#### Option 3: Direct Query Service

```python
from query_service import query, QueryRequest

request = QueryRequest(question="How many customers do we have?")
result = query(request)
print(result)
```

## 📁 Project Structure

```text
Ask-Your-Data/
├── api.py                  # FastAPI server
├── ui.py                   # Streamlit web interface
├── query_service.py        # Main query processing service
├── retriever_node.py       # RAG retrieval component
├── sql_node.py            # SQL generation with LLM (Groq)
├── sql_validator.py       # SQL syntax validation
├── sql_executor.py        # Database query execution
├── ingest_pipeline.py     # Vector database creation
├── .env-example           # Environment variable template
├── data/
│   └── vector_store/      # ChromaDB vector storage
└── scripts/
    └── create_xpto_db.py  # Sample database creation
```

## 🔧 Configuration

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `LLM_MODEL` | Groq model name | `llama-3.3-70b-versatile` |
| `LLM_API_KEY` | Groq API key | Required |
| `LLM_TEMPERATURE` | LLM temperature | `0` |
| `DATABASE_PATH` | Path to SQLite database | `data/xpto_empresa.db` |
| `EMBED_MODEL` | HuggingFace embedding model | `paraphrase-multilingual-MiniLM-L12-v2` |
| `RERANK_MODEL` | CrossEncoder rerank model | `ms-marco-MiniLM-L-6-v2` |
| `VECTOR_COLLECTION` | ChromaDB collection name | `xpto_database_embeddings` |
| `VECTOR_STORE_DIR` | ChromaDB persist directory | `data/vector_store` |
| `TOP_K` | Number of docs to retrieve | `5` |
| `ANONYMIZED_TELEMETRY` | Disable ChromaDB telemetry | `False` |

### Supported Query Types

- **Aggregation**: "How many sales were made?"
- **Filtering**: "Show customers from New York"
- **Sorting**: "Top 10 products by revenue"
- **Grouping**: "Sales by month"
- **Joins**: "Customer orders with product details"

## 📊 Example Usage

### Web Interface

1. Open `http://localhost:8501`
2. Type your question: "What are our top-selling products?"
3. View the generated SQL and results
4. Download results as CSV if needed

### API Request

```bash
curl -X POST "http://localhost:8000/query" \
     -H "Content-Type: application/json" \
     -d '{"question": "How many orders were placed last month?", "show_sql": true}'
```

### Response

```json
{
  "success": true,
  "sql": "SELECT COUNT(*) as total_orders FROM orders WHERE DATE(created_at) >= DATE('now', '-1 month')",
  "cols": ["total_orders"],
  "rows": [{"total_orders": 150}]
}
```

## 🛡️ Security Features

- **SQL Injection Protection**: Only SELECT statements allowed
- **Table Validation**: Queries limited to allowed tables
- **Syntax Validation**: SQL validated before execution
- **Read-only Access**: No INSERT/UPDATE/DELETE operations

## 🤝 Contributing

1. Fork the repository
2. Create your feature branch (`git checkout -b feature/AmazingFeature`)
3. Commit your changes (`git commit -m 'Add some AmazingFeature'`)
4. Push to the branch (`git push origin feature/AmazingFeature`)
5. Open a Pull Request

## 🆘 Troubleshooting

### Common Issues

#### API Connection Failed

- Ensure the API server is running on port 8000
- Check if your API key is correctly set in the `.env` file

#### No Results Returned

- Verify your database path is correct
- Check if the vector store is properly created
- Ensure your question relates to existing table schemas

#### SQL Generation Errors

- Make sure your database schema is properly indexed in the vector store
- Try rephrasing your question more specifically

### Getting Help

- Open an issue on GitHub
- Check existing issues for solutions
- Review the API documentation at `/docs`

---
 
Made with ❤️ by [Prachi Choudhary](https://github.com/prachichoudhary2004) using LangChain, Streamlit, and FastAPI
