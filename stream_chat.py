"""stream_chat.py — 流式对话终端版（周三学习产物）。

运行：python stream_chat.py
- 输入问题回车，模型回复逐字打到终端（SSE 流式）
- 输入 exit / quit / 空行退出
- 自动保留多轮对话历史

和 chat_completion（一次性返回）的区别：这里用 chat_stream 生成器，
后端来一段 delta 就打印一段，不用等整段回答生成完。
"""

import sys

from chat_with_llm import chat_stream


HISTORY_LIMIT = 20  # 最多保留的对话轮数（和网页版一致）


def main() -> None:
    history: list[dict] = []
    print("=== 流式对话终端（输入 exit 退出）===\n")

    while True:
        try:
            question = input("你：").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n再见！")
            break

        if not question or question.lower() in ("exit", "quit"):
            print("再见！")
            break

        history.append({"role": "user", "content": question})
        # 截断历史，避免上下文无限变长
        messages = [{"role": "system", "content": "你是一个简洁友好的中文助手。"}]
        messages.extend(history[-HISTORY_LIMIT:])

        print("助手：", end="", flush=True)
        answer_parts: list[str] = []
        try:
            for chunk in chat_stream(messages, temperature=0.7):
                answer_parts.append(chunk)
                sys.stdout.write(chunk)
                sys.stdout.flush()
        except Exception as exc:  # 终端演示：出错不崩，打印原因后回到输入
            print(f"\n[调用失败] {exc}")
            history.pop()  # 失败的这轮不进历史
            continue

        print("\n")
        history.append({"role": "assistant", "content": "".join(answer_parts)})


if __name__ == "__main__":
    main()
