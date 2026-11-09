"""chat_with_llm.py — 通用大模型调用层（第 1 周学习产物）。

职责：
- 从 .env 加载密钥
- 封装千问（DashScope OpenAI 兼容接口）chat completion
- 提供重试/计时装饰器
- 提供高层 chat_json()：强制 JSON 模式 + 解析重试 + 失败降级

任何项目只要 import 这个模块就能调大模型，不用重复写 HTTP 调用。

用法：
    from chat_with_llm import chat_completion, chat_json

    # 普通对话
    reply = chat_completion([
        {"role": "system", "content": "你是助手"},
        {"role": "user", "content": "你好"},
    ])

    # 要结构化 JSON：自动重试 3 次，失败时降级成普通对话
    data, raw_text = chat_json([
        {"role": "system", "content": '只输出 JSON：{"city": "..."}'},
        {"role": "user", "content": "北京天气"},
    ])
    # data 是 dict；raw_text 是原始文本（用于降级/调试）
"""

from __future__ import annotations

import json
import time
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Optional

import requests

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
ENV_FILE_PATH = Path(__file__).resolve().parent / ".env"
LLM_MODEL_NAME = "qwen-plus"
LLM_BASE_URL = "https://ws-ljhpruzo85ykejwp.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
REQUEST_TIMEOUT_SECONDS = 30
DEFAULT_JSON_RETRIES = 3


# ---------------------------------------------------------------------------
# 装饰器
# ---------------------------------------------------------------------------
def retry(attempts: int = 3, delay: float = 1.0) -> Callable:
    """自动重试装饰器：网络抖动时最多重试 attempts 次。

    捕获 requests.RequestException 和 RuntimeError，其他异常直接抛。
    """

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            last_exc: Optional[Exception] = None
            for _ in range(attempts):
                try:
                    return func(*args, **kwargs)
                except (requests.RequestException, RuntimeError) as exc:
                    last_exc = exc
                    time.sleep(delay)
            raise RuntimeError(f"{func.__name__} 重试 {attempts} 次仍失败: {last_exc}") from last_exc

        return wrapper

    return decorator


def timed(func: Callable) -> Callable:
    """计时装饰器：打印每次调用耗时（调试性能用）。"""

    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        start = time.time()
        result = func(*args, **kwargs)
        print(f"[耗时] {func.__name__}: {time.time() - start:.1f}s")
        return result

    return wrapper


# ---------------------------------------------------------------------------
# .env 加载
# ---------------------------------------------------------------------------
def load_env(env_file: Path = ENV_FILE_PATH) -> dict[str, str]:
    """读取 .env 文件为字典。

    .env 格式：每行 KEY=VALUE，# 开头是注释。
    """
    if not env_file.exists():
        raise FileNotFoundError(f"未找到配置文件: {env_file}")
    config: dict[str, str] = {}
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            config[key.strip()] = value.strip()
    return config


_ENV = load_env()
DASHSCOPE_KEY = _ENV.get("DASHSCOPE_API_KEY", "")


# ---------------------------------------------------------------------------
# 底层：chat completion
# ---------------------------------------------------------------------------
@retry(attempts=3, delay=1.0)
def chat_completion(messages: list[dict],
                    temperature: float = 0.7,
                    json_mode: bool = False) -> str:
    """调用千问大模型，返回回复文本。

    Args:
        messages: OpenAI 格式消息列表 [{"role": "...", "content": "..."}]。
        temperature: 采样温度，0=确定，1=发散。
        json_mode: True 时强制返回 JSON（response_format=json_object）。

    Raises:
        RuntimeError: 调用失败或返回格式异常（已由 retry 装饰器重试过 3 次）。
    """
    headers = {
        "Authorization": f"Bearer {DASHSCOPE_KEY}",
        "Content-Type": "application/json",
    }
    payload: dict[str, Any] = {
        "model": LLM_MODEL_NAME,
        "messages": messages,
        "temperature": temperature,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    resp = requests.post(
        f"{LLM_BASE_URL}/chat/completions",
        headers=headers,
        json=payload,
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    resp.raise_for_status()

    try:
        return resp.json()["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as exc:
        raise RuntimeError(f"千问 API 返回格式异常: {resp.text[:200]}") from exc


# ---------------------------------------------------------------------------
# 高层：要 JSON 时的一站式调用（含重试 + 降级）
# ---------------------------------------------------------------------------
def chat_json(messages: list[dict],
              retries: int = DEFAULT_JSON_RETRIES,
              temperature: float = 0.3) -> tuple[Optional[dict], str]:
    """要求大模型返回 JSON，自动重试解析，失败时降级成普通对话。

    Returns:
        (data, raw_text):
        - 解析成功：data 是 dict，raw_text 是原始 JSON 字符串
        - 解析失败且降级成功：data 是 None，raw_text 是普通自然语言回复
        - 全部失败：抛 RuntimeError

    典型用法：
        data, raw = chat_json(system_msgs)
        if data:
            card = render_card(data)
        else:
            bubble = raw   # 降级成纯文本
    """
    last_err: Optional[Exception] = None

    # 第一轮：强制 JSON 模式 + 解析重试
    for attempt in range(retries):
        try:
            content = chat_completion(messages, temperature=temperature, json_mode=True)
            data = json.loads(content)
            if not isinstance(data, dict):
                raise ValueError(f"JSON 顶层不是对象: {type(data).__name__}")
            return data, content
        except (json.JSONDecodeError, ValueError, RuntimeError) as exc:
            last_err = exc
            continue

    # 第二轮：降级成普通自然语言模式（不强制 JSON），保证用户总能看到回复
    try:
        fallback = chat_completion(messages, temperature=0.7, json_mode=False)
        return None, fallback
    except RuntimeError as exc:
        raise RuntimeError(
            f"chat_json 全部失败：JSON 解析最后错误={last_err}；降级调用也失败={exc}"
        ) from exc


# ---------------------------------------------------------------------------
# 直接运行时自测：python chat_with_llm.py
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("=== 普通对话 ===")
    print(chat_completion([
        {"role": "user", "content": "用一句话介绍你自己"},
    ]))

    print("\n=== JSON 模式（自动重试） ===")
    sys_prompt = (
        "你是一个城市提取器。只输出 JSON，格式："
        '{"city": "城市名", "population_rank": 1到5之间的数字}。'
    )
    data, raw = chat_json([
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": "北京有多少人"},
    ])
    if data:
        print("解析成功:", data)
    else:
        print("降级为纯文本:", raw)
