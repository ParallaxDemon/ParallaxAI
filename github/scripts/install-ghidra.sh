#!/usr/bin/env bash
set -euo pipefail
# Optional heavyweight (official) Ghidra release. Enable only for native analysis.
python3 - <<'PY'
import json, urllib.request, pathlib
req=urllib.request.Request('https://api.github.com/repos/NationalSecurityAgency/ghidra/releases/latest',headers={'User-Agent':'Authorized-RE-Analyzer','Accept':'application/vnd.github+json'})
with urllib.request.urlopen(req,timeout=30) as r: data=json.load(r)
asset=next((a for a in data.get('assets',[]) if a['name'].startswith('ghidra_') and '_PUBLIC_' in a['name'] and a['name'].endswith('.zip')),None)
if not asset:raise SystemExit('Ghidra official zip not found in latest release')
url=asset['browser_download_url']
if not url.startswith('https://github.com/NationalSecurityAgency/ghidra/releases/download/'):raise SystemExit('Unrecognized Ghidra URL')
print('Downloading optional Ghidra release:',asset['name'],flush=True)
urllib.request.urlretrieve(url,'/tmp/ghidra.zip')
PY
mkdir -p /tmp/ghidra-release
unzip -q /tmp/ghidra.zip -d /tmp/ghidra-release
HOME=$(find /tmp/ghidra-release -maxdepth 2 -type f -name ghidraRun | head -1 | xargs -r dirname)
if [ -z "$HOME" ] || [ ! -f "$HOME/support/analyzeHeadless" ]; then echo 'Ghidra extraction incomplete'; exit 1; fi
printf 'GHIDRA_HOME=%s\n' "$HOME" >> "$GITHUB_ENV"
