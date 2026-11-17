# 学习笔记 · 周二 11/17 — ReAct 范式 + min_agent.py 解析骨架

> 第 3 周第 2 天。目标：吃透 ReAct 的 Thought→Action→Observation 循环，并手写 `min_agent.py` 骨架——先把模型输出解析成结构化的一步推理，工具执行与记忆组件留给本周后续几天。

---

## 一、ReAct 三要素 + 终止信号

| 环节 | 英文 | 模型做什么 | Agent 做什么 |
|------|------|-----------|-------------|
| **思考** | Thought | 分析已知信息，推理下一步 | 解析文本，保留到轨迹 |
| **行动** | Action | 选择工具 + 给出参数 | 解析工具名和参数（今天）；真正执行（周四） |
| **观察** | Observation | 不输出（由系统回填） | 把工具结果写回上下文，供下一轮 Thought 使用 |
| **最终回答** | Final Answer | 信息足够时直接给答案 | 识别终止信号，结束循环 |

ReAct 的核心：模型**不是一次生成答案**，而是在"思考—行动—观察"的循环里，根据真实反馈一步步逼近答案。Observation 是连接模型推理和外部世界的接口。

## 二、我们约定的输出格式

骨架用纯文本标记（简单、可读、易解析），由 system prompt 约束模型遵守：

```
Thought: <分析现状，推理下一步>
Action: <工具名>
Action Input: <JSON 参数，例如 {"expression": "128 * 46"}>
```

终止轮不再调工具：

```
Thought: <确认信息已足够>
Final Answer: <最终回答>
```

> Observation 由 Agent 在工具执行后回填进消息历史，模型不写这个字段。

## 三、解析器设计（`parse_react_step`）

模型输出是一段自由文本，需要解析成结构化的 `ReActStep` dataclass：

```python
@dataclass
class ReActStep:
    thought: Optional[str]          # 思考
    action: Optional[str]           # 工具名（Final Answer 轮为 None）
    action_input: Optional[dict]    # 参数 dict；JSON 失败降级 {"_raw": ...}
    observation: Optional[str]       # 工具结果（执行后回填）
    final_answer: Optional[str]      # 非 None 即终止
    raw: str                        # 模型原始文本
```

解析要点：
1. **按标记切分**：用正则定位 `Thought:` / `Action:` / `Action Input:` / `Final Answer:`，字段内容取到下一个标记为止（容忍大小写和多余空格）。
2. **工具名清洗**：Action 行可能写成 `calculator(expression=...)`，正则只取开头的 `[A-Za-z0-9_]+` 作为工具名。
3. **参数 JSON 降级**：`Action Input` 先 `json.loads`；失败不抛异常，把原文放进 `{"_raw": 原始文本}`，保证解析流程不中断。
4. **终止判定**：`final_answer is not None` 即 `is_final=True`，循环据此退出。

## 四、循环骨架结构（`ReActAgent`）

```
messages = [system(ReAct 格式约定), user(原始问题)]
循环最多 max_steps 次：
    raw = llm(messages)                  # 调模型
    step = parse_react_step(raw)         # 解析一步
    记录到 trajectory
    若 step.is_final → 返回 final_answer
    step.observation = _execute_action(step)   # 今天是桩，周四接真实工具
    messages 追加 assistant(raw) + user("Observation: ...")  # 轨迹回填
超过 max_steps → 返回最后一条 Thought（防止死循环）
```

关键设计：
- **依赖注入**：`llm` 参数默认是 `chat_completion`，测试时可传入假 LLM 脚本，离线跑通循环、不耗 token。
- **最大步数**：`max_steps=5` 是终止保护，避免模型反复调工具陷入死循环。
- **轨迹回填**：上一轮模型输出作为 `assistant`，Observation 作为新的 `user`，下一轮 Thought 才能看到工具结果。

## 五、今天刻意没做的部分（本周排期边界）

| 能力 | 安排在哪天 | 今天状态 |
|------|-----------|---------|
| 真实工具定义（计算器、时间查询） | 周三 | 未定义 |
| 工具注册与路由（替换 `_execute_action` 桩） | 周四 | `_execute_action` 返回占位文本 |
| 独立记忆字典 | 周五 | 轨迹暂存在 `trajectory` 列表，未抽象成记忆组件 |
| 接真实 LLM 端到端调试跑通 | 周六 | 自测用注入的假 LLM |

## 六、代码文件结构（`min_agent.py`）

| 部分 | 内容 |
|------|------|
| `ReActStep` | 一步推理的 dataclass + `is_final` 属性 |
| `parse_react_step(text)` | 文本 → ReActStep 解析器 |
| `ReActAgent.run(question)` | ReAct 主循环 |
| `ReActAgent._execute_action(step)` | 工具执行桩（周四替换） |
| `ReActAgent.print_trajectory()` | 轨迹调试打印 |
| `__main__` 自测 | 两个解析断言 + 假 LLM 跑三步循环到 Final Answer |

## 七、易混点 / 面试话术

- **Thought 是内部推理**：不展示给最终用户；只有 Final Answer 才是对外输出。
- **解析和执行分开**：今天只解决"模型输出怎么读"；"读到工具名后怎么真的调用"是周四的路由层。
- **Observation 不是模型生成的**：它来自工具执行结果，Agent 负责把它放回上下文。
- **循环必须有两个出口**：Final Answer（正常结束）+ 最大步数（异常保护）。

## 八、今天的产物
- 代码：`min_agent.py`（解析器 + ReAct 循环骨架 + 离线自测，自测通过：三步到达 Final Answer，结果 6638）。
- 笔记：`notes/2026-11-17_react-skeleton.md`。
- 明天（周三）：Function Calling / Tool Use，定义计算器、时间查询两个工具。
