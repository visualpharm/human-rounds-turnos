"""OpenRouter LLM client for usability-kit: GLM Flash over OpenRouter.

    glm = GLM()
    text = glm(system, messages, max_tokens=1500)

Model resolution, in order: the daily model roster's ``glm-flash`` provider id,
then ``$USABILITY_KIT_MODEL``, then ``z-ai/glm-5.3-flash``. The key comes ONLY
from ``$OPENROUTER_API_KEY`` (injected by the hub's broker or the caller's
environment): this module never starts a vault CLI, never prompts, never opens a
window. No key -> ``RuntimeError``; the caller reports the step as skipped. The
study JSON's ``llm.openrouter_op_item`` is accepted for old configs and ignored.
The key is never written to any file, log, trace or report.

This client talks ONLY to ``https://openrouter.ai/api/v1`` — never to any Z.ai
endpoint directly — and never falls back to another model: five attempts with
exponential backoff, then it raises.
"""
import json
import os
import subprocess
import time
import urllib.request
from pathlib import Path

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "z-ai/glm-5.3-flash"
REFERER = "https://github.com/visualpharm/usability-kit"
TITLE = "usability-kit"
ATTEMPTS = 5
BACKOFF_S = (2, 4, 8, 16)   # seconds before retries 2..5, exponential


def log(*a):
    print(f"[or {time.strftime('%H:%M:%S')}]", *a, flush=True)


def _roster_model():
    """glm-flash provider id from the daily model roster, or None when unavailable."""
    script = os.environ.get("USABILITY_KIT_ROSTER") or Path.home() / ".claude/bin/model-roster-refresh.py"
    if not Path(script).exists():
        return None
    try:
        roster = json.loads(subprocess.check_output(["python3", str(script), "--if-stale", "--json"], text=True, timeout=60))
        return (roster["glm-flash"]["provider_id"] or "").lower() or None
    except Exception as err:  # noqa: BLE001 — the roster is an optimization, not a requirement
        log(f"model roster unavailable ({err.__class__.__name__}); falling back to env/default")
        return None


def resolve_model():
    """Model resolution order: roster provider id → $USABILITY_KIT_MODEL → default."""
    return _roster_model() or os.environ.get("USABILITY_KIT_MODEL") or DEFAULT_MODEL


def resolve_key(op_item=None):
    """The key is $OPENROUTER_API_KEY, nothing else. `op_item` (the old vault item name) is
    accepted and ignored so existing study JSON keeps loading."""
    env = os.environ.get("OPENROUTER_API_KEY")
    if env:
        return env
    raise RuntimeError("no OpenRouter key: $OPENROUTER_API_KEY is not set (the hub broker or the caller's environment provides it)")


def openai_content(content):
    """Anthropic-style content (string or block list) → OpenAI content format.
    Text blocks pass through; base64 image blocks become image_url data URLs."""
    if isinstance(content, str) or content is None:
        return content or ""
    parts = []
    for b in content:
        if not isinstance(b, dict):
            continue
        if b.get("type") == "text":
            parts.append({"type": "text", "text": b.get("text", "")})
        elif b.get("type") == "image":
            src = b.get("source") or {}
            if src.get("type") == "base64" and src.get("data"):
                parts.append({"type": "image_url", "image_url": {"url": f"data:{src.get('media_type', 'image/png')};base64,{src['data']}"}})
            elif src.get("url"):
                parts.append({"type": "image_url", "image_url": {"url": src["url"]}})
    return parts


def openai_messages(system, messages):
    """OpenAI chat format: the system prompt as the first message, Anthropic blocks converted."""
    out = [{"role": "system", "content": system or ""}]
    for m in messages:
        out.append({"role": m.get("role", "user"), "content": openai_content(m.get("content"))})
    return out


class GLM:
    """GLM Flash through OpenRouter. Same call signature as the previous client."""

    def __init__(self, op_item=None):
        self.model = resolve_model()
        self.key = resolve_key(op_item)
        self.calls = []   # per call: {"ms", "reported", "usage"} — never the key

    def __call__(self, system, messages, max_tokens=1500):
        body = json.dumps({"model": self.model, "max_tokens": max_tokens,
                           "messages": openai_messages(system, messages)}).encode()
        for attempt in range(ATTEMPTS):
            if attempt:
                time.sleep(BACKOFF_S[min(attempt - 1, len(BACKOFF_S) - 1)])
            t0 = time.time()
            try:
                req = urllib.request.Request(OPENROUTER_URL, data=body, headers={
                    "Authorization": f"Bearer {self.key}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": REFERER,
                    "X-Title": TITLE})
                j = json.load(urllib.request.urlopen(req, timeout=180))
                content = (j.get("choices") or [{}])[0].get("message", {}).get("content")
                if isinstance(content, list):
                    content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
                self.calls.append({"ms": round((time.time() - t0) * 1000), "reported": j.get("model"), "usage": j.get("usage")})
                return str(content or "").strip()
            except Exception as err:  # noqa: BLE001 — retried, then raised
                log(f"GLM call failed ({err}); retry {attempt + 1}")
        raise RuntimeError(f"GLM Flash did not answer over OpenRouter after {ATTEMPTS} attempts (no fallback model is used)")
