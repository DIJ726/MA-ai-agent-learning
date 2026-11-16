"""llm_demo.py — 第一个 LLM 调用 demo（第 2 周周六产物）。

对应 Hello-Agents 第 1-2 章：先跑通「应用 → HTTP 请求 → LLM → 文本回复」这条最小链路。
后续第 3 周手写的 ReAct Agent，就是在这个最小调用之上再加三件东西：
  - 执行：让模型能调用工具（Function Calling）
  - 规划：Thought → Action → Observation 循环
  - 记忆：把多轮历史存下来反复带上

运行：python llm_demo.py
"""

import time

from chat_with_llm import LLM_BASE_URL, LLM_MODEL_NAME, chat_with_usage


def main() -> None:
    # 1. 组装 OpenAI 格式消息：system 定人设，user 是问题
    messages = [
        {"role": "system", "content": "你是一个简洁的中文助手，回答不超过两句话。"},
        {"role": "user", "content": "用一句话说明：大语言模型本身有没有记忆？"},
    ]

    print(f"模型: {LLM_MODEL_NAME}")
    print(f"接口: {LLM_BASE_URL}\n")

    # 2. 发请求并拿到回复文本 + token 用量
    start = time.time()
    reply, usage = chat_with_usage(messages, temperature=0.7)
    elapsed = time.time() - start

    # 3. 展示结果
    print("助手:", reply)
    print(f"\n耗时: {elapsed:.1f}s")
    print("token 用量:", usage)


if __name__ == "__main__":
    main()
