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


# ------------------------------------------------ Gemini / NVIDIA (OpenAI-compatible)
def chat_reply(content, model="gemini-3.8-flash"):
    return {"model": model, "choices": [{"message": {"role": "assistant", "content": content}}]}


PAYLOAD = {"spec": GOOD_SPEC, "explanation_tr": "Yorum", "unsupported": []}


@pytest.fixture
def only(monkeypatch):
    def _set(**keys):
        for k in ("ANTHROPIC_API_KEY", "GEMINI_API_KEY", "NVIDIA_API_KEY", "AI_PROVIDER"):
            monkeypatch.delenv(k, raising=False)
        for k, v in keys.items():
            monkeypatch.setenv(k, v)
    return _set


def openai_client(replies, seen):
    def handler(request):
        seen.append({"url": str(request.url), "auth": request.headers.get("authorization"),
                     "body": json.loads(request.content)})
        return httpx.Response(200, json=replies.pop(0))
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_provider_selection(only):
    from app.ai.translator import active_provider
    only()
    assert active_provider() is None
    only(NVIDIA_API_KEY="n")
    assert active_provider() == "nvidia"
    only(NVIDIA_API_KEY="n", GEMINI_API_KEY="g")
    assert active_provider() == "gemini"
    only(NVIDIA_API_KEY="n", GEMINI_API_KEY="g", AI_PROVIDER="nvidia")
    assert active_provider() == "nvidia"
    only(GEMINI_API_KEY="g", AI_PROVIDER="anthropic")      # no key for it -> fall back
    assert active_provider() == "gemini"


def test_gemini_plain_json(only):
    only(GEMINI_API_KEY="g-key")
    seen = []
    out = translate("strateji", openai_client([chat_reply(json.dumps(PAYLOAD))], seen))
    assert out["provider"] == "gemini" and out["spec"]["stop_loss_pct"] == 2
    req = seen[0]
    assert req["url"] == "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
    assert req["auth"] == "Bearer g-key"
    assert req["body"]["messages"][0]["role"] == "system"
    assert "JSON" in req["body"]["messages"][0]["content"]


def test_nvidia_fenced_json_with_think_block_and_retry(only):
    only(NVIDIA_API_KEY="n-key")
    bad = {**PAYLOAD, "spec": {**GOOD_SPEC, "entry": {"all": [{"left": "close", "op": ">",
                                                               "right": "ema50"}]}}}
    first = "<think>hmm {not json}</think>Here you go:\n```json\n" + json.dumps(bad) + "\n```"
    seen = []
    out = translate("strateji", openai_client(
        [chat_reply(first, "meta/llama"), chat_reply(json.dumps(PAYLOAD), "meta/llama")], seen))
    assert out["provider"] == "nvidia" and out["model"] == "meta/llama"
    assert seen[0]["url"].startswith("https://integrate.api.nvidia.com/v1/")
    retry = seen[1]["body"]["messages"]
    assert retry[-2]["role"] == "assistant" and "ema50" in retry[-1]["content"]


def test_openai_provider_gives_up_on_garbage(only):
    only(GEMINI_API_KEY="g")
    with pytest.raises(AIError) as err:
        translate("strateji", openai_client([chat_reply("sorry"), chat_reply("still no")], []))
    assert err.value.status == 422


def test_openai_provider_rate_limit_message(only):
    only(GEMINI_API_KEY="g")
    c = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(429)))
    with pytest.raises(AIError) as err:
        translate("strateji", c)
    assert err.value.status == 429 and "Gemini" in str(err.value)


def test_status_reports_provider(only):
    only(GEMINI_API_KEY="g")
    body = client.get("/api/ai/status").json()
    assert body["enabled"] is True and body["provider"] == "gemini"
    assert body["provider_label"] == "Gemini"


def test_gemini_sends_low_reasoning_effort_nvidia_does_not(only):
    only(GEMINI_API_KEY="g")
    seen = []
    translate("strateji", openai_client([chat_reply(json.dumps(PAYLOAD))], seen))
    assert seen[0]["body"]["reasoning_effort"] == "low"
    only(NVIDIA_API_KEY="n")
    seen = []
    translate("strateji", openai_client([chat_reply(json.dumps(PAYLOAD))], seen))
    assert "reasoning_effort" not in seen[0]["body"]


def test_gemini_retries_without_reasoning_effort_if_rejected(only):
    only(GEMINI_API_KEY="g")
    bodies = []

    def handler(request):
        body = json.loads(request.content)
        bodies.append(body)
        if "reasoning_effort" in body:
            return httpx.Response(400, text='{"error": "Invalid reasoning_effort: low"}')
        return httpx.Response(200, json=chat_reply(json.dumps(PAYLOAD)))

    out = translate("strateji", httpx.Client(transport=httpx.MockTransport(handler)))
    assert out["provider"] == "gemini"
    assert "reasoning_effort" in bodies[0] and "reasoning_effort" not in bodies[1]


def test_gemini_timeout_gives_clear_504(only):
    only(GEMINI_API_KEY="g")

    def handler(request):
        raise httpx.ReadTimeout("The read operation timed out", request=request)

    with pytest.raises(AIError) as err:
        translate("strateji", httpx.Client(transport=httpx.MockTransport(handler)))
    assert err.value.status == 504 and "GEMINI_MODEL" in str(err.value)
