"""天气智能助手：网页对话式天气查询与分析。

功能：在网页对话框输入城市 + 具体日期（一次最多 3 个城市），程序调用
和风天气 API 获取「过去 5 天历史 + 未来 7 天预报 + 实时空气质量」，
多城市数据用 httpx.AsyncClient 异步并发获取（3 城约等于 1 城的耗时），
再把原始数据交给千问大模型分析，重点回答用户问的日期，并给出出行建议。
只有用户问到具体日期才查询数据；只说城市时助手会先追问日期。

运行方式：python weather_assistant.py，浏览器自动打开 http://127.0.0.1:8765
密钥存放：同目录 .env 文件（DASHSCOPE_API_KEY / QWEATHER_API_KEY / QWEATHER_API_HOST）
"""

import asyncio
import json
import time
import webbrowser
from dataclasses import dataclass, field
from datetime import date, timedelta
from functools import wraps
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Optional

import httpx

from chat_with_llm import (
    DASHSCOPE_KEY,
    chat_completion,
    load_env,
    retry,
    timed,
)

# ---------------------------------------------------------------------------
# 常量配置
# ---------------------------------------------------------------------------
ENV_FILE_PATH = Path(__file__).resolve().parent / ".env"
SERVER_HOST = "127.0.0.1"
SERVER_PORT = 8765
REQUEST_TIMEOUT_SECONDS = 30
HISTORY_DAYS = 5   # 过去 5 天
FORECAST_DAYS = 7  # 未来 7 天
MAX_HISTORY_TURNS = 20  # 多轮对话保留的最大轮数
MAX_CITIES = 3     # 单次最多同时查询 3 个城市
JSON_RETRY_ATTEMPTS = 3  # 结构化输出解析失败的重试次数

# 千问分析提示词：强制模型只输出固定结构的 JSON（结构化输出）
ANALYST_SYSTEM_PROMPT = (
    "你是一名贴心的天气助理。用户会给你一个或多个城市（最多 3 个）的天气原始数据"
    "（过去若干天历史 + 未来若干天预报 + 实时空气质量），并指定他关心的具体日期。"
    "你必须只输出一个 JSON 对象，不要输出任何解释文字、markdown 代码块标记。"
    "JSON 结构固定如下：\n"
    '{\n'
    '  "overview": "一句话总体概览（必填）",\n'
    '  "days": [\n'
    '    {\n'
    '      "date": "yyyy-MM-dd（用户问的日期，必填）",\n'
    '      "cities": [\n'
    '        {\n'
    '          "city": "城市名",\n'
    '          "weather": "天气现象，如 小雨",\n'
    '          "temp": "温度范围，如 1~6℃",\n'
    '          "wind": "风向风力，如 北风3级",\n'
    '          "suitability": "适合出行的简短结论，如 很适合/一般/不建议",\n'
    '          "comment": "一句话点评（穿衣、雨具、注意事项）"\n'
    '        }\n'
    '      ]\n'
    '    }\n'
    '  ],\n'
    '  "advice": {\n'
    '    "clothing": "穿衣建议",\n'
    '    "gear": "随身物品建议（雨具/防晒等）",\n'
    '    "activity": "户外活动安排建议",\n'
    '    "air": "空气敏感人群注意事项"\n'
    '  }\n'
    '}\n'
    "要求：days 数组覆盖用户问的每个日期，每个日期下 cities 覆盖每个查询的城市；"
    "所有字段都是字符串，内容用中文，简短自然；"
    "多城市时在 comment 或 overview 里体现对比（哪个更适合出行）。"
)

# 提取城市（可多个）+ 具体日期的系统提示词
CITY_SYSTEM_PROMPT = (
    "从用户的话里提取：1) 他想查询天气的所有城市或地区名称（可能 1~3 个，"
    "如「北京上海广州这周六天气怎么样」有 3 个城市）；"
    "2) 他明确问到的具体日期。今天是 {today}。"
    "相对说法（今天/明天/后天/大后天/昨天/前天/周几/本周几/这周六等）"
    "和绝对日期（如 11月8日、2026-11-08）都要换算成 yyyy-MM-dd。"
    '只输出 JSON，格式：{{"cities": ["城市1", "城市2"], "dates": ["日期1", "日期2"]}}。'
    "用户没有明确提到任何具体日期时，dates 必须是空数组 []。"
    "城市数量超过 3 个时只保留前 3 个。"
    "如果一个城市都提取不到，cities 为空数组。不要输出任何其他内容。"
)


# ---------------------------------------------------------------------------
# dataclass：结构化数据模型
# ---------------------------------------------------------------------------
@dataclass
class CityInfo:
    """城市信息（来自 GeoAPI）。"""

    name: str
    location_id: str
    lat: str
    lon: str
    adm1: str  # 省/直辖市
    adm2: str  # 城市


@dataclass
class DaySummary:
    """单日天气摘要（历史或预报通用）。"""

    fx_date: str
    tag: str        # "历史" 或 "预报"
    temp_max: str
    temp_min: str
    text_day: str
    precip: str
    humidity: str
    wind_dir: str = ""
    wind_scale: str = ""

    def to_line(self) -> str:
        """转成给大模型看的一行文本。"""
        return (
            f"{self.fx_date}（{self.tag}）：{self.text_day}，"
            f"{self.temp_min}~{self.temp_max}℃，降水 {self.precip}mm，"
            f"湿度 {self.humidity}%，{self.wind_dir}{self.wind_scale}级风"
        )


@dataclass
class AirBrief:
    """实时空气质量摘要。"""

    aqi: str
    category: str
    primary_pollutant: str

    def to_line(self) -> str:
        return f"实时空气质量：AQI {self.aqi}（{self.category}），首要污染物 {self.primary_pollutant}"


@dataclass
class WeatherReport:
    """某城市完整天气数据包。"""

    city: CityInfo
    history_days: list[DaySummary] = field(default_factory=list)
    forecast_days: list[DaySummary] = field(default_factory=list)
    air: Optional[AirBrief] = None
    generated_at: str = ""

    def to_prompt_text(self) -> str:
        """把整包数据拼成大模型可读的文本。"""
        lines = [f"城市：{self.city.adm1} {self.city.name}（{self.generated_at} 查询）"]
        lines.extend(d.to_line() for d in self.history_days)
        lines.extend(d.to_line() for d in self.forecast_days)
        if self.air:
            lines.append(self.air.to_line())
        return "\n".join(lines)


@dataclass
class CityAnswer:
    """单城市单日的结论文本（来自大模型 JSON 输出）。"""

    city: str
    weather: str
    temp: str
    wind: str
    suitability: str
    comment: str


@dataclass
class DayAnswer:
    """单日期（含多个城市）的结论文本。"""

    date: str
    cities: list[CityAnswer] = field(default_factory=list)


@dataclass
class AnswerReport:
    """大模型结构化回答的完整数据包。

    overview: 一句话概览；days: 逐日期逐城市结论；advice: 出行建议四件套。
    """

    overview: str = ""
    days: list[DayAnswer] = field(default_factory=list)
    clothing: str = ""
    gear: str = ""
    activity: str = ""
    air: str = ""


def parse_answer_json(raw: str) -> AnswerReport:
    """把大模型返回的 JSON 文本解析成 AnswerReport。

    做了三层防御：剥掉可能的 markdown 代码块围栏、字段缺省填空字符串、
    类型不符的列表字段自动包装。

    Raises:
        json.JSONDecodeError: raw 不是合法 JSON。
        ValueError: JSON 结构对不上（如 days 不是列表）。
    """
    text = raw.strip()
    if text.startswith("```"):  # 剥掉 ```json ... ``` 围栏
        text = text.strip("`").lstrip("json").strip()
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError(f"回答 JSON 顶层应是对象，实际是 {type(data).__name__}")

    def as_str(value: Any) -> str:
        return str(value).strip() if value is not None else ""

    report = AnswerReport(overview=as_str(data.get("overview")))
    days = data.get("days") or []
    if isinstance(days, dict):
        days = [days]
    for day_item in days:
        if not isinstance(day_item, dict):
            continue
        cities = day_item.get("cities") or []
        if isinstance(cities, dict):
            cities = [cities]
        day = DayAnswer(date=as_str(day_item.get("date")))
        for c in cities:
            if not isinstance(c, dict):
                continue
            day.cities.append(CityAnswer(
                city=as_str(c.get("city")),
                weather=as_str(c.get("weather")),
                temp=as_str(c.get("temp")),
                wind=as_str(c.get("wind")),
                suitability=as_str(c.get("suitability")),
                comment=as_str(c.get("comment")),
            ))
        if day.date or day.cities:
            report.days.append(day)
    advice = data.get("advice") or {}
    if isinstance(advice, str):
        advice = {"clothing": advice}
    report.clothing = as_str(advice.get("clothing"))
    report.gear = as_str(advice.get("gear"))
    report.activity = as_str(advice.get("activity"))
    report.air = as_str(advice.get("air"))
    if not report.overview and not report.days:
        raise ValueError("回答 JSON 既无 overview 也无 days，视为无效")
    return report


# ---------------------------------------------------------------------------
# 装饰器：通用能力增强
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# 装饰器：ttl_cache（和风天气接口结果缓存）
# retry / timed 已抽到 chat_with_llm.py，从那里 import
# ---------------------------------------------------------------------------
def ttl_cache(seconds: int = 600) -> Callable:
    """简单缓存装饰器：相同城市 10 分钟内不重复请求和风 API。"""

    def decorator(func: Callable) -> Callable:
        store: dict[str, tuple[float, Any]] = {}

        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            key = f"{func.__name__}:{args}"
            hit = store.get(key)
            if hit and time.time() - hit[0] < seconds:
                return hit[1]
            result = func(*args, **kwargs)
            store[key] = (time.time(), result)
            return result

        return wrapper

    return decorator


# ---------------------------------------------------------------------------
# 和风天气密钥（千问密钥已由 chat_with_llm 加载）
# ---------------------------------------------------------------------------
ENV = load_env()
QW_KEY = ENV.get("QWEATHER_API_KEY", "")
QW_HOST = ENV.get("QWEATHER_API_HOST", "")


def qweather_get(path: str, params: dict[str, str]) -> dict:
    """同步请求和风天气 API 并返回 JSON（单次后台调用用）。

    Raises:
        RuntimeError: 状态码非 200 或网络失败。
    """
    return asyncio.run(qweather_get_async(path, params))


async def qweather_get_async(path: str, params: dict[str, str],
                             client: Optional[httpx.AsyncClient] = None) -> dict:
    """异步请求和风天气 API 并返回 JSON。

    Args:
        client: 可复用的 httpx.AsyncClient；不传则临时创建一个。

    Raises:
        RuntimeError: 状态码非 200 或网络失败。
    """
    url = f"https://{QW_HOST}{path}"
    headers = {"X-QW-Api-Key": QW_KEY}
    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS)
    try:
        resp = await client.get(url, params=params, headers=headers)
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise RuntimeError(f"和风天气请求失败: {exc}") from exc
    finally:
        if own_client:
            await client.aclose()
    data = resp.json()
    if str(data.get("code", "200")) != "200":
        raise RuntimeError(f"和风天气返回错误: {data.get('code')}")
    return data


# chat_completion 已从 chat_with_llm 导入，这里不再重复定义。


# ---------------------------------------------------------------------------
# 数据获取（异步并发版）：城市 → 历史 + 预报 + 空气
# ---------------------------------------------------------------------------
async def search_city_async(query: str, client: httpx.AsyncClient) -> CityInfo:
    """GeoAPI 城市查询，返回第一个匹配结果。

    Raises:
        RuntimeError: 查不到该城市。
    """
    data = await qweather_get_async(
        "/geo/v2/city/lookup", {"location": query, "lang": "zh"}, client)
    locations = data.get("location") or []
    if not locations:
        raise RuntimeError(f"查不到城市「{query}」，换个写法试试（如用城市全名）")
    top = locations[0]
    return CityInfo(
        name=top["name"],
        location_id=top["id"],
        lat=top["lat"],
        lon=top["lon"],
        adm1=top.get("adm1", ""),
        adm2=top.get("adm2", ""),
    )


async def fetch_forecast_async(city: CityInfo, client: httpx.AsyncClient) -> list[DaySummary]:
    """获取未来 7 天预报。"""
    data = await qweather_get_async("/v7/weather/7d", {"location": city.location_id}, client)
    return [
        DaySummary(
            fx_date=d["fxDate"], tag="预报",
            temp_max=d["tempMax"], temp_min=d["tempMin"],
            text_day=d["textDay"], precip=d["precip"], humidity=d["humidity"],
            wind_dir=d.get("windDirDay", ""), wind_scale=d.get("windScaleDay", ""),
        )
        for d in data.get("daily", [])
    ]


async def fetch_history_one_day_async(
    city: CityInfo, day: date, client: httpx.AsyncClient,
) -> Optional[DaySummary]:
    """获取某一天的历史天气（近 10 天内，不含今天）。失败返回 None，不中断整体。"""
    try:
        data = await qweather_get_async(
            "/v7/historical/weather",
            {"location": city.location_id, "date": day.strftime("%Y%m%d")},
            client,
        )
    except RuntimeError:
        return None
    daily = data.get("weatherDaily")
    if not daily:
        return None
    return DaySummary(
        fx_date=daily["date"], tag="历史",
        temp_max=daily["tempMax"], temp_min=daily["tempMin"],
        text_day="", precip=daily["precip"], humidity=daily["humidity"],
    )


async def fetch_history_async(
    city: CityInfo, client: httpx.AsyncClient, days: int = HISTORY_DAYS,
) -> list[DaySummary]:
    """获取过去 N 天历史天气（N 天并发请求，而非逐天串行）。"""
    today = date.today()
    tasks = [
        fetch_history_one_day_async(city, today - timedelta(days=offset), client)
        for offset in range(1, days + 1)
    ]
    results = await asyncio.gather(*tasks)
    return [r for r in results if r]


async def fetch_air_async(city: CityInfo, client: httpx.AsyncClient) -> Optional[AirBrief]:
    """获取实时空气质量（取中国国标 AQI）。失败返回 None。"""
    try:
        data = await qweather_get_async(
            f"/airquality/v1/current/{float(city.lat):.2f}/{float(city.lon):.2f}",
            {"lang": "zh"}, client,
        )
    except RuntimeError:
        return None
    for idx in data.get("indexes", []):
        if idx.get("code") == "cn-mee":
            pollutant = idx.get("primaryPollutant") or {}
            return AirBrief(
                aqi=str(idx.get("aqiDisplay", "")),
                category=idx.get("category", ""),
                primary_pollutant=pollutant.get("name", "无"),
            )
    return None


@timed
async def build_report_async(city_query: str, client: httpx.AsyncClient) -> WeatherReport:
    """组装单个城市的完整天气数据包（内部三路并发：历史 + 预报 + 空气）。"""
    city = await search_city_async(city_query, client)
    history, forecast, air = await asyncio.gather(
        fetch_history_async(city, client),
        fetch_forecast_async(city, client),
        fetch_air_async(city, client),
    )
    return WeatherReport(
        city=city, history_days=history, forecast_days=forecast,
        air=air, generated_at=time.strftime("%Y-%m-%d %H:%M"),
    )


async def build_reports_async(city_queries: list[str]) -> list[WeatherReport]:
    """并发组装多个城市（最多 3 个）的天气数据包。

    所有城市所有接口同时发出请求，总耗时 ≈ 单城市单接口的耗时。
    某个城市失败不影响其他城市，失败城市会被跳过。
    """
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
        tasks = [build_report_async(q, client) for q in city_queries]
        reports = await asyncio.gather(*tasks, return_exceptions=True)
    return [r for r in reports if isinstance(r, WeatherReport)]


# ---------------------------------------------------------------------------
# 对话逻辑
# ---------------------------------------------------------------------------
def extract_cities_and_dates(question: str) -> tuple[list[str], list[str]]:
    """用大模型从用户问题中提取城市列表（1~3 个）和具体日期。

    Returns:
        (城市列表, 日期列表)。无日期时列表为空；提取失败时城市返回原文。

    Raises:
        RuntimeError: 大模型调用失败时按原文当地名处理。
    """
    today = date.today().strftime("%Y-%m-%d")
    messages = [
        {"role": "system", "content": CITY_SYSTEM_PROMPT.format(today=today)},
        {"role": "user", "content": question},
    ]
    try:
        content = chat_completion(messages, temperature=0.0, json_mode=True)
        data = json.loads(content)
        cities = data.get("cities") or data.get("city") or []
        if isinstance(cities, str):          # 防御：模型把数组写成字符串
            cities = [cities]
        cities = [str(c).strip() for c in cities if str(c).strip()][:MAX_CITIES]
        # 去重且保持顺序
        seen: set[str] = set()
        cities = [c for c in cities if not (c in seen or seen.add(c))]
        dates = data.get("dates") or []
        if isinstance(dates, str):
            dates = [dates]
        dates = [str(d).strip() for d in dates if str(d).strip()][:5]
        return cities, dates
    except (RuntimeError, json.JSONDecodeError, ValueError):
        return [question.strip()[:20]], []


def answer(question: str, history: list[dict]) -> tuple[str, Optional[dict]]:
    """主入口：先判断用户是否问到具体日期，问到才拉数据并分析。

    支持 1~3 个城市并发查询。分析阶段用 JSON 模式让模型固定输出结构化
    结果；JSON 解析失败会重试，仍失败则降级为自然语言回复。

    Args:
        question: 用户输入。
        history: 对话历史（OpenAI 消息格式，会被截断到最近几轮）。

    Returns:
        (纯文本回复, 结构化 JSON dict 或 None)。
        追问/报错场景 JSON 为 None。
    """
    def done(text: str, structured: Optional[dict] = None) -> tuple[str, Optional[dict]]:
        history.append({"role": "user", "content": question})
        history.append({"role": "assistant", "content": text})
        return text, structured

    cities, dates = extract_cities_and_dates(question)
    if not cities:
        return ("你想查哪个城市的天气呢？告诉我城市名和具体日期，"
                "比如「北京这周六天气怎么样」。", None)

    cities_text = "、".join(cities)
    if not dates:
        # 用户没有问到具体日期：不查数据、不分析，引导他说出日期
        return (
            f"好的，{cities_text}。你想了解哪一天（或哪几天）的天气呢？"
            "可以直接说「明天」「这周六」或具体日期，"
            "我能查过去 5 天到未来 7 天的天气。", None
        )

    # 多城市并发拉数据（异步事件循环在这里驱动）
    reports = asyncio.run(build_reports_async(cities))
    if not reports:
        return (f"抱歉，{cities_text} 的天气数据都查不到，请检查城市名后重试。", None)

    data_text = "\n\n".join(r.to_prompt_text() for r in reports)
    dates_text = "、".join(dates)

    messages: list[dict] = [{"role": "system", "content": ANALYST_SYSTEM_PROMPT}]
    messages.extend(history[-MAX_HISTORY_TURNS:])
    messages.append({
        "role": "user",
        "content": (
            f"我的问题：{question}\n\n"
            f"请重点回答 {dates_text} 的天气情况"
            f"（涉及城市：{'、'.join(r.city.name for r in reports)}）。"
            f"\n\n各城市天气原始数据：\n{data_text}"
        ),
    })

    # 第一步：JSON 模式请求，失败自动重试，仍失败降级纯文本
    report: Optional[AnswerReport] = None
    last_error: Optional[Exception] = None
    for _ in range(JSON_RETRY_ATTEMPTS):
        try:
            raw = chat_completion(messages, json_mode=True)
            report = parse_answer_json(raw)
            break
        except (RuntimeError, json.JSONDecodeError, ValueError) as exc:
            last_error = exc
            print(f"[重试] 结构化输出解析失败: {exc}")
    if report is None:
        print(f"[降级] 结构化输出不可用，改用自然语言回复（{last_error}）")
        text = chat_completion(messages)
        return done(text)

    # 第二步：结构化结果 → 渲染纯文本（给历史和降级显示用）+ JSON（给前端）
    structured = report_to_dict(report)
    return done(report_to_plain_text(report), structured)


def report_to_dict(report: AnswerReport) -> dict:
    """把 AnswerReport 转成给前端的 JSON dict。"""
    return {
        "overview": report.overview,
        "days": [
            {
                "date": d.date,
                "cities": [
                    {
                        "city": c.city, "weather": c.weather, "temp": c.temp,
                        "wind": c.wind, "suitability": c.suitability,
                        "comment": c.comment,
                    }
                    for c in d.cities
                ],
            }
            for d in report.days
        ],
        "advice": {
            "clothing": report.clothing, "gear": report.gear,
            "activity": report.activity, "air": report.air,
        },
    }


def report_to_plain_text(report: AnswerReport) -> str:
    """把结构化结果渲染成可读纯文本（存进对话历史，多轮追问用）。"""
    lines = [f"概览：{report.overview}"] if report.overview else []
    for day in report.days:
        lines.append(f"\n【{day.date}】")
        for c in day.cities:
            lines.append(
                f"{c.city}：{c.weather}，{c.temp}，{c.wind}，"
                f"出行 {c.suitability}。{c.comment}"
            )
    advice = [f"\n建议："]
    if report.clothing:
        advice.append(f"- 穿衣：{report.clothing}")
    if report.gear:
        advice.append(f"- 随身：{report.gear}")
    if report.activity:
        advice.append(f"- 活动：{report.activity}")
    if report.air:
        advice.append(f"- 空气：{report.air}")
    lines.extend(advice)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 网页服务器
# ---------------------------------------------------------------------------
PAGE_HTML = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>天气智能助手</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: "Microsoft YaHei", sans-serif; background: #eef3f8; height: 100vh; display: flex; }
  .side { width: 280px; background: #2c5f8a; color: #fff; padding: 24px 20px; }
  .side h1 { font-size: 20px; margin-bottom: 8px; }
  .side p { font-size: 13px; opacity: .8; line-height: 1.6; }
  .side .tips { margin-top: 24px; font-size: 12px; opacity: .7; line-height: 2; }
  .chat { flex: 1; display: flex; flex-direction: column; }
  .log { flex: 1; overflow-y: auto; padding: 24px 32px; }
  .bubble { max-width: 640px; margin: 10px 0; padding: 12px 16px; border-radius: 12px;
            white-space: pre-wrap; line-height: 1.7; font-size: 15px; }
  .user { background: #2c5f8a; color: #fff; margin-left: auto; }
  .bot { background: #fff; color: #222; box-shadow: 0 1px 3px rgba(0,0,0,.08); }
  .bot.pending { color: #999; font-style: italic; }
  .card { padding: 16px 20px; }
  .card-overview { font-weight: bold; font-size: 15px; margin-bottom: 10px; line-height: 1.6; }
  .card-date { font-weight: bold; color: #2c5f8a; margin: 12px 0 6px; font-size: 14px; }
  .card-table { border-collapse: collapse; width: 100%; font-size: 13px; white-space: normal; }
  .card-table th, .card-table td { border: 1px solid #dfe6ee; padding: 6px 10px; text-align: left; }
  .card-table th { background: #f0f5fa; color: #2c5f8a; }
  .card-table td.good { color: #1a7f37; font-weight: bold; }
  .card-table td.bad { color: #c0392b; font-weight: bold; }
  .card-advice { font-size: 13px; line-height: 1.9; color: #333; }
  .inputbar { display: flex; gap: 10px; padding: 16px 32px; background: #fff;
              border-top: 1px solid #e3e8ee; }
  .inputbar input { flex: 1; font-size: 15px; padding: 12px 16px; border: 1px solid #cfd8e3;
                   border-radius: 10px; outline: none; }
  .inputbar input:focus { border-color: #2c5f8a; }
  .inputbar button { font-size: 15px; padding: 12px 28px; background: #2c5f8a; color: #fff;
                    border: none; border-radius: 10px; cursor: pointer; }
  .inputbar button:disabled { background: #9ab; cursor: not-allowed; }
</style>
</head>
<body>
<div class="side">
  <h1>天气智能助手</h1>
  <p>过去 5 天 + 未来 7 天<br>天气分析 & 出行建议<br>支持一次问 3 个城市</p>
  <div class="tips">
    试试这样问（记得带上日期）：<br>
    · 北京这周六天气怎么样<br>
    · 上海明天适合出去玩吗<br>
    · 北京上海广州这周六哪个适合出游<br>
    · 成都昨天冷不冷
  </div>
</div>
<div class="chat">
  <div class="log" id="log">
    <div class="bubble bot">你好！我是天气智能助手。告诉我城市 + 具体日期（如「北京这周六天气怎么样」），我会查询过去 5 天到未来 7 天的数据，重点回答你问的那一天。一次最多可以同时问 3 个城市（如「北京上海广州这周六哪个适合出游」）。</div>
  </div>
  <div class="inputbar">
    <input id="q" placeholder="输入城市或问题，回车发送" autofocus>
    <button id="send">发送</button>
  </div>
</div>
<script>
const log = document.getElementById('log');
const q = document.getElementById('q');
const send = document.getElementById('send');

function addBubble(cls, text) {
  const div = document.createElement('div');
  div.className = 'bubble ' + cls;
  div.textContent = text;
  log.appendChild(div);
  log.scrollTop = log.scrollHeight;
  return div;
}

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}

/* 把后端返回的结构化 JSON 渲染成卡片 */
function renderCard(pending, data) {
  pending.className = 'bubble bot card';
  pending.textContent = '';
  const box = pending;

  if (data.overview) {
    box.appendChild(el('div', 'card-overview', data.overview));
  }
  for (const day of (data.days || [])) {
    if (day.date) box.appendChild(el('div', 'card-date', '📅 ' + day.date));
    const table = el('table', 'card-table');
    const head = el('tr');
    ['城市', '天气', '温度', '出行', '点评'].forEach(t => head.appendChild(el('th', '', t)));
    table.appendChild(head);
    for (const c of (day.cities || [])) {
      const row = el('tr');
      row.appendChild(el('td', '', c.city));
      row.appendChild(el('td', '', c.weather));
      row.appendChild(el('td', '', c.temp));
      const suit = el('td', '', c.suitability);
      if (c.suitability && c.suitability.indexOf('不建议') >= 0) suit.classList.add('bad');
      else if (c.suitability && c.suitability.indexOf('适合') >= 0) suit.classList.add('good');
      row.appendChild(suit);
      row.appendChild(el('td', '', c.comment));
      table.appendChild(row);
    }
    box.appendChild(table);
  }
  const advice = data.advice || {};
  const items = [['👕 穿衣', advice.clothing], ['🎒 随身', advice.gear],
                 ['🚶 活动', advice.activity], ['🌿 空气', advice.air]];
  const useful = items.filter(it => it[1]);
  if (useful.length) {
    box.appendChild(el('div', 'card-date', '出行建议'));
    for (const [label, val] of useful) {
      box.appendChild(el('div', 'card-advice', label + '：' + val));
    }
  }
  log.scrollTop = log.scrollHeight;
}

async function ask() {
  const text = q.value.trim();
  if (!text) return;
  q.value = '';
  addBubble('user', text);
  send.disabled = true;
  const pending = addBubble('bot pending', '正在查询天气数据并分析，约需 10~30 秒……');
  try {
    const resp = await fetch('/api/chat', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({message: text})
    });
    const data = await resp.json();
    if (data.error) {
      pending.className = 'bubble bot';
      pending.textContent = data.error;
    } else if (data.reply_json) {
      renderCard(pending, data.reply_json);
    } else {
      pending.className = 'bubble bot';
      pending.textContent = data.reply || '（空回复）';
    }
  } catch (err) {
    pending.className = 'bubble bot';
    pending.textContent = '请求失败：' + err.message;
  } finally {
    send.disabled = false;
    q.focus();
  }
}

send.onclick = ask;
q.onkeydown = e => { if (e.key === 'Enter') ask(); };
</script>
</body>
</html>"""


class ChatHandler(BaseHTTPRequestHandler):
    """HTTP 服务器：首页 + /api/chat 聊天接口。"""

    conversation: list[dict] = []  # 简单起见，全局一份对话历史

    def do_GET(self) -> None:
        """返回聊天页面。"""
        if self.path != "/":
            self.send_error(404)
            return
        body = PAGE_HTML.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        """处理聊天请求。"""
        if self.path != "/api/chat":
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", 0))
        payload = json.loads(self.rfile.read(length) or b"{}")
        message = str(payload.get("message", "")).strip()

        result: dict[str, Any] = {}
        try:
            if not message:
                raise RuntimeError("请输入内容")
            text, structured = answer(message, ChatHandler.conversation)
            result["reply"] = text
            if structured:
                result["reply_json"] = structured
        except Exception as exc:  # 对话接口兜底：出错也返回可读信息
            result["error"] = f"出错了：{exc}"

        body = json.dumps(result, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        """静默默认访问日志，只留业务输出。"""
        return


def main() -> None:
    """程序入口：启动本地服务器并自动打开浏览器。"""
    for name, value in (("DASHSCOPE_API_KEY", DASHSCOPE_KEY),
                        ("QWEATHER_API_KEY", QW_KEY),
                        ("QWEATHER_API_HOST", QW_HOST)):
        if not value:
            raise RuntimeError(f"请在 .env 中配置 {name}")

    server = ThreadingHTTPServer((SERVER_HOST, SERVER_PORT), ChatHandler)
    url = f"http://{SERVER_HOST}:{SERVER_PORT}"
    print(f"天气智能助手已启动：{url}  （Ctrl+C 退出）")
    webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已退出")
        server.server_close()


if __name__ == "__main__":
    main()
