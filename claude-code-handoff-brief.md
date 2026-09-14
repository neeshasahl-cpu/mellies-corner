# HANDOFF BRIEF — "Mellie's Corner" website build

This document briefs Claude Code on building a personal gift website. Everything the
project needs in terms of taste, content direction, and design is already decided and
supplied as assets. Your job is the build: the site shell, the clickable windows, and
the daily content automation.

---

## 1. WHAT THIS IS

A one-page, no-scroll personal gift website for **Melany** — a Singapore-based
multidisciplinary creative. The page shows an illustrated desk scene ("Mellie's
Corner"). Certain regions of that scene — a computer monitor, a phone, and pegboard
cards — are **clickable "windows."** Each window opens curated content that **rotates
daily**, so every time she opens the site it feels fresh.

The vibe: like a friend who leaves a beautiful little corner of the internet for her,
refreshed each morning. It should feel warm, hand-made, and surprising — never
corporate or templated.

---

## 2. ASSETS PROVIDED

1. **Mellie's Corner** (PNG) — the clean desk-scene artwork. This is the static
   background of the site.
2. **Mellie's Corner 2** (PNG) — the SAME scene with colored boxes drawn over each
   activation zone, labeled with window names. Use this to read off where each
   clickable window sits, then translate those regions into positioned elements over
   the clean PNG. (Coordinates as % of image width/height so it stays responsive.)
3. **Seed list — six CSV files, one per window.** Each filename is numbered to match a
   labeled window in the scene (see "Mellie's Corner 2"). The rows in each CSV are the
   seed sources / taste anchors for that window's content:
   - `window-1-design-and-web-toys.csv`   → Window 1 (the monitor screen)
   - `window-2-substack-and-culture.csv`  → Window 2 (taped pegboard card)
   - `window-3-mocktail-sources.csv`      → Window 3 (the phone screen)
   - `window-4-interior-decor.csv`        → Window 4 (small pegboard note)
   - `window-5-sg-food-sources.csv`       → Window 5 (the boombox)
   - `how-to-use.csv` (a legend / notes file, not a content window)
4. **melany-taste-prompt.md** — the master curation brief describing Melany's taste,
   the daily categories, the once-a-week "wildcard," and the guardrails. This is the
   instruction the daily AI curation runs on. Treat it as the source of truth for
   *what* content to surface.

---

## 3. HOW THE WINDOWS MAP TO CONTENT

- Read the labeled zones in **Mellie's Corner 2**. Each zone is labeled "Window N —
  Category," and each corresponds to the matching **`window-N-*.csv`** seed file:
  - Window 1 → Design & Web Toys (monitor)
  - Window 2 → Substack & Culture (pegboard card)
  - Window 3 → Mocktail Sources (phone)
  - Window 4 → Interior Decor (pegboard note)
  - Window 5 → SG Food Sources (boombox)
- Each window displays that category's "pick of the day."
- The seed-list rows are **taste anchors + starting sources**, NOT a fixed playlist.
  The daily automation uses them (plus live web search) to find fresh picks in that
  same spirit.

---

## 4. HOW THE DAILY AUTOMATION SHOULD WORK

A script runs **once per day** (via GitHub Actions on a schedule) and produces a small
data file (e.g. `today.json`) that the website reads to fill its windows.

Pipeline:
```
GitHub Actions (daily cron)
        │
        ▼
  daily script
   ├── loads melany-taste-prompt.md  (the taste brief)
   ├── loads seed sources per category from the six seed CSVs
   ├── calls the Anthropic API (Claude) WITH the web_search tool enabled
   │      → for each window/category, find today's pick in Melany's taste
   │      → apply the wildcard rule (~once a week, one surprise pick, labeled)
   └── writes today.json  { perWindow: { title, url, blurb, isWildcard } }
        │
        ▼
  The website reads today.json and fills each window
```

Key rules for the script:
- **Use the Anthropic API with the web_search tool** so picks are CURRENT, not just
  from training data or the static seed list.
- **No web scraping.** Substacks can use RSS; everything else is AI-curated via the
  API + web search. (This was a deliberate decision — scraping is fragile and often
  against ToS.)
- Each pick = title, link, one warm sentence on why she'd love it.
- Honor the taste-prompt guardrails (no historical fine-art canon; keep it indie,
  warm, surprising; never streamline to one aesthetic).
- The wildcard should appear roughly weekly, clearly flagged, and lean fully into an
  unexpected aesthetic world.

---

## 5. THE WEBSITE ITSELF

- **One page, no scroll.** The desk scene fills the viewport.
- **Background:** the clean "Mellie's Corner" PNG. (Consider exporting/serving a WebP
  version for faster load; compress the PNG if it's heavy.)
- **Windows:** positioned elements over the background at the coordinates from the
  marked-up image. Each is clickable/tappable and shows today's pick for its category.
  - For screen-type windows (monitor, phone), the pick can render as a live preview /
    embed where the site allows it, with a graceful fallback "postcard" (preview card +
    link) for sites that block embedding (many do — handle this).
  - For pegboard cards, a small styled card with the pick's title + blurb + link.
- **Reads `today.json`** to populate windows on load. Fails gracefully if a pick is
  missing (show the last good pick or a gentle placeholder).
- **Responsive:** windows anchored by % so they track the scene on different screens.
- Keep it a single self-contained front end where practical.

---

## 6. DEPLOYMENT

- Host the repo on **GitHub**.
- Use **GitHub Actions** for the daily scheduled run (cron) that regenerates
  `today.json` and commits it (or writes to wherever the site reads it).
- Deploy the site via **GitHub Pages** (or Netlify) — pick whichever is simplest given
  the final structure and note the choice.

---

## 7. WHAT THE OWNER WILL PROVIDE

- An **Anthropic API key** (stored as a GitHub Actions secret — never committed).
- Their **GitHub account** to host the repo and run Actions.
- The four assets in section 2.

---

## 8. SUGGESTED BUILD ORDER

1. Static site: background PNG + windows positioned from the marked-up image
   (hard-coded placeholder content first, just to nail placement).
2. Wire windows to read from a sample `today.json`.
3. Write the daily script (Anthropic API + web_search, driven by the taste prompt +
   seed list) to generate a real `today.json`.
4. Add embedding + postcard-fallback behavior for screen windows.
5. Set up GitHub Actions daily cron + secrets.
6. Deploy; confirm the site updates the morning after a run.
7. Polish: WebP/compression, graceful failure states, mobile check.

---

*Taste, content direction, seed sources, and scene design are all finalized in the
provided assets. Lean on melany-taste-prompt.md for any "what would she like?" question
rather than re-deriving it.*
