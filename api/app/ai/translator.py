"""Natural language → StrategySpec via the Claude API.

The model only ever produces JSON for the DSL; that JSON is validated by the same
pydantic schema as hand-written specs before anything runs. If validation fails,
the model gets the error once to fix it; after that the request fails loudly.
"""

from __future__ import annotations

import copy
import json
import os

import httpx
from pydantic import ValidationError

from app.strategies.dsl import StrategySpec

DEFAULT_BASE_URL = "https://api.anthropic.com"
DEFAULT_MODEL = "claude-sonnet-5-5"
TOOL_NAME = "submit_strategy"
MAX_TEXT = 3000


class AIError(RuntimeError):
    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


def _inline_refs(schema: dict) -> dict:
    """Inline $defs so the tool schema has no $ref (simplest form for any client)."""
    defs = schema.pop("$defs", {})

    def walk(node):
        if isinstance(node, dict):
            if "$ref" in node:
                return walk(copy.deepcopy(defs[node["$ref"].split("/")[-1]]))
            return {k: walk(v) for k, v in node.items() if k != "title"}
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    return walk(schema)


def tool_definition() -> dict:
    return {
        "name": TOOL_NAME,
        "description": "Submit the trading strategy translated into the Strategy Lab DSL.",
        "input_schema": {
            "type": "object",
            "properties": {
                "spec": _inline_refs(StrategySpec.model_json_schema()),
                "explanation_tr": {
                    "type": "string",
                    "description": "Türkçe, 2-5 cümle: stratejiyi nasıl yorumladın, hangi varsayımları yaptın.",
                },
                "unsupported": {
                    "type": "array", "items": {"type": "string"},
                    "description": "Türkçe: kullanıcının istediği ama DSL'de ifade edilemeyen her şey.",
                },
            },
            "required": ["spec", "explanation_tr", "unsupported"],
        },
    }


SYSTEM_PROMPT = """You translate a trader's strategy description (usually Turkish) into the
Strategy Lab DSL and submit it with the submit_strategy tool. Always call the tool exactly once.

DSL facts:
- Long/flat only (no shorting, no leverage, no pyramiding). Signals are evaluated at bar CLOSE,
  orders fill at the NEXT bar's open. One timeframe per strategy (the one the user runs it on).
- indicators: list of {id, type, ...}. Types and their params:
  sma/ema/rsi/roc: period, source(open|high|low|close|volume, default close)
  highest/lowest: period (uses high/low by default; INCLUDES the current bar)
  atr: period
  macd: fast, slow, signal → reference as "<id>.line", "<id>.signal", "<id>.hist"
  bb (Bollinger): period, std → "<id>.upper", "<id>.middle", "<id>.lower"
- entry/exit: {"all": [conditions], "any": [conditions]} → all must hold AND (if any is given)
  at least one of any. Condition: {"left": operand, "op": one of > < >= <= crosses_above
  crosses_below, "right": operand}.
- Operands are strings: open high low close volume, hour (UTC 0-23), weekday (0=Monday),
  an indicator id or output, a number like "30", and an optional look-back suffix "[n]"
  meaning n bars ago (e.g. "close[1]"). "Break of the previous 20-bar high" = close > hh[1]
  with hh = highest(20).
- Exits: exit conditions and/or stop_loss_pct, take_profit_pct (CLOSE-based, relative to the
  fill price) and/or max_bars_in_trade. At least one exit is required.
- Session times are UTC. Istanbul is UTC+3, London is UTC+0 in winter / +1 in summer,
  New York UTC-5 / -4. Convert and say so in explanation_tr.

Rules:
- Never silently drop or change what the user asked. If something cannot be expressed
  (shorting, intrabar stops, trailing stops, multi-timeframe filters, Fibonacci levels,
  fair value gaps, market structure, news, position sizing, order book, other symbols),
  approximate only when the approximation is faithful, and list every gap in `unsupported`.
- If the user gives no exit, choose a simple, conventional one and state it in explanation_tr.
- Use short lowercase ids like ema200, rsi14, bb20. Give the spec a short Turkish name.
- explanation_tr and unsupported are written in Turkish."""


def _call(messages: list[dict], api_key: str, model: str, client: httpx.Client) -> dict:
    base = os.getenv("ANTHROPIC_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    resp = client.post(f"{base}/v1/messages", headers={
        "x-api-key": api_key, "anthropic-version": "2023-06-01", "content-type": "application/json",
    }, json={
        "model": model, "max_tokens": 4000, "system": SYSTEM_PROMPT,
        "tools": [tool_definition()], "messages": messages,
    })
    if resp.status_code == 401:
        raise AIError("ANTHROPIC_API_KEY geçersiz", 502)
    if resp.status_code == 429:
        raise AIError("Claude API limitine takıldı, biraz sonra tekrar dene", 429)
    if resp.status_code != 200:
        raise AIError(f"Claude API hatası {resp.status_code}: {resp.text[:300]}")
    return resp.json()


def _tool_use(body: dict) -> dict | None:
    for block in body.get("content", []):
        if block.get("type") == "tool_use" and block.get("name") == TOOL_NAME:
            return block
    return None


def translate(text: str, client: httpx.Client | None = None) -> dict:
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise AIError("AI kapalı: Vercel'de ANTHROPIC_API_KEY ortam değişkeni tanımlı değil", 503)
    text = text.strip()
    if not text:
        raise AIError("Strateji metni boş", 400)
    if len(text) > MAX_TEXT:
        raise AIError(f"Metin en fazla {MAX_TEXT} karakter olabilir", 400)
    model = os.getenv("ANTHROPIC_MODEL", DEFAULT_MODEL)

    own = client is None
    client = client or httpx.Client(timeout=90.0)
    messages: list[dict] = [{"role": "user", "content": text}]
    try:
        for attempt in range(2):
            body = _call(messages, api_key, model, client)
            block = _tool_use(body)
            if block is None:
                if attempt == 0:
                    messages += [{"role": "assistant", "content": body.get("content", [])},
                                 {"role": "user", "content": "Please call submit_strategy now."}]
                    continue
                raise AIError("AI stratejiyi kurala çeviremedi; metni biraz daha net yazmayı dene", 422)
            payload = block.get("input", {})
            try:
                spec = StrategySpec.model_validate(payload.get("spec", {}))
            except ValidationError as exc:
                if attempt == 0:
                    messages += [
                        {"role": "assistant", "content": body["content"]},
                        {"role": "user", "content": [{
                            "type": "tool_result", "tool_use_id": block["id"], "is_error": True,
                            "content": "Spec failed validation, fix and call again:\n"
                                       + json.dumps(exc.errors(include_url=False), default=str)[:3000],
                        }]},
                    ]
                    continue
                raise AIError(f"AI geçersiz kural üretti: {exc.errors()[0]['msg']}", 422) from exc
            return {
                "spec": spec.model_dump(exclude_none=True),
                "explanation": str(payload.get("explanation_tr", "")),
                "unsupported": [str(u) for u in payload.get("unsupported", [])][:20],
                "model": body.get("model", model),
            }
        raise AIError("AI yanıtı işlenemedi", 502)
    except httpx.HTTPError as exc:
        raise AIError(f"Claude API'ye ulaşılamadı: {exc}") from exc
    finally:
        if own:
            client.close()
