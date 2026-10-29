"""Ollama client for small local models that can't do structured output.

Ollama's MLX runner (which serves nemotron-3.5-bolt) rejects `format`, so the
model writes JSON as plain text. Code parses it, checks its shape, and on a bad
answer retries once with the error message.
"""

import json
import re
import time
import urllib.request

OLLAMA = "http://localhost:11434/api/chat"
MODEL = "nemotron-3.5-bolt:e4b-nvfp4"
# Bolt defaults to thinking on and temperature 1. Both off for repeatable extraction.
OPTIONS = {"temperature": 0, "seed": 42, "num_ctx": 16384}


class BadAnswer(Exception):
    pass


def parse_json(text):
    """First JSON value in the text, ignoring code fences and chatter around it."""
    text = re.sub(r"```(?:json)?", "", text)
    starts = [i for i in (text.find("["), text.find("{")) if i >= 0]
    if not starts:
        raise BadAnswer("no JSON array or object in the answer")
    try:
        value, _ = json.JSONDecoder().raw_decode(text[min(starts):])
    except json.JSONDecodeError as e:
        raise BadAnswer(f"invalid JSON: {e}") from None
    return value


class Bolt:
    def __init__(self, model=MODEL):
        self.model = model
        self.calls = []  # one entry per HTTP call: tokens and seconds

    def _chat(self, messages):
        body = json.dumps({"model": self.model, "stream": False, "think": False, "keep_alive": "30m",
                           "options": OPTIONS, "messages": messages}).encode()
        req = urllib.request.Request(OLLAMA, body, {"Content-Type": "application/json"})
        t = time.time()
        d = json.load(urllib.request.urlopen(req, timeout=600))
        self.calls.append({"in": d.get("prompt_eval_count", 0), "out": d.get("eval_count", 0),
                           "secs": round(time.time() - t, 1)})
        return d["message"]["content"]

    def say(self, user):
        """Plain-text answer to a one-message prompt."""
        return self._chat([{"role": "user", "content": user}])

    def ask(self, system, user, check, parse=parse_json):
        """`check(parse(answer))` returns the cleaned value or raises BadAnswer; one retry with the reason."""
        messages = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": user}]
        answer = self._chat(messages)
        try:
            return check(parse(answer))
        except BadAnswer as e:
            messages += [{"role": "assistant", "content": answer},
                         {"role": "user", "content": f"That answer was rejected: {e}. "
                                                     "Reply again with only the corrected answer."}]
            return check(parse(self._chat(messages)))
