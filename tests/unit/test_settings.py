# 读取指定的配置文件
# 未设置APP_ENV读取.env.dev, 模型使用默认值
# 系统环境变量覆盖。env中的同名配置
# 缺少必填项，抛出ValidationError
# 缓存，get_settings()首次调用后复用缓存结果

import pytest
from pydantic import ValidationError

from settings import get_settings


@pytest.fixture(autouse=True)
def clean_settings_env(monkeypatch):
    """清理测试环境变量，并在每个测试前后清空配置缓存。"""
    env_keys = [
        "APP_ENV",
        "LLM_BASE_URL",
        "LLM_MODEL",
        "LLM_API_KEY",
        "LLM_TIME_OUT",
        "MAX_RETRY_TIME",
        "EMBEDDING_BASE_URL",
        "EMBEDDING_API_KEY",
        "EMBEDDING_MODEL",
    ]
    # 删除环境变量
    for env_key in env_keys:
        monkeypatch.delenv(env_key, raising=False)

    get_settings.cache_clear()  # 清缓存
    yield
    get_settings.cache_clear()  # 测试后再清

def write_valid_env_file(tmp_path, filename):
    # 创建一个临时环境配置文件, 并往里面写入一些配置的key value
    env_file = tmp_path / filename

    env_file.write_text(
        "\n".join([
            "LLM_BASE_URL=https://file.example/v1",
            "LLM_API_KEY=test-llm-key",
            "LLM_TIME_OUT=30",
            "MAX_RETRY_TIME=3",
            "EMBEDDING_BASE_URL=https://embedding.example/v1",
            "EMBEDDING_API_KEY=test-embedding-key",
            "UNKNOWN_ENV_KEY=ignored",
        ]),
        encoding="utf-8",
    )

    return env_file

def test_get_settings_loads_test_env_file(monkeypatch, tmp_path):
    """测试读取.env.test, 并验证默认模型值和类型转换"""
    write_valid_env_file(tmp_path, ".env.test")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("APP_ENV", "test")
    settings = get_settings()

    assert settings.LLM_BASE_URL == "https://file.example/v1"
    assert settings.LLM_API_KEY == "test-llm-key"
    assert settings.LLM_TIME_OUT == 30
    assert isinstance(settings.LLM_TIME_OUT, int)
    assert settings.MAX_RETRY_TIME == 3

    assert settings.EMBEDDING_BASE_URL == "https://embedding.example/v1"
    assert settings.EMBEDDING_API_KEY == "test-embedding-key"

    assert settings.LLM_MODEL == "deepseek-ai/DeepSeek-V3.2"
    assert settings.EMBEDDING_MODEL == "BAAI/bge-m3"

def test_get_settings_uses_dev_file_by_default(monkeypatch, tmp_path):
    """测试未设置 APP_ENV 时，默认读取 .env.dev。"""

    write_valid_env_file(tmp_path, ".env.dev")
    monkeypatch.chdir(tmp_path)

    settings = get_settings()

    assert settings.LLM_BASE_URL == "https://file.example/v1"

def test_get_settings_environment_variable_has_priority(
    monkeypatch, tmp_path
):
    """测试系统环境变量优先于 .env 文件中的同名配置。"""
    write_valid_env_file(tmp_path, ".env.test")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("APP_ENV", "test")
    monkeypatch.setenv(
        "LLM_BASE_URL",
        "https://environment.example/v1",
    )

    settings = get_settings()

    assert settings.LLM_BASE_URL == "https://environment.example/v1"

def test_get_settings_raises_validation_error_when_required_fields_missing(
    monkeypatch, tmp_path
):
    """测试缺少必填配置时，Pydantic 是否抛出校验异常。"""
    (tmp_path / ".env.test").write_text("", encoding="utf-8")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("APP_ENV", "test")

    with pytest.raises(ValidationError) as exc_info:  # 断言, 要求with块里的带阿米必须抛出ValidationError
        get_settings()

    # errors会返回字典：type, loc, msg
    missing_fields = {
        error["loc"][0]
        for error in exc_info.value.errors()
        if error["loc"] and error["type"] == "missing"
    }

    # LLM_MODEL和EMBEDDING_MODEL不在名单里，因为有默认值
    required_fields = {
        "LLM_BASE_URL",
        "LLM_API_KEY",
        "LLM_TIME_OUT",
        "MAX_RETRY_TIME",
        "EMBEDDING_BASE_URL",
        "EMBEDDING_API_KEY",
    }

    assert required_fields.issubset(missing_fields)

def test_get_settings_uses_cached_result(monkeypatch, tmp_path):
    """测试 get_settings 是否复用 lru_cache 缓存结果。"""
    # 清理掉缓存->设置好缓存->查看是否用了缓存结果
    write_valid_env_file(tmp_path, ".env.test")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("APP_ENV", "test")

    settings_before_change = get_settings()

    # 首次调用后再修改环境变量。
    monkeypatch.setenv(
        "LLM_BASE_URL",
        "https://changed.example/v1",
    )

    settings_after_change = get_settings()

    assert settings_after_change is settings_before_change
    assert settings_after_change.LLM_BASE_URL == "https://file.example/v1"