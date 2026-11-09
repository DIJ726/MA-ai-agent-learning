# 学习笔记 · 周日 11/08 — Prompt Engineering 后半 + 结构化输出（JSON mode）

> 学习目标：搞清楚"怎么让大模型稳定吐出可被程序解析的 JSON"，以及背后为什么要重试和降级。

---

## 一、为什么要 JSON mode

大模型默认是"话痨"——你让它输出 JSON，它可能：
- 前面加一句"好的，这是结果："
- 外面包一整段 ```json ... ``` 代码块
- 末尾补一句"希望对你有帮助"

后端拿到这种字符串，`json.loads()` 直接炸。**JSON mode** 就是在 API 请求里加一个开关，强制模型只输出合法 JSON，不允许任何额外文字。

OpenAI 兼容接口（千问也支持）的写法：

```python
payload["response_format"] = {"type": "json_object"}
```

光开这个开关还不够——你必须在 System Prompt 里把 JSON 模板画出来，不然模型不知道字段长什么样。

---

## 二、我们项目里的两个 JSON 场景

### 1. 城市提取（轻量 JSON）

让模型从"北京上海这周六天气怎么样"里抠出 `cities` 和 `dates`：

```python
CITY_SYSTEM_PROMPT = (
    "从用户的话里提取城市和日期……"
    '只输出 JSON，格式：{{"cities": ["城市1"], "dates": ["2026-11-08"]}}。'
    "用户没有提到日期时 dates 必须是空数组 []。"
)
```

关键技巧：
- 把"今天是哪一天"注入 prompt（`{today}` 占位），模型才能把"这周六"换算成真实日期
- 明确写"没有就返回空数组"，防止模型自己瞎编
- temperature 调到 0.0——这种提取任务不需要创造性

### 2. 天气卡片（复杂嵌套 JSON）

让模型输出一张带嵌套结构的卡片：

```json
{
  "overview": "一句话概览",
  "days": [
    {"date": "2026-11-10", "cities": [{"city": "北京", "weather": "晴", ...}]}
  ],
  "advice": {"clothing": "...", "gear": "...", "activity": "...", "air": "..."}
}
```

关键技巧：
- prompt 里把完整 JSON 模板贴出来，一个字段都不能少
- 写明"所有字段都是字符串，用中文"
- 写明"多城市时在 comment 里体现对比"
- temperature 0.3——比提取高一点，让建议自然，但不能太飘

---

## 三、光靠 prompt 不够：还要重试 + 降级

即使开了 JSON mode + 写了模板，模型偶尔还是会违约（比如今天服务器抽风、上下文超长、字段漏了）。生产代码不能赌它每次都听话，所以我们在 `chat_with_llm.py` 里写了两层保险：

```python
def chat_json(messages, retries=3, temperature=0.3):
    # 第一层：JSON 模式最多试 3 次
    for attempt in range(retries):
        try:
            content = chat_completion(messages, json_mode=True)
            data = json.loads(content)
            if not isinstance(data, dict):
                raise ValueError("JSON 顶层不是对象")
            return data, content
        except (json.JSONDecodeError, ValueError, RuntimeError):
            continue

    # 第二层：降级成普通自然语言，保证用户总能看到回复
    fallback = chat_completion(messages, temperature=0.7, json_mode=False)
    return None, fallback
```

调用方拿到结果后的处理：
- `data` 不是 None → 前端渲染成卡片
- `data` 是 None → 把 `raw_text` 当普通气泡显示

**用户感知层面**：模型正常时看到漂亮卡片，模型抽风时看到一段自然语言建议，永远不会因为 JSON 解析失败而白屏。

---

## 四、手写 JSON parser vs Pydantic

我们现在用的是 `json.loads()` + 手工校验字段。如果以后字段变复杂，更稳的做法是用 Pydantic：

```python
from pydantic import BaseModel

class CityAnswer(BaseModel):
    city: str
    weather: str
    temp: str
    suitability: str

class AnswerReport(BaseModel):
    overview: str
    days: list[dict]
    advice: dict

# 直接从 JSON 字符串校验 + 转对象
report = AnswerReport.model_validate_json(content)
```

学习计划第 6 周学 RAG 时会大量用到 Pydantic，现在先有个印象。

---

## 五、自测清单（今天跑过的）

- [x] `python chat_with_llm.py`：普通对话返回自我介绍
- [x] JSON 模式返回 `{'city': '北京', 'population_rank': 1}`，一次解析成功
- [x] 集成进 weather_assistant 后，"北京明天"返回完整卡片（overview/days/advice）
- [x] 降级路径：故意传无法识别的问题，应退回纯文本（今天没触发，代码路径已覆盖）
