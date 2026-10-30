import os
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file_encoding="utf-8",
        extra="ignore",     # Settings中没有定义的配置字段会被忽略
    )

    LLM_BASE_URL: str
    LLM_MODEL: str = "deepseek-ai/DeepSeek-V3.2"
    LLM_API_KEY: str
    LLM_TIME_OUT: int
    MAX_RETRY_TIME: int

    # Embedding模型接入
    EMBEDDING_BASE_URL: str
    EMBEDDING_API_KEY: str
    EMBEDDING_MODEL: str = "BAAI/bge-m3"

@lru_cache(maxsize=1)
def get_settings() -> Settings:
    env = os.getenv("APP_ENV", "dev")
    return Settings(_env_file=f".env.{env}")
