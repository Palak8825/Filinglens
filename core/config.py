from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://filinglens:filinglens@localhost:5432/filinglens"
    llm_provider: str = "gemini"
    llm_api_key: str = ""
    llm_model: str = "gemini-2.5-flash"
    embed_model: str = "BAAI/bge-m3"
    rerank_model: str = "BAAI/bge-reranker-base"
    data_dir: Path = Path("./data")

    embed_dim: int = 1024  # bge-m3
    chunk_tokens: int = 450
    chunk_overlap: int = 60
    retrieve_k: int = 40
    rerank_k: int = 8


settings = Settings()
