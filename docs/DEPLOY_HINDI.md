# Full setup — हिंदी में

## कौन-कौन सी चीज़ ready हैं?

**Code ready:** PHP dashboard, MySQL schema, API key authentication, job dispatch, job status polling, Python worker, GitHub Actions workflow, DeepSeek 1.5B Abliterated inference through llama.cpp CPU, JADX/Apktool/ELF analysis, exact-match Smali patch, optional Ghidra headless, optional read-only MCP tool server.

**Live deployment नहीं हुआ:** अभी GitHub connector `ParallaxDemon` पर है और उपलब्ध tools में **नया repository create** करने का function नहीं है। Shared-hosting login, domain, MySQL और token भी इस बातचीत में उपलब्ध नहीं हैं। इसलिए live GitHub workflow/model run नहीं हो सका।

## Step A — GitHub repo बनाकर code push करो

1. ZIP extract करके `github` folder में जाओ।
2. `gh auth status` से सही GitHub account देखो।
3. `git init -b main`, `git add .`, `git commit -m "Initial agent"` करो।
4. `gh repo create deepseek-re-bridge --private --source=. --remote=origin --push` चलाओ।
5. GitHub → repository → Actions में दो workflows दिखेंगे: **Check PHP, Python and worker tests** और **DeepSeek RE Agent (on demand)**।

## Step B — Hostinger पर panel upload करो

1. hPanel में MySQL database और user बनाओ।
2. phpMyAdmin में `hosting/panel/schema.sql` import करो।
3. `hosting/panel/` के अंदर की सभी files `public_html/agent/` में upload करो। `panel` folder को फिर से nested मत करना।
4. `php tools/generate-secrets.php` से password hash + 2 keys बनाओ; values सुरक्षित रखो।
5. `config.example.php` → `config.php` copy करके DB, admin_password_hash, client_api_key, worker_key, github_repo, github_token भर दो।
6. GitHub token fine-grained PAT हो: केवल नए repo की **Actions: Read and write** permission।
7. PHP 8.1+, `pdo_mysql`, cURL, mbstring enabled; `upload_max_filesize=25M+` and `post_max_size=30M+`.
8. Dashboard: `https://YOUR-DOMAIN/agent/`।

## Step C — GitHub Actions secrets

GitHub repo → Settings → Secrets and variables → Actions:

- `PANEL_BASE_URL` = `https://YOUR-DOMAIN/agent` (आख़िर में slash नहीं)
- `WORKER_KEY` = config.php के `worker_key` की exact value

इसके बाद panel से prompt भेजो। PHP GitHub Actions trigger करेगा; runner DeepSeek GGUF model download/load करेगा; completion वापस PHP में आएगी। Result refresh में दिखाई देगा।

## API access example

```bash
curl -X POST 'https://YOUR-DOMAIN/agent/api/v1/jobs' \
 -H 'Authorization: Bearer YOUR_CLIENT_API_KEY' \
 -H 'Content-Type: application/json' \
 -d '{"kind":"chat","prompt":"JNI functions ka explanation Hindi me do"}'
```

Response में `id` मिलेगा। Poll:

```bash
curl 'https://YOUR-DOMAIN/agent/api/v1/jobs/JOB_ID' \
 -H 'Authorization: Bearer YOUR_CLIENT_API_KEY'
```

## Real-world limits

यह **API job/queue service** है, live streaming LLM नहीं। `1.5B` छोटा model है और बहुत मुश्किल reverse engineering में गलतियाँ कर सकता है। Actions CPU workers on-demand होते हैं; हर job पर startup/download overhead होगा। Native `.so` में automatic machine-code patching नहीं दी गई। Test किए बिना APK को production में deploy मत करो।
