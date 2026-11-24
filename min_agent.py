"""min_agent.py — 最小 ReAct Agent（第 3 周产物）。

演进记录：
  - 周二：解析模型 ReAct 输出（Thought / Action / Action Input / Final Answer）+ 循环骨架（工具为桩）。
  - 周四：工具注册与路由。ReActAgent 读取 tools.TOOL_REGISTRY，把模型选的工具按名
    分派到 tools.execute_tool 真实执行，结果作为 Observation 回填。
  - 周五：接入记忆模块 memory.AgentMemory。run 前从长期记忆召回相关事实补进上下文，
    问答写入短期对话记忆（滑动窗口，跨轮保留）。

后续任务分工（本周排期，不在今天实现）：
  - 周六：接真实 LLM 端到端跑通并调试。

ReAct 输出格式（由 system prompt 约束模型遵守）：

    Thought: <分析现状，推理下一步>
    Action: <工具名>
    Action Input: <JSON 参数，例如 {"expression": "128 * 46"}>

信息足够时直接终止：

    Thought: <确认信息已足够>
    Final Answer: <最终回答>

Observation 由 Agent 在工具执行后回填，模型不需要输出。

离线自测：python min_agent.py（用假 LLM 脚本跑循环，不消耗真实 token）。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Callable, Optional

from chat_with_llm import chat_completion
from memory import AgentMemory
from tools import TOOL_REGISTRY, execute_tool

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
DEFAULT_MAX_STEPS = 5  # 循环上限，防止 Agent 死循环

DEFAULT_SYSTEM_PROMPT = (
    "你是一个使用 ReAct（思考-行动-观察）范式工作的 Agent。严格按下面的格式逐轮输出，一轮只做一个动作：\n"
    "\n"
    "Thought: 分析当前已知信息，推理下一步该做什么。\n"
    "Action: 工具名。\n"
    "Action Input: 给该工具的 JSON 参数，例如 {\"expression\": \"128 * 46\"}。\n"
    "\n"
    "如果信息已经足够回答用户，不要再调用工具，改用：\n"
    "Thought: 确认信息已足够。\n"
    "Final Answer: 最终回答。\n"
    "\n"
    "硬性规则：\n"
    "1. 凡是数值计算、复利/利息、日期推算，必须调用 calculator 工具，禁止自己心算后直接给结果。\n"
    "2. Action Input 只写 JSON 参数，写完不要在这一行/这一段后面续写 Thought、解释或其他字段。\n"
    "3. 一轮只输出一个 Action；拿到 Observation 后再进入下一轮。\n"
    "4. Observation（工具返回结果）由系统自动回填，你不要写。"
)

# 已知标记：用于在文本里定位各字段边界
_NEXT_FIELD = "Action Input|Action|Observation|Final Answer"


def _tools_catalog_text() -> str:
    """从工具注册表生成给模型看的可用工具清单（名称 + 用途 + 参数）。"""
    if not TOOL_REGISTRY:
        return "当前没有注册任何可用工具。"
    lines = ["可用工具（Action 必须从下列名称中选择）："]
    for tool in TOOL_REGISTRY.values():
        params = ", ".join(tool.parameters.get("properties", {}).keys()) or "无参数"
        lines.append(f"- {tool.name}：{tool.description}（参数：{params}）")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 数据结构：一步 ReAct 推理
# ---------------------------------------------------------------------------
@dataclass
class ReActStep:
    """模型一轮输出解析后的结构化结果。

    属性:
        thought: 思考内容；缺失为 None。
        action: 工具名；Final Answer 步骤为 None。
        action_input: 解析后的参数 dict；解析失败时保留 {"_raw": 原始文本}。
        observation: 工具执行结果（由 _execute_action 回填）。
        final_answer: 最终回答；非 None 表示本轮应终止循环。
        raw: 模型原始文本。
    """

    thought: Optional[str] = None
    action: Optional[str] = None
    action_input: Optional[dict] = None
    observation: Optional[str] = None
    final_answer: Optional[str] = None
    raw: str = ""

    @property
    def is_final(self) -> bool:
        """是否已给出 Final Answer（循环终止信号）。"""
        return self.final_answer is not None


# ---------------------------------------------------------------------------
# 解析工具函数
# ---------------------------------------------------------------------------
def _extract_field(text: str, label: str) -> Optional[str]:
    """提取 ``Label: 内容`` 字段，内容取到下一个已知标记或文本结束为止。"""
    pattern = re.compile(
        rf"{label}\s*:\s*(.*?)(?=\n(?:{_NEXT_FIELD})\s*:|$)",
        re.S | re.I,
    )
    match = pattern.search(text)
    return match.group(1).strip() if match else None


def _clean_action_name(action_line: Optional[str]) -> Optional[str]:
    """从 Action 行提取纯工具名，去掉 ``calculator(...)`` 这类括号参数。"""
    if action_line is None:
        return None
    name = action_line.strip()
    match = re.match(r"([A-Za-z0-9_]+)", name)
    return match.group(1) if match else name


def _extract_json_span(text: str) -> Optional[str]:
    """截取文本中首个平衡的 JSON 对象/数组（按括号配对，跳过字符串内括号）。

    模型偶尔把下一段 Thought 续写在 Action Input 后面，这里只取到 JSON 闭合为止。
    """
    start = next((i for i, ch in enumerate(text) if ch in "{["), None)
    if start is None:
        return None
    open_ch = text[start]
    close_ch = "}" if open_ch == "{" else "]"
    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    return None


def _parse_action_input(raw: Optional[str]) -> dict:
    """把 Action Input 文本解析成 dict；JSON 失败时把原文放进 _raw 字段。

    先尝试整段解析；失败再截取首个平衡 JSON 对象；仍失败才降级。
    """
    if not raw:
        return {}
    raw = raw.strip()
    for candidate in (raw, _extract_json_span(raw)):
        if not candidate:
            continue
        try:
            data = json.loads(candidate)
            return data if isinstance(data, dict) else {"value": data}
        except json.JSONDecodeError:
            continue
    return {"_raw": raw}


def parse_react_step(text: str) -> ReActStep:
    """把模型一轮 ReAct 输出解析成 ReActStep。

    支持两种轮次：
      - 动作轮：Thought + Action + Action Input
      - 终止轮：Thought + Final Answer

    解析不抛异常：缺字段置 None，参数 JSON 解析失败降级为 {"_raw": ...}。
    """
    final_answer = _extract_field(text, "Final Answer")
    action_line = _extract_field(text, "Action")

    return ReActStep(
        thought=_extract_field(text, "Thought"),
        action=_clean_action_name(action_line),
        action_input=_parse_action_input(_extract_field(text, "Action Input")),
        final_answer=final_answer,
        raw=text.strip(),
    )


# ---------------------------------------------------------------------------
# Agent 主体：ReAct 循环骨架
# ---------------------------------------------------------------------------
class ReActAgent:
    """最小 ReAct Agent。

    Args:
        llm: 调用模型的函数，签名 ``llm(messages: list[dict]) -> str``。
            默认用 chat_with_llm.chat_completion；测试时可注入假 LLM。
        max_steps: 循环最大轮数，防止死循环。
        system_prompt: 约束模型输出 ReAct 格式的系统提示；传 None 时自动
            拼接基础格式说明 + 当前注册的工具清单。
        memory: 记忆门面（短期对话 + 长期事实）；传 None 时新建默认 AgentMemory。
    """

    def __init__(
        self,
        llm: Callable[[list[dict]], str] = chat_completion,
        max_steps: int = DEFAULT_MAX_STEPS,
        system_prompt: Optional[str] = None,
        memory: Optional[AgentMemory] = None,
    ) -> None:
        self.llm = llm
        self.max_steps = max_steps
        if system_prompt is None:
            self.system_prompt = DEFAULT_SYSTEM_PROMPT + "\n\n" + _tools_catalog_text()
        else:
            self.system_prompt = system_prompt
        self.memory = memory or AgentMemory()
        self.trajectory: list[ReActStep] = []  # 本轮任务的完整推理轨迹

    # -- 对外主流程 ---------------------------------------------------------
    def run(self, question: str) -> str:
        """跑 ReAct 循环，返回最终回答。

        循环步骤：召回长期记忆 → 调模型 → 解析一步 → 终止则返回 → 否则执行动作 →
        把 Observation 回填进上下文，进入下一轮；问答同时写入短期记忆。
        """
        # 长期记忆召回：把与问题相关的既有事实补进系统上下文
        recall = self.memory.recall_relevant(question)
        system_content = self.system_prompt
        if recall:
            system_content = system_content + "\n\n已知长期记忆（仅作参考，不要臆造）：\n" + recall

        messages: list[dict] = [{"role": "system", "content": system_content}]
        messages.extend(self.memory.short.to_messages())
        messages.append({"role": "user", "content": question})
        self.memory.short.add_user(question)

        for _ in range(self.max_steps):
            raw = self.llm(messages)
            step = parse_react_step(raw)
            self.trajectory.append(step)

            if step.is_final:
                answer = step.final_answer or ""
                self.memory.short.add_assistant(answer)
                return answer

            # 执行动作：按模型选择的工具名路由到注册表真实执行
            step.observation = self._execute_action(step)

            # 轨迹回填：模型上一轮输出 + 工具观察，作为下一轮上下文
            messages.append({"role": "assistant", "content": raw})
            messages.append({"role": "user", "content": f"Observation: {step.observation}"})

        # 超过最大步数仍未给出 Final Answer
        last = self.trajectory[-1] if self.trajectory else None
        answer = last.thought or "达到最大循环步数，仍未给出最终答案。"
        self.memory.short.add_assistant(answer)
        return answer

    # -- 工具路由（周四接入：注册表按名分派到真实工具） -----------------------
    def _execute_action(self, step: ReActStep) -> str:
        """按 Action 里的工具名查注册表执行，返回观察文本。

        路由职责：模型决定调哪个工具（Action）和传什么参数（Action Input），
        Agent 只负责按名找到对应工具并执行；未知工具、参数异常由
        tools.execute_tool 兜成错误文本回传，不会中断循环。
        """
        if not step.action:
            return "观察：本轮没有给出 Action 工具名，无法执行。"
        return execute_tool(step.action, step.action_input or {})

    # -- 调试辅助 -----------------------------------------------------------
    def print_trajectory(self) -> None:
        """打印本轮轨迹（Thought / Action / Observation），调试用。"""
        for index, step in enumerate(self.trajectory, start=1):
            print(f"\n--- Step {index} ---")
            print(f"Thought: {step.thought}")
            if step.action:
                print(f"Action: {step.action}  Input: {step.action_input}")
            if step.observation:
                print(f"Observation: {step.observation}")
            if step.is_final:
                print(f"Final Answer: {step.final_answer}")


# ---------------------------------------------------------------------------
# 直接运行时离线自测：python min_agent.py
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import re as _re

    # 1. 解析器自测：动作轮
    action_sample = (
        "Thought: 需要先算乘积，心算容易错，应该调用计算器。\n"
        "Action: calculator\n"
        'Action Input: {"expression": "128 * 46"}'
    )
    parsed_action = parse_react_step(action_sample)
    assert parsed_action.action == "calculator"
    assert parsed_action.action_input == {"expression": "128 * 46"}
    assert not parsed_action.is_final
    print("解析动作轮 OK：", parsed_action.action, parsed_action.action_input)

    # 1b. Action Input 混入下一段 Thought 时，仍截出干净 JSON
    mixed_sample = (
        "Thought: 先算乘积。\n"
        "Action: calculator\n"
        'Action Input: {"expression": "128 * 46"}\n\n'
        "Thought: 拿到结果后继续下一步。"
    )
    parsed_mixed = parse_react_step(mixed_sample)
    assert parsed_mixed.action_input == {"expression": "128 * 46"}, parsed_mixed.action_input
    print("混入文本解析 OK：", parsed_mixed.action_input)

    # 2. 解析器自测：终止轮
    final_sample = (
        "Thought: 结果 6638 已确认，可以回答。\n"
        "Final Answer: 128 × 46 + 750 = 6638。"
    )
    parsed_final = parse_react_step(final_sample)
    assert parsed_final.is_final
    assert "6638" in parsed_final.final_answer
    print("解析终止轮 OK：", parsed_final.final_answer)

    # 3. 计算器路由自测：假 LLM 脚本三步，观察必须是真实工具结果（5888 / 6638）
    calc_scripts = [
        action_sample,
        "Thought: 乘积是 5888，下一步加 750，继续用计算器。\n"
        "Action: calculator\n"
        'Action Input: {"expression": "5888 + 750"}',
        final_sample,
    ]

    def fake_calc_llm(_messages: list[dict]) -> str:
        return calc_scripts.pop(0)

    calc_agent = ReActAgent(llm=fake_calc_llm, max_steps=5)
    answer = calc_agent.run("128 乘以 46，再加上 750，结果是多少？")

    print("\n=== 计算器场景轨迹 ===")
    calc_agent.print_trajectory()
    assert calc_agent.trajectory[0].observation == "5888"
    assert calc_agent.trajectory[1].observation == "6638"
    assert "6638" in answer
    print("计算器路由 OK：观察 =", calc_agent.trajectory[0].observation,
          "→", calc_agent.trajectory[1].observation)

    # 4. 时间工具路由自测：get_current_time 无参调用，观察为北京时间
    time_scripts = [
        "Thought: 用户想知道当前时间，调用时间查询工具。\n"
        "Action: get_current_time\n"
        "Action Input: {}",
        "Thought: 已经拿到当前时间，可以回答。\n"
        "Final Answer: 现在是查询到的北京时间。",
    ]

    def fake_time_llm(_messages: list[dict]) -> str:
        return time_scripts.pop(0)

    time_agent = ReActAgent(llm=fake_time_llm, max_steps=5)
    time_answer = time_agent.run("现在几点了？")
    time_obs = time_agent.trajectory[0].observation
    print("\n=== 时间场景观察 ===")
    print(time_obs)
    assert _re.search(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", time_obs)
    assert "周" in time_obs
    print("时间工具路由 OK")

    # 5. 未知工具兜底：不崩，返回错误说明观察
    unknown_step = ReActStep(action="not_a_tool", action_input={"x": 1})
    unknown_obs = calc_agent._execute_action(unknown_step)
    assert "未知工具" in unknown_obs
    print("\n未知工具兜底 OK：", unknown_obs)

    # 6. 记忆接入自测：同一 Agent 连续两轮，短期历史保留；长期事实召回进上下文
    captured: list[list[dict]] = []

    def recording_llm(messages: list[dict]) -> str:
        captured.append(messages)
        return "Thought: 已了解。\nFinal Answer: 好的。"

    mem_agent = ReActAgent(llm=recording_llm, max_steps=3)
    mem_agent.memory.remember_fact("用户喜欢晚上学习", tags=["偏好"])
    mem_agent.run("我平时什么时候学习？")
    mem_agent.run("再确认一下")

    assert len(mem_agent.memory.short) == 4, "两轮问答应留下 4 条短期记忆"
    first_messages = captured[0]
    second_messages = captured[1]
    assert any("晚上学习" in m["content"] for m in first_messages), "长期召回应进系统上下文"
    assert any(m["role"] == "user" and "我平时什么时候学习" in m["content"]
               for m in second_messages), "上一轮问答应保留在短期历史"
    print("\n记忆接入 OK：短期", len(mem_agent.memory.short),
          "条；长期召回已进上下文")

    print("\n工具路由自测通过。")
