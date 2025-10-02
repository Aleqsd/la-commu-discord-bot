# la-commu-discord-bot

`la-commu-discord-bot` offers slash commands that scrape job links (and referenced images), extract structured data with the OpenAI API, and redistribute formatted embeds to speciality channels (`art`, `game-design`, `dev`, `others`).

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

## Requirements
- Python 3.12+ (or Docker)
- Discord bot token with the **Message Content Intent** enabled.
- OpenAI API key (default model: `gpt-5.1-mini`; optional vision model override).

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

## Testing
1. Install test tooling (after the runtime deps above):
   ```bash
   python3 -m pip install -r requirements-test.txt
   ```
2. Run the full suite:
   ```bash
   python3 -m pytest
   ```
   Use `python3 -m pytest -k utils` to target specific modules or add `-vv` for verbose logs.

## Environment Variables
| Variable | Description | Default |
| --- | --- | --- |
| `DISCORD_BOT_TOKEN` | Discord bot token. | — |
| `OPENAI_API_KEY` | OpenAI API key. | — |
| `OPENAI_MODEL` | OpenAI text model name. | `gpt-5.1-mini` |
| `OPENAI_IMAGE_MODEL` | Optional OpenAI vision-capable model. | mirrors `OPENAI_MODEL` |
| `OPENAI_TEMPERATURE` | Model temperature. | `0.1` |
| `JOB_TEAM_CHANNEL_IDS` | Comma-separated mapping of team→channel ID (e.g. `art:123,...`). IDs must belong to the same guild where the bot runs. | — |
| `MAX_SCRAPE_BYTES` | Max characters read from a page. | `600000` |
| `MAX_IMAGE_BYTES` | Max image payload size (bytes). | `5000000` |
| `REQUEST_TIMEOUT` | Page/image fetch timeout in seconds. | `30` |
| `RESPONSE_TIMEOUT` | Reserved for future use. | `60` |
| `PORT` | HTTP health port for hosting platforms (Scaleway expects 8080). | `8080` |
| `LOG_LEVEL` | Root logging level (`DEBUG`, `INFO`, ...). | `INFO` |

## How It Works
1. Staff trigger `/jobbot post` or `/jobbot preview`, providing URLs or image references (including `image: https://...` syntax).
2. The bot fetches each URL/image, cleans the content, and primes it for OpenAI.
3. OpenAI returns a JSON array of jobs; the bot classifies each into a team bucket.
4. For `/jobbot post`, embeds are dropped into the mapped channels with consistent formatting; `/jobbot preview` reports what *would* happen.
5. Detailed logs keep track of progress (`🌐`, `🖼️`, `📤`, etc.), while command responses surface any parsing or routing issues.

Supported teams by default are `art`, `game_design`, `dev`, and `others`. You can extend this list by adding additional `team:id` pairs to `JOB_TEAM_CHANNEL_IDS`; any unmapped team falls back to posting issues in-command.

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

## Notes
- Ensure your Discord guild has text channels matching the IDs you provide.
- The OpenAI API call runs in a background thread to avoid blocking the event loop.
- The bot adds a ✅ reaction when at least one job is posted; otherwise it replies with diagnostics.
- Tests mock external services; avoid running them while the bot is logged into production to keep logs clean.
- A lightweight health server responds on `PORT` (default `8080`) so hosting platforms can probe readiness.

## Future Enhancements
- Persisted caching to avoid re-processing the same URL or image.
- Automatic thread creation per job for discussion.
- Additional validation checks before posting embeds.
