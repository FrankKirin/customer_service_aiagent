from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field, SecretStr
from pathlib import Path

class AgentSettings(BaseSettings):

    _project_root = Path("__file__").resolve().parent

    model_config = SettingsConfigDict(
        env_file=_project_root/".env",
        env_file_encoding="UTF-8",
        extra="ignore"
    )

    # LLM配置
    llm_model: str = Field(default="deepseek-v4-flash", alias="LLM_MODEL")
    llm_base_url: str = Field(alias="LLM_BASE_URL")
    llm_api_key: SecretStr = Field(default=SecretStr(""), alias="LLM_API_KEY")

    llm_timeout: int = Field(default=60, alias="LLM_TIMEOUT")
    max_retry_time: int = Field(default=3, alias="MAX_RETRY_TIME")
    

settings = AgentSettings()
