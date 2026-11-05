import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app import main
from app.ai import translator
from app.ai.translator import AIError, translate

client = TestClient(main.app)

GOOD_SPEC = {
    "name": "EMA200 + RSI",
    "indicators": [{"id": "ema200", "type": "ema", "period": 200},
                   {"id": "rsi14", "type": "rsi", "period": 14}],
    "entry": {"all": [{"left": "close", "op": ">", "right": "ema200"},
                      {"left": "rsi14", "op": "crosses_above", "right": "30"}]},
    "exit": {"any": [{"left": "rsi14", "op": ">=", "right": "70"}]},
    "stop_loss_pct": 2,
}


def tool_reply(spec, tool_id="t1"):
    return {"model": "claude-sonnet-5-5", "content": [{
        "type": "tool_use", "id": tool_id, "name": "submit_strategy",
        "input": {"spec": spec, "explanation_tr": "Açıklama", "unsupported": ["short yok"]}}]}


def mock_client(replies, seen):
    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=replies.pop(0))
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_translate_valid_first_try(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    seen = []
    out = translate("EMA200 üstünde RSI 30 yukarı keserse al", mock_client([tool_reply(GOOD_SPEC)], seen))
    assert out["spec"]["stop_loss_pct"] == 2
    assert out["unsupported"] == ["short yok"]
    body = seen[0]
    assert body["model"] == "claude-sonnet-5-5" and "tool_choice" not in body
    assert body["tools"][0]["name"] == "submit_strategy"


def test_translate_retries_once_with_validation_error(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    bad = {**GOOD_SPEC, "entry": {"all": [{"left": "close", "op": ">", "right": "ema50"}]}}
    seen = []
    out = translate("x" * 10, mock_client([tool_reply(bad), tool_reply(GOOD_SPEC, "t2")], seen))
    assert out["spec"]["name"] == "EMA200 + RSI"
    retry_msg = seen[1]["messages"][-1]["content"][0]
    assert retry_msg["type"] == "tool_result" and retry_msg["is_error"] is True
    assert retry_msg["tool_use_id"] == "t1" and "ema50" in retry_msg["content"]


def test_translate_gives_up_after_second_invalid(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    bad = {"entry": {"all": []}}
    with pytest.raises(AIError) as err:
        translate("strateji", mock_client([tool_reply(bad), tool_reply(bad)], []))
    assert err.value.status == 422


def test_translate_without_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(AIError) as err:
        translate("strateji")
    assert err.value.status == 503


def test_ai_endpoint_access_code(monkeypatch):
    monkeypatch.setenv("AI_ACCESS_CODE", "gizli")
    monkeypatch.setattr(main, "translate", lambda text: {
        "spec": GOOD_SPEC, "explanation": "a", "unsupported": [], "model": "m"})
    assert client.post("/api/ai/strategy", json={"text": "deneme"}).status_code == 401
    ok = client.post("/api/ai/strategy", json={"text": "deneme", "access_code": "gizli"})
    assert ok.status_code == 200 and ok.json()["rules"]["entry"]
    assert client.get("/api/ai/status").json()["access_code_required"] is True


def test_custom_validate_and_backtest():
    v = client.post("/api/custom/validate", json={"spec": GOOD_SPEC}).json()
    assert v["warmup_bars"] >= 200 and v["rules"]["risk"]
    bad = client.post("/api/custom/validate", json={"spec": {"entry": {"all": []}}})
    assert bad.status_code == 400 and "entry" in bad.json()["detail"]
    r = client.post("/api/custom/backtest", json={"spec": GOOD_SPEC, "bars": 1500,
                                                  "include_candles": True})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["strategy"] == "custom" and body["metrics"]["trades"] == len(body["trades"])
    assert [o["name"] for o in body["overlays"]] == ["ema200 (EMA 200)"]


def test_custom_backtest_needs_enough_bars():
    r = client.post("/api/custom/backtest", json={"spec": GOOD_SPEC, "bars": 400})
    assert r.status_code == 400


def test_tool_schema_has_no_refs():
    assert "$ref" not in json.dumps(translator.tool_definition())
