# la-commu-discord-bot

`la-commu-discord-bot` is a Discord bot that exposes slash commands to scrape job links (and referenced images), extract structured data with the OpenAI API, and redistribute formatted embeds to speciality channels (`art`, `game-design`, `dev`, `others`). The production instance runs on an OVH VPS as a `systemd` service (see [Production (OVH VPS)](#production-ovh-vps)); a Docker image and a Scaleway Serverless Containers workflow are kept as alternatives.

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
- Reads only the post itself on LinkedIn post pages (the "More Relevant Posts" block of other people's offers is dropped).
- Links each job to its own posting when the page links to it (the model sees every link with its text); when several offers from one page would get the same link, they link to the page itself instead.
- Skips reposting jobs that were already shared by tracking them in `data/posted_jobs.log` (normalized job URL; URL + company + title when several offers from one source share a link).

## Requirements

- Python 3.12+ (or Docker)
- Discord bot token with the **Message Content Intent** enabled.
- OpenAI API key (default model: `gpt-6.1-terra`; optional vision model override).

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

| Variable                       | Description                                                                                                            | Default                |
| ------------------------------ | ---------------------------------------------------------------------------------------------------------------------- | ---------------------- |
| `DISCORD_BOT_TOKEN`            | Discord bot token.                                                                                                     | —                      |
| `OPENAI_API_KEY`               | OpenAI API key.                                                                                                        | —                      |
| `OPENAI_MODEL`                 | OpenAI text model name.                                                                                                | `gpt-6.1-terra`        |
| `OPENAI_IMAGE_MODEL`           | Optional OpenAI vision-capable model.                                                                                  | mirrors `OPENAI_MODEL` |
| `OPENAI_TEMPERATURE`           | Model temperature. Leave unset for gpt-5.4+ / gpt-6 models, which reject it.                                           | unset                  |
| `JOB_TEAM_CHANNEL_IDS`         | Comma-separated mapping of team→channel ID (e.g. `art:123,...`). IDs must belong to the same guild where the bot runs. | —                      |
| `MAX_SCRAPE_BYTES`             | Max bytes downloaded from a page (larger pages are cut off or refused).                                                | `3000000`              |
| `REQUEST_TIMEOUT`              | Page fetch timeout in seconds.                                                                                         | `30`                   |
| `RESPONSE_TIMEOUT`             | OpenAI request timeout in seconds (one retry).                                                                         | `60`                   |
| `PORT`                         | HTTP health port for hosting platforms (Scaleway expects 8080).                                                        | `8080`                 |
| `LOG_LEVEL`                    | Root logging level (`DEBUG`, `INFO`, ...).                                                                             | `INFO`                 |
| `JOBBOT_TRUSTED_USER_IDS`      | Comma-separated Discord user IDs that bypass the `/jobbot` limits (as do Manage Server / Administrator members).       | —                      |
| `JOBBOT_MAX_REQUESTS_PER_HOUR` | `/jobbot post` + `preview` requests per member per hour (sliding window, `0` disables).                                | `20`                   |
| `JOBBOT_MAX_REQUESTS_PER_DAY`  | `/jobbot post` + `preview` requests per member per 24 h (sliding window, `0` disables).                                | `100`                  |

## How It Works

1. Members trigger `/jobbot post` or `/jobbot preview`, providing URLs or image references (including `image: https://...` syntax).
2. The bot fetches each URL/image, cleans the content, and primes it for OpenAI.
3. OpenAI returns a JSON object (`{"jobs": [...]}`); the bot classifies each job into a team bucket.
4. For `/jobbot post`, embeds are dropped into the mapped channels with consistent formatting; `/jobbot preview` reports what _would_ happen.
5. Detailed logs keep track of progress (`🌐`, `🖼️`, `📤`, etc.), while command responses surface any parsing or routing issues.

Supported teams by default are `art`, `game_design`, `dev`, and `others`. You can extend this list by adding additional `team:id` pairs to `JOB_TEAM_CHANNEL_IDS`; any unmapped team falls back to posting issues in-command.

## Production (OVH VPS)

Production runs on an OVH VPS (Ubuntu) as the `systemd` unit `la-commu-discord-bot`, set up as described in [Running as a Systemd Service](#running-as-a-systemd-service-ubuntu-server):

| What              | Where                                                         |
| ----------------- | ------------------------------------------------------------- |
| Checkout (`main`) | `/home/ubuntu/GitHub/la-commu-discord-bot`                    |
| Virtualenv        | `.venv/` in the checkout                                      |
| Secrets           | `la-commu-discord-bot.env` in the checkout (not committed)    |
| Duplicate history | `data/posted_jobs.log` in the checkout                        |
| Logs              | `sudo journalctl -u la-commu-discord-bot` and `job-caster.log` |

To deploy, push to `main`, then on the VPS:

```bash
cd ~/GitHub/la-commu-discord-bot
git pull --ff-only origin main
.venv/bin/pip install -r requirements.txt        # only when requirements.txt changed
sudo systemctl restart la-commu-discord-bot
sudo journalctl -u la-commu-discord-bot -n 30 --no-pager   # expect "🚀 Logged in as ..."
```

Unfinished `/jobbot post` requests are resumed on startup, so a restart does not lose submissions.

## Scaleway Delivery Model

Production runs on the OVH VPS (see above); the project still ships with the Scaleway workflow if you want to deploy there instead.

- Docker image is built locally (`make build`) and pushed to the Scaleway Container Registry (`make push`).
- Serverless container (Always On) pulls `rg.fr-par.scw.cloud/la-commu-discord-bot/la-commu-discord-bot:latest` and runs the bot.
- `PORT` exposes a lightweight health server so Scaleway health checks succeed.
- `make deploy` combines push + redeploy and tails logs to confirm startup.

## Slash Commands

- `/jobbot post [reference]` — Parse job URLs/posters and publish embeds to the team channels.
- `/jobbot preview [reference]` — Inspect how the bot would route jobs (no posting).
- `/jobbot status` — View the configured source + destination channels and cached routes.

All `/jobbot` commands are available to every server member. `post` and `preview` are rate-limited per member (`JOBBOT_MAX_REQUESTS_PER_HOUR` / `JOBBOT_MAX_REQUESTS_PER_DAY`, in memory, reset on restart) and read at most 20 URLs per request. Members with Manage Server or Administrator, and users listed in `JOBBOT_TRUSTED_USER_IDS`, skip the rate limit and may send up to 50 URLs. Server admins can still restrict who sees the commands under Server Settings > Integrations.

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
- UptimeRobot watches the public health check at https://stats.uptimerobot.com/Q2aJxylmN9/801513961 to alert if the production server stops responding.
- Duplicate protection stores identifiers in `data/posted_jobs.log`; remove a line if you need to re-post a specific offer.

## Future Enhancements

- Persisted caching to avoid re-processing the same URL or image.
- Automatic thread creation per job for discussion.
- Additional validation checks before posting embeds.

---

## Running as a Systemd Service (Ubuntu server)

This is how production runs on the OVH VPS: **systemd** keeps the bot alive in the
background, restarts it on crash or reboot, and collects its logs.

### 1. Create a virtualenv (once per machine)

```bash
git clone https://github.com/Aleqsd/la-commu-discord-bot.git ~/GitHub/la-commu-discord-bot
cd ~/GitHub/la-commu-discord-bot
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
Description=Discord Bot La Commu
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/GitHub/la-commu-discord-bot
ExecStart=/home/ubuntu/GitHub/la-commu-discord-bot/.venv/bin/python main.py
EnvironmentFile=/home/ubuntu/GitHub/la-commu-discord-bot/la-commu-discord-bot.env
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

> `EnvironmentFile` loads the secrets (Discord token, OpenAI key, channel IDs, etc.): create
> `la-commu-discord-bot.env` from `.env.example`.
> Console output also goes to `job-caster.log` in the checkout; add `--log-file <path>` to `ExecStart` to change it.

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

See [Production (OVH VPS)](#production-ovh-vps) for the deploy commands
(`git pull --ff-only origin main`, `pip install` when needed, then `systemctl restart`).

The service will automatically restart if it crashes or after a reboot,
and logs will always be available through `journalctl`.
