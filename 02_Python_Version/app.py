"""Flask 演示应用。"""

from __future__ import annotations

from flask import Flask, jsonify, request, send_from_directory

from src.agent.agent import CampusServiceAgent
from src.config import settings
from src.ingestion.chunker import chunk_documents
from src.ingestion.loader import load_documents
from src.llm.client import DeterministicGroundedClient
from src.memory.conversation import ConversationMemory
from src.rag.pipeline import RAGPipeline
from src.retrieval.embedder import HashingEmbedder
from src.retrieval.retriever import HybridRetriever
from src.tools.registry import build_default_registry


def build_agent() -> CampusServiceAgent:
    documents = load_documents(settings.docs_dir)
    chunks = chunk_documents(documents, settings.chunk_size, settings.chunk_overlap)
    embedder = HashingEmbedder(settings.embedding_dimension)
    retriever = HybridRetriever(chunks, embedder)
    rag = RAGPipeline(
        retriever,
        DeterministicGroundedClient(),
        top_k=settings.top_k,
        threshold=settings.similarity_threshold,
    )
    tools = build_default_registry(settings.database_path)
    memory = ConversationMemory(settings.memory_turns)
    return CampusServiceAgent(rag, tools, memory, settings.max_agent_steps)


def create_app() -> Flask:
    app = Flask(__name__, static_folder="static")
    app.config["MAX_CONTENT_LENGTH"] = 16 * 1024
    agent = build_agent()

    @app.get("/")
    def index():
        return send_from_directory(app.static_folder, "index.html")

    @app.get("/api/health")
    def health():
        return jsonify({"ok": True, "mode": settings.llm_mode, "model": "deterministic-grounded-v1"})

    @app.post("/api/chat")
    def chat():
        payload = request.get_json(silent=True) or {}
        question = str(payload.get("question", ""))[:1000]
        session_id = str(payload.get("session_id", "demo"))[:80]
        return jsonify(agent.respond(question, session_id).to_dict())

    @app.post("/api/reset")
    def reset():
        payload = request.get_json(silent=True) or {}
        session_id = str(payload.get("session_id", "demo"))[:80]
        agent.memory.clear(session_id)
        return jsonify({"ok": True})

    return app


if __name__ == "__main__":
    create_app().run(host="127.0.0.1", port=7860, debug=False)
