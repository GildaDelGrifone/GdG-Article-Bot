# GdG-Article-Bot - Technical Specification & Architecture Guide

> **Status: scaffold.** The repository currently holds documentation and empty package folders only. The step-by-step build plan is `docs/plans/IMPLEMENTATION_PLAN.md`. When a phase is implemented, update this file in the same change (sections marked *planned* become facts).

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
Both entry points call the same pipeline (`core/pipeline.py`, *planned*).

### 2.1. Telegram bot (`bot/`, *planned*)
- Admin-only: every handler is restricted to `ADMIN_CHAT_ID`.
- `/news` or `/collab` starts a conversation: the admin sends photos (or documents, which keep full quality) and a text brief, then `/genera`. `/annulla` aborts.
- Optional markers in the brief override the AI: `copertina: 2` (cover = image 2), `logo: 3` (partner logo = image 3, collaborations only).
- The bot replies with the draft card (title, excerpt, draft link, final link) and buttons `[Pubblica sul sito]`, `[✏️ Rivedi]`, `[🗑️ Scarta]`.
  - **Pubblica** merges the draft branch into `ASTRO_GITHUB_BRANCH` and deletes it.
  - **Rivedi** asks for feedback with a ForceReply and regenerates on the same branch, reusing the already uploaded images.
  - **Scarta** deletes the branch (a missing branch means it was already published).

### 2.2. Claude skill (`.claude/skills/write-article/`, *planned*)
The skill interviews the user to refine the article vision (type, facts, audience, tone, partner details, cover/logo), writes `data/briefs/<name>.md`, then runs the CLI. **The skill never writes the article text itself**: the text always comes from the configured AI provider, so both paths behave the same.

### 2.3. CLI (`scripts/`, *planned*)
- `scripts/create_article.py --kind news|collab --brief FILE --images IMG [IMG ...] [--cover N] [--logo N] [--date DD-MM-YYYY] [--dry-run] [--output FILE]`. `--dry-run` writes the front matter and body to a file or stdout without uploading or committing.
- `scripts/publish_article.py <id|slug> [--discard]` merges or discards a draft.

## 3. Article Formats
The website schema is `GdG-Website/src/content.config.ts`. **Keep this section in sync with it** and re-check it on the latest `main` of the website before changing the generators (the local checkout of the website may lag behind).

### 3.1. News (`src/content/news/<YYYY-MM-DD>-<slug>.md`)
Front matter (Zod `articolo` schema): `title` (string), `date` (`YYYY-MM-DD`), `cover` (optional URL string), `excerpt` (optional, about 160 characters ending with `…`). Strings are JSON-quoted (`json.dumps(..., ensure_ascii=False)`), which is valid YAML.

Body layout (free narrative, about 300-600 words, Italian):
1. Intro paragraph with no heading; **Gilda del Grifone** in bold.
2. Two to four `##` sections chosen from the brief. Images as `![alt](url)` followed by a short italic caption line.
3. A line with only `---`.
4. Closing call to action (how to join, Telegram group, address, opening hours), ending with 🦅.

Rules: the body never starts with `#` and never repeats the title or the cover (the page template already renders title, date and cover); links are always `[text](url)`, never bare URLs; plain Markdown only (the site loads `*.md`, no MDX, no components); never invent facts, dates, prices or names that are not in the brief.

### 3.2. Collaboration
A collaboration produces **two files in the same draft branch**:
- a news article (`src/content/news/<YYYY-MM-DD>-collaborazione-con-<partner-slug>.md`) in partner-profile shape: intro announcing the collaboration with **Partner**, `## Chi è <Partner>`, `## Cosa significa per i soci` (bullet list of benefits), `## Come approfittarne` with the partner link, `---`, closing with 🦅;
- a partner card (`src/content/collaborazioni/<partner-slug>.md`), front matter only: `nome`, `logo` (R2 URL), `url` (optional, must be a valid URL), `descrizione` (at most 200 characters), `ordine: 100`.

The site shows partner cards only on `/collaborazioni` (no per-card page, the body is never rendered). The cards folder does not exist on the site until the first card is committed.

### 3.3. Slugs, URLs and branches
- The public URL has **no date**: `/news/<slug>/`. The site derives the slug from the file name by removing `^\d{4}-\d{2}(-\d{2})?-` (`articleSlug` in `GdG-Website/src/lib/urls.ts`).
- **A duplicate slug breaks the website build.** Before committing, list the target folder through the GitHub API, compare *stripped* slugs and append `-2`, `-3`, … when needed. Same check for the partner-card file name.
- Draft branches: `news/<slug>` and `collaborazione/<slug>`. Commit message `Bozza news <slug>` / `Bozza collaborazione <slug>`; merge message `Pubblica <branch>`.

## 4. Image Pipeline
Ported from `GdG-Event-Bot/core/astro/storage.py`:
- JPEG/PNG are EXIF-transposed and resized to at most **1600 px** wide (LANCZOS); JPEG saved as RGB quality 85, PNG kept as PNG; other types uploaded as raw bytes.
- R2 key: `news/<slug>/img_<n>_<uuid8>.<ext>`; partner logo: `collaborazioni/<partner-slug>/logo_<uuid8>.<ext>`. Public URL: `{R2_PUBLIC_URL}/{key}`.
- boto3 is imported lazily; missing R2 settings or upload errors return `None` and are reported to the admin (a failed upload must stop the pipeline: never publish an article with missing images).
- Images sent to the AI are separate downscaled copies (long side at most 1568 px, JPEG) to stay under provider limits; the R2 copy is the 1600 px version.
- Images are never committed to the website repo.

## 5. AI Providers
`core/ai/` (*planned*): one interface, two implementations.
- `generate(system, prompt, images) -> dict`: returns the parsed JSON contract below. Raises `AIQuotaError` on quota/credit/rate errors (Gemini 429 / `RESOURCE_EXHAUSTED`, Claude 429 / overloaded), so the bot can alert the admin.
- `gemini.py`: `google-genai`, `response_mime_type="application/json"`, images as `Part.from_bytes`.
- `claude.py`: `anthropic` SDK, images as base64 image blocks, JSON obtained through a forced tool call. Load the `claude-api` skill before writing or changing it.
- `get_provider()` reads `AI_PROVIDER` (`gemini` default, `claude`); an invalid value logs a warning and falls back to `gemini`.

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
├── main.py              # (planned) Telegram bot entry point
├── run.sh               # starts main.py with .venv / venv
├── bot/                 # (planned) Telegram layer: handlers, keyboards, texts.py, auth
├── core/
│   ├── config.py        # (planned) .env loading, parsing helpers, missing_settings()
│   ├── pipeline.py      # (planned) create_article / revise_article / publish / discard
│   ├── prompts.py       # (planned) prompt texts for news and collaboration
│   ├── validate.py      # (planned) structure checks on the AI output
│   ├── db.py            # (planned) SQLite state of the drafts
│   ├── ai/              # (planned) base.py, gemini.py, claude.py, get_provider()
│   └── site/            # (planned) client.py, storage.py (R2), github.py, content.py
├── scripts/             # (planned) create_article.py, publish_article.py
├── tests/               # mirrors the source tree; helpers in tests/support/
├── data/                # runtime: articles.db, briefs/, temp images (git-ignored)
├── docs/plans/          # IMPLEMENTATION_PLAN.md
├── .claude/skills/      # write-article skill
├── .github/workflows/   # CI: pyflakes + pytest
└── AGENTS.md  CLAUDE.md  README.md  .env.example  requirements*.txt
```
`main.py` and `requirements.txt` stay at the repository root: the k3s pod runs them from a hostPath mount (see README).

## 7. Database Schema
SQLite `DATA_DIR/articles.db` (*planned*, created by `init_db()`), table `articles`:

| Column | Type | Description |
|---|---|---|
| `id` | INTEGER PK | Draft id |
| `kind` | TEXT | `news` or `collab` |
| `slug` | TEXT | Final unique slug |
| `branch` | TEXT | Draft branch on the website repo |
| `title` | TEXT | Article title |
| `status` | TEXT | `draft`, `published`, `discarded` |
| `draft_url` | TEXT | GitHub link to the draft file |
| `public_url` | TEXT | Final URL on the site |
| `image_keys` | TEXT | JSON list of R2 keys, in upload order |
| `brief` | TEXT | Original admin brief |
| `last_body` | TEXT | Last generated Markdown (input of Rivedi) |
| `chat_id`, `message_id` | INTEGER | Draft card in Telegram |
| `created_at` | TIMESTAMP | Default `CURRENT_TIMESTAMP` |

Schema changes: add the column to `CREATE TABLE` *and* to a `COLUMN_MIGRATIONS` list applied by `init_db()`, as in `GdG-Event-Bot/core/db/schema.py`.

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
| `DATA_DIR` | Runtime folder | `data` |
| `LOG_LEVEL` | `DEBUG`..`CRITICAL`; a typo falls back to `INFO` | `INFO` |

Notes: the Gemini, GitHub, R2 and `ADMIN_CHAT_ID` values were copied from `GdG-Event-Bot`'s `.env` at scaffold time. `ASTRO_CONTENT_DIR` (the *serate* folder) was deliberately **not** copied: this project writes to `NEWS_CONTENT_DIR` / `COLLAB_CONTENT_DIR`.

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
