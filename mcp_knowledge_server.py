"""mcp_knowledge_server.py — 暴露知识库 Resource 与 Prompt 的 MCP Server（第 4 周周四产物）。

今天任务：掌握 MCP 的另外两种原语——
  - Resource（资源）：把知识库数据作为可读取资源暴露给 Client（对应 GET，被动数据）。
  - Prompt（提示）：注册按名字调用、可带参数的消息模板（类似斜杠命令）。

Resource 两类内容：
  - 文本资源：``text/markdown``，直接读取 ``data/knowledge/agent-guide.md``。
  - 二进制资源：``application/pdf``，读取约定路径下的 PDF（blob，返回 bytes）；
    文件暂缺时返回空 bytes 兜底，不抛异常。把真实 PDF 放到该路径即自动作为二进制资源暴露。

Prompt 两个模板：
  - ``knowledge_qa(topic)``：基于知识库回答某主题，先引用依据再给结论。
  - ``order_insight(customer)``：结合订单数据对某客户做简短点评。

运行方式：
  python mcp_knowledge_server.py          # 内存 Client 自测（列/读资源 + 列/取提示）
  python mcp_knowledge_server.py stdio    # 标准输入输出，给 Inspector / 外部 Client
  python mcp_knowledge_server.py http     # streamable-http，127.0.0.1:8769/mcp
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
from typing import Any

from mcp import Client
from mcp.server import MCPServer

BASE_DIR = Path(__file__).resolve().parent
KNOWLEDGE_DIR = BASE_DIR / "data" / "knowledge"
GUIDE_MD = KNOWLEDGE_DIR / "agent-guide.md"
GUIDE_PDF = KNOWLEDGE_DIR / "agent-guide.pdf"

server = MCPServer("knowledge-base")


# ---------------------------------------------------------------------------
# Resource：知识库数据
# ---------------------------------------------------------------------------
@server.resource(
    "knowledge://agent-guide",
    name="agent-guide",
    title="Agent 入门知识库（文本）",
    description="Agent / MCP 入门要点的 Markdown 文本，可直接喂进上下文阅读。",
    mime_type="text/markdown",
)
def read_agent_guide() -> str:
    """读取知识库 Markdown 文本；文件缺失时返回兜底说明，不抛异常。"""
    if not GUIDE_MD.exists():
        return "（知识库文本文件尚未提供：data/knowledge/agent-guide.md）"
    return GUIDE_MD.read_text(encoding="utf-8")


@server.resource(
    "knowledge://agent-guide.pdf",
    name="agent-guide-pdf",
    title="Agent 入门知识库（PDF 二进制）",
    description="同一份知识库的 PDF 二进制版本（blob）；文件暂缺时为空内容。",
    mime_type="application/pdf",
)
def read_agent_guide_pdf() -> bytes:
    """读取约定 PDF 的二进制内容；文件缺失时返回空 bytes 兜底。"""
    if not GUIDE_PDF.exists():
        return b""
    return GUIDE_PDF.read_bytes()


# ---------------------------------------------------------------------------
# Prompt：可复用消息模板
# ---------------------------------------------------------------------------
@server.prompt(
    name="knowledge_qa",
    title="知识库问答提示词",
    description="基于已提供的知识库回答某主题：先引用依据，再给简明结论；无依据时明确说明。",
)
def knowledge_qa(topic: str) -> str:
    """生成一条知识库问答的 user 消息。

    Args:
        topic: 用户关心的主题，如 MCP、ReAct、工具调用。
    """
    return (
        f"请基于已提供的知识库内容回答关于「{topic}」的问题："
        "先引用知识库中的依据，再给出简明结论；知识库没有依据时要明确说明，不要臆造。"
    )


@server.prompt(
    name="order_insight",
    title="客户订单点评提示词",
    description="结合订单库数据，对指定客户的下单情况做简短点评与建议。",
)
def order_insight(customer: str) -> str:
    """生成一条客户订单点评的 user 消息。

    Args:
        customer: 客户名，如 张伟。
    """
    return (
        f"请结合订单库中「{customer}」的历史订单（商品、金额、状态）做简短点评："
        "消费偏好、近期活跃度，以及一条可执行的跟进建议；数据不足时先说明缺哪些订单。"
    )


# ---------------------------------------------------------------------------
# 自测辅助：从结果中取文本
# ---------------------------------------------------------------------------
def _resource_text(result: Any) -> str:
    """从 ReadResourceResult 的 contents 里拼接文本（文本资源取 .text，二进制取长度）。"""
    parts: list[str] = []
    for content in getattr(result, "contents", None) or []:
        text = getattr(content, "text", None)
        if text is not None:
            parts.append(text)
        else:
            blob = getattr(content, "blob", "") or ""
            parts.append(f"<binary blob, {len(blob)} bytes>")
    return "\n".join(parts)


def _prompt_text(result: Any) -> str:
    """从 GetPromptResult 的 messages 里拼接各消息文本。"""
    parts: list[str] = []
    for message in getattr(result, "messages", None) or []:
        content = getattr(message, "content", None)
        parts.append(getattr(content, "text", str(content)))
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# 离线自测：内存直连 Server
# ---------------------------------------------------------------------------
async def _self_test() -> None:
    """用内存 Client 直连：列出/读取资源，列出/取提示模板，验证链路打通。"""
    async with Client(server) as client:
        # 1. 列出资源
        resources = await client.list_resources()
        uris = [r.uri for r in resources.resources]
        print("=== 已注册资源 ===")
        for r in resources.resources:
            print(f"- {r.uri}（{r.mime_type}）: {r.description}")
        assert "knowledge://agent-guide" in uris, "文本知识库资源未注册"
        assert "knowledge://agent-guide.pdf" in uris, "PDF 二进制资源未注册"

        # 2. 读取文本资源并断言关键词
        guide = await client.read_resource("knowledge://agent-guide")
        guide_text = _resource_text(guide)
        print("\n=== read knowledge://agent-guide ===")
        print(guide_text)
        assert "ReAct" in guide_text and "MCP" in guide_text, "知识库文本缺少核心概念"

        # 3. 读取 PDF 资源：文件暂缺时为空 blob，不抛异常
        pdf = await client.read_resource("knowledge://agent-guide.pdf")
        pdf_text = _resource_text(pdf)
        print("\n=== read knowledge://agent-guide.pdf ===")
        print(pdf_text)

        # 4. 列出提示模板
        prompts = await client.list_prompts()
        prompt_names = [p.name for p in prompts.prompts]
        print("\n=== 已注册提示 ===")
        for p in prompts.prompts:
            args = [a.name for a in (p.arguments or [])]
            print(f"- {p.name}: {p.description}（参数：{args}）")
        assert "knowledge_qa" in prompt_names, "knowledge_qa 提示未注册"
        assert "order_insight" in prompt_names, "order_insight 提示未注册"

        # 5. 取提示模板并断言参数替换生效
        qa = await client.get_prompt("knowledge_qa", {"topic": "MCP"})
        qa_text = _prompt_text(qa)
        print("\n=== get_prompt knowledge_qa(topic=MCP) ===")
        print(qa_text)
        assert "MCP" in qa_text, "提示模板未替换主题参数"

        insight = await client.get_prompt("order_insight", {"customer": "张伟"})
        insight_text = _prompt_text(insight)
        print("\n=== get_prompt order_insight(customer=张伟) ===")
        print(insight_text)
        assert "张伟" in insight_text, "提示模板未替换客户参数"

        print("\n知识库 MCP Server 自测通过。")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="MCP knowledge base server")
    parser.add_argument(
        "mode",
        nargs="?",
        default="test",
        choices=["test", "stdio", "http"],
        help="test=内存自测；stdio=标准输入输出；http=streamable-http",
    )
    args = parser.parse_args()

    if args.mode == "stdio":
        asyncio.run(server.run_stdio_async())
    elif args.mode == "http":
        asyncio.run(server.run_streamable_http_async(host="127.0.0.1", port=8769))
    else:
        asyncio.run(_self_test())
