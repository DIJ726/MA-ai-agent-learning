"""调用千问 API 并返回一句话的最小示例。

密钥存放在同目录下的 .env 文件中（DASHSCOPE_API_KEY=sk-...），
禁止将密钥硬编码进代码，禁止将 .env 提交到 Git。
"""

from pathlib import Path

import requests

BASE_URL = "https://ws-ljhpruzo85ykejwp.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
MODEL_NAME = "qwen-plus"
TIMEOUT_SECONDS = 30
ENV_FILE_PATH = Path(__file__).resolve().parent / ".env"


def load_api_key(env_file: Path = ENV_FILE_PATH) -> str:
    """从 .env 文件中读取 API 密钥。

    Args:
        env_file: .env 文件路径，默认取脚本同目录下的 .env。

    Returns:
        API 密钥字符串。

    Raises:
        FileNotFoundError: .env 文件不存在。
        RuntimeError: .env 中未配置 DASHSCOPE_API_KEY。
    """
    if not env_file.exists():
        raise FileNotFoundError(f"未找到密钥文件: {env_file}")

    env_config = {}
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            env_config[key.strip()] = value.strip()

    api_key = env_config.get("DASHSCOPE_API_KEY", "")
    if not api_key:
        raise RuntimeError("请在 .env 中配置 DASHSCOPE_API_KEY")
    return api_key


def chat(prompt: str) -> str:
    """调用千问 API，返回模型对 prompt 的回答。

    Args:
        prompt: 用户输入的提示词。

    Returns:
        模型返回的文本内容。

    Raises:
        RuntimeError: 接口调用失败或返回格式异常。
    """
    headers = {
        "Authorization": f"Bearer {load_api_key()}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": MODEL_NAME,
        "messages": [
            {"role": "user", "content": prompt},
        ],
    }

    try:
        response = requests.post(
            f"{BASE_URL}/chat/completions",
            headers=headers,
            json=payload,
            timeout=TIMEOUT_SECONDS,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise RuntimeError(f"请求千问 API 失败: {exc}") from exc

    result = response.json()
    try:
        return result["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as exc:
        raise RuntimeError(f"接口返回格式异常: {result}") from exc


def main() -> None:
    """程序入口：发送一句话请求并打印模型回复。"""
    reply = chat("用一句话介绍你自己。")
    print(reply)


if __name__ == "__main__":
    main()
