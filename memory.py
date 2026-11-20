"""memory.py — Agent 记忆模块（第 3 周周五产物）。

两层记忆：
  - 短期记忆 ``ShortTermMemory``：当前会话的对话消息，滑动窗口截断（对话记忆）。
    同一 Agent 实例连续多轮对话时，历史消息自动带上下一轮。
  - 长期记忆 ``LongTermMemory``：字典存事实条目 + 词袋相似度检索（向量检索的
    极简雏形），可选 JSON 落盘跨会话保留。

边界（本周排期，不在今天实现）：
  - 真正的文本 embedding（向量化）+ Chroma/FAISS 向量库检索是第 6 周 RAG 内容；
    这里用「英文词 + 中文字/二字组」构造词袋集合，用 Jaccard 相似度做召回，
    目的是先把「存事实 → 按相关性取出来」的链路跑通。
  - 自动从对话里抽取长期事实，在后续周次接入；今天由调用方显式 ``remember_fact``。

用法：
    from memory import AgentMemory

    mem = AgentMemory()
    mem.remember_conversation("我叫小明", "你好，小明")
    mem.remember_fact("用户习惯晚上学习", tags=["偏好"])
    relevant = mem.recall_relevant("晚上一般做什么")  # 拼好的相关记忆文本
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
DEFAULT_SHORT_TURNS = 10      # 短期记忆保留的最近轮数（一轮 = user + assistant）
DEFAULT_TOP_K = 3             # 长期记忆默认召回条数
_TIME_FMT = "%Y-%m-%d %H:%M:%S"


# ---------------------------------------------------------------------------
# 数据结构：一条记忆
# ---------------------------------------------------------------------------
@dataclass
class MemoryEntry:
    """一条记忆条目。

    属性:
        role: 条目类型 —— ``user`` / ``assistant`` / ``fact``。
        content: 记忆正文。
        created_at: 写入时间（本地时间字符串）。
        tags: 标签，便于按主题归类检索。
        access_count: 被检索命中的次数（可用于后续做重要性排序，本周仅记录）。
    """

    role: str
    content: str
    created_at: str = field(default_factory=lambda: datetime.now().strftime(_TIME_FMT))
    tags: list[str] = field(default_factory=list)
    access_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "content": self.content,
            "created_at": self.created_at,
            "tags": self.tags,
            "access_count": self.access_count,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MemoryEntry":
        return cls(
            role=str(data.get("role", "fact")),
            content=str(data.get("content", "")),
            created_at=str(data.get("created_at", "")),
            tags=list(data.get("tags", [])),
            access_count=int(data.get("access_count", 0)),
        )


# ---------------------------------------------------------------------------
# 短期记忆：当前会话对话历史（滑动窗口）
# ---------------------------------------------------------------------------
class ShortTermMemory:
    """短期对话记忆：只保留最近 ``max_turns`` 轮，超出的旧对话丢弃。

    与 chat_with_llm.Conversation 的区别：这里服务于 Agent 层，条目带标签和
    时间戳；消息格式仍是 OpenAI 的 ``{role, content}``。
    """

    def __init__(self, max_turns: int = DEFAULT_SHORT_TURNS) -> None:
        self.max_turns = max_turns
        self._entries: list[MemoryEntry] = []

    def add(self, role: str, content: str, tags: Optional[list[str]] = None) -> None:
        """写入一条消息；``role`` 通常是 user / assistant。"""
        content = (content or "").strip()
        if not content:
            return
        self._entries.append(MemoryEntry(role=role, content=content, tags=tags or []))

    def add_user(self, content: str) -> None:
        self.add("user", content)

    def add_assistant(self, content: str) -> None:
        self.add("assistant", content)

    def to_messages(self) -> list[dict[str, str]]:
        """组装成 OpenAI 格式消息列表；只保留最近 ``2 * max_turns`` 条。"""
        recent = self._entries[-2 * self.max_turns:]
        # 截断后若首条是 assistant，丢掉它，保证序列从 user 开始
        if recent and recent[0].role == "assistant":
            recent = recent[1:]
        return [{"role": entry.role, "content": entry.content} for entry in recent]

    def recent_text(self) -> str:
        """把最近对话拼成纯文本（调试/查看用）。"""
        return "\n".join(f"{e.role}: {e.content}" for e in self._entries)

    def __len__(self) -> int:
        return len(self._entries)

    def clear(self) -> None:
        self._entries.clear()


# ---------------------------------------------------------------------------
# 长期记忆：字典存事实 + 词袋相似度检索（向量检索雏形）
# ---------------------------------------------------------------------------
_WORD_RE = re.compile(r"[a-z0-9]+")
_CN_RE = re.compile(r"[\u4e00-\u9fff]")


def _tokenize(text: str) -> set[str]:
    """把文本拆成词袋集合：英文/数字按词，中文按单字 + 相邻二字组。

    这是「把文本变成向量」的最朴素实现：每个 token 对应一维，集合表示出现与否。
    第 6 周会换成 embedding 模型生成稠密向量。
    """
    text = (text or "").lower()
    tokens: set[str] = set(_WORD_RE.findall(text))
    chars = _CN_RE.findall(text)
    tokens.update(chars)
    tokens.update(a + b for a, b in zip(chars, chars[1:]))
    return tokens


def _jaccard(a: set[str], b: set[str]) -> float:
    """Jaccard 相似度：交集大小 / 并集大小；空集记 0。"""
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


class LongTermMemory:
    """长期事实记忆：``id -> MemoryEntry`` 字典，按相关性检索召回。

    Args:
        store_path: JSON 落盘路径；传 None 时纯内存，不读写文件。
    """

    def __init__(self, store_path: Optional[Path] = None) -> None:
        self.store_path = Path(store_path) if store_path else None
        self._items: dict[str, MemoryEntry] = {}
        self._seq = 0
        if self.store_path and self.store_path.exists():
            self._load()

    # -- 写入 ---------------------------------------------------------------
    def save(self, fact: str, tags: Optional[list[str]] = None) -> str:
        """存一条长期事实，返回条目 id。空正文忽略。"""
        fact = (fact or "").strip()
        if not fact:
            return ""
        self._seq += 1
        entry_id = f"mem-{self._seq:04d}"
        self._items[entry_id] = MemoryEntry(
            role="fact", content=fact, tags=tags or []
        )
        self._persist()
        return entry_id

    # -- 检索 ---------------------------------------------------------------
    def search(self, query: str, top_k: int = DEFAULT_TOP_K) -> list[tuple[str, MemoryEntry, float]]:
        """按词袋 Jaccard 相似度召回最相关的事实条目。

        返回 ``[(id, entry, score), ...]``，按相似度从高到低；相似度为 0 的条目不返回。
        """
        query_tokens = _tokenize(query)
        scored: list[tuple[str, MemoryEntry, float]] = []
        for entry_id, entry in self._items.items():
            tags_text = " ".join(entry.tags)
            score = max(
                _jaccard(query_tokens, _tokenize(entry.content)),
                _jaccard(query_tokens, _tokenize(tags_text)),
            )
            if score > 0:
                scored.append((entry_id, entry, score))
        scored.sort(key=lambda item: item[2], reverse=True)
        for _, entry, _ in scored[:top_k]:
            entry.access_count += 1
        return scored[:top_k]

    def recall_text(self, query: str, top_k: int = DEFAULT_TOP_K) -> str:
        """召回并拼成给模型看的文本；无相关条目时返回空字符串。"""
        hits = self.search(query, top_k=top_k)
        if not hits:
            return ""
        lines = [f"- {entry.content}" + (f"（标签：{', '.join(entry.tags)}）" if entry.tags else "")
                 for _, entry, _ in hits]
        return "\n".join(lines)

    # -- 读取 / 删除 ---------------------------------------------------------
    def get(self, entry_id: str) -> Optional[MemoryEntry]:
        return self._items.get(entry_id)

    def forget(self, entry_id: str) -> bool:
        """删除一条长期记忆；返回是否删除成功。"""
        existed = self._items.pop(entry_id, None) is not None
        if existed:
            self._persist()
        return existed

    def __len__(self) -> int:
        return len(self._items)

    def clear(self) -> None:
        self._items.clear()
        self._seq = 0
        self._persist()

    # -- JSON 持久化 ---------------------------------------------------------
    def _persist(self) -> None:
        """把长期记忆写回 JSON 文件；纯内存模式跳过。"""
        if not self.store_path:
            return
        payload = {
            "seq": self._seq,
            "items": {entry_id: entry.to_dict() for entry_id, entry in self._items.items()},
        }
        self.store_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def _load(self) -> None:
        """从 JSON 文件读回长期记忆；文件损坏时不崩，按空记忆处理。"""
        try:
            payload = json.loads(self.store_path.read_text(encoding="utf-8"))
            self._seq = int(payload.get("seq", 0))
            for entry_id, data in payload.get("items", {}).items():
                self._items[entry_id] = MemoryEntry.from_dict(data)
        except (json.JSONDecodeError, OSError, ValueError, TypeError):
            self._items = {}
            self._seq = 0


# ---------------------------------------------------------------------------
# 组合记忆：短期 + 长期，对外统一入口
# ---------------------------------------------------------------------------
class AgentMemory:
    """Agent 对外使用的记忆门面，组合短期对话记忆和长期事实记忆。

    Args:
        short_max_turns: 短期记忆保留轮数。
        long_store_path: 长期记忆 JSON 落盘路径；None 为纯内存。
    """

    def __init__(
        self,
        short_max_turns: int = DEFAULT_SHORT_TURNS,
        long_store_path: Optional[Path] = None,
    ) -> None:
        self.short = ShortTermMemory(max_turns=short_max_turns)
        self.long = LongTermMemory(store_path=long_store_path)

    def remember_conversation(self, user_content: str, assistant_content: str) -> None:
        """把一轮问答同时写入短期记忆。"""
        self.short.add_user(user_content)
        self.short.add_assistant(assistant_content)

    def remember_fact(self, fact: str, tags: Optional[list[str]] = None) -> str:
        """写入一条长期事实。"""
        return self.long.save(fact, tags=tags)

    def recall_relevant(self, query: str, top_k: int = DEFAULT_TOP_K) -> str:
        """按问题召回相关长期记忆，返回拼好的文本；无结果返回空串。"""
        return self.long.recall_text(query, top_k=top_k)

    def clear(self) -> None:
        self.short.clear()
        self.long.clear()


# ---------------------------------------------------------------------------
# 直接运行时离线自测：python memory.py
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import tempfile

    # 1. 短期记忆：滑动窗口截断
    short = ShortTermMemory(max_turns=2)
    for i in range(4):
        short.add_user(f"问题 {i}")
        short.add_assistant(f"回答 {i}")
    messages = short.to_messages()
    assert len(messages) == 4, f"应保留最近 2 轮共 4 条，实际 {len(messages)}"
    assert messages[0]["content"] == "问题 2"
    assert messages[-1]["content"] == "回答 3"
    print("短期滑动窗口 OK：", len(messages), "条 ->", [m["content"] for m in messages])

    # 2. 长期记忆：存入 + 按相关性召回
    long_term = LongTermMemory()
    long_term.save("用户习惯晚上 8 点以后学习", tags=["偏好", "作息"])
    long_term.save("用户正在做一个 AI Agent 八周学习计划", tags=["项目"])
    long_term.save("天气助手网页端口是 8766", tags=["配置"])

    hits = long_term.search("晚上一般几点学习")
    assert hits, "应召回作息相关条目"
    top_id, top_entry, score = hits[0]
    assert "晚上 8 点" in top_entry.content
    print(f"长期召回 OK：top={top_id} score={score:.2f} -> {top_entry.content}")

    recalled = long_term.recall_text("学习计划是什么")
    assert "AI Agent" in recalled
    print("长期召回文本 OK：\n" + recalled)

    # 3. JSON 落盘往返：新实例从文件读回
    with tempfile.TemporaryDirectory() as tmp:
        store = Path(tmp) / "longterm.json"
        lt1 = LongTermMemory(store_path=store)
        lt1.save("长期事实：喜欢极简代码风格", tags=["偏好"])
        lt2 = LongTermMemory(store_path=store)
        assert len(lt2) == 1
        assert "极简代码风格" in lt2.get("mem-0001").content
        print("JSON 落盘往返 OK：新实例读回", len(lt2), "条")

    # 4. 组合门面：问答入短期，事实入长期，召回相关片段
    mem = AgentMemory()
    mem.remember_conversation("我喜欢什么编程语言", "你偏好 Python。")
    mem.remember_fact("用户偏好 Python，零基础入门", tags=["技术栈"])
    relevant = mem.recall_relevant("编程语言偏好")
    assert "Python" in relevant
    assert len(mem.short) == 2
    print("组合门面 OK：短期", len(mem.short), "条；长期召回 ->", relevant)

    print("\n记忆模块自测通过。")
