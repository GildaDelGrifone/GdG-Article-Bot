---
name: write-article
description: Help the user shape and generate one or more news or collaboration articles for the Gilda del Grifone website with GdG-Article-Bot. Reads the user's notes and Telegram channel posts, interviews the user for what is missing, writes a brief per article, runs the project's AI pipeline (Gemini or Claude, per AI_PROVIDER) to create the drafts on the website repo, checks and corrects each draft, and publishes on request. Use when the user wants to write, draft, fix or publish a news item, announcement or partner/collaboration article for the site.
---

# Write articles for the GdG website

The article text is **written by the AI provider** set in `AI_PROVIDER` (Gemini or Claude), the same as the Telegram bot: you never write an article from scratch. Your job: collect the facts, write a good brief, run the pipeline, **check the draft, fix what is wrong**, and publish when the user says so. Run everything from the repository root with `.venv/bin/python`. Never print tokens or keys from `.env`.

## 1. Gather the input
Several articles can be requested at once (e.g. three collaborations): handle each one as its own article (own brief, own draft, own date) and ask your questions for all of them together.

- **Notes from the user**: files they mention (often `.md` files and images in `.sandbox/`). Read them all; look at every image (Read tool) and say in one line what you see.
- **Telegram channel posts**: for every `https://t.me/<channel>/<N>` link run
  `.venv/bin/python scripts/fetch_telegram.py <link>`
  It saves the text and photos in `data/telegram/<channel>-<N>/`, and prints the post date as `GG-MM-AAAA` (use it for `--date` when the user says the article has the date of the post) and the links in the text. The text is the *source of the facts*: use only what it says.
- **Images in any format** (JPEG, PNG, WEBP, AVIF, JFIF, GIF...) can be passed as they are: the pipeline converts them.

## 2. Pick the kind
- **news**: an announcement or report (an event we attended, a tournament, a change, an anniversary).
- **collab**: a collaboration with a partner. It produces the news article **and** the partner card shown on `/collaborazioni`. It needs: partner name, website URL (optional), what members get, how to use it, the logo.

If unsure, ask. If the website already has the collaboration article and the user wants only the card, see *Card only* below.

## 3. Interview (only what is missing, one or two questions at a time)
- What happened / will happen, and the one thing a reader must remember.
- Dates, places, names, prices, links. **Only facts the user gives or that are in the sources**: unknown details are left out or flagged, never invented.
- Tone (default: warm, enthusiastic, plural "voi", like the existing site).
- Collab: one-sentence partner description, member benefits, partner link, which image is the logo and which the cover.
- Images: list them in order. Propose a **cover** (the strongest landscape image) and, for collabs, the **logo**. The user can override.
- Anything to avoid or include.

## 4. Write the briefs
Save `data/briefs/<short-name>.md` (plain text, Italian or English):

```
Messaggio chiave: …
Fatti: – … – …
Tono: …
Partner (collab): nome, url, descrizione di una frase, vantaggi per i soci
Immagini: 1 = …, 2 = …, 3 = …
Da evitare: …  (what the sources do not say: addresses, hours, deadlines, how to get a discount...)
copertina: 2                (optional, line on its own)
logo: 1                     (collab, optional, line on its own)
ordine: 10                  (collab, optional, position of the partner card; default 100)
```

The kind and the date are CLI arguments (`--kind`, `--date GG-MM-AAAA`, default today), not brief lines. Say in "Da evitare" the details a model tends to invent for that kind of article.

Show the user a **short summary** (a table when there are several articles: partner/topic, date, benefit, cover, logo) and ask **once** for a yes for all of them.

## 5. Generate
After the yes, run the real command for each article, one after the other:

`.venv/bin/python scripts/create_article.py --kind <news|collab> --brief data/briefs/<name>.md --images <files in the numbered order> [--cover N] [--logo N] [--date GG-MM-AAAA]`

It uploads the images to R2 and creates the draft branch on the website repo, which is the preview: **nothing goes online until publishing**. It prints the draft id, the draft link and the final URL.

Use `--dry-run --output drafts/<name>.md` (nothing uploaded or committed) only if the user asks for it or to try an uncertain brief.

### Card only (a partner whose collaboration article is already on the site)
Collect: name, logo file, the slug of the collaboration article on the site (the card links to `/news/<slug>/`), a one-sentence description (at most 200 characters, only facts the user gave or that are on the existing article) and an optional `ordine`. Show the description for a yes, then run `.venv/bin/python scripts/create_card.py --name "<name>" --logo <file> --description "<text>" [--article <slug>] [--order N]` (add `--dry-run` to only see it). It creates the draft branch `scheda/<slug>`.

## 6. Check every draft
Run `.venv/bin/python scripts/edit_draft.py show <id>` and compare the text with the brief and the sources. Look for:
- **Typos and Italian grammar**: articles and prepositions with the association name ("alla Gilda del Grifone"), invented or broken words, agreement errors.
- **Invented facts**: numbers, quantities, products, brands, addresses, hours, dates, conditions, superlatives ("migliaia di prodotti") that are not in the brief or the sources.
- **Missing or wrong facts**: discount, code, exclusions, conditions, links, the date.
- **The partner card** (collab): description at most 200 characters, reads well, only true facts.
- Links: the CLI logs a warning for links that are not in the brief.

Report the problems to the user with the fix you propose, and fix them (next section) unless the user says otherwise. If the draft is fine, say so.

## 7. Fix a draft
- **Small or precise fixes** (typos, a wrong sentence, an invented detail, an address to add, the date): download the draft, edit the files, send them back:
  1. `.venv/bin/python scripts/edit_draft.py pull <id>` writes `drafts/<id>/articolo.md` (and `scheda.md` for a collab);
  2. change them with Edit (front matter keys: `title`, `date`, `cover`, `excerpt`; for the card `nome`, `logo`, `url`, `descrizione`, `ordine`). Images appear as R2 addresses: leave them;
  3. `.venv/bin/python scripts/edit_draft.py push <id>` checks the rules (title ≤ 80, excerpt ≤ 160, 2-4 sections, one `---`, closing with 🦅, no bare URLs, card description ≤ 200) and commits to the draft. If you change `date` it renames the article file and removes the old one. It also saves the corrected text in the database, so a later `revise` starts from it.
- **Bigger rewrites** (tone, structure, a missing section): `.venv/bin/python scripts/edit_draft.py revise <id> --feedback "what to change"` has the AI rewrite the same draft with the same images.
- The article is never written from scratch by you: always start from the generated text.

## 8. Publish only on an explicit yes
Never publish on your own. When the user confirms (one or several drafts):
`.venv/bin/python scripts/publish_article.py <id>`
It merges to the deploy branch; the site is online in 1-2 minutes. Then check, with `curl -s -o /dev/null -w '%{http_code}' <final URL>`, that every page answers 200 (retry for a couple of minutes) and, for collaborations, that `/collaborazioni/` lists the new card. To drop a draft: `… publish_article.py <id> --discard` (its images stay on R2). `publish_article.py --list` shows the pending drafts.

## Rules
- Real run directly is fine (the draft is the preview); a dry run only on request.
- Never publish without a clear yes; never push to `main` of the website by hand.
- If the run reports a missing setting (R2, GitHub token, API key), tell the user which variable is missing, without showing its value.
- A quota/credit error means the chosen provider is out of credit: offer `--provider gemini|claude` for this run.
- Anything you only half-know goes to the user as a question, not into the article.
