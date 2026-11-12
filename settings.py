import os
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file_encoding="utf-8",
        extra="ignore",     # Settings中没有定义的配置字段会被忽略
    )

    LLM_BASE_URL: str
    # 测试契约要求缺省时可回退到默认模型；真实环境仍可由 .env 覆盖
    LLM_MODEL: str = "deepseek-ai/DeepSeek-V3.2"
    LLM_API_KEY: str
    LLM_TIME_OUT: int          # 必填, 在 .env.{APP_ENV} 中提供
    MAX_RETRY_TIME: int

    # Embedding模型接入
    EMBEDDING_BASE_URL: str
    EMBEDDING_API_KEY: str
    EMBEDDING_MODEL: str = "BAAI/bge-m3"

    # 图片 caption 可选视觉模型；未配置时 --caption-images 会明确报错
    VISION_MODEL: str | None = None

@lru_cache(maxsize=1)
def get_settings() -> Settings:
    env = os.getenv("APP_ENV", "dev")
    return Settings(_env_file=f".env.{env}")
