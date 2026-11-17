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
    llm_fallback_models: str = "gemini-3.5-flash,gemini-3.1-flash-lite"  # gemini-2.5-* retired for new keys (Nov 2026)
    llm_timeout_s: int = 60
    # Eval grader. Deliberately a different model from the one answering, so no model grades its own
    # output (self-preference bias), and not one any eval arm answers with. JUDGE_MODEL in .env overrides it.
    judge_model: str = "gemini-3-flash-preview"  # 3.6-flash was overloaded (503) for a day; this one graded the report

    # Small, fast, English. 384-dim. Swap to BAAI/bge-m3 (embed_dim 1024) only on a GPU.
    embed_model: str = "BAAI/bge-small-en-v1.5"
    embed_dim: int = 384
    # 90 MB cross-encoder, ~2.5 s for 40 candidates on CPU. Lifted a buried answer chunk from rank 16
    # into the top 8 in the eval; bge-reranker-base (1.1 GB) was too heavy for a CPU-only demo.
    rerank_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"
    rerank: bool = True  # RERANK=0 in .env or the environment disables it

    data_dir: Path = Path("./data")

    chunk_tokens: int = 450
    chunk_overlap: int = 60
    retrieve_k: int = 40
    rerank_k: int = 8


settings = Settings()
