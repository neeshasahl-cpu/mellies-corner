# Mellie's Corner

A one-page, no-scroll gift website for Melany — an illustrated desk scene where five
clickable "windows" (a monitor, a phone, and three pegboard/boombox spots) each show a
daily-refreshed pick in her taste: a web toy, a culture read, a mocktail recipe, an
interior idea, and a Singapore food spot.

## How it works

- **`index.html` / `style.css` / `script.js`** — the site itself. Reads `today.json` on
  load and renders each window's pick as an image-backed card (screenshot or promo
  image, title + blurb legible over it), falling back to a plain text card if no image
  is available. All five zones are positioned by percentage and locked to the artwork's
  aspect ratio, so they track the scene at any screen size.
- **`today.json`** — today's five picks (title, url, blurb, isWildcard, previewImage).
  Regenerated daily by the script below; committed so the live site always has content.
- **`assets/previews/`** — the resolved preview image for each window, saved locally
  (never hotlinked) so the site loads instantly.
- **`seeds/`** — `melany-taste-prompt.md` (the curation brief) and the five
  `window-N-*.csv` seed lists (taste anchors + a real source pool) the daily script
  curates from.
- **`scripts/generate_today.py`** — the daily generator. For each window, calls the
  Claude API (`web_search` enabled) to find a fresh pick — roughly half the time
  directly from that window's seed list (cycling through it before repeating any row),
  the rest from open web search in the same spirit — then resolves a preview image via
  a fallback chain (a real screenshot via SnapRender → the page's `og:image` → no image,
  plain card), recompressing whichever image is found to a small WebP so every preview
  stays lightweight. Writes `today.json`, updates `scripts/seed_usage_state.json`
  (internal cycling bookkeeping), and appends one row per pick to `history.csv` (a
  human-readable log of everything ever served).

## Running the generator locally

Requires Python 3.11+ and:

```
pip install -r scripts/requirements.txt
```

Set both API keys as environment variables first — **never** hardcode them or commit a
file containing them:

```powershell
$env:ANTHROPIC_API_KEY = "sk-ant-..."
$env:SNAPRENDER_API_KEY = "sk_live_..."
python scripts/generate_today.py
```

In CI (GitHub Actions), these are read from repository secrets of the same names.

## Deployment

Static site served via GitHub Pages directly from this repo. `today.json`,
`history.csv`, `scripts/seed_usage_state.json`, and `assets/previews/` are committed
so the live site always reflects the last successful generation run — the daily
scheduler that regenerates them automatically is set up separately (not yet wired up
as of this commit).
