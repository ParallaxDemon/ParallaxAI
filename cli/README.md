# ParallaxAI terminal client

One command, any terminal (Termux included). Talks to the live panel API.

## Setup (once)

```sh
python3 cli/parallax.py config --base https://ai.parallaxserver.online --key YOUR_CLIENT_API_KEY
```

Or install the short launcher (already done on this device):

```sh
mkdir -p ~/.local/bin
cat > ~/.local/bin/parallax <<'EOF'
#!/bin/sh
exec python3 "$HOME/projects/ParallaxAI/cli/parallax.py" "$@"
EOF
chmod 700 ~/.local/bin/parallax
```

`~/.local/bin` must be in `PATH`. Environment variables `PANEL_BASE_URL` and
`PANEL_API_KEY` override the saved config.

## Usage

```sh
# One chat — waits and prints the answer
parallax chat "Explain this DEX file"

# MANY chats in ONE command (up to 20), from arguments or a file
parallax batch "Question one" "Question two" "Question three"
parallax batch --file prompts.txt

# Don't wait; just get IDs
parallax chat "hi" --no-wait
parallax batch --file prompts.txt --no-wait

# Check status of a job or a whole batch
parallax status 3e6ede1a71cd4c5e982b3681f2ac0919
parallax status <job_id>
parallax status <job_id> --json

# Recent jobs / download a report ZIP
parallax jobs --limit 10
parallax artifact <job_id> report.zip
```

Batch mode returns a single `batch_id`; `parallax status <batch_id>` shows all
chats in that batch with progress (`done/total`). All jobs run in parallel on
GitHub Actions, so a 3-chat batch finishes in roughly the time of one chat.
