"""OpenAI-compatible chat-completions backends. Standard library only.

`lab` is a single-slot local engine: one request at a time.
`openrouter` needs OPENROUTER_API_KEY in the environment, never in a file.
"""

import json
import os
import time
import urllib.error
import urllib.request
from typing import Dict, List, Optional, Tuple

LAB_URL = "http://cm64labs:8001/v1"
LAB_MODEL = "qwen3.8-flash-next-iq3_s"
OPENROUTER_URL = "https://openrouter.ai/api/v1"

MAX_RETRIES = 3
RETRY_WAIT = 4.0
# Rate limits (429) and provider overloads (5xx) get more patience: with a
# small balance OpenRouter throttles hard, and 3 quick retries turned 18 of 24
# runs invalid on the first battery.
MAX_RETRIES_THROTTLED = 7
MAX_WAIT = 90.0


class BackendError(Exception):
    pass


class Backend:
    name = "base"
    parallel = False

    # Message keys the API is sent. Our transcript also keeps the model's
    # reasoning (`reasoning_content`) for the log; hosted APIs don't need it
    # back and some providers reject unknown fields.
    SEND_KEYS = ("role", "content", "tool_calls", "tool_call_id", "name")
    strip_reasoning = False
    extra_body: Dict = {}

    def __init__(self, base_url: str, model: str, api_key: Optional[str] = None):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.last_provider: Optional[str] = None

    def headers(self) -> Dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = "Bearer {}".format(self.api_key)
        return h

    def chat(
        self,
        messages: List[dict],
        tools: List[dict],
        max_tokens: int = 16384,
        extra: Optional[dict] = None,
    ) -> Tuple[dict, dict, float, str]:
        """One completion. Returns (message, usage, seconds, finish_reason).

        Thinking models spend max_tokens on reasoning first: with a small cap
        they end with finish_reason=length, no content and no tool call, which
        would read as a decision. The caller must treat "length" as invalid.

        Raises BackendError after MAX_RETRIES failures. Client errors (4xx
        other than 408/429) are not retried: the request itself is wrong.
        """
        if self.strip_reasoning:
            messages = [{k: v for k, v in m.items() if k in self.SEND_KEYS}
                        for m in messages]
        body = {
            "model": self.model,
            "messages": messages,
            "max_tokens": max_tokens,
        }
        body.update(self.extra_body)
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        if extra:
            body.update(extra)

        last = None
        attempt = 0
        limit = MAX_RETRIES
        while attempt < limit:
            attempt += 1
            t0 = time.time()
            try:
                req = urllib.request.Request(
                    self.base_url + "/chat/completions",
                    data=json.dumps(body).encode("utf-8"),
                    headers=self.headers(),
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=600) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                dt = time.time() - t0
                if not data.get("choices"):
                    raise BackendError("no choices in response")
                choice = data["choices"][0]
                msg = choice["message"]
                usage = data.get("usage") or {}
                # OpenRouter routes to one of several providers: record which.
                self.last_provider = data.get("provider")
                return msg, usage, dt, choice.get("finish_reason") or ""
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", "replace")[:300]
                last = "HTTP {}: {}".format(exc.code, detail)
                if 400 <= exc.code < 500 and exc.code not in (408, 429):
                    break
                limit = MAX_RETRIES_THROTTLED
                if attempt < limit:
                    try:
                        wait = float(exc.headers.get("Retry-After") or 0)
                    except (TypeError, ValueError):
                        wait = 0.0
                    time.sleep(min(MAX_WAIT, max(wait, RETRY_WAIT * 2 ** attempt)))
                continue
            except Exception as exc:  # noqa: BLE001 - retry anything network-ish
                dt = time.time() - t0
                last = "{}: {}".format(type(exc).__name__, exc)
                if attempt < MAX_RETRIES:
                    time.sleep(RETRY_WAIT * attempt)
        raise BackendError(
            "{} failed {} times: {}".format(self.name, attempt, last)
        )


def lab_backend(model: Optional[str] = None, base_url: Optional[str] = None) -> Backend:
    # base_url: other local engines on the box (the `llm` CLI serves on :8000).
    b = Backend(base_url or LAB_URL, model or LAB_MODEL)
    b.name = "lab"
    b.parallel = False
    return b


def openrouter_backend(model: str) -> Backend:
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise BackendError(
            "OPENROUTER_API_KEY is not set. Export it; do not put it in this repo."
        )
    b = Backend(OPENROUTER_URL, model, key)
    b.name = "openrouter"
    b.parallel = True
    b.strip_reasoning = True
    b.extra_body = {"usage": {"include": True}}   # returns usage.cost in USD
    return b


def make(backend: str, model: Optional[str] = None,
         base_url: Optional[str] = None) -> Backend:
    if backend == "lab":
        return lab_backend(model, base_url)
    if backend == "openrouter":
        if not model:
            raise BackendError("--model is required for the openrouter backend")
        return openrouter_backend(model)
    raise BackendError("unknown backend: {}".format(backend))
