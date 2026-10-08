"""集中配置与日志初始化。"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


_load_dotenv(PROJECT_ROOT / ".env")


@dataclass(frozen=True)
class Settings:
    project_root: Path = PROJECT_ROOT
    docs_dir: Path = PROJECT_ROOT / "docs"
    database_path: Path = PROJECT_ROOT / "data" / "campus.db"
    log_path: Path = PROJECT_ROOT / "logs" / "campus-agent.log"
    llm_base_url: str = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1")
    llm_api_key: str = os.getenv("LLM_API_KEY", "")
    llm_model: str = os.getenv("LLM_MODEL", "gpt-4.1-mini")
    llm_mode: str = os.getenv("LLM_MODE", "offline")
    llm_timeout_seconds: int = int(os.getenv("LLM_TIMEOUT_SECONDS", "30"))
    chunk_size: int = int(os.getenv("CHUNK_SIZE", "260"))
    chunk_overlap: int = int(os.getenv("CHUNK_OVERLAP", "40"))
    embedding_dimension: int = int(os.getenv("EMBEDDING_DIMENSION", "512"))
    top_k: int = int(os.getenv("TOP_K", "3"))
    similarity_threshold: float = float(os.getenv("SIMILARITY_THRESHOLD", "0.25"))
    max_agent_steps: int = int(os.getenv("MAX_AGENT_STEPS", "3"))
    memory_turns: int = int(os.getenv("MEMORY_TURNS", "6"))


@dataclass(frozen=True)
class EmbeddingSettings:
    """API 模式的云端语义向量；未配置密钥时为 None，继续使用本地哈希向量。"""

    api_key: str
    base_url: str
    model: str
    dimension: int
    batch_size: int
    timeout_seconds: int
    semantic_weight: float
    threshold: float


def embedding_settings() -> EmbeddingSettings | None:
    key = os.getenv("EMBEDDING_API_KEY", "").strip()
    base_url = os.getenv("EMBEDDING_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1").strip()
    local = base_url.startswith(("http://127.0.0.1", "http://localhost", "http://[::1]"))
    if not key and not local:
        return None
    return EmbeddingSettings(
        api_key=key,
        base_url=base_url,
        model=os.getenv("EMBEDDING_MODEL", "text-embedding-v4").strip(),
        dimension=int(os.getenv("EMBEDDING_API_DIMENSION", "1024")),
        batch_size=int(os.getenv("EMBEDDING_BATCH_SIZE", "10")),
        timeout_seconds=int(os.getenv("EMBEDDING_TIMEOUT_SECONDS", "20")),
        semantic_weight=float(os.getenv("SEMANTIC_WEIGHT", "0.7")),
        # 0.40 由 semantic_check 对 text-embedding-v4 校准：22 道正例最低 0.415，库外题 4/6 低于阈值。
        threshold=float(os.getenv("SEMANTIC_THRESHOLD", "0.40")),
    )


settings = Settings()


def configure_logging() -> logging.Logger:
    settings.log_path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("campus_agent")
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s %(message)s", "%Y-%m-%d %H:%M:%S"
    )
    file_handler = logging.FileHandler(settings.log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    return logger


logger = configure_logging()
