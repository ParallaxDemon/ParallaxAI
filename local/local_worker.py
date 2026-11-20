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
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

TERMUX_HOME = os.path.expanduser("~")
TERMUX_BIN = "/data/user/0/com.termux/files/usr/bin"
SU_BIN = shutil.which("su") or "/system/bin/su"
OUT_LIMIT = 6000      # max characters returned from a tool to the model
MAX_FILE_BYTES = 200000  # max bytes read by read_file

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


# ---------------------------------------------------------------------------
# Phone tools — give ParallaxAI real full access to the device.
# The model calls these natively; results are fed back until it gives a final
# answer. Everything runs on THIS phone as the Termux app user (or root via su).
# ---------------------------------------------------------------------------

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": (
                "Run a shell command on this Android phone (Termux environment). Use this for "
                "ANY real action: system settings, managing files/apps, network, git, installs, "
                "reading other apps' data, etc. Set root=true for system-level access (needs "
                "root). Returns the command output and exit code."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "The shell command to execute."},
                    "root": {"type": "boolean", "description": "Run as root via su (default false)."},
                    "timeout": {"type": "integer", "description": "Timeout in seconds (default 30, max 120)."},
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read the text contents of a file on the phone. Supports ~ and /sdcard paths.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "File path to read."}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": (
                "Create or overwrite a file on the phone (parent folders are created). Use for "
                "~ and /sdcard paths. For system/root paths instead use run_command with root=true."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path to write."},
                    "content": {"type": "string", "description": "Full text content to write."},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_dir",
            "description": "List files and folders in a directory on the phone. Supports ~ and /sdcard.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "Directory path. Defaults to home."}},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search",
            "description": "Recursively search file contents for text on the phone (like grep -rn).",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Text or pattern to search for."},
                    "path": {"type": "string", "description": "Directory or file to search in. Defaults to home."},
                },
                "required": ["query"],
            },
        },
    },
]

# Refuse only clearly catastrophic, irreversible commands. Everything else is allowed.
_DANGEROUS = [
    r"(?i)\brm\s+(-[a-z]*[rf][a-z]*\s+){0,3}(/|\/\*|~|\$HOME)(\s|$)",
    r"(?i)\bmkfs\b",
    r"(?i)\bdd\b[^\n]*\bof=/dev/",
    r"(?i):\(\)\s*\{.*\|\s*:.*&\s*\}",  # fork bomb
    r"(?i)\bchown\s+-R[^/]*\s+/(\s|$)",
]


def _resolve(path):
    p = str(path or "").strip()
    if not p:
        return TERMUX_HOME
    if p.startswith("~/"):
        p = TERMUX_HOME + p[1:]
    elif p == "~":
        p = TERMUX_HOME
    return os.path.abspath(os.path.expandvars(p))


def _tool_env():
    env = os.environ.copy()
    env["HOME"] = TERMUX_HOME
    env["PATH"] = TERMUX_BIN + ":" + env.get("PATH", "/system/bin:/system/xbin")
    return env


def execute_tool(name, args):
    """Execute one phone tool call and return its text result."""
    args = args or {}
    try:
        if name == "run_command":
            cmd = str(args.get("command", ""))
            if not cmd.strip():
                return "ERROR: empty command"
            for pat in _DANGEROUS:
                if re.search(pat, cmd):
                    return "BLOCKED: refused a potentially destructive command for safety: " + cmd[:160]
            root = bool(args.get("root", False))
            timeout = max(1, min(int(args.get("timeout", 30) or 30), 120))
            env = _tool_env()
            if root:
                wrapped = "export PATH='%s':$PATH; cd '%s' 2>/dev/null || true; %s" % (TERMUX_BIN, TERMUX_HOME, cmd)
                proc = subprocess.run([SU_BIN, "-c", wrapped], capture_output=True, text=True,
                                      timeout=timeout, env=env)
            else:
                proc = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                                      timeout=timeout, env=env, cwd=TERMUX_HOME)
            out = (proc.stdout or "")
            if proc.stderr:
                out += "\n[stderr] " + proc.stderr
            out += "\n[exit=%d]" % proc.returncode
            return out[:OUT_LIMIT] or "(no output)"

        if name == "read_file":
            path = _resolve(args.get("path"))
            if not os.path.isfile(path):
                return "ERROR: file not found: " + path
            with open(path, "rb") as f:
                data = f.read(MAX_FILE_BYTES)
            text = data.decode("utf-8", "replace")
            if len(data) >= MAX_FILE_BYTES:
                text += "\n...[truncated at %d bytes]" % MAX_FILE_BYTES
            return text[:OUT_LIMIT]

        if name == "write_file":
            path = _resolve(args.get("path"))
            content = str(args.get("content", ""))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            return "OK: wrote %d chars to %s" % (len(content), path)

        if name == "list_dir":
            path = _resolve(args.get("path"))
            if not os.path.isdir(path):
                return "ERROR: directory not found: " + path
            entries = sorted(os.listdir(path))
            listed = []
            for e in entries[:300]:
                full = os.path.join(path, e)
                kind = "d" if os.path.isdir(full) else "-"
                listed.append("%s %s" % (kind, e))
            return "\n".join(listed) + ("\n...[more entries]" if len(entries) > 300 else "")

        if name == "search":
            query = str(args.get("query", ""))
            path = _resolve(args.get("path"))
            if not query:
                return "ERROR: empty query"
            proc = subprocess.run(
                ["grep", "-RIn", "--exclude-dir=.git", "-m", "30", "--", query, path],
                capture_output=True, text=True, timeout=15, env=_tool_env(),
            )
            return ((proc.stdout or "") + (proc.stderr or ""))[:OUT_LIMIT] or "No matches."

        return "ERROR: unknown tool " + str(name)
    except subprocess.TimeoutExpired:
        return "ERROR: command timed out"
    except Exception as e:
        return "ERROR: " + type(e).__name__ + ": " + str(e)[:300]


PANEL_FALLBACK_IP = "104.21.33.84"  # Cloudflare IP for ai.parallaxserver.online


def _curl(url, headers, data, timeout, resolve=None):
    cmd = ["curl", "-sS", "--max-time", str(timeout), "--retry", "1", "-w", "\n%{http_code}"]
    if resolve:
        cmd += ["--resolve", resolve]
    cmd += [url]
    for k, v in headers.items():
        cmd += ["-H", k + ": " + v]
    if data is not None:
        cmd += ["-X", "POST", "-H", "Content-Type: application/json", "--data-binary", json.dumps(data)]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 10)
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or "curl failed").strip()[:200])
    raw, _, code = proc.stdout.rpartition("\n")
    if code not in ("200", "202"):
        raise RuntimeError("HTTP " + code + " " + raw[:200])
    return json.loads(raw) if raw.strip() else {}


_RESOLVE_CACHE = {}


def http(url, headers, data=None, timeout=30):
    """HTTP via curl with a cached DNS fallback for the su/KernelSU boot context."""
    from urllib.parse import urlparse
    u = urlparse(url)
    key = (u.hostname, u.port or 443)
    if key in _RESOLVE_CACHE:
        return _curl(url, headers, data, timeout, resolve=_RESOLVE_CACHE[key])
    try:
        return _curl(url, headers, data, timeout)
    except RuntimeError as e:
        if "Could not resolve host" not in str(e) and "6" not in str(e):
            raise
        resolve = f"{u.hostname}:{u.port or 443}:{PANEL_FALLBACK_IP}"
        result = _curl(url, headers, data, timeout, resolve=resolve)
        _RESOLVE_CACHE[key] = resolve
        log("DNS fallback cached ->", u.hostname, "=", PANEL_FALLBACK_IP)
        return result


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


def llm(messages, c, port, with_tools=False, max_tokens=None):
    """One inference call to the local llama-server. Returns the message dict."""
    body = {
        "model": c.get("model_name", "local"),
        "messages": messages,
        "temperature": float(c.get("temperature", 0.3)),
        "max_tokens": max_tokens or int(c.get("max_tokens", 400)),
        "stream": False,
    }
    if with_tools:
        body["tools"] = TOOLS
        body["tool_choice"] = "auto"
    url = f"http://127.0.0.1:{port}/v1/chat/completions"
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(), method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=600) as r:
        data = json.load(r)
    return data["choices"][0]["message"]


def infer(prompt, c, port):
    """Full agent loop: call model -> execute any phone tools -> feed results back
    -> repeat until the model produces a final text answer."""
    messages = [
        {"role": "system", "content": system_prompt()},
        {"role": "user", "content": clean_prompt(prompt)[:23000]},
    ]
    prefill = bool(c.get("prefill_think", False))
    if prefill:
        messages.append({"role": "assistant", "content": "</think>\n\n"})

    max_iter = max(1, min(int(c.get("max_tool_iterations", 8)), 12))
    actions = []
    for _ in range(max_iter):
        msg = llm(messages, c, port, with_tools=True,
                  max_tokens=max(384, int(c.get("max_tokens", 256))))
        calls = msg.get("tool_calls") or []
        if not calls:
            text = (msg.get("content") or "").strip()
            if prefill:
                text = text.replace("</think>", "").strip()
            if actions:
                # Transparency: show exactly what was actually done on the phone.
                text += "\n\n" + "\n".join(actions)
            return text or "[Model returned no text]"

        messages.append({"role": "assistant", "content": msg.get("content") or "",
                         "tool_calls": calls})
        for call in calls:
            fn = call.get("function", {})
            name = fn.get("name", "")
            raw = fn.get("arguments", "{}")
            try:
                args = json.loads(raw) if isinstance(raw, str) else (raw or {})
            except Exception:
                args = {}
            log("tool:", name, str(args)[:200])
            result = execute_tool(name, args)
            messages.append({"role": "tool", "tool_call_id": call.get("id", ""),
                             "content": result[:OUT_LIMIT]})
            # Build a short human-readable record of the action.
            what = args.get("command") or args.get("path") or args.get("query") or ""
            actions.append("- `" + name + "`: " + str(what)[:120].replace("\n", " "))

    return "[Reached the tool step limit] " + "\n".join(actions)


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
    session_seconds = int(c.get("session_seconds", 6 * 3600))
    deadline = time.monotonic() + session_seconds
    log("v2 curl-http worker ready (6h session = %ds)" % session_seconds)
    log("watching", base, "every", c.get("poll_seconds", 1.5), "s")
    while True:
        # End the 6h session cleanly only when idle, so no job is ever cut off.
        # The launcher restarts us immediately with a fresh session.
        if time.monotonic() >= deadline:
            log("6h session complete - clean exit for restart")
            return
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
