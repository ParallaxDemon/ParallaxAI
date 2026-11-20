#!/usr/bin/env bash
set -euo pipefail
# Only fetch official JADX release assets. Runner is ephemeral.
python3 - <<'PY'
import json, urllib.request, os, pathlib
req=urllib.request.Request('https://api.github.com/repos/skylot/jadx/releases/latest',headers={'User-Agent':'Authorized-Android-RE-Agent','Accept':'application/vnd.github+json'})
with urllib.request.urlopen(req,timeout=25) as r: release=json.load(r)
assets=[a for a in release.get('assets',[]) if a['name'].startswith('jadx-') and a['name'].endswith('.zip') and not 'sources' in a['name']]
if not assets: raise SystemExit('JADX official release zip not found')
url=assets[0]['browser_download_url']
if not url.startswith('https://github.com/skylot/jadx/releases/download/'): raise SystemExit('Unexpected JADX URL')
p=pathlib.Path('/tmp/jadx.zip');print('Downloading JADX release:',assets[0]['name'])
urllib.request.urlretrieve(url,p)
PY
mkdir -p /tmp/jadx
unzip -q /tmp/jadx.zip -d /tmp/jadx
chmod +x /tmp/jadx/bin/jadx
printf '%s\n' '/tmp/jadx/bin' >> "$GITHUB_PATH"
