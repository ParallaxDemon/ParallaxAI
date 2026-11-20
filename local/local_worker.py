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

SYSTEM = (
    "You are a helpful, concise assistant. Answer the user directly and briefly. "
    "You are text-only: you cannot see, read, or open images or files. If an image is attached, "
    "say you cannot see images and ask the user to describe it in text. "
    "Never invent job metadata, findings or runtime observations."
)


def log(*args):
    # The launcher redirects stdout to ~/.local/share/parallax-local-worker.log.
    print(time.strftime("%Y-%m-%d %H:%M:%S") + " " + " ".join(str(a) for a in args), flush=True)


def cfg():
    with CONFIG.open() as f:
        return json.load(f)


def http(url, headers, data=None, timeout=30):
    body = json.dumps(data).encode() if data is not None else None
    h = dict(headers)
    if body is not None:
        h["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=h, method="POST" if body is not None else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read().decode("utf-8", "replace")
    return json.loads(raw) if raw else {}


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
    body = {
        "model": "local-deepseek",
        # DeepSeek-R1 burns many tokens on hidden <think> reasoning. Prefilling an
        # assistant turn that closes the think block makes it answer directly:
        # ~2-6s instead of ~20-30s, with clean short replies for chat use.
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": prompt[:23000]},
            {"role": "assistant", "content": "</think>\n\n"},
        ],
        "temperature": 0.25,
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
    return text.replace("</think>", "").strip()


def process(job, c, client_h, worker_h, port):
    jid = job["id"]
    try:
        claimed = http(c["base_url"].rstrip("/") + "/api/v1/worker/jobs/" + jid, worker_h, timeout=20)
    except urllib.error.HTTPError as e:
        log(jid, "not claimable:", e.code)
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
