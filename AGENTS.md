# Repository Guidelines

## Project Structure & Module Organization
- `main.py` boots the `LaCommuDiscordBot`, wiring config and the OpenAI parser.
- `bot/` hosts core modules: `client.py` (Discord logic and slash commands), `formatter.py` (embeds & error cards), `scraping.py` (HTTP + HTML/image handling), `openai_client.py` (text/vision parsing), and `models.py`/`utils.py` for shared dataclasses and helpers.
- `requirements.txt`, `.env.example`, `Dockerfile`, and `README.md` sit at root for dependency management, configuration, and containerization assets.

## Operational Workflow
- Primary entrypoints are `/jobbot post` (parse & publish) and `/jobbot preview` (parse-only).
- `reference` text can include regular URLs or `image: https://...` markers pointing at poster images.
- `/jobbot status` reveals current routing (Manage Guild permission required).
- A lightweight `aiohttp` health server listens on the env `PORT` (default 8080) to satisfy hosting probes.

## Build, Test, and Development Commands
- `python3 -m ensurepip --upgrade` (if `pip` is missing) then `python3 -m pip install --upgrade pip` — prep your global interpreter.
- `python3 -m pip install -r requirements.txt` — install runtime dependencies (discord.py, httpx, openai, etc.).
- `python3 main.py` — run the bot with your system Python.
- `python3 -m compileall .` — quick syntax smoke check across all modules.
- `docker build -t la-commu-discord-bot .` / `docker run --env-file .env la-commu-discord-bot` — container workflow.

## Coding Style & Naming Conventions
- Target Python 3.12, using `from __future__ import annotations` and type hints throughout.
- Prefer dataclasses for structured data and async/await for I/O.
- Keep logging consistent with existing emoji-prefixed messages; use structured titles for errors (`create_error_embed`).
- Stick to snake_case for modules, functions, and variables; PascalCase for classes (e.g., `LaCommuDiscordBot`).

## Testing Guidelines
- Automated tests live under `tests/` and use `pytest` + `pytest-asyncio` (install via `python3 -m pip install -r requirements-test.txt`).
- Add new cases as `test_<feature>.py`; prefer mocking over live HTTP/Discord calls.
- Run `python3 -m pytest` locally before pushing; CI-compatible command is the same.
- Manual validation still matters: exercise `/jobbot` slash commands in a staging guild and confirm auto triage with sample URLs/images.

## Commit & Pull Request Guidelines
- Craft commits with concise, imperative subjects (e.g., `Add slash command preview flow`).
- For pull requests, include: summary of changes, testing notes (commands run), and screenshots/log excerpts for Discord-facing updates.
- Reference relevant issues or TODOs, and call out configuration changes (env vars, channel mappings) in the description.

## Security & Configuration Tips
- Never commit real `.env` files or API keys; rely on `.env.example` for placeholders.
- Ensure Discord bots have Message Content intent enabled and OpenAI keys scoped appropriately.
- Populate `JOB_TEAM_CHANNEL_IDS` with numeric IDs (art, game_design, dev, others) so routing stays stable even after channel renames.
