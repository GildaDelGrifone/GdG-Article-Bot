# GdG-Article-Bot - Technical Specification & Architecture Guide

## Table of Contents
- [1. Project Overview](#1-project-overview)
- [2. Workflows](#2-workflows)
- [3. Article Formats](#3-article-formats)
- [4. Image Pipeline](#4-image-pipeline)
- [5. AI Providers](#5-ai-providers)
- [6. Directory Structure](#6-directory-structure)
- [7. Database Schema](#7-database-schema)
- [8. Environment Variables](#8-environment-variables)
- [9. Code Conventions](#9-code-conventions)
- [10. Testing](#10-testing)
- [11. Git Conventions](#11-git-conventions)
- [12. Related Projects](#12-related-projects)

---

## 1. Project Overview
**GdG-Article-Bot** writes **news** and **collaboration** articles for the website of **Gilda del Grifone**, a tabletop games association in Turin (Italy). The site is the Astro project `GdG-Website` (`../GdG-Website`, GitHub `GildaDelGrifone/GdG-Website`).

An admin supplies a free-text brief and one or more images. The bot:
1. uploads the images to the Cloudflare R2 bucket (public host `https://media.gildadelgrifonetorino.it`);
2. asks an AI model (Gemini **or** Claude, chosen by `AI_PROVIDER`) to write the Italian Markdown article, looking at the images;
3. commits a draft on a branch of the website repo through the GitHub API;
4. on approval merges the branch into `main` (Cloudflare then deploys the site).

It does **not** handle game-night recaps ("serate"): those stay in `GdG-Event-Bot`.

## 2. Workflows
Both entry points call the same pipeline (`core/pipeline.py`: `create_article`, `create_card`, `revise_article`, `show_draft`, `pull_draft`, `push_draft`, `publish`, `discard`; blocking functions, `PipelineError` carries an Italian message for the user, `SettingsError` marks missing `.env` values).

### 2.1. Telegram bot (`main.py`, `bot/`)
- Admin-only: every handler is restricted to `ADMIN_CHAT_ID`.
- `/news` or `/collab` starts a conversation: the admin sends photos (or documents, which keep full quality) and a text brief, then `/genera`. `/annulla` aborts.
- Optional markers in the brief override the AI: `copertina: 2` (cover = image 2), `logo: 3` (partner logo = image 3, collaborations only).
- The bot replies with the draft card (title, excerpt, draft link, final link) and buttons `[Pubblica sul sito]`, `[✏️ Rivedi]`, `[🗑️ Scarta]`.
  - **Pubblica** merges the draft branch into `ASTRO_GITHUB_BRANCH` and deletes it.
  - **Rivedi** asks for feedback with a ForceReply and regenerates on the same branch, reusing the already uploaded images.
  - **Scarta** deletes the branch (a missing branch means it was already published).

### 2.1b. Chat assistant (`/scrivi`, `bot/agent.py`, `core/agent/`)
The skill's flow inside Telegram, for use where Claude Code is not available (the k3s pod). A tool-calling agent (Claude or Gemini: `AGENT_PROVIDER` / `AGENT_MODEL`, default `AI_PROVIDER`) whose tools wrap the pipeline: `fetch_telegram_post`, `create_article`, `create_card`, `show_draft`, `fix_draft` (exact single-occurrence replacements through pull → push), `revise_draft`, `list_drafts`, `request_publication`, `check_live` (only `ASTRO_BASE_URL`). **There is no tool that publishes or discards**: `request_publication` only marks a draft to be sent with the `[Pubblica][Rivedi][Scarta]` buttons, so a Telegram post with instructions inside cannot put anything online. The article text is still written by `create_article` / `revise_draft`, never by the agent.
- `/scrivi` opens a session (and closes a `/news`/`/collab` collection); `/news` and `/collab` close it. **`/fine` (alias `/reset`) wipes the session by hand** (history and photos; drafts stay) so no tokens are spent on a finished conversation. A session also restarts after `AGENT_MAX_MESSAGES` messages or `AGENT_IDLE_HOURS` idle hours (the user is told), or if provider/model change.
- Messages (text, photos, image documents) are collected for ~3 s so an album plus text is one turn; one turn runs up to `AGENT_MAX_STEPS` model calls; replies are plain text split under 4096 characters; drafts are sent as cards with buttons.
- History is stored in the provider's native format in `agent_sessions` and is append-only (never trimmed in the middle: Claude needs unchanged thinking blocks, Gemini its `thought_signature`s); the system prompt and the tool list are frozen at session start. A failed turn is rolled back. Claude: no forced `tool_choice`, no sampling parameters, same `effort` rule as `core/ai/claude.py`. Gemini: `automatic_function_calling` disabled.
- After **Pubblica** (any flow) the bot checks the final page after 2 minutes (retry once after 2 more) and writes the result.
- The system prompt (`core/agent/prompts.py`) restates the rules of `.claude/skills/write-article/SKILL.md`: **keep the two aligned**. `scripts/agent_chat.py` runs the same loop from the terminal (`--provider`, `/foto <file>`, `/fine`).

### 2.2. Claude skill (`.claude/skills/write-article/`)
The skill is the main way to use the project. It reads the user's notes and Telegram channel posts (`scripts/fetch_telegram.py`), interviews the user for what is missing, writes one `data/briefs/<name>.md` per article (several articles can be requested at once) and asks for a single yes. Then it runs the CLI for real (the draft branch is the preview; a dry run only on request), **checks each draft against the brief** (typos, invented facts, card description) and fixes it: small fixes with `scripts/edit_draft.py pull` → Edit → `push`, bigger rewrites with `edit_draft.py revise`. It publishes only on an explicit yes and then checks that the pages answer 200. **The skill never writes an article from scratch**: the text always comes from the configured AI provider, so both paths behave the same.

### 2.3. CLI (`scripts/`)
- `scripts/create_article.py --kind news|collab --brief FILE --images IMG [IMG ...] [--cover N] [--logo N] [--order N] [--date DD-MM-YYYY] [--provider gemini|claude] [--dry-run] [--output FILE]`. `--dry-run` writes the front matter and body (and the partner card, each preceded by a `<!-- FILE: <site path> -->` line) to a file or stdout without uploading or committing; it needs only the AI key. Exit codes: 0 ok, 1 pipeline error, 2 bad arguments or missing settings.
- `scripts/create_card.py --name NAME --logo FILE --description TEXT [--article SLUG] [--order N] [--dry-run] [--output FILE]` creates a draft of the **partner card only** (no article, no AI: needs only R2 and GitHub settings; the description is at most 200 characters). `--article` is the slug of the collaboration news article already on the site: the card's `url` becomes `<ASTRO_BASE_URL>/news/<slug>/` and the command fails if no such article exists (without `--article` the card has no link). It is a draft of kind `card` on the branch `scheda/<card-slug>`, commit message `Bozza scheda <card-slug>`, published or discarded like any draft; its final link is the `/collaborazioni` page. A card cannot be revised: discard it and create a new one. Same exit codes as `create_article.py`.
- `scripts/publish_article.py <id|slug> [--discard]` merges or discards a draft; `--list` shows the pending drafts.
- `scripts/edit_draft.py show|pull|push|revise <id|slug>` works on an existing draft without publishing it. `show` prints the files as they are on the draft branch (`<!-- FILE: <site path> -->` before each). `pull [--dir DIR]` writes `articolo.md` (and `scheda.md` for a collaboration or a card) to `drafts/<id>/`. `push [--dir DIR]` validates the files with the same rules as the AI output (`validate.check_body`: sections, one `---`, closing 🦅, only `img:N` images, no bare URLs; title ≤ 80, excerpt ≤ 160, card description ≤ 200, valid `date`) and commits them to the draft branch; R2 image addresses are mapped back to `img:N`, the corrected body is saved in `last_body` (so a later Rivedi starts from it) and a changed `date` renames the article file (`github.delete_file` removes the old one). `revise --feedback TEXT [--provider P]` is the CLI version of Rivedi. Needs only GitHub/R2 settings, except `revise` (AI key). Published or discarded drafts give an error.
- `scripts/agent_chat.py [--provider P] [--model M]` talks to the `/scrivi` assistant from the terminal (same loop and tools; `/foto <file>` attaches images, `/fine` wipes the session, `/esci` quits; uses chat id 0, separate from the bot's session). The drafts it creates are real; it cannot publish.
- `scripts/fetch_telegram.py <https://t.me/channel/N> [--out DIR]` reads a public channel post through its embed page (`core/telegram_post.py`, no login): saves `post.md` and `img_N.jpg` in `data/telegram/<channel>-<N>/` and prints the date as `GG-MM-AAAA` (Europe/Rome, ready for `--date`), the photos, the links and the text.

## 3. Article Formats
The website schema is `GdG-Website/src/content.config.ts`. **Keep this section in sync with it** and re-check it on the latest `main` of the website before changing the generators (the local checkout of the website may lag behind).

### 3.1. News (`src/content/news/<YYYY-MM-DD>-<slug>.md`)
Front matter (Zod `articolo` schema): `title` (string), `date` (`YYYY-MM-DD`), `cover` (optional URL string), `excerpt` (optional, about 160 characters ending with `…`). Strings are JSON-quoted (`json.dumps(..., ensure_ascii=False)`), which is valid YAML.

Body layout (free narrative, about 300-600 words, Italian):
1. Intro paragraph with no heading; **Gilda del Grifone** in bold.
2. Two to four sections (`##` or `###`) chosen from the brief. Images as `![alt](url)` followed by a short italic caption line.
3. A line with only `---`.
4. Closing call to action (how to join, Telegram group, address, opening hours), ending with 🦅.

Rules: the body never starts with `#` and never repeats the title or the cover (the page template already renders title, date and cover); links are always `[text](url)`, never bare URLs; plain Markdown only (the site loads `*.md`, no MDX, no components); never invent facts, dates, prices or names that are not in the brief.

### 3.2. Collaboration
A collaboration produces **two files in the same draft branch**:
- a news article (`src/content/news/<YYYY-MM-DD>-collaborazione-con-<partner-slug>.md`) in guided-flexible shape, modelled on the real article `collaborazione-con-aces-games` on the site: intro announcing the collaboration with **Partner** in bold; at least two sections (`##` or `###`) chosen from the brief (e.g. one per product or benefit, each with its image); a "how to use it" section with the partner link; a thank-you to the partner; `---`; closing with 🦅. Headings are not fixed; the prompt carries a trimmed style example (`COLLAB_STYLE_EXAMPLE`);
- a partner card (`src/content/collaborazioni/<partner-slug>.md`), front matter only: `nome`, `logo` (R2 URL), `url` (optional, **the address of the collaboration news article** `<ASTRO_BASE_URL>/news/<slug>/`, not the partner's website, which is linked inside the article), `descrizione` (at most 200 characters), `ordine: 100`.

The site shows partner cards only on `/collaborazioni` (no per-card page, the body is never rendered). The cards folder does not exist on the site until the first card is committed.

### 3.3. Slugs, URLs and branches
- The public URL has **no date**: `/news/<slug>/`. The site derives the slug from the file name by removing `^\d{4}-\d{2}(-\d{2})?-` (`articleSlug` in `GdG-Website/src/lib/urls.ts`).
- **A duplicate slug breaks the website build.** Before committing, list the target folder through the GitHub API, compare *stripped* slugs and append `-2`, `-3`, … when needed. Same check for the partner-card file name.
- Draft branches: `news/<slug>`, `collaborazione/<slug>` and `scheda/<card-slug>` (card only). Commit message `Bozza news <slug>` / `Bozza collaborazione <slug>`; merge message `Pubblica <branch>`.

## 4. Image Pipeline
Ported from `GdG-Event-Bot/core/astro/storage.py`:
- Any format Pillow opens is accepted (JPEG/`.jfif`, PNG, WEBP, AVIF, GIF, BMP, TIFF): `create_article` and `create_card` first copy the images with `storage.normalize_image` as `NN.jpg`/`NN.png` (the format is detected from the content: JPEG/PNG are copied as they are, anything else becomes PNG if transparent, otherwise JPEG quality 90). The converted copies are what the AI, R2 and `data/tmp/<id>/` see; an unreadable file is a `PipelineError` naming its number. AVIF needs Pillow ≥ 11.3.
- JPEG/PNG are EXIF-transposed and resized to at most **1600 px** wide (LANCZOS); JPEG saved as RGB quality 85, PNG kept as PNG; other types uploaded as raw bytes.
- R2 key: `news/<slug>/img_<n>_<uuid8>.<ext>`; partner logo: `collaborazioni/<partner-slug>/logo_<uuid8>.<ext>`. Public URL: `{R2_PUBLIC_URL}/{key}`.
- boto3 is imported lazily; missing R2 settings or upload errors return `None` and are reported to the admin (a failed upload must stop the pipeline: never publish an article with missing images).
- Images sent to the AI are separate downscaled copies (long side at most 1568 px, JPEG) to stay under provider limits; the R2 copy is the 1600 px version.
- Images are never committed to the website repo.

## 5. AI Providers
`core/ai/`: one interface, two implementations.
- `generate(system, prompt, images, schema) -> dict`: returns the parsed JSON contract below (`images` = list of `(bytes, mime)`, `schema` from `base.article_schema(kind)`). Raises `AIQuotaError` on quota/credit/rate errors (Gemini 429 / `RESOURCE_EXHAUSTED`, Claude 429 / overloaded), so the bot can alert the admin.
- `gemini.py`: `google-genai`, `response_mime_type="application/json"` + `response_json_schema`, images as `Part.from_bytes`.
- `claude.py`: `anthropic` SDK, images as base64 image blocks, JSON through `output_config.format` (`json_schema`). **No forced `tool_choice`** (rejected with a 400 by the 5.x models, including the default `claude-sonnet-5-5`), no `thinking` / sampling parameters; `output_config.effort` (`medium`) is sent only to 4.6+/5.x models. Load the `claude-api` skill before changing it.
- `provider.py`: `get_provider()` reads `AI_PROVIDER` (`gemini` default, `claude`); the invalid-value fallback lives in `core/config.py`.

**Output contract** (both providers, both kinds):
```json
{
  "title": "…",
  "excerpt": "…",
  "cover_index": 1,
  "body": "Markdown with images as ![alt](img:2)",
  "partner": { "nome": "…", "url": "…", "descrizione": "…", "logo_index": 3 }
}
```
`partner` only for collaborations. Images are numbered from 1 in the order received. **The model never writes image URLs**: it writes `![alt](img:N)` and the code replaces `img:N` with the R2 URL. This removes URL hallucination. A user-supplied cover/logo choice overrides `cover_index` / `logo_index`; the cover and the logo are removed from the body. The output is validated (`core/validate.py`) with up to 3 attempts.

## 6. Directory Structure
```text
GdG-Article-Bot/
├── main.py              # Telegram bot entry point (build_application, polling)
├── run.sh               # starts main.py with .venv / venv
├── bot/                 # Telegram layer (entry points)
│   ├── auth.py          # admin_only decorator
│   ├── conversation.py  # /news /collab ConversationHandler: collect photos + text, /genera, /annulla
│   ├── agent.py         # /scrivi, /fine: chat assistant (message intake with debounce, turns, cards)
│   ├── callbacks.py     # [Pubblica] [Rivedi] [Scarta] buttons and the Rivedi reply handler
│   ├── commands.py      # /help, /bozze
│   ├── cards.py         # draft card text + send helper
│   ├── keyboards.py     # inline keyboard (callback prefixes art_publish_ / art_revise_ / art_discard_)
│   └── texts.py         # every user-facing text
├── core/
│   ├── config.py        # .env loading, parsing helpers, missing_settings(mode)
│   ├── brief.py         # parse_markers: copertina: / logo: / ordine: lines of a brief
│   ├── prompts.py       # system + task prompts (news, collab, revise, fix), COLLAB_STYLE_EXAMPLE
│   ├── validate.py      # normalize + check_article + generate_valid (retry loop, 3 attempts)
│   ├── pipeline.py      # create_article / create_card / revise_article / show_draft / pull_draft / push_draft / publish / discard (+ Draft, PipelineError)
│   ├── telegram_post.py # reads a public Telegram channel post (text, date, photos) for fetch_telegram.py
│   ├── db.py            # SQLite state of the drafts and of the agent sessions
│   ├── agent/           # llm.py (interface), claude_llm.py, gemini_llm.py, tools.py, prompts.py, loop.py (run_turn, sessions)
│   ├── ai/              # base.py (contract, schema, errors), gemini.py, claude.py, provider.py (get_provider)
│   └── site/            # client.py (GitHub + R2 clients), storage.py (R2), github.py, content.py (slugs, front matter, draft lifecycle)
├── scripts/             # create_article.py, create_card.py, edit_draft.py, publish_article.py, fetch_telegram.py
├── tests/               # mirrors the source tree; helpers in tests/support/
├── data/                # runtime: articles.db, briefs/, tmp/ (originals of the open drafts), logs/ (git-ignored)
├── .claude/skills/      # write-article skill
├── .github/workflows/   # CI: pyflakes + pytest
└── AGENTS.md  CLAUDE.md  README.md  .env.example  requirements*.txt  pytest.ini
```
`main.py` and `requirements.txt` stay at the repository root: the k3s pod runs them from a hostPath mount (see README).

## 7. Database Schema
SQLite `DATA_DIR/articles.db` (`core/db.py`, created by `init_db()`), table `articles`:

| Column | Type | Description |
|---|---|---|
| `id` | INTEGER PK | Draft id |
| `kind` | TEXT | `news`, `collab` or `card` (partner card only) |
| `slug` | TEXT | Final unique article slug (URL `/news/<slug>/`) |
| `card_slug` | TEXT | Partner card file name (collaborations only) |
| `branch` | TEXT | Draft branch on the website repo |
| `title` | TEXT | Article title |
| `status` | TEXT | `draft`, `published`, `discarded` |
| `date_iso` | TEXT | Article date `YYYY-MM-DD` |
| `draft_url` | TEXT | GitHub link to the draft file |
| `public_url` | TEXT | Final URL on the site |
| `image_keys` | TEXT | JSON list of R2 keys, in image order (index N-1 = image N) |
| `image_paths` | TEXT | JSON list of the original images kept in `DATA_DIR/tmp/<id>/` until publish/discard (needed by Rivedi) |
| `cover_override`, `logo_override` | INTEGER | Image numbers chosen by the admin (`copertina:`, `logo:`); reused by Rivedi |
| `cover_index`, `logo_index` | INTEGER | Image numbers actually used |
| `logo_key` | TEXT | R2 key of the partner logo (`collaborazioni/<card_slug>/logo_<uuid8>.<ext>`) |
| `card_order` | INTEGER | `ordine` of the partner card (default 100) |
| `brief` | TEXT | Admin brief, markers removed |
| `last_body` | TEXT | Last generated Markdown, with `img:N` placeholders (input of Rivedi) |
| `chat_id`, `message_id` | INTEGER | Draft card in Telegram |
| `created_at` | TIMESTAMP | Default `CURRENT_TIMESTAMP` |

Table `agent_sessions` (`chat_id` PK, `provider`, `model`, `system`, `messages` JSON in the provider's native format, `images` JSON `[{number, path}]`, `updated_at`): one row per chat, created by `init_db()` (`get/save/delete_agent_session`).

Schema changes: add the column to `CREATE TABLE` *and* to `COLUMN_MIGRATIONS` (`(name, definition)` pairs applied by `init_db()` to existing databases).

## 8. Environment Variables
Template: `.env.example` (the real `.env` is git-ignored). Values are read at call time through `from core import config`.

| Variable | Description | Default |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | Token of a **new** bot (never share it with GdG-Event-Bot: two pollers conflict) | required |
| `ADMIN_CHAT_ID` | Only chat allowed to use the bot | required |
| `AI_PROVIDER` | `gemini` or `claude` | `gemini` |
| `GEMINI_API_KEY`, `GEMINI_MODEL` | Gemini credentials / model | `gemini-3.1-flash-lite` |
| `ANTHROPIC_API_KEY`, `CLAUDE_MODEL` | Claude credentials / model | `claude-sonnet-5-5` |
| `AGENT_PROVIDER`, `AGENT_MODEL` | Provider / model of the `/scrivi` assistant | `AI_PROVIDER` / that provider's model |
| `AGENT_MAX_STEPS`, `AGENT_MAX_MESSAGES`, `AGENT_IDLE_HOURS` | Model calls per user message / messages and idle hours after which a session restarts | `12` / `60` / `12` |
| `ASTRO_GITHUB_REPO` | Website repo | `GildaDelGrifone/GdG-Website` |
| `ASTRO_GITHUB_BRANCH` | Branch the site deploys from | `main` |
| `ASTRO_GITHUB_TOKEN` | Fine-grained token, Contents read/write on the website repo only | required |
| `ASTRO_BASE_URL` | Public site URL | `https://www.gildadelgrifonetorino.it` |
| `NEWS_CONTENT_DIR` | News folder in the website repo | `src/content/news` |
| `COLLAB_CONTENT_DIR` | Partner cards folder | `src/content/collaborazioni` |
| `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` | R2 credentials (Object Read & Write) | required |
| `R2_BUCKET` | Bucket | `gdg-media` |
| `R2_PUBLIC_URL` | Public bucket URL, no trailing slash | required |
| `ASSOCIATION_ADDRESS`, `OPENING_HOURS`, `TELEGRAM_GROUP_URL`, `ARTICLE_CONTACTS` | Facts used in the article closing | see `.env.example` |
| `BOOKING_BOT_NAME` | Telegram bot used to book tables (e.g. `Grifone Scriba`); optional, the AI mentions it only when relevant | empty |
| `DATA_DIR` | Runtime folder | `data` |
| `LOG_LEVEL` | `DEBUG`..`CRITICAL`; a typo falls back to `INFO` | `INFO` |

Notes: the Gemini, GitHub, R2 and `ADMIN_CHAT_ID` values were copied from `GdG-Event-Bot`'s `.env` when the project was created. `ASTRO_CONTENT_DIR` (the *serate* folder) was deliberately **not** copied: this project writes to `NEWS_CONTENT_DIR` / `COLLAB_CONTENT_DIR`.

## 9. Code Conventions
- **Language:** user-facing strings, docstrings and code comments are in **Italian**; this file is in English because it is meant for tools. Prompts to the AI are in English and instruct it to write the article in Italian.
- **Layering:** `bot/` and `scripts/` are entry points; both call `core/pipeline.py`; `core/` never imports `bot/`. Package `__init__.py` files are empty: import from the specific module.
- **Configuration at call time:** `from core import config` then `config.R2_BUCKET`, never copies at import. Numeric settings fall back to their default on a typo.
- **All Telegram texts** live in `bot/texts.py` as constants or `format_*` helpers that `html.escape` raw values; messages use `parse_mode="HTML"`.
- **Blocking I/O** (boto3, requests, AI SDKs) runs in `asyncio.to_thread` inside handlers. Answer callback queries immediately.
- **Restricted handlers** use an `@admin_only` decorator (replies `Non sei autorizzato.` only in private chats).
- **Secrets:** never print or log tokens/keys; log errors with the reason only.
- Keep `.env.example`, `README.md` and this file aligned when you add a setting or change a flow.

## 10. Testing
- `tests/` mirrors the source tree, one test file per module. Helpers in `tests/support/` (temporary DB, patched config, fake images, Telegram doubles), modelled on `GdG-Event-Bot/tests/support/`.
- Patch settings at `core.config.*`; patch functions where they are **used**. Tests never read real tokens, never hit the network (mock `core.site.client.requests.request`, `r2_client`, the AI providers) and never touch the real `DATA_DIR`.
- Run: `.venv/bin/python -m pytest tests` and `.venv/bin/python -m pyflakes bot core scripts tests main.py`.

## 11. Git Conventions
- Work on a feature branch; conventional commit prefixes (`feat:`, `fix:`, `docs:`, `test:`, `chore:`).
- Commit as the repository's configured identity (`Destard20`). **No AI `Co-Authored-By` trailers.**
- Do not push, merge or open PRs unless explicitly asked.

## 12. Related Projects
- `../GdG-Website`: Astro site. Its `AGENTS.md` describes content and URL rules; `src/content.config.ts` is the schema this bot must satisfy.
- `../GdG-Event-Bot` (development copy `../DEV_EVENTBOT`): source of the code ported here (`core/astro/*`, article prompts in `core/ai_parser.py`). It keeps publishing the *serate* on `serata/<slug>` branches; the two bots never write the same folder.
- Deployment follows `../K8S_BOT_SETUP_GUIDE.md` (see README).
