# Pulse Hub

A free, multi-client lead and mention radar. Every client gets a short profile (brand, what the product can and can't
do, keywords, sources). Pulse then searches Reddit and Quora for:
- people asking for exactly what the client sells, and
- every mention of the brand.

It classifies each hit with free-tier AI, sends Slack alerts and keeps a per-client history for the dashboard.

Everything runs on free tiers: GitHub Actions, the Gemini / Groq / OpenRouter free models, free web search,
F5Bot, Google Alerts, and optionally Serper's free credits and Apify's free monthly credits.

## Two repos

| Repo | Visibility | Holds |
|---|---|---|
| `pulse-hub` (this one) | **public** (so GitHub Actions minutes are unlimited) | code, prompt, workflow, a **fictional** example profile |
| `pulse-hub-data` | **private** | `clients/<slug>.json` profiles and `data/<slug>/results.json` results |

No client name or client data ever goes into this repo. A test (`test_no_webhook_or_real_client_data_in_the_public_repo`)
guards that.

## How a run works

```
every 6h (GitHub Actions)  ->  run.py
  ├─ F5Bot inbox: read once, route each alert to the client(s) whose keywords it contains
  └─ for each active client in pulse-hub-data/clients/:
       sources: google_alerts · serper · web (free metasearch) · linkedin   (each optional, per profile)
       -> clean snippets (drop Google's "related posts" sidebar) -> one record per thread
       -> brand check (is the brand in the *post*, or only in the sidebar?)
       -> classify with the client's own prompt (known threads reuse their old verdict: no AI cost)
       -> vault (qualified threads) -> Slack (that client's webhook) -> data/<slug>/results.json
```

Categories (the same for every client): **PR Support** (complaint about the brand), **Prime Target** (someone asking
for what the client does), **Brand Engagement** (praise), **Monitor** (neutral mention), **Ignore**.

## Setup (one time)

1. **Data-repo token.** GitHub → Settings → Developer settings → Fine-grained personal access tokens → *Generate new
   token*. For *Repository access*, choose **Only select repositories → `pulse-hub-data`**. Under *Permissions →
   Repository permissions*, set **Contents: Read and write**. Copy the token.
2. In **this** repo: Settings → Secrets and variables → Actions → *New repository secret*:

   | Secret | Needed? | What |
   |---|---|---|
   | `DATA_REPO_TOKEN` | **required** | the token from step 1 |
   | `GEMINI_API_KEY` | **required** | free at aistudio.google.com |
   | `GROQ_API_KEY`, `OPENROUTER_API_KEY` | recommended | free fallbacks when Gemini's quota runs out |
   | `SLACK_WEBHOOK_<SLUG>` | per client | e.g. `SLACK_WEBHOOK_ACME`; no secret = no alerts for that client |
   | `EMAIL_USER`, `EMAIL_PASS` | for F5Bot | the inbox F5Bot e-mails (Gmail: use an *app password*) |
   | `SERPER_API_KEY` | optional | Google results; 2,500 free credits once |
   | `APIFY_API_TOKEN` | optional | LinkedIn source |

   Optional repository **variables**: `DATA_REPO` (if the data repo is named differently), `F5BOT_MARK_SEEN=false`
   (while another tool reads the same inbox), and `GEMINI_MODEL` / `GROQ_MODEL` / `OPENROUTER_MODEL` (when a free
   model is retired).
3. Actions → **Pulse** → *Run workflow*. Results appear in `pulse-hub-data/data/<slug>/results.json`.

## Adding a client

1. Copy `examples/acme.json` to `pulse-hub-data/clients/<slug>.json`. The slug is lowercase letters, digits and
   dashes, and becomes the file and secret name.
2. Fill it in (fields below). Check it with `python run.py --validate --clients-dir ../pulse-hub-data/clients`.
3. Add the `SLACK_WEBHOOK_<SLUG>` secret here.
4. Add the profile's `sources.f5bot.keywords` to your F5Bot account (f5bot.com, free).
5. Optional: create Google Alerts (google.com/alerts → *Show options → Deliver to: RSS feed*), e.g.
   `"acme invoicing" site:reddit.com`, and paste each feed URL into `sources.google_alerts.feeds`.

The first run on a new client never floods Slack with history. Only threads it hasn't seen before are alerted.

### Profile fields

| Field | Meaning |
|---|---|
| `name`, `website`, `description`, `audience`, `tone` | who the client is; shapes the prompt and the suggested reply angles |
| `brand.terms` | distinctive spellings that are **always** a mention (`acmeinvoicing`) |
| `brand.ambiguous_terms` | spellings that are also ordinary words (`acme`); a mention unless the context shows plain English |
| `brand.ambiguous_not_after` / `ambiguous_not_before` | the context words that mean "plain English" (`acme corp`, `a tiny doc file`) |
| `brand.false_positives` | phrases that are never the brand |
| `brand.note` | an extra sentence for the AI's Step 1 (brand detection) |
| `can` / `cannot` | what the product does and does not do: the core of "is this a Prime Target?" |
| `competitors` | named in the prompt; a post naming only a competitor is not a brand mention |
| `examples` | few-shot examples `{"text", "category"}`: the fastest way to fix a misclassification |
| `prompt_rules` | extra one-line rules for the classifier |
| `queries.brand` / `queries.intent` | search queries shared by the search sources. Use `site:reddit.com` or `site:quora.com` |
| `sources.<name>` | `enabled`, plus source options; `min_hours_between_runs` throttles a source (to save credits) |
| `slack.webhook_secret`, `slack.alert_on` | which secret holds the webhook; alert on `mention` and/or any categories |
| `active` | `false` pauses the client |

### Sources

| Source | Cost | Notes |
|---|---|---|
| `web` | free, no key | the `ddgs` metasearch (DuckDuckGo, Bing, Brave, …); rate-limited, so keep it to about 15 queries per client |
| `google_alerts` | free | Google's own crawl, delivered as RSS; slower (hours) but unlimited |
| `f5bot` | free | near-real-time Reddit/HN keyword alerts by e-mail; one inbox for all clients |
| `serper` | 2,500 free credits once | give it its **own short** `queries` list and `min_hours_between_runs` |
| `linkedin` | Apify free monthly credits | brand mentions only, from the last 48h |

Reddit RSS and the unauthenticated Reddit JSON endpoints are **not** used. Reddit shut both off in 2026.

## What's in `results.json`

The same layout as the original single-client dashboard (`brand_alerts`, `hot_leads`, `archive`, `f5bot_leads`), plus:
- `query_stats`: per query, the runs, results, **new** threads, how many of those qualified, and the last error. This
  shows which queries are worth keeping.
- `source_health`: per source, `ok` / `degraded` / `down` with the last error. A dead free source shows up as
  "down", not as a quiet day.
- `source_last_run`, `date_anchors` (Reddit id → date calibration) and `slack_sent_urls` (alert de-dupe).

Dates carry a `date_confidence`:
- `exact` / `approx`: from the source itself
- `estimated`: from the Reddit thread id
- `email`: F5Bot
- `discovered`: when a crawler first saw it
- `unknown`

## Moving a client over from the old single-client scraper

1. **Shadow run (now).** Only `DATA_REPO_TOKEN` and the AI keys are set here. The hub runs on the free web search,
   sends no alerts and leaves the F5Bot inbox alone, while the old system keeps working. Compare results for a few
   days.
2. **Switch.**
   - Disable the old repo's workflows (Actions → each workflow → *Disable workflow*).
   - Re-import its latest `results.json`, so nothing it already alerted is alerted again:
     `python tools/import_legacy.py old/results.json ../pulse-hub-data/data/<slug>/results.json`.
   - Then add `EMAIL_USER`, `EMAIL_PASS`, `SERPER_API_KEY`, `APIFY_API_TOKEN` and `SLACK_WEBHOOK_<SLUG>` here.

## Development

```bash
pip install -r requirements-dev.txt
python -m pytest -q
python run.py --validate --clients-dir examples
```

The tests run every source and the whole pipeline with network and AI mocked, on synthetic data only.

## Roadmap

1. ~~Engine: client profiles, templated prompt, free sources, one workflow for all clients~~ (this version)
2. Dashboard: client switcher, reads the private data with your GitHub token, 👍/👎 feedback stored per client
3. Onboarding wizard: website + description → AI-drafted profile, F5Bot keyword list and Google Alerts list, plus a
   7-day back-test before going live
4. Quora tab: digest e-mail ingestion, and ranking by search value instead of freshness
5. Self-tuning: retire queries with no qualified results for 2 weeks, and propose new ones from winning threads
