"""LLM JSON 输出解析。

模型即使开启 JSON mode，也可能返回 Markdown 代码块或前后说明文字。
这里只做格式清理和 JSON 解析，不补造缺失字段。
"""

import json
from typing import Any, Dict, Type, TypeVar

from pydantic import BaseModel

ModelT = TypeVar("ModelT", bound=BaseModel)


def extract_json_object(content: str) -> Dict[str, Any]:
    """从模型输出中提取第一个完整 JSON 对象。"""

    if not isinstance(content, str) or not content.strip():
        raise ValueError("模型输出为空")

    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("模型输出中没有找到 JSON 对象")

    payload = json.loads(text[start:end + 1])
    if not isinstance(payload, dict):
        raise ValueError("模型输出的 JSON 顶层不是对象")
    return payload


def parse_json_model(content: str, model_type: Type[ModelT]) -> ModelT:
    """把模型输出解析成指定 Pydantic 模型。"""

    return model_type.model_validate(extract_json_object(content))
