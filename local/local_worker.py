#!/usr/bin/env python3
"""Always-on local ParallaxAI chat worker.

Polls the panel for queued chat jobs, keeps one llama-server process warm on this
phone, and posts replies straight back to the panel. Binary-analysis jobs
(.apk/.dex/.smali/.so) are left to the GitHub Actions worker.

No secrets live in this file: credentials are read from
~/.config/parallax-ai/local-worker.json (mode 600).
"""
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

CONFIG = Path(os.environ.get("PARALLAX_LOCAL_CONFIG", "~/.config/parallax-ai/local-worker.json")).expanduser()
LOG = Path("~/.local/share/parallax-local-worker.log").expanduser()
SYSTEM_FILE = Path(__file__).with_name("system_prompt.txt")

FALLBACK_SYSTEM = (
    "You are ParallaxAI, a helpful concise assistant. Answer in the user's language "
    "(reply in Hinglish if they write Hinglish). You are text-only: say so for images. "
    "Never invent results."
)


def system_prompt():
    """Load the merged all-agents persona; fall back if the file is missing."""
    try:
        text = SYSTEM_FILE.read_text(encoding="utf-8").strip()
        return text or FALLBACK_SYSTEM
    except OSError:
        return FALLBACK_SYSTEM


def clean_prompt(text):
    """Strip hidden app metadata blocks so the small model sees only the real request.

    AntiGem prepends [AntiGem app context: ...] to every agent's prompt, and the chat
    backend wraps history as 'Continuing the same conversation...'. The hidden block
    confuses a 1.5B model, so remove it before inference while keeping the useful
    conversation history.
    """
    import re
    s = text or ""
    # Remove bracketed hidden context notes (single or multi-line) at the start.
    s = re.sub(r"(?s)^\[AntiGem app context:.*?\]\s*", "", s)
    # Remove hidden attached-file notes anywhere.
    s = re.sub(r"(?s)\(Hidden note added by the AntiGem app:.*?(?=\n\n|\Z)", "", s)
    # Remove the image-attachment warning the app appends for ParallaxAI.
    s = re.sub(r"ParallaxAI is text-only[^\n]*", "", s)
    return s.strip()


def log(*args):
    # The launcher redirects stdout to ~/.local/share/parallax-local-worker.log.
    print(time.strftime("%Y-%m-%d %H:%M:%S") + " " + " ".join(str(a) for a in args), flush=True)


def cfg():
    with CONFIG.open() as f:
        return json.load(f)


def http(url, headers, data=None, timeout=30):
    """HTTP via curl: Android's Python resolver can fail in long-lived background
    processes ('No address associated with hostname') while curl resolves fine.
    """
    cmd = ["curl", "-sS", "--max-time", str(timeout), "--retry", "1", "-w", "\n%{http_code}", url]
    for k, v in headers.items():
        cmd += ["-H", k + ": " + v]
    if data is not None:
        cmd += ["-X", "POST", "-H", "Content-Type: application/json", "--data-binary", json.dumps(data)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 10)
    if proc.returncode != 0:
        raise RuntimeError("curl failed: " + (proc.stderr or "").strip()[:200])
    raw, _, code = proc.stdout.rpartition("\n")
    if code != "200" and code != "202":
        raise RuntimeError("HTTP " + code + " " + raw[:200])
    return json.loads(raw) if raw.strip() else {}


def healthy(port):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def ensure_llama(c, port):
    if healthy(port):
        return
    binary = Path(c["llama_binary"]).expanduser()
    model = Path(c["model"]).expanduser()
    if not binary.is_file() or not model.is_file():
        raise RuntimeError("llama binary or model missing; run local/download-assets.sh")
    log("starting llama-server (model load can take a minute)")
    out = LOG.with_suffix(".llama.log").open("ab")
    subprocess.Popen(
        [str(binary), "-m", str(model), "--host", "127.0.0.1", "--port", str(port),
         "-c", "8192", "-t", str(c.get("threads", 8)), "-ngl", "0"],
        stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True,
    )
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        if healthy(port):
            log("llama-server ready")
            return
        time.sleep(2)
    raise RuntimeError("llama-server failed to become healthy; see " + str(LOG.with_suffix(".llama.log")))


def infer(prompt, c, port):
    messages = [
        {"role": "system", "content": system_prompt()},
        {"role": "user", "content": clean_prompt(prompt)[:23000]},
    ]
    # DeepSeek-R1 (reasoning model) burns tokens on hidden <think> blocks; a prefill
    # that closes the block makes it answer directly. Instruct models (Qwen) must NOT
    # use it. Controlled by the private config flag `prefill_think`.
    prefill = bool(c.get("prefill_think", False))
    if prefill:
        messages.append({"role": "assistant", "content": "</think>\n\n"})
    body = {
        "model": c.get("model_name", "local"),
        "messages": messages,
        "temperature": float(c.get("temperature", 0.3)),
        "max_tokens": int(c.get("max_tokens", 400)),
        "stream": False,
    }
    url = f"http://127.0.0.1:{port}/v1/chat/completions"
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(), method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=420) as r:
        data = json.load(r)
    msg = data["choices"][0]["message"]
    text = (msg.get("content") or msg.get("reasoning_content") or "[Model returned no text]")
    if prefill:
        text = text.replace("</think>", "")
    return text.strip()


def process(job, c, client_h, worker_h, port):
    jid = job["id"]
    try:
        claimed = http(c["base_url"].rstrip("/") + "/api/v1/worker/jobs/" + jid, worker_h, timeout=20)
    except Exception as e:
        log(jid, "not claimable:", str(e)[:200])
        return False
    prompt = claimed.get("prompt") or job.get("prompt") or ""
    log(jid, "running chat job")
    started = time.monotonic()
    reply = infer(prompt, c, port)
    http(c["base_url"].rstrip("/") + "/api/v1/worker/jobs/" + jid + "/result",
        worker_h, {"status": "done", "result": reply}, timeout=30)
    log(jid, "done in %.1fs" % (time.monotonic() - started))
    return True


def main():
    c = cfg()
    base = c["base_url"].rstrip("/")
    client_h = {"Authorization": "Bearer " + c["api_key"], "User-Agent": "ParallaxLocalWorker/1.0"}
    worker_h = {"X-Worker-Key": c["worker_key"], "User-Agent": "ParallaxLocalWorker/1.0"}
    port = int(c.get("port", 8080))
    ensure_llama(c, port)
    log("v2 curl-http worker ready")
    log("watching", base, "every", c.get("poll_seconds", 1.5), "s")
    while True:
        try:
            jobs = http(base + "/api/v1/jobs", client_h, timeout=20).get("jobs", [])
            for job in jobs:
                if job.get("status") != "queued" or job.get("kind") != "chat":
                    continue
                try:
                    process(job, c, client_h, worker_h, port)
                except Exception as exc:
                    log(job.get("id"), "job error:", type(exc).__name__, str(exc)[:300])
                    try:
                        http(base + "/api/v1/worker/jobs/" + job["id"] + "/result", worker_h,
                             {"status": "failed", "error": str(exc)[:2000]}, timeout=20)
                    except Exception:
                        pass
        except Exception as exc:
            log("poll error:", type(exc).__name__, str(exc)[:300])
        time.sleep(float(c.get("poll_seconds", 1.5)))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
    except Exception as exc:
        log("fatal:", type(exc).__name__, exc)
        sys.exit(1)
