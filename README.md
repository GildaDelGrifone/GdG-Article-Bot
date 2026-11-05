# 📰 GdG-Article-Bot

Bot che scrive gli **articoli di news e collaborazioni** del sito della **Gilda del Grifone** (Torino). Gli dai un testo con le idee e delle immagini, un'AI (**Gemini** o **Claude**, a tua scelta) scrive l'articolo in italiano guardando le immagini, le immagini finiscono su Cloudflare R2 e l'articolo viene preparato come **bozza** nel repository del sito. Quando ti piace, lo pubblichi con un click.

## ✨ Funzionalità

- **Due tipi di articolo**: *news* e *collaborazione* (articolo + scheda partner per la pagina `/collaborazioni`).
- **Due AI intercambiabili**: `AI_PROVIDER=gemini` oppure `AI_PROVIDER=claude`.
- **Le AI vedono le immagini**: scrivono i testi alternativi, mettono ogni foto dove serve e scelgono la copertina (a meno che tu non la indichi).
- **Immagini su Cloudflare R2**, ridimensionate a 1600 px, come per le serate. Accetta qualunque formato (JPEG, PNG, WEBP, AVIF…): le converte da sole.
- **Bozza → Pubblica**: l'articolo nasce su un branch del repo del sito; `Pubblica` fa il merge su `main` e Cloudflare mette online il sito in circa 1-2 minuti.
- **Due modi d'uso**: bot Telegram oppure skill di Claude Code.

## 🧭 Come funziona

```
  testo + immagini ──► R2 (upload) ──► AI (Gemini/Claude, con le immagini)
                                            │
                                            ▼
                       controlli sulla struttura (max 3 tentativi)
                                            │
                                            ▼
              bozza su branch  news/<slug>  del repo GdG-Website
                                            │
              [Pubblica] ──► merge su main ──► deploy Cloudflare
              [Rivedi]   ──► nuove indicazioni, stessa bozza
              [Scarta]   ──► branch eliminato
```

### Formato degli articoli

| Tipo | File nel repo del sito | Contenuto |
|---|---|---|
| News | `src/content/news/AAAA-MM-GG-<slug>.md` | Introduzione, 2-4 sezioni `##`, immagini con didascalia, `---`, chiusura con 🦅 (300-600 parole) |
| Collaborazione | news `…-collaborazione-con-<partner>.md` **+** scheda `src/content/collaborazioni/<partner>.md` | Introduzione col partner, almeno 2 sezioni scelte dal brief (es. una per prodotto o vantaggio), come approfittarne, ringraziamento, `---`, chiusura con 🦅; la scheda ha `nome`, `logo`, `url`, `descrizione` (max 200 caratteri), `ordine` |

Il titolo, la data e la copertina li mostra già il sito: il testo dell'articolo non li ripete. L'URL pubblico è `…/news/<slug>/` (senza data).

## 🛠️ Installazione

### 1. Prerequisiti
- Python 3.10 o superiore
- Un **nuovo bot** creato con [@BotFather](https://t.me/BotFather) (non riusare il token di GdG-Event-Bot: due bot con lo stesso token si bloccano a vicenda)
- Chiave API di Gemini e/o di Anthropic
- Token GitHub *fine-grained* con permesso **Contents read/write** solo sul repo `GdG-Website`
- Credenziali R2 (Object Read & Write) del bucket `gdg-media`

### 2. Ambiente virtuale
```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt
```

### 3. Configurazione
```bash
cp .env.example .env
```
Compila `.env` (tutte le variabili sono spiegate in `.env.example`). Le chiavi di Gemini, GitHub e R2 e l'`ADMIN_CHAT_ID` possono essere le stesse di GdG-Event-Bot; mancano solo `TELEGRAM_BOT_TOKEN` (nuovo bot) e, se usi Claude, `ANTHROPIC_API_KEY`.

> ℹ️ Nella cartella dei contenuti **non** usare `ASTRO_CONTENT_DIR` di GdG-Event-Bot: questo progetto scrive in `NEWS_CONTENT_DIR` e `COLLAB_CONTENT_DIR`.

### 4. Test
```bash
.venv/bin/python -m pytest tests
.venv/bin/python -m pyflakes bot core scripts tests
```

### 5. Avvio
```bash
./run.sh
```

## 💬 Uso da Telegram

Solo la chat `ADMIN_CHAT_ID` può usare il bot.

| Comando | Cosa fa |
|---|---|
| `/news` | Inizia una news: mandi foto e testo, poi `/genera` |
| `/collab` | Inizia una collaborazione (stesso flusso, più la scheda partner) |
| `/genera` | Genera la bozza |
| `/annulla` | Annulla la bozza in corso |

Nel testo puoi scrivere, in righe separate, `copertina: 2` per scegliere la foto di copertina e `logo: 3` per indicare il logo del partner. Le foto mandate come *file* (documento) mantengono la qualità originale.

Sotto la bozza trovi `[Pubblica sul sito]`, `[✏️ Rivedi]` (scrivi cosa cambiare e l'AI rigenera) e `[🗑️ Scarta]`. Dopo la pubblicazione il bot controlla che la pagina sia online e te lo scrive.

### Assistente in chat: `/scrivi`

| Comando | Cosa fa |
|---|---|
| `/scrivi` | Apre la conversazione con l'assistente (la stessa cosa della skill, dentro Telegram) |
| `/fine` (o `/reset`) | **Azzera la conversazione** (cronologia e foto): usalo quando hai finito, così non si spendono token per un contesto che non serve. Le bozze create restano |

Mandagli note, link di post del canale (`https://t.me/canale/123`) e foto, anche in più messaggi (foto e testo arrivati insieme sono un solo turno). Ti fa le domande che mancano, crea le bozze, le controlla e corregge, poi le mostra con i pulsanti `[Pubblica]` `[Rivedi]` `[Scarta]`: **pubblica solo il pulsante**, l'assistente non ha modo di farlo. Scrivere `/news` o `/collab` chiude l'assistente. La conversazione riparte da sola dopo `AGENT_MAX_MESSAGES` messaggi o `AGENT_IDLE_HOURS` ore di pausa.

Usa `AGENT_PROVIDER` / `AGENT_MODEL` (default: `AI_PROVIDER`) e richiede anche la chiave API di quel provider. Per provarlo dal terminale senza Telegram: `.venv/bin/python scripts/agent_chat.py [--provider claude]`.

## 🤖 Uso con la skill di Claude Code

È il modo più comodo di usare il progetto. Apri Claude Code in questa cartella e scrivi per esempio *"scrivi un articolo sulla nostra partecipazione al festival"* oppure *"fai gli articoli per queste tre collaborazioni, le note sono in `.sandbox`"*. La skill `write-article`:

1. legge le tue note e le immagini (in qualunque formato: JPEG, PNG, WEBP, AVIF…) e, se le dai il link di un messaggio del canale Telegram pubblico, ne prende testo, data e foto;
2. ti fa solo le domande che mancano, scrive un brief per ogni articolo in `data/briefs/` e ti chiede **un solo ok** per tutti;
3. crea le bozze sul repo del sito (la bozza è già l'anteprima: niente va online prima di pubblicare);
4. **controlla ogni bozza** (refusi, fatti inventati, scheda partner) e la corregge: le correzioni piccole sul testo generato, quelle grandi facendo riscrivere l'articolo all'AI;
5. pubblica solo quando glielo dici e poi verifica che le pagine siano online.

Il testo lo scrive sempre l'AI configurata in `AI_PROVIDER`, non la skill.

## ⌨️ Da riga di comando

```bash
.venv/bin/python scripts/create_article.py --kind news --brief data/briefs/festival.md \
    --images foto1.jpg foto2.jpg --dry-run --output drafts/festival.md
.venv/bin/python scripts/create_article.py --kind collab --brief data/briefs/partner.md \
    --images logo.png foto.jpg --logo 1
.venv/bin/python scripts/create_card.py --name "Partner" --logo logo.png --article collaborazione-con-partner \
    --description "Una frase di presentazione." --dry-run   # solo la scheda partner, senza articolo né AI
.venv/bin/python scripts/publish_article.py <id|slug>            # pubblica
.venv/bin/python scripts/publish_article.py <id|slug> --discard  # scarta
```
`--dry-run` non carica nulla su R2 e non scrive su GitHub.

Guardare, correggere e rigenerare una bozza già creata (senza pubblicarla) e leggere un post del canale Telegram:
```bash
.venv/bin/python scripts/edit_draft.py show <id|slug>      # i file come sono sul branch della bozza
.venv/bin/python scripts/edit_draft.py pull <id|slug>      # li scrive in drafts/<id>/ (articolo.md, scheda.md)
.venv/bin/python scripts/edit_draft.py push <id|slug>      # controlla le tue correzioni e le rimanda sulla bozza
.venv/bin/python scripts/edit_draft.py revise <id|slug> --feedback "più breve"   # l'AI riscrive il testo
.venv/bin/python scripts/fetch_telegram.py https://t.me/canale/123   # testo, data (per --date) e foto del messaggio
```
Se in `articolo.md` cambi `date`, `push` rinomina il file dell'articolo.

## 🚢 Deploy su k3s

Come gli altri bot (vedi `../K8S_BOT_SETUP_GUIDE.md`): il pod usa `python:3.12-slim` con la cartella del progetto montata in `/app`, crea `.venv-k8s`, installa `requirements.txt` e avvia `main.py`. Per aggiungerlo:

1. Copia `../k8s_manifests/ledger-bot-deploy.yaml` in `../k8s_manifests/article-bot-deploy.yaml`.
2. Cambia nome, label e `hostPath` (`/home/destard/Code/GdG-Article-Bot`).
3. `kubectl apply -f /home/destard/Code/k8s_manifests/`.
4. Per aggiornare: `git pull` e elimina il pod.

## 🗂️ Struttura

Vedi [`AGENTS.md`](AGENTS.md) (specifica tecnica, in inglese, letta dai tool AI).

## 🔗 Progetti collegati

- **GdG-Website**: il sito Astro dove finiscono gli articoli (`src/content.config.ts` definisce i campi accettati).
- **GdG-Event-Bot**: gestisce eventi e articoli delle *serate*; questo bot non li tocca.
