---
name: write-article
description: Help the user shape and generate a news or collaboration article for the Gilda del Grifone website with GdG-Article-Bot. Interviews the user to refine the article's vision (type, facts, tone, partner details, which image is the cover or logo), writes a brief file, then runs the project's AI pipeline (Gemini or Claude, per AI_PROVIDER) to create the draft on the website repo. Use when the user wants to write, draft or prepare a news item, announcement or partner/collaboration article for the site.
---

# Write an article for the GdG website

You **do not write the article text**. The text is always written by the AI provider set in `AI_PROVIDER` (Gemini or Claude), so the skill and the Telegram bot give the same kind of result. Your job: pull the right facts out of the user, produce a good brief, run the pipeline, show the result. Run everything from the repository root with `.venv/bin/python`. Never print tokens or keys from `.env`.

## 1. Pick the kind
- **news**: an announcement or report (an event we attended, a tournament, a change, an anniversary).
- **collab**: a collaboration with a partner. It produces the news article **and** the partner card shown on `/collaborazioni`.

If unsure, ask. A collaboration needs: partner name, website URL (optional), what members get, how to use it.

## 2. Interview (ask only what is missing, one or two questions at a time)
- What happened / will happen, and the one thing a reader must remember.
- Dates, places, names, prices, links. **Only facts the user gives**: never fill gaps yourself; unknown details are left out or flagged.
- Audience and tone (default: warm, enthusiastic, plural "voi", like the existing site).
- Collab only: partner description in one sentence, member benefits, partner link.
- Images: list the files. Look at them (Read tool) and tell the user in one line each what you see. Propose a **cover** (the strongest, landscape-friendly image) and, for collabs, which one is the **logo**. The user can override; if they have no preference the AI chooses.
- Anything to avoid or include (names to mention, disclaimers).

## 3. Write the brief
Save `data/briefs/<short-name>.md` (create the folder). Plain text in Italian or English, structured like:

```
Messaggio chiave: …
Fatti: – … – …
Tono: …
Partner (collab): nome, url, descrizione di una frase, vantaggi per i soci
Immagini: 1 = …, 2 = …, 3 = …
Da evitare: …
copertina: 2                (optional, line on its own: the AI is told this image is the cover)
logo: 1                     (collab, optional, line on its own)
ordine: 10                  (collab, optional, position of the partner card; default 100)
```

The kind (news/collab) and the date (`--date GG-MM-AAAA`, default today) are CLI arguments, not brief lines. The `copertina:` / `logo:` / `ordine:` lines are removed from the brief before the AI sees it.

Show it to the user and get a yes before generating.

## 4. Generate
1. Dry run first (nothing is uploaded or committed):
   `.venv/bin/python scripts/create_article.py --kind <news|collab> --brief data/briefs/<name>.md --images <files in the numbered order> [--cover N] [--logo N] --dry-run --output drafts/<name>.md`
2. Read `drafts/<name>.md` (the article, and for a collab the partner card, each preceded by a `<!-- FILE: ... -->` line) and summarise it for the user (title, excerpt, sections, image placement). If something is wrong, **change the brief**, not the output, and rerun. Up to 3 attempts, then ask the user.
3. After the user agrees, run the same command without `--dry-run` and without `--output` (the CLI rejects `--output` on a real run). It uploads the images to R2 and creates the draft branch; note the draft link and the final URL it prints.

### Card only (a partner whose collaboration article is already on the site)
If the user wants only the partner card for `/collaborazioni`, no article and no AI are involved. Collect: name, logo file, the slug of the collaboration article on the site (the card links to `/news/<slug>/`; list `src/content/news/` of the website, ignoring the date prefix), one-sentence description (at most 200 characters, only facts the user gave or that are on the partner's existing article) and optional `ordine`. Show the description for a yes, then:
1. `.venv/bin/python scripts/create_card.py --name "<name>" --logo <file> --description "<text>" [--article <slug>] [--order N] --dry-run` (add `--output drafts/<name>.md` to save it), show the card.
2. After the user agrees, run it again without `--dry-run`; it uploads the logo to R2 and creates the draft branch `scheda/<slug>`. Publish it as in step 5.

## 5. Publish only on an explicit yes
Never publish on your own. When the user confirms:
`.venv/bin/python scripts/publish_article.py <id|slug>`
(merges to the deploy branch; the site is online in 1-2 minutes). To drop it: `… --discard`.

## Rules
- Dry run before any real run; a real run only after the user saw the dry-run result.
- Never edit the generated article by hand: change the brief and regenerate (or use the bot's *Rivedi*).
- If the run reports a missing setting (R2, GitHub token, API key), tell the user which variable is missing, without showing its value.
- A quota/credit error means the chosen provider is out of credit: offer to switch `AI_PROVIDER` for this run.
