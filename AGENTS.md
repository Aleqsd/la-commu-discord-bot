# Repository Guidelines

## Project Structure & Module Organization
- `main.py` boots the `LaCommuDiscordBot`, wiring config, the OpenAI parser, and the aiohttp health server used by Scaleway.
- `bot/` hosts core modules: `client.py` (Discord logic and slash commands), `formatter.py` (embeds & error cards), `scraping.py` (HTTP + HTML/image handling), `openai_client.py` (text/vision parsing), `health.py` (health endpoint), and `models.py`/`utils.py`.
- `requirements.txt`, `.env.example`, `Dockerfile`, `Makefile`, and `README.md` sit at root for dependency management, automation, and docs.

## Operational Workflow
- Primary entrypoints are `/jobbot post` (parse & publish) and `/jobbot preview` (parse-only).
- `reference` text can include regular URLs or `image: https://...` markers; images are sent directly to OpenAI via `image_url`.
- `/jobbot status` reveals current routing (Manage Guild permission required).
- A lightweight `aiohttp` health server listens on env `PORT` (default 8080) to satisfy Scaleway probes.
- Makefile targets (`build`, `push`, `deploy`, `test`) automate the Docker/Scaleway flow. Container `2fed6267-5ce1-4331-80f8-241f411a782e` deploys `rg.fr-par.scw.cloud/la-commu-discord-bot/la-commu-discord-bot:latest`.
- Slash-command requests are persisted; unfinished `/jobbot post` submissions are retried automatically on startup (max 3 attempts).

## Build, Test, and Development Commands
- `python -m pip install -r requirements.txt` — install runtime dependencies.
- `python main.py` — run the bot locally.
- `python -m compileall .` — quick syntax smoke check.
- `docker build -t la-commu-discord-bot .` / `docker push ...` — container workflow.
- `make build|push|deploy|test` — convenience targets.

## Coding Style & Naming Conventions
- Target Python 3.12+, use annotations and type hints.
- Dataclasses for structured data; async/await for I/O.
- Keep logging consistent with emoji-prefixed messages.
- snake_case for modules/functions, PascalCase for classes.

## Testing Guidelines
- Tests live under `tests/` and use pytest + pytest-asyncio.
- Install deps via `python -m pip install -r requirements-test.txt` and run `python -m pytest` (or `make test`).
- Prefer mocking over live HTTP/Discord calls; manually validate slash commands in staging.

## Commit & Pull Request Guidelines
- Write imperative commit subjects.
- For PRs include summary, testing notes, and screenshots/log excerpts for Discord-facing changes.
- Call out env/config changes explicitly.

## Security & Configuration Tips
- Never commit secrets; rely on `.env.example`.
- Ensure the Discord bot has Message Content intent enabled.
- Keep `JOB_TEAM_CHANNEL_IDS` in sync with live channel IDs.
- Rotate registry tokens and Discord/OpenAI keys as needed.
