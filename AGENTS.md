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
Both entry points call the same pipeline (`core/pipeline.py`: `create_article`, `create_card`, `revise_article`, `publish`, `discard`; blocking functions, `PipelineError` carries an Italian message for the user, `SettingsError` marks missing `.env` values).

### 2.1. Telegram bot (`main.py`, `bot/`)
- Admin-only: every handler is restricted to `ADMIN_CHAT_ID`.
- `/news` or `/collab` starts a conversation: the admin sends photos (or documents, which keep full quality) and a text brief, then `/genera`. `/annulla` aborts.
- Optional markers in the brief override the AI: `copertina: 2` (cover = image 2), `logo: 3` (partner logo = image 3, collaborations only).
- The bot replies with the draft card (title, excerpt, draft link, final link) and buttons `[Pubblica sul sito]`, `[✏️ Rivedi]`, `[🗑️ Scarta]`.
  - **Pubblica** merges the draft branch into `ASTRO_GITHUB_BRANCH` and deletes it.
  - **Rivedi** asks for feedback with a ForceReply and regenerates on the same branch, reusing the already uploaded images.
  - **Scarta** deletes the branch (a missing branch means it was already published).

### 2.2. Claude skill (`.claude/skills/write-article/`)
The skill interviews the user to refine the article vision (type, facts, audience, tone, partner details, cover/logo), writes `data/briefs/<name>.md`, then runs the CLI. **The skill never writes the article text itself**: the text always comes from the configured AI provider, so both paths behave the same.

### 2.3. CLI (`scripts/`)
- `scripts/create_article.py --kind news|collab --brief FILE --images IMG [IMG ...] [--cover N] [--logo N] [--order N] [--date DD-MM-YYYY] [--provider gemini|claude] [--dry-run] [--output FILE]`. `--dry-run` writes the front matter and body (and the partner card, each preceded by a `<!-- FILE: <site path> -->` line) to a file or stdout without uploading or committing; it needs only the AI key. Exit codes: 0 ok, 1 pipeline error, 2 bad arguments or missing settings.
- `scripts/create_card.py --name NAME --logo FILE --description TEXT [--article SLUG] [--order N] [--dry-run] [--output FILE]` creates a draft of the **partner card only** (no article, no AI: needs only R2 and GitHub settings; the description is at most 200 characters). `--article` is the slug of the collaboration news article already on the site: the card's `url` becomes `<ASTRO_BASE_URL>/news/<slug>/` and the command fails if no such article exists (without `--article` the card has no link). It is a draft of kind `card` on the branch `scheda/<card-slug>`, commit message `Bozza scheda <card-slug>`, published or discarded like any draft; its final link is the `/collaborazioni` page. A card cannot be revised: discard it and create a new one. Same exit codes as `create_article.py`.
- `scripts/publish_article.py <id|slug> [--discard]` merges or discards a draft; `--list` shows the pending drafts.

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
│   ├── pipeline.py      # create_article / create_card / revise_article / publish / discard (+ Draft, PipelineError)
│   ├── db.py            # SQLite state of the drafts
│   ├── ai/              # base.py (contract, schema, errors), gemini.py, claude.py, provider.py (get_provider)
│   └── site/            # client.py (GitHub + R2 clients), storage.py (R2), github.py, content.py (slugs, front matter, draft lifecycle)
├── scripts/             # create_article.py, create_card.py, publish_article.py
├── tests/               # mirrors the source tree; helpers in tests/support/
├── data/                # runtime: articles.db, briefs/, tmp/ (originals of the open drafts), logs/ (git-ignored)
├── docs/plans/          # IMPLEMENTATION_PLAN.md (history and rationale of the build)
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
