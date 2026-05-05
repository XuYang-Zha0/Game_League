from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any, Dict, Generator, List, Optional

import requests
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field


def load_local_env() -> None:
    env_path = os.path.abspath(os.path.join(os.path.dirname(__file__), ".env"))
    if not os.path.exists(env_path):
        return
    with open(env_path, "r", encoding="utf-8") as env_file:
        for line in env_file:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


load_local_env()

router = APIRouter()

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "").strip()
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").strip().rstrip("/")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash").strip()
DEEPSEEK_TIMEOUT_SECONDS = max(10, int(os.getenv("DEEPSEEK_TIMEOUT_SECONDS", "45")))
DEEPSEEK_MAX_TOKENS = max(256, int(os.getenv("DEEPSEEK_MAX_TOKENS", "1000")))
DEEPSEEK_TEMPERATURE = float(os.getenv("DEEPSEEK_TEMPERATURE", "0.25"))
DEEPSEEK_THINKING = os.getenv("DEEPSEEK_THINKING", "disabled").strip().lower()
AI_CONTEXT_CHAR_LIMIT = max(2000, int(os.getenv("AI_CONTEXT_CHAR_LIMIT", "18000")))
AI_HISTORY_TURNS = max(0, int(os.getenv("AI_HISTORY_TURNS", "4")))


class ChatMessage(BaseModel):
    role: str = Field(default="user")
    content: str = Field(default="")


class AIChatRequest(BaseModel):
    gameId: str = Field(default="")
    gameName: str = Field(default="")
    page: str = Field(default="")
    question: str = Field(default="", min_length=1, max_length=3000)
    context: Dict[str, Any] = Field(default_factory=dict)
    history: List[ChatMessage] = Field(default_factory=list)


def compact_json(value: Any, limit: int = AI_CONTEXT_CHAR_LIMIT) -> str:
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
    if len(text) <= limit:
        return text
    return text[: limit - 80] + "...[context truncated]"


def clean_history(history: List[ChatMessage]) -> List[Dict[str, str]]:
    allowed_roles = {"user", "assistant"}
    cleaned: List[Dict[str, str]] = []
    for item in history[-AI_HISTORY_TURNS * 2 :]:
        role = str(item.role or "").strip().lower()
        content = str(item.content or "").strip()
        if role not in allowed_roles or not content:
            continue
        cleaned.append({"role": role, "content": content[:2000]})
    return cleaned


GAME_PROMPTS: Dict[str, str] = {
    "cs2": (
        "你是 Game League 的 CS2 赛事数据分析助手。"
        "你精通 Counter-Strike 2 职业赛场，熟悉 HLTV rating 2.1、ADR、KAST、impact rating、"
        "首杀成功率（opening kill success）、多杀回合率、残局胜率等核心指标。"
        "你了解 CS2 竞技图池（Mirage、Inferno、Ancient、Anubis、Nuke、Overpass、Dust2），"
        "能结合地图特性分析战队/选手风格（如某队偏向 Inferno 的香蕉道控制、某队在 Ancient 的 CT 防守效率）。"
        "你关注赛事体系：S 级（BLAST、IEM、PGL、Major）、A 级（CCT、ESL Challenger）等。"
        "你理解 CS2 经济系统（force buy、eco、full buy）及其对回合走势的影响。"
        "回答规则：\n"
        "1. 只能依据提供的项目数据和用户问题做分析；数据不足时要明确说明。\n"
        "2. 回答用中文，口吻偏向赛事解说/分析师风格，直接给结论再给依据。\n"
        "3. 涉及选手时优先引用 rating 2.1、ADR、impact 数据。\n"
        "4. 涉及战队时关注近期状态（连胜/连败）、地图池深度、风格特点。\n"
        "5. 涉及比赛时关注地图 BP、关键回合、经济转折点。\n"
        "6. 不要编造数据库里没有的事实。"
    ),
    "valorant": (
        "你是 Game League 的无畏契约（VALORANT）赛事数据分析助手。"
        "你精通 VALORANT 职业赛场，熟悉 VCT 赛事体系（国际联赛、大师赛、冠军赛）、"
        "ACS（场均战斗评分）、K/D、首杀率（FK/FD）、残局胜率、rating 等核心指标。"
        "你了解 VALORANT 特工体系（决斗者、控场者、先锋、哨卫）及其在不同地图上的战术定位。"
        "你熟悉竞技地图池（Ascent、Bind、Haven、Split、Lotus、Pearl 等）的地图特性。"
        "你关注赛区差异：Americas、EMEA、Pacific、China 各赛区的风格特点。"
        "你理解 VALORANT 的经济系统（credits、半场换边、加时规则）。"
        "回答规则：\n"
        "1. 只能依据提供的项目数据和用户问题做分析；数据不足时要明确说明。\n"
        "2. 回答用中文，口吻偏向赛事解说/分析师风格，直接给结论再给依据。\n"
        "3. 涉及选手时优先引用 ACS、K/D、首杀率、残局数据。\n"
        "4. 涉及战队时关注近期状态、赛区排名、特工组合偏好。\n"
        "5. 涉及比赛时关注地图 BP、特工选择、关键回合翻盘。\n"
        "6. 不要编造数据库里没有的事实。"
    ),
    "lol": (
        "你是 Game League 的英雄联盟（League of Legends）赛事数据分析助手。"
        "你精通 LOL 职业赛场，熟悉四大赛区（LCK、LPL、LEC、LCS）及国际赛事体系（MSI、Worlds）。"
        "你关注核心指标：KDA、DPM（每分钟伤害）、伤害占比、参团率（KP%）、"
        "CS 差（CSD）、经验差（XPD）、视野得分、一血率、一塔率等。"
        "你了解 LOL 位置体系（上单、打野、中单、ADC、辅助）及其在不同版本中的战术权重。"
        "你关注版本（patch）对英雄优先级和战术 meta 的影响。"
        "你理解 LOL 的资源系统：小龙控制率、峡谷先锋/纳什男爵争夺、防御塔经济等。"
        "回答规则：\n"
        "1. 只能依据提供的项目数据和用户问题做分析；数据不足时要明确说明。\n"
        "2. 回答用中文，口吻偏向赛事解说/分析师风格，直接给结论再给依据。\n"
        "3. 涉及选手时优先引用 KDA、DPM、参团率、对位数据。\n"
        "4. 涉及战队时关注近期状态、赛区排名、版本适应能力。\n"
        "5. 涉及比赛时关注 BP 博弈、关键资源团、中期运营转折。\n"
        "6. 不要编造数据库里没有的事实。"
    ),
}

GAME_WELCOME_MESSAGES: Dict[str, str] = {
    "cs2": "你好！我是 CS2 赛事 AI 分析助手。你可以问我关于战队排名、选手数据、地图胜率、近期比赛等方面的问题。",
    "valorant": "你好！我是无畏契约赛事 AI 分析助手。你可以问我关于 VCT 赛事、选手 ACS/KDA、战队赛区排名等方面的问题。",
    "lol": "你好！我是英雄联盟赛事 AI 分析助手。你可以问我关于 LCK/LPL 赛事、选手数据、版本 meta、战队运营风格等方面的问题。",
}


def build_messages(payload: AIChatRequest) -> List[Dict[str, str]]:
    game_id = (payload.gameId or "").strip().lower()
    game_name = payload.gameName or game_id or "当前游戏"
    context_text = compact_json(
        {
            "gameId": game_id,
            "gameName": game_name,
            "page": payload.page,
            "generatedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "data": payload.context,
        }
    )

    system_prompt = GAME_PROMPTS.get(game_id, GAME_PROMPTS["cs2"])
    context_prompt = (
        f"当前游戏：{game_name}\n"
        f"当前页面：{payload.page or '-'}\n"
        f"可用数据快照 JSON：\n{context_text}"
    )

    messages: List[Dict[str, str]] = [
        {"role": "system", "content": system_prompt},
        {"role": "system", "content": context_prompt},
    ]
    messages.extend(clean_history(payload.history))
    messages.append({"role": "user", "content": payload.question.strip()})
    return messages


def call_deepseek(messages: List[Dict[str, str]]) -> Dict[str, Any]:
    if not DEEPSEEK_API_KEY:
        raise HTTPException(
            status_code=503,
            detail="DEEPSEEK_API_KEY is not configured in backend/.env",
        )

    request_body: Dict[str, Any] = {
        "model": DEEPSEEK_MODEL,
        "messages": messages,
        "temperature": DEEPSEEK_TEMPERATURE,
        "max_tokens": DEEPSEEK_MAX_TOKENS,
        "stream": False,
    }
    if DEEPSEEK_THINKING in {"enabled", "disabled"}:
        request_body["thinking"] = {"type": DEEPSEEK_THINKING}

    try:
        response = requests.post(
            f"{DEEPSEEK_BASE_URL}/chat/completions",
            headers={
                "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                "Content-Type": "application/json",
            },
            json=request_body,
            timeout=(10, DEEPSEEK_TIMEOUT_SECONDS),
        )
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"DeepSeek request failed: {exc}") from exc

    if response.status_code >= 400:
        raise HTTPException(
            status_code=502,
            detail=f"DeepSeek API returned {response.status_code}: {response.text[:500]}",
        )

    try:
        data = response.json()
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="DeepSeek API returned invalid JSON") from exc

    choices = data.get("choices") if isinstance(data, dict) else None
    first = choices[0] if isinstance(choices, list) and choices else {}
    message = first.get("message") if isinstance(first, dict) else {}
    answer = str((message or {}).get("content") or "").strip()
    if not answer:
        raise HTTPException(status_code=502, detail="DeepSeek API returned an empty answer")

    return {
        "answer": answer,
        "model": data.get("model") or DEEPSEEK_MODEL,
        "usage": data.get("usage") or {},
        "finishReason": first.get("finish_reason") if isinstance(first, dict) else "",
    }


@router.post("/api/ai/chat")
def ai_chat(payload: AIChatRequest) -> Dict[str, Any]:
    question = payload.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="question is required")
    messages = build_messages(payload)
    result = call_deepseek(messages)
    return {"success": True, "data": result}


def _deepseek_stream_generator(messages: List[Dict[str, str]]) -> Generator[str, None, None]:
    if not DEEPSEEK_API_KEY:
        yield f"data: {json.dumps({'error': 'DEEPSEEK_API_KEY is not configured'})}\n\n"
        return

    request_body: Dict[str, Any] = {
        "model": DEEPSEEK_MODEL,
        "messages": messages,
        "temperature": DEEPSEEK_TEMPERATURE,
        "max_tokens": DEEPSEEK_MAX_TOKENS,
        "stream": True,
    }
    if DEEPSEEK_THINKING in {"enabled", "disabled"}:
        request_body["thinking"] = {"type": DEEPSEEK_THINKING}

    try:
        response = requests.post(
            f"{DEEPSEEK_BASE_URL}/chat/completions",
            headers={
                "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                "Content-Type": "application/json",
            },
            json=request_body,
            timeout=(10, DEEPSEEK_TIMEOUT_SECONDS),
            stream=True,
        )
    except requests.RequestException as exc:
        yield f"data: {json.dumps({'error': f'DeepSeek request failed: {exc}'})}\n\n"
        return

    if response.status_code >= 400:
        yield f"data: {json.dumps({'error': f'DeepSeek API returned {response.status_code}'})}\n\n"
        return

    try:
        for line in response.iter_lines(decode_unicode=True):
            if not line or not line.startswith("data:"):
                continue
            payload_str = line[len("data:"):].strip()
            if payload_str == "[DONE]":
                break
            try:
                chunk = json.loads(payload_str)
            except (ValueError, TypeError):
                continue
            choices = chunk.get("choices") if isinstance(chunk, dict) else None
            if not choices:
                continue
            delta = (choices[0] or {}).get("delta") if isinstance(choices, list) else None
            content = (delta or {}).get("content")
            if content:
                yield f"data: {json.dumps({'c': content})}\n\n"
            if (choices[0] or {}).get("finish_reason"):
                yield f"data: {json.dumps({'done': True})}\n\n"
    except Exception as exc:
        yield f"data: {json.dumps({'error': f'Stream error: {exc}'})}\n\n"


@router.post("/api/ai/chat/stream")
def ai_chat_stream(payload: AIChatRequest):
    question = payload.question.strip()
    if not question:
        raise HTTPException(status_code=400, detail="question is required")
    messages = build_messages(payload)
    return StreamingResponse(
        _deepseek_stream_generator(messages),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/api/ai/status")
def ai_status() -> Dict[str, Any]:
    return {
        "success": True,
        "data": {
            "configured": bool(DEEPSEEK_API_KEY),
            "model": DEEPSEEK_MODEL,
            "baseUrl": DEEPSEEK_BASE_URL,
            "thinking": DEEPSEEK_THINKING,
        },
    }


@router.get("/api/ai/welcome/{game_id}")
def ai_welcome(game_id: str) -> Dict[str, Any]:
    gid = (game_id or "").strip().lower()
    welcome = GAME_WELCOME_MESSAGES.get(gid, GAME_WELCOME_MESSAGES["cs2"])
    return {"success": True, "data": {"message": welcome}}
