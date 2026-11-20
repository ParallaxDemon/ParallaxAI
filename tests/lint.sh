#!/usr/bin/env bash
set -euo pipefail
find hosting/panel -name '*.php' ! -name config.php -print0 | xargs -0 -n1 php -l
python -m compileall -q github/worker github/mcp
python -m unittest discover -s tests -p 'test_*.py' -v
