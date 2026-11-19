# 学习笔记 · 周四 11/19 — 工具注册与路由（接入 min_agent.py）

> 第 3 周第 4 天。目标：把周三定义好的工具注册表接进 ReAct 循环，替换 `_execute_action` 占位桩。模型负责决定调哪个工具、传什么参数，Agent 负责按名找到工具并真实执行，结果回填为 Observation。

---

## 一、注册与路由分别是什么

- **注册（Registry）**：把工具登记到一张 `工具名 -> Tool` 的表里，Agent 启动时能看到全部可用工具。周三已在 `tools.py` 建好 `TOOL_REGISTRY`（含 `calculator`、`get_current_time`）。
- **路由（Routing）**：模型一轮输出 `Action: <工具名>` + `Action Input: <参数>` 后，Agent 按工具名查注册表、调用对应函数、把结果作为观察返回。今天把这条链路接到 `min_agent.py`。

## 二、完整路由链路（一轮）

```
用户问题
  → LLM 输出 ReAct 文本
  → parse_react_step() 解析出 step.action / step.action_input
  → ReActAgent._execute_action(step)
      → execute_tool(step.action, step.action_input)
          → TOOL_REGISTRY[name].run(arguments)   # 真实执行
          → 返回结果字符串（或错误说明）
  → 结果写回 step.observation，作为 "Observation: ..." 回填 messages
  → 下一轮 LLM 基于观察继续 Thought / 给 Final Answer
```

分工边界（关键）：
- **模型**：决定用不用工具、用哪个、参数怎么填（出现在 Action / Action Input 文本里）。
- **应用（我们的代码）**：只做查表和执行，不替模型选工具；工具结果以文本回传，由模型继续推理。

## 三、今天在 `min_agent.py` 的改动

| 位置 | 改动前（周二桩） | 改动后（周四） |
|------|------------------|---------------|
| 导入 | 仅 `chat_completion` | 额外 `from tools import TOOL_REGISTRY, execute_tool` |
| 系统提示 | 只有 ReAct 格式说明 | 默认提示自动拼接 `_tools_catalog_text()` 生成的可用工具清单（名称 + 用途 + 参数），帮助模型选型 |
| `_execute_action` | 返回 `[骨架占位] ...` 文本 | 空 Action 给错误观察；否则 `execute_tool(action, action_input)` 真实执行 |
| `__init__` | `system_prompt` 用固定常量 | `system_prompt=None` 时自动拼格式说明 + 工具清单；可外部覆盖 |

### 3.1 工具清单如何进提示
`_tools_catalog_text()` 遍历 `TOOL_REGISTRY`，拼出：

```
可用工具（Action 必须从下列名称中选择）：
- calculator：计算算术表达式……（参数：expression）
- get_current_time：查询当前北京时间……（参数：无参数）
```

工具增减只改 `tools.py` 注册表，Agent 提示自动跟随，不用改 `min_agent.py`。

### 3.2 路由兜底（都不抛断循环，转成观察文本）
- **空 Action**：返回"本轮没有给出 Action 工具名，无法执行"。
- **未知工具**：`execute_tool` 返回"未知工具: xxx。可用工具: ..."。
- **工具内部异常**：`execute_tool` 捕获后返回"工具 xxx 执行出错: ..."。

## 四、自测结果（离线，假 LLM，不耗真实 token）

- **计算器场景**：两步真实计算，Observation 依次为 `5888`（128 × 46）、`6638`（5888 + 750），最终回答含 6638。
- **时间场景**：`get_current_time` 无参调用，Observation 为 `2026-11-19 15:48:59 周四`（日期 + 星期格式正确）。
- **未知工具**：返回可用工具清单，循环不崩。
- 回归：全部 `.py` 语法解析 + import 通过；`tools.py` 自测仍通过。

## 五、代码结构现状

| 文件 | 角色 |
|------|------|
| `tools.py` | 工具定义 + 注册表 + schema + `execute_tool` 分派 |
| `min_agent.py` | ReAct 循环：解析 → 决策 → 路由执行 → 观察回填；`trajectory` 轨迹 |
| `chat_with_llm.py` | LLM 调用层（默认模型 `qwen-plus`） |

## 六、本周排期边界（今天没做的）

- **记忆字典（周五）**：当前轨迹存在 `trajectory` 列表；周五抽象成独立记忆组件（短期对话 + 长期向量存）。
- **真实 LLM 端到端（周六）**：今天自测仍用注入的假 LLM；周六接真实模型跑通并调试循环。
- **原生 `tools` 字段**：当前走自写 ReAct 文本协议（`Action:` / `Action Input:` 解析）；`tools.py` 的 schema 已备好，端到端阶段可再联调 `chat/completions` 的原生 `tools` 参数。

## 七、易混点 / 面试话术

- **路由不是模型执行工具**：模型只产出工具名和参数；真正算数、查时间的是注册表后的 Python 函数。
- **工具清单写进 system prompt**：模型看不到注册表内部代码，只能靠提示里的名称和描述判断何时调用；描述质量直接影响选型准确率。
- **错误也要回填成 Observation**：未知工具/参数错误不能让程序崩，转成文本观察发回，让模型下一轮自己纠正。
- **注册表与 Agent 解耦**：Agent 只依赖工具名和 `execute_tool` 入口；新增工具不需要改循环代码。

## 八、今天的产物
- 代码：`min_agent.py`（工具清单提示 + 真实路由 + 三类兜底，离线自测通过）。
- 笔记：`notes/2026-11-19_tool-routing.md`。
- 明天（周五）：给 Agent 加简单 memory 字典（短期对话 + 长期向量存的雏形）。
