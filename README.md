# DeepSeek RE Bridge: GitHub Actions + PHP Shared Hosting

**Scope:** authorized Android APK/DEX/Smali/ELF .so analysis, CPU-run community DeepSeek inference, reviewed exact-match Smali replacements, private async jobs and PHP/MySQL panel. This is a working starter implementation; **production integration has not been verified against your GitHub account and host**, and it is **not** a 24/7 model server or fully autonomous native binary patcher.

## Package layout

- `github/` → **push the CONTENTS of this directory** to a new PRIVATE GitHub repository. It includes workflows, CPU model worker, static binary inspection, optional Ghidra installer, optional MCP server, tests, and `panel/` source.
- `hosting/panel/` → **upload the CONTENTS of this directory** to `public_html/agent/` in Hostinger or another Apache/LiteSpeed PHP host.
- `docs/DEPLOY_HINDI.md` → full deployment guide (Hindi).
- `tests/` → local tests.

## Tested in this environment

- PHP 8.4 syntax lint: passed.
- Worker Python compilation and exact Smali patch/analysis unit tests: passed.
- PHP built-in server: `GET api.php?route=v1/health` returned JSON 200.
- **Not tested**: private GitHub repo creation (not supported by connected GitHub actions), a live GitHub Actions workflow run, model checkpoint download/inference, MySQL connection on your hosting, or upload callbacks over HTTPS. These require your repository and hosting credentials/URLs.

## 1 — Create repository (Termux with `gh auth login` already done)

```bash
pkg install git gh unzip -y
unzip DeepSeek-RE-Bridge-Full.zip
cd deepseek-re-bridge/github
gh auth status
git init -b main
git add .
git commit -m 'Initial DeepSeek RE bridge'
gh repo create deepseek-re-bridge --private --source=. --remote=origin --push
```

**Do not push** any `config.php`, API key, private APK, GGUF, signing key or other credential to GitHub. The workflow must exist on the repository default branch.

## 2 — Prepare Hostinger PHP + MySQL

1. In hPanel, create a MySQL database and user. Import `hosting/panel/schema.sql` in phpMyAdmin.
2. Upload CONTENTS of `hosting/panel/` to `public_html/agent/`; ensure `data/uploads` and `data/artifacts` are writable by PHP but not browsable over HTTP.
3. Ensure PHP 8.1+ with `pdo_mysql`, `curl`, `mbstring`, sessions, file uploads; PHP `upload_max_filesize` >= 25M and `post_max_size` >= 30M.
4. SSH on hosting, from inside `public_html/agent/`: `php tools/generate-secrets.php`. Choose an admin password; record the 3 generated values privately.
5. Copy `config.example.php` to `config.php`. Fill `admin_password_hash`, `client_api_key`, `worker_key`, database connection, `github_repo`, `github_ref` and the GitHub PAT. Do not place real secrets into GitHub source files.
6. Create a **fine-grained GitHub PAT** restricted to this new repo, permission **Actions: Read and write**. It is used only by the PHP server to dispatch the workflow. Store the PAT in `config.php`, inaccessible via the web.
7. Test `https://YOUR-DOMAIN/agent/api/v1/health` => `{ "ok": true, ... }`. Open `https://YOUR-DOMAIN/agent/` and sign in.

**Host security:** Keep HTTPS on and `.htaccess` enabled (Hostinger/LiteSpeed). If on Nginx, deny direct access to `data/`, `config.php`, `schema.sql`, and `tools/` using Nginx rules, since `.htaccess` is ignored. Where possible, place private data OUTSIDE `public_html`; this package defaults to `.htaccess`-guarded directories for typical shared hosting.

## 3 — GitHub secrets and variable

In the NEW repo: Settings → Secrets and variables → Actions → Repository secrets:

| Key | Example |
|---|---|
| `PANEL_BASE_URL` | `https://YOUR-DOMAIN/agent` |
| `WORKER_KEY` | same random `worker_key` as in `config.php` |

Optional Repository variable `CACHE_MODEL` = `true` to cache ~1.2GB model between runs (uses GitHub Actions cache quota).

Optional Repository variable `ENABLE_GHIDRA` = `true` to download official Ghidra and run headless native imports. This is resource-intensive, may exceed runner storage/time quotas. Default `false` gives ELF headers + symbols via `readelf`.

The model used by default: `mradermacher/DeepSeek-R1-Distill-Qwen-1.5B-abliterated-GGUF:Q4_K_M` (~1.2GB). `llama-server` is compiled and launched on 127.0.0.1 within each Actions job. No inference HTTP port is exposed publicly. GitHub Actions CPU throughput is modest; expect latency per job.

## 4 — Examples: PHP-hosted API

```bash
export BASE='https://YOUR-DOMAIN/agent'
export KEY='YOUR_CLIENT_API_KEY'

# Chat (asynchronous HTTP 202)
curl -X POST "$BASE/api/v1/jobs" \
  -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -d '{"kind":"chat","prompt":"Explain Android JNI OnLoad in Hindi"}'

# Analyze an owned APK
curl -X POST "$BASE/api/v1/jobs" -H "Authorization: Bearer $KEY" \
  -F 'kind=analyze' -F 'prompt=Inventory packages, native imports and JNI' \
  -F 'file=@/path/to/owned.apk'

# Poll result (replace returned job ID)
curl "$BASE/api/v1/jobs/JOB_ID" -H "Authorization: Bearer $KEY"

# Download ZIP after completion
curl -L "$BASE/api/v1/jobs/JOB_ID/artifact" \
  -H "Authorization: Bearer $KEY" -o report.zip
```

Alternative `POST /api/v1/chat/completions` accepts `messages: [{"role":"user","content":"..."}]`, but **returns HTTP 202 and a job ID**, not a synchronous OpenAI chat response. Use polling. Production code should use retry/backoff and rate-limits at the reverse proxy/WAF.

## 5 — Exact-match Smali patch (only owned/authorized files)

```bash
curl -X POST "$BASE/api/v1/jobs" -H "Authorization: Bearer $KEY" \
  -F 'kind=patch' -F 'prompt=Review the specified Smali edit and syntax' \
  -F 'file=@/path/to/owned.apk' \
  -F 'patch_spec={"path":"smali/com/example/Feature.smali","find":"    nop","replace":"    return-void"}'
```

- Path must be an existing `smali*/.../*.smali` inside the decoded APK, or empty for a standalone uploaded `.smali` file.
- Exact `find` must occur **once**. No AI-produced code is automatically executed or blindly applied.
- Produces `patch.diff`, patched Smali, and tries an **UNSIGNED** APK rebuild; a rebuilt APK will not install over the original signed package unless signed appropriately with the same original signing key. Native `.so` is **read-only** here.
- Recompilation may fail for protected/unsupported APKs. Large APKs may be retained only in GitHub Actions artifacts rather than uploaded to shared hosting.

## 6 — Optional MCP (local client)

On a trusted machine with Python, run:

```bash
cd github
python -m pip install -r mcp/requirements.txt
export WORKSPACE=/absolute/path/to/your/owned/binaries
export GHIDRA_HOME=/absolute/path/to/installed/ghidra
python mcp/server.py
```

Configure your MCP-compatible client with `github/mcp/client-config.example.json` (absolute paths). Tools: `apk_inventory`, `elf_symbols`, and `ghidra_headless_import` if Ghidra is installed. **This MCP server is separate from the single-job GitHub LLM pipeline.** It offers read-only tool invocations and does not imply autonomous AI function calling.

## 7 — Operations / limits

- GitHub-hosted jobs are ephemeral, with 6-hour maximum per job. This worker sets a 75-minute timeout per request. Every job must launch a new VM, load model and run inference. **Not 24/7**, and no realtime synchronous OpenAI-compatible inference endpoint.
- Heavy 14B/32B models need GPU VPS/self-hosted runner or paid inference provider for practical throughput.
- PHP requires MySQL and configured secrets; the full path-based API works with Apache or LiteSpeed rewrite rules. Host must support outbound HTTPS (GitHub API) and inbound HTTPS callback.
- This package does **not** bypass licenses, integrity checks, hardware attestation or game anti-cheat, and does not write patches to native machine code.

## Troubleshooting

| Symptom | Check |
|---|---|
| `dispatch_failed` | GitHub fine-grained token, exact `OWNER/REPO`, workflow on `main`, Actions write permission |
| Job starts but fails | GitHub Actions logs and 7-day workflow artifact `error.txt` / `llama-server.log` |
| Model startup timeout | Hugging Face download access, llama.cpp compile, network availability, CPU constraints |
| Panel 404 for `/api/v1/...` | Apache/LiteSpeed `mod_rewrite`, correct subfolder path; test `api.php?route=v1/health` |
| Panel 500 | PHP error log, PDO MySQL, database schema, directory permissions |
| Upload rejected | `upload_max_filesize`, `post_max_size`, max upload bytes, file magic |
| No ZIP on panel | PHP upload limits; inspect workflow artifact or configure max upload |

## Security

Keep the repository private. Never expose `WORKER_KEY`, PAT, or client API key in browser frontend JS. Separate worker endpoints require `X-Worker-Key`; clients require `Authorization: Bearer CLIENT_API_KEY`, and admin form POSTs use CSRF protection. Never upload proprietary binaries without authorization and be aware that input and output travel through both your shared hosting and GitHub's infrastructure.
