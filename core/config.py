from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # SQLite — no server, no Docker. File lives under data/.
    database_url: str = "sqlite+pysqlite:///./data/filinglens.db"
    llm_provider: str = "gemini"
    llm_api_key: str = ""
    # flash-lite: 1-2 s per answer when benchmarked 2026-11-14; newer flash models were 503-ing.
    llm_model: str = "gemini-3.5-flash-lite"
    llm_fallback_models: str = "gemini-3.5-flash,gemini-2.5-flash-lite"
    llm_timeout_s: int = 60

    # Small, fast, English. 384-dim. Swap to BAAI/bge-m3 (embed_dim 1024) only on a GPU.
    embed_model: str = "BAAI/bge-small-en-v1.5"
    embed_dim: int = 384
    rerank_model: str = "BAAI/bge-reranker-base"

    data_dir: Path = Path("./data")

    chunk_tokens: int = 450
    chunk_overlap: int = 60
    retrieve_k: int = 40
    rerank_k: int = 8


settings = Settings()
