"""token_cost.py — token 计数 + 成本估算小工具（周二学习产物）。

演示三件事：
1. 本地粗估一段文本的 token 数（不调 API）
2. 调一次真实 API，从 response 里拿官方 usage
3. 按 qwen-plus 单价算这次调用花了多少钱

用法：
    python token_cost.py                # 用默认示例跑一遍
    python token_cost.py "我的问题"      # 用你自己的问题跑一遍
"""

from __future__ import annotations

import re
import sys

from chat_with_llm import chat_with_usage, LLM_MODEL_NAME

# qwen-plus 公开参考价（单位：元 / 千 token）
# 注意：真实单价以阿里云百炼控制台账单为准，不同时期/不同套餐会变。
PRICE_PER_1K_INPUT = 0.0008   # 输入
PRICE_PER_1K_OUTPUT = 0.002    # 输出


def estimate_tokens(text: str) -> int:
    """本地粗估 token 数（不调 API）。

    经验法则（千问 tokenizer 大致符合）：
    - 1 个汉字 ≈ 1.5 token
    - 1 个英文单词 ≈ 1.3 token
    - 标点/空格另算
    """
    chinese_chars = len(re.findall(r"[\u4e00-\u9fff]", text))
    english_words = len(re.findall(r"[a-zA-Z]+", text))
    other = len(text) - chinese_chars - len(re.findall(r"[a-zA-Z0-9]+", text))
    return int(chinese_chars * 1.5 + english_words * 1.3 + max(other, 0) * 0.5)


def calculate_cost(prompt_tokens: int, completion_tokens: int) -> dict:
    """按单价算钱，返回一分钱到厘的明细。"""
    input_cost = prompt_tokens / 1000 * PRICE_PER_1K_INPUT
    output_cost = completion_tokens / 1000 * PRICE_PER_1K_OUTPUT
    return {
        "input_cost": input_cost,
        "output_cost": output_cost,
        "total_cost": input_cost + output_cost,
        "per_1k_input": PRICE_PER_1K_INPUT,
        "per_1k_output": PRICE_PER_1K_OUTPUT,
    }


def run_once(question: str) -> None:
    print(f"=== 问题 ===")
    print(question)
    print()

    # 1. 本地粗估
    est_input = estimate_tokens(question)
    print(f"[本地粗估] 输入 token ≈ {est_input}")

    # 2. 真实调用
    messages = [{"role": "user", "content": question}]
    answer, usage = chat_with_usage(messages, temperature=0.3)

    real_prompt = usage.get("prompt_tokens", 0)
    real_completion = usage.get("completion_tokens", 0)
    real_total = usage.get("total_tokens", 0)

    print(f"[API 真实] 输入 {real_prompt} | 输出 {real_completion} | 合计 {real_total}")
    print(f"[API 真实] 模型回复：{answer[:80]}{'...' if len(answer) > 80 else ''}")
    print()

    # 3. 成本
    cost = calculate_cost(real_prompt, real_completion)
    print(f"[成本] qwen-plus @ 输入 {cost['per_1k_input']} 元/千tok，输出 {cost['per_1k_output']} 元/千tok")
    print(f"  输入费用：{cost['input_cost']:.6f} 元")
    print(f"  输出费用：{cost['output_cost']:.6f} 元")
    print(f"  本次合计：{cost['total_cost']:.6f} 元（约 {cost['total_cost']*1000:.2f} 厘）")
    print()

    # 4. 外推：一天调 100 次 / 一个月
    daily = cost["total_cost"] * 100
    monthly = daily * 30
    print(f"[外推] 如果每天调 100 次这种问题：")
    print(f"  每天约 {daily:.4f} 元，每月约 {monthly:.2f} 元")
    print()


if __name__ == "__main__":
    q = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else (
        "用一句话解释什么是大模型的 token。"
    )
    print(f"模型：{LLM_MODEL_NAME}")
    print()
    run_once(q)
