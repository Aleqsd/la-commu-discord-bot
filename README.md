# la-commu-discord-bot

`la-commu-discord-bot` is a Discord bot packaged for Scaleway Serverless Containers. It exposes slash commands that scrape job links (and referenced images), extract structured data with the OpenAI API, and redistribute formatted embeds to speciality channels (`art`, `game-design`, `dev`, `others`). The production instance currently runs on a DigitalOcean Droplet managed via `systemd`.

## Quick Start

1. Clone the repo and copy `.env.example` to `.env` with your Discord token, OpenAI key, and channel IDs.
2. Install dependencies globally:
   ```bash
   python -m pip install -r requirements.txt
   ```
3. Run the bot locally:
   ```bash
   python main.py
   ```
   Optional helpers via `make`: `make build`, `make test`, `make deploy`.
   Logs are also written to `job-caster.log` in the project root; use `--log-file logs/bot.log` (or another path) to change the destination.

## Features

- `/jobbot post` ingests job links or posters and fans them out to the right team channels.
- Fetches and cleans job posting pages via `httpx` + `BeautifulSoup`.
- Understands `image: https://...` references pointing to posters with multiple offers.
- Summarises and classifies **every** job found (text or image) using OpenAI.
- Routes postings to the appropriate channel with consistent emoji-rich embeds.
- Provides `/jobbot` slash commands to inspect status, resync channels, and dry-run parsing.
- Slash commands return friendly, developer-focused feedback (with troubleshooting notes when parsing fails).
- Configurable channel mapping and OpenAI models (separate text vs. vision) via environment variables.
- Ready-to-run Docker image.
- Skips reposting jobs that were already shared by tracking their URLs in `data/posted_jobs.log`.

## Requirements

- Python 3.12+ (or Docker)
- Discord bot token with the **Message Content Intent** enabled.
- OpenAI API key (default model: `gpt-4o-mini`; optional vision model override).

## Setup (Local)

1. Copy `.env.example` to `.env` and fill in your secrets.
2. Install dependencies globally (or with your preferred tool):
   ```bash
   python3 -m ensurepip --upgrade        # only if pip is missing
   python3 -m pip install --upgrade pip
   python3 -m pip install -r requirements.txt
   ```
3. Run the bot:
   ```bash
   python3 main.py
   ```
   Tip: set `JOB_TEAM_CHANNEL_IDS` (see below) before launching so the bot knows where to route each specialty (art, game_design, dev, others).
   Logs also go to `job-caster.log` by default; override with `--log-file path/to/log.log` if you prefer another location.

## Testing

1. Install test tooling (after the runtime deps above):
   ```bash
   python -m pip install -r requirements-test.txt
   ```
2. Run the full suite:
   ```bash
   python -m pytest
   ```
   Or simply run `make test`.

## Environment Variables

| Variable               | Description                                                                                                            | Default                |
| ---------------------- | ---------------------------------------------------------------------------------------------------------------------- | ---------------------- |
| `DISCORD_BOT_TOKEN`    | Discord bot token.                                                                                                     | —                      |
| `OPENAI_API_KEY`       | OpenAI API key.                                                                                                        | —                      |
| `OPENAI_MODEL`         | OpenAI text model name.                                                                                                | `gpt-4o-mini`          |
| `OPENAI_IMAGE_MODEL`   | Optional OpenAI vision-capable model.                                                                                  | mirrors `OPENAI_MODEL` |
| `OPENAI_TEMPERATURE`   | Model temperature.                                                                                                     | `0.1`                  |
| `JOB_TEAM_CHANNEL_IDS` | Comma-separated mapping of team→channel ID (e.g. `art:123,...`). IDs must belong to the same guild where the bot runs. | —                      |
| `MAX_SCRAPE_BYTES`     | Max characters read from a page.                                                                                       | `600000`               |
| `MAX_IMAGE_BYTES`      | Max image payload size (bytes).                                                                                        | `5000000`              |
| `REQUEST_TIMEOUT`      | Page/image fetch timeout in seconds.                                                                                   | `30`                   |
| `RESPONSE_TIMEOUT`     | Reserved for future use.                                                                                               | `60`                   |
| `PORT`                 | HTTP health port for hosting platforms (Scaleway expects 8080).                                                        | `8080`                 |
| `LOG_LEVEL`            | Root logging level (`DEBUG`, `INFO`, ...).                                                                             | `INFO`                 |

## How It Works

1. Staff trigger `/jobbot post` or `/jobbot preview`, providing URLs or image references (including `image: https://...` syntax).
2. The bot fetches each URL/image, cleans the content, and primes it for OpenAI.
3. OpenAI returns a JSON array of jobs; the bot classifies each into a team bucket.
4. For `/jobbot post`, embeds are dropped into the mapped channels with consistent formatting; `/jobbot preview` reports what _would_ happen.
5. Detailed logs keep track of progress (`🌐`, `🖼️`, `📤`, etc.), while command responses surface any parsing or routing issues.

Supported teams by default are `art`, `game_design`, `dev`, and `others`. You can extend this list by adding additional `team:id` pairs to `JOB_TEAM_CHANNEL_IDS`; any unmapped team falls back to posting issues in-command.

## Scaleway Delivery Model

Although production currently runs on a DigitalOcean Droplet, the project still ships with the Scaleway workflow if you want to deploy there instead.

- Docker image is built locally (`make build`) and pushed to the Scaleway Container Registry (`make push`).
- Serverless container (Always On) pulls `rg.fr-par.scw.cloud/la-commu-discord-bot/la-commu-discord-bot:latest` and runs the bot.
- `PORT` exposes a lightweight health server so Scaleway health checks succeed.
- `make deploy` combines push + redeploy and tails logs to confirm startup.

## Slash Commands

- `/jobbot post [reference]` — Parse job URLs/posters and publish embeds to the team channels.
- `/jobbot preview [reference]` — Inspect how the bot would route jobs (no posting).
- `/jobbot status` — View the configured source + destination channels and cached routes (requires Manage Guild).

## Sharing Multi-Offer Sources

- Use `/jobbot post image: https://cdn.../offer.jpg` or attach the poster directly to the command.
- The parser will scan for multiple roles in the capture or web page and fan them out automatically.

## Customising Channel Mapping

Set `JOB_TEAM_CHANNEL_IDS` in your environment. Example:

```
JOB_TEAM_CHANNEL_IDS=art:111111111111111111,game_design:222222222222222222,dev:333333333333333333,others:444444444444444444
```

## Updating the Scaleway Container

After changing code locally:

```bash
docker build -t la-commu-discord-bot:latest .
docker tag la-commu-discord-bot:latest rg.fr-par.scw.cloud/la-commu-discord-bot/la-commu-discord-bot:latest
docker push rg.fr-par.scw.cloud/la-commu-discord-bot/la-commu-discord-bot:latest
scw container container deploy 2fed6267-5ce1-4331-80f8-241f411a782e
```

If you rotate registry credentials, run `docker login rg.fr-par.scw.cloud/la-commu-discord-bot` again before the push.

## Publishing to GitHub Container Registry

1. Generate a GitHub Personal Access Token (classic) with the `write:packages` scope and keep it handy.
2. Authenticate Docker once:
   ```bash
   echo "$GITHUB_PAT" | docker login ghcr.io -u <your-github-username> --password-stdin
   ```
3. Push using the Makefile helper (override `GHCR_NAMESPACE` with your username or organisation):
   ```bash
   make GHCR_NAMESPACE=<your-github-namespace> ghcr-push
   ```

The helper tags the local image as `ghcr.io/<namespace>/<IMAGE>` (defaults to `la-commu-discord-bot:latest`) before pushing. Re-run the `docker login` step whenever the token changes or expires.

## Notes

- Ensure your Discord guild has text channels matching the IDs you provide.
- The OpenAI API call runs in a background thread to avoid blocking the event loop.
- The bot adds a ✅ reaction when at least one job is posted; otherwise it replies with diagnostics.
- Tests mock external services; avoid running them while the bot is logged into production to keep logs clean.
- Slash-command submissions are persisted; if the bot restarts mid-process they are retried automatically (up to three attempts).
- A lightweight health server responds on `PORT` (default `8080`) so hosting platforms can probe readiness.
- UptimeRobot watches the public health check at https://stats.uptimerobot.com/Q2aJxylmN9/801513961 to alert if the Droplet stops responding.
- Duplicate protection stores identifiers in `data/posted_jobs.log`; remove a line if you need to re-post a specific offer.

## Future Enhancements

- Persisted caching to avoid re-processing the same URL or image.
- Automatic thread creation per job for discussion.
- Additional validation checks before posting embeds.

---

## Running Locally as a Systemd Service (Ubuntu server)

When running outside of Scaleway (e.g. on your own Ubuntu host), you can keep the bot
alive in the background and view logs easily by using **systemd**.

### 1. Create a virtualenv (once per machine)

```bash
cd ~/la-commu-discord-bot
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip
pip install -r requirements.txt
deactivate
```

### 2. Create the systemd unit

Create `/etc/systemd/system/la-commu-discord-bot.service` (requires `sudo`) with:

```ini
[Unit]
Description=Job Caster Discord Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=ubuntu                        # change if needed
Group=ubuntu
WorkingDirectory=/home/ubuntu/la-commu-discord-bot
Environment="PYTHONUNBUFFERED=1"
EnvironmentFile=-/home/ubuntu/la-commu-discord-bot/.env
ExecStart=/home/ubuntu/la-commu-discord-bot/.venv/bin/python /home/ubuntu/la-commu-discord-bot/main.py --log-file /home/ubuntu/la-commu-discord-bot/logs/jobbot.log
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

> `EnvironmentFile` loads your `.env` secrets (Discord token, OpenAI key, etc.).
> `--log-file` mirrors console output into `/home/ubuntu/la-commu-discord-bot/logs/jobbot.log`; change the path if you prefer a different location.

### 3. Enable and start the service

```bash
sudo systemctl daemon-reload
sudo systemctl enable la-commu-discord-bot       # start at boot
sudo systemctl start la-commu-discord-bot        # launch immediately
```

### 4. Useful commands

```bash
sudo systemctl stop la-commu-discord-bot         # stop the bot
sudo systemctl restart la-commu-discord-bot      # restart after code/config change
sudo systemctl status la-commu-discord-bot       # see current status
sudo journalctl -u la-commu-discord-bot -f       # follow logs live (Ctrl-C to quit)
```

Inside the repository you can also run:

```bash
make systemd-restart  # wraps systemctl restart $SYSTEMD_UNIT (default: la-commu-discord-bot)
make systemd-tail     # tails journalctl for the same unit
```

Override `SYSTEMD_UNIT`, `SYSTEMCTL`, or `JOURNALCTL` when invoking `make` if your setup differs, e.g. `make SYSTEMD_UNIT=my-bot systemd-restart`.

### 5. Updating code or dependencies

```bash
cd ~/la-commu-discord-bot
git pull                               # fetch new code
source .venv/bin/activate
pip install -r requirements.txt
deactivate
sudo systemctl restart la-commu-discord-bot      # apply the changes
```

The service will automatically restart if it crashes or after a reboot,
and logs will always be available through `journalctl`.
