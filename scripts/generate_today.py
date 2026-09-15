"""
Daily content generator for Mellie's Corner.

Loads melany-taste-prompt.md (the curation brief) and each window's seed CSV
(taste anchors + real source pool), calls Claude with the web_search tool
enabled to find one fresh pick per window, resolves a preview image
(og:image) for each pick's URL, and writes the result to today.json.

Per the taste prompt's "SEED USAGE" rule, about half of each window's picks
(over time) should be actual links from that window's seed CSV rather than
open web search -- cycling through the full seed list before repeating any
row. Window 1 (Design & Web Toys) is an exception: it skews ~70/30 toward
seed picks (SEED_PICK_PROBABILITY_OVERRIDES), since open web search for that
category tends to surface shallow novelty/gag sites rather than the quietly
crafted web toys Melany actually likes -- see the taste prompt's "WINDOW 1
EXCEPTION" and "WINDOW 1 CALIBRATION" sections, both passed through verbatim
in that window's system prompt. seed_usage_state.json tracks which seed rows
have been used per window so that cycle persists across daily runs; it is
not a secret and is meant to be committed alongside today.json.

Every run also appends one row per pick to history.csv -- a human-readable,
append-only log (date, window, category, title, blurb, url, source,
isWildcard, image_source) for tracking what's been served over time, how
often seed vs. web picks show up, how often each preview-image source is
landing, and -- via blurb -- the curation reasoning behind each pick. The
blurb is never shown on the site itself (a window is a clean preview of the
site, not a recommendation card); it's kept here and in today.json purely as
the log record. history.csv is distinct from seed_usage_state.json (that's
internal bookkeeping for the cycling logic; history.csv is the readable
record). Never overwritten, only appended to; meant to be committed and grow
indefinitely.

Preview images: fallback chain of screenshot (SnapRender) / og:image / plain
card, with per-window ordering --
  - window-1 (Design & Web Toys): interactive/generative web toys often
    screenshot blank or black when captured cold, but their og:image is
    usually a proper promo shot, so this window tries og:image FIRST, then
    screenshot, then the card. See OG_IMAGE_FIRST_WINDOWS.
  - every other window (content pages -- articles, recipes, house tours,
    cafes -- which screenshot well): screenshot first, then og:image, then
    the card.
Whichever source succeeds is downloaded and saved locally under
assets/previews/ (never hotlinked), one fixed filename per window, so the
site loads instantly and never depends on a remote host at view time. Which
source was used is recorded per-pick in history.csv's image_source column.

Every image that ends up in a window -- screenshot or og:image alike -- is
normalized to WebP at PREVIEW_MAX_WIDTH and PREVIEW_QUALITY (see
recompress_image()) before it's saved, so file sizes stay small and
consistent regardless of source: SnapRender is asked to render WebP at that
size directly; a downloaded og:image (which can arrive as an arbitrarily
large PNG/JPEG straight from the source CMS) is recompressed to match after
download. Keeps every preview lightweight for mobile.

Requires ANTHROPIC_API_KEY and SNAPRENDER_API_KEY in the environment. Never
hardcode either key here and never commit a file that contains them -- see
.gitignore. If SNAPRENDER_API_KEY is unset, every pick simply falls through
to the og:image / card steps -- the script still runs.

Usage:
    python scripts/generate_today.py
"""

import csv
import datetime
import html
import io
import json
import os
import random
import re
import sys
import urllib.request
from pathlib import Path
from urllib.parse import urljoin

import anthropic
import requests
from PIL import Image

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SEEDS_DIR = PROJECT_ROOT / "seeds"
OUTPUT_PATH = PROJECT_ROOT / "today.json"
STATE_PATH = Path(__file__).resolve().parent / "seed_usage_state.json"
HISTORY_PATH = PROJECT_ROOT / "history.csv"
HISTORY_FIELDS = [
    "date",
    "window",
    "category",
    "title",
    "blurb",
    "url",
    "source",
    "isWildcard",
    "image_source",
]
PREVIEWS_DIR = PROJECT_ROOT / "assets" / "previews"

MODEL = "claude-opus-5"
SEED_PICK_PROBABILITY = 0.5  # "roughly a 50/50 mix over time" per the taste prompt

# Per-window override of SEED_PICK_PROBABILITY. Window 1's own seed list is
# the strongest calibration for that category, and open web search tends to
# surface shallow novelty/gag sites there -- so it skews seed-heavy (~70/30)
# per the taste prompt's "WINDOW 1 EXCEPTION". Windows not listed here use
# the default 50/50.
SEED_PICK_PROBABILITY_OVERRIDES = {"window-1": 0.7}

SNAPRENDER_BASE = "https://app.snap-render.com"
SNAPRENDER_TIMEOUT = 35  # server-side render timeout is 30s; give it margin

# Shared target for every preview image, whatever its source -- keeps file
# sizes small and consistent (roughly 70-370KB in practice) for fast mobile
# loads. SnapRender is asked to capture at this size/format directly;
# downloaded og:images are recompressed to match via recompress_image().
PREVIEW_MAX_WIDTH = 1280
PREVIEW_QUALITY = 85

# window-1 (Design & Web Toys) skews toward interactive/generative web toys
# that screenshot blank or black when captured cold (nothing's been clicked
# yet) -- their og:image is usually a proper designed promo shot instead, so
# that window's fallback order is flipped: og:image first, screenshot second.
OG_IMAGE_FIRST_WINDOWS = {"window-1"}

WINDOWS = {
    "window-1": {
        "label": "Design & Web Toys",
        "seed_file": "window-1-design-and-web-toys.csv",
        "ask": "a beautiful, strange, or interactive website or web toy",
    },
    "window-2": {
        "label": "Substack & Culture",
        "seed_file": "window-2-substack-and-culture.csv",
        "ask": "a culture or design article / newsletter worth reading today",
    },
    "window-3": {
        "label": "Mocktail Sources",
        "seed_file": "window-3-mocktail-sources.csv",
        "ask": "one specific mocktail recipe",
    },
    "window-4": {
        "label": "Interior Decor",
        "seed_file": "window-4-interior-decor.csv",
        "ask": "one specific interior or decor idea",
    },
    "window-5": {
        "label": "SG Food Sources",
        "seed_file": "window-5-sg-food-sources.csv",
        "ask": "one specific Singapore place to eat (aesthetic cafe, hidden gem, or new opening)",
    },
}

RESULT_RE = re.compile(r"<RESULT>\s*(\{.*?\})\s*</RESULT>", re.DOTALL)


def load_taste_prompt() -> str:
    return (SEEDS_DIR / "melany-taste-prompt.md").read_text(encoding="utf-8")


def load_seed_csv_text(filename: str) -> str:
    return (SEEDS_DIR / filename).read_text(encoding="utf-8")


def parse_seed_rows(csv_text: str) -> list[dict]:
    """Parse a seed CSV into rows of {source, url_field, what, why}, skipping
    the header and any blank template rows (empty Source/URL)."""
    reader = csv.reader(io.StringIO(csv_text))
    rows = []
    for i, cols in enumerate(reader):
        if i == 0 or len(cols) < 2:
            continue
        source, url_field = cols[0].strip(), cols[1].strip()
        if not source or not url_field:
            continue
        rows.append(
            {
                "source": source,
                "url_field": url_field,
                "what": cols[2].strip() if len(cols) > 2 else "",
                "why": cols[3].strip() if len(cols) > 3 else "",
            }
        )
    return rows


def is_name_search_row(url_field: str) -> bool:
    """Some seed rows have no clean domain to visit directly -- either just
    "search 'Nylon Coffee Everton Park'", or (window-2's CSV) a domain
    followed by a search hint, "substack.com -- search 'Feeling! Magazine'".
    Per how-to-use.csv, these need a name search rather than a domain visit.
    Matches "search '...'" ANYWHERE in the field, not just as a prefix --
    an earlier prefix-only check missed the "domain -- search 'X'" form and
    fed the whole field (domain, dash, and all) to allowed_domains as a
    single bogus value."""
    return bool(re.search(r"search\s+['‘’\"]", url_field, re.IGNORECASE))


def domain_of(url_field: str) -> str:
    d = re.sub(r"^https?://", "", url_field.strip())
    return d.split("/")[0]


def load_state() -> dict:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    return {}


def save_state(state: dict) -> None:
    STATE_PATH.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def append_history(rows: list[dict]) -> None:
    """Append rows to history.csv, writing the header only on first creation.
    Never overwrites -- always appends, so it builds into a full record over
    time. utf-8-sig (BOM) only on creation, so Excel/Sheets render accents and
    em dashes correctly; a BOM on every append would corrupt the file, so
    subsequent runs open in plain utf-8 append mode instead."""
    is_new = not HISTORY_PATH.exists()
    encoding = "utf-8-sig" if is_new else "utf-8"
    with open(HISTORY_PATH, "a", newline="", encoding=encoding) as f:
        writer = csv.DictWriter(f, fieldnames=HISTORY_FIELDS)
        if is_new:
            writer.writeheader()
        writer.writerows(rows)


def peek_next_seed_row(window_id: str, rows: list[dict], state: dict, rng: random.Random) -> dict:
    """Choose the next seed row for this window, cycling through the full
    list before any row repeats. Does NOT mutate `state` -- call
    mark_seed_row_used() only after the row is actually successfully served,
    so a failed generation doesn't silently burn a cycle slot on a pick
    nobody ever saw."""
    used = set(state.get(window_id, []))
    remaining = [r for r in rows if r["source"] not in used]
    if not remaining:
        remaining = rows[:]
    return rng.choice(remaining)


def mark_seed_row_used(window_id: str, row: dict, rows: list[dict], state: dict) -> None:
    """Record that `row` was actually served. If the cycle was already
    complete (every row already marked used), start a fresh cycle instead of
    just adding to an already-full set."""
    used = set(state.get(window_id, []))
    if used >= {r["source"] for r in rows}:
        used = set()
    used.add(row["source"])
    state[window_id] = sorted(used)


def pick_wildcard_window(today: datetime.date) -> str | None:
    """Roughly once a week, one window's pick is an off-theme wildcard.
    Seeded by date so re-running the script the same day is stable."""
    rng = random.Random(f"wildcard-{today.isoformat()}")
    if rng.random() < 1 / 7:
        return rng.choice(list(WINDOWS.keys()))
    return None


def pick_mode(window_id: str, today: datetime.date) -> str:
    """"seed" or "search", independently per window, at that window's seed
    probability (default 50/50, overridden per SEED_PICK_PROBABILITY_OVERRIDES
    -- e.g. window-1 skews ~70/30 toward seed). Seeded by date+window
    (distinct stream from the wildcard RNG) so a same-day re-run is stable."""
    probability = SEED_PICK_PROBABILITY_OVERRIDES.get(window_id, SEED_PICK_PROBABILITY)
    rng = random.Random(f"mode-{today.isoformat()}-{window_id}")
    return "seed" if rng.random() < probability else "search"


def web_search_tool(allowed_domains: list[str] | None = None) -> dict:
    tool = {"type": "web_search_20260209", "name": "web_search", "max_uses": 5}
    if allowed_domains:
        tool["allowed_domains"] = allowed_domains
    return tool


def build_messages(
    window_id: str,
    info: dict,
    taste_prompt: str,
    is_wildcard: bool,
    mode: str,
    seed_row: dict | None,
):
    system = f"""You are the daily curator for "Mellie's Corner", a personal gift website for \
Melany. Follow this taste brief exactly:

{taste_prompt}

You are curating today's pick for one specific window: **{info['label']}**. \
Today's ask for this window: {info['ask']}.
"""

    tool = web_search_tool()

    if is_wildcard:
        system += """
IMPORTANT: today is a WILDCARD day for this window. Per the taste brief's wildcard \
rule, deliberately break the normal pattern: pick something from a totally unexpected \
aesthetic world she hasn't signaled (e.g. medieval magic, fairycore, deep-forest, \
mushrooms & moss, retro-futurism, deep-sea, folklore & myth, botanical illustration, \
brutalism, outer space, ancient maps) while still roughly fitting this window's \
category ({info[ask]}). Use web search freely to find it. Make it genuinely lovely, \
not random for its own sake.
""".format(info=info)

    elif mode == "seed":
        if is_name_search_row(seed_row["url_field"]):
            source_hint = (
                f'This source has no clean custom domain -- its seed entry says: '
                f'{seed_row["url_field"]}. Search the web to find its exact current site '
                f'(e.g. its Substack handle) first, then find a specific current pick there.'
            )
            if window_id == "window-2":
                tool = web_search_tool(allowed_domains=["substack.com"])
        else:
            domain = domain_of(seed_row["url_field"])
            source_hint = (
                f'Its site is {seed_row["url_field"]}. Use web search (restricted to this '
                f"site) to find a SPECIFIC current item there that fits today's ask -- e.g. a "
                f"specific article, recipe, room idea, or menu item currently live on this "
                f"site -- not just the homepage, unless the site itself IS the single thing "
                f"(e.g. a one-page web toy)."
            )
            tool = web_search_tool(allowed_domains=[domain])

        system += f"""
Today is this window's turn to serve a SEED pick, per the taste brief's seed-usage rule. \
Use this specific seed source -- do not substitute a different one:

  Source: {seed_row['source']}
  URL field: {seed_row['url_field']}
  What it is: {seed_row['what']}
  Why it fits Melany: {seed_row['why']}

{source_hint}
"""

    else:  # mode == "search"
        system += """
Today is this window's turn for a FRESH FIND, per the taste brief's seed-usage rule -- \
find something CURRENT via open web search in the same spirit as this window's seed \
list, rather than picking directly from that list.
"""

    system += """
Use the web_search tool to confirm the pick is real, currently live, and to find its \
exact URL. Do not invent a URL -- only use one you found via search.

When you have a confirmed pick, end your entire response with a single line of the \
exact form below and nothing after it (no closing remarks, no markdown fences):

<RESULT>{"title": "...", "url": "https://...", "blurb": "..."}</RESULT>

Rules for that JSON: "title" is the pick's name (short). "url" is the exact real URL \
you found via search. "blurb" is exactly one warm sentence, in your voice as her \
friend, on why she'd love it -- no corporate tone, no hedging."""

    user = f"Find today's pick for {info['label']}."
    return system, user, tool


def call_claude(client: anthropic.Anthropic, system: str, user: str, tool: dict) -> dict:
    messages = [{"role": "user", "content": user}]
    restarts = 0
    while True:
        with client.messages.stream(
            model=MODEL,
            max_tokens=8000,
            system=system,
            messages=messages,
            tools=[tool],
        ) as stream:
            response = stream.get_final_message()

        if response.stop_reason != "pause_turn":
            break
        restarts += 1
        if restarts > 4:
            raise RuntimeError("gave up: search turn still paused after 4 restarts")
        messages = [{"role": "user", "content": user}, {"role": "assistant", "content": response.content}]

    text = "".join(block.text for block in response.content if block.type == "text")
    match = RESULT_RE.search(text)
    if not match:
        raise ValueError(f"no <RESULT>...</RESULT> block found in response:\n{text[:500]}")
    data = json.loads(match.group(1))
    for field in ("title", "url", "blurb"):
        if not data.get(field):
            raise ValueError(f"result missing required field '{field}': {data}")
    return data


def fetch_og_image(url: str, timeout: int = 8) -> str | None:
    """Best-effort fetch of a page's og:image / twitter:image meta tag.
    Returns None on any failure so the caller can fall back gracefully."""
    try:
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; MelliesCornerBot/1.0; "
                "+https://example.com/bot)"
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(400_000)  # cap: only need the <head>
        page = raw.decode("utf-8", errors="ignore")
    except Exception as err:
        print(f"    (preview image fetch failed: {err})", file=sys.stderr)
        return None

    patterns = [
        r'<meta[^>]+property=["\']og:image(?::secure_url)?["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image(?::secure_url)?["\']',
        r'<meta[^>]+name=["\']twitter:image["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']twitter:image["\']',
    ]
    for pattern in patterns:
        m = re.search(pattern, page, re.IGNORECASE)
        if m:
            return urljoin(url, html.unescape(m.group(1)))
    return None


def capture_screenshot(url: str, window_id: str) -> Path | None:
    """Try a real screenshot of `url` via SnapRender. Returns the saved local
    path on success, or None on any failure (missing key, error, timeout,
    blocked target) so the caller can fall back to og:image."""
    api_key = os.environ.get("SNAPRENDER_API_KEY")
    if not api_key:
        print("    (SNAPRENDER_API_KEY not set -- skipping screenshot)", file=sys.stderr)
        return None

    params = {
        "url": url,
        "format": "webp",
        "width": PREVIEW_MAX_WIDTH,
        "height": 800,
        "quality": PREVIEW_QUALITY,
        "delay": 2000,  # ms to let the page settle before capture
        "block_cookie_banners": "true",
        "block_ads": "true",
    }
    try:
        resp = requests.get(
            f"{SNAPRENDER_BASE}/v1/screenshot",
            params=params,
            headers={"X-API-Key": api_key},
            timeout=SNAPRENDER_TIMEOUT,
        )
    except requests.RequestException as err:
        print(f"    (screenshot request failed: {err})", file=sys.stderr)
        return None

    if resp.status_code != 200:
        try:
            detail = resp.json().get("error", {})
            reason = f"{detail.get('code')}: {detail.get('message')}"
        except Exception:
            reason = resp.text[:200]
        print(f"    (screenshot failed, HTTP {resp.status_code}: {reason})", file=sys.stderr)
        return None

    PREVIEWS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = PREVIEWS_DIR / f"{window_id}.webp"
    out_path.write_bytes(resp.content)
    return out_path


def recompress_image(raw_bytes: bytes) -> bytes:
    """Normalize any image (arbitrary format/size, as downloaded straight
    from a source CMS) to WebP at PREVIEW_MAX_WIDTH / PREVIEW_QUALITY, so
    every window image -- screenshot or og:image alike -- lands in the same
    small size range. Flattens transparency onto white first, since a
    downloaded og:image is sometimes a PNG with alpha and WebP-with-alpha
    over the site's photo scrim can look wrong."""
    img = Image.open(io.BytesIO(raw_bytes))
    img.load()

    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        img = img.convert("RGBA")
        flattened = Image.new("RGB", img.size, (255, 255, 255))
        flattened.paste(img, mask=img.split()[-1])
        img = flattened
    else:
        img = img.convert("RGB")

    if img.width > PREVIEW_MAX_WIDTH:
        new_height = round(img.height * (PREVIEW_MAX_WIDTH / img.width))
        img = img.resize((PREVIEW_MAX_WIDTH, new_height), Image.LANCZOS)

    out = io.BytesIO()
    img.save(out, format="WEBP", quality=PREVIEW_QUALITY, method=6)
    return out.getvalue()


def save_remote_image(image_url: str, window_id: str, timeout: int = 10) -> Path | None:
    """Download an already-resolved image URL (e.g. an og:image), recompress
    it to match the SnapRender captures (see recompress_image()), and save
    it locally so the site never hotlinks a remote host. Returns None on any
    failure so the caller can fall back to the plain card."""
    try:
        resp = requests.get(
            image_url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; MelliesCornerBot/1.0)"},
            timeout=timeout,
        )
        resp.raise_for_status()
    except requests.RequestException as err:
        print(f"    (og:image download failed: {err})", file=sys.stderr)
        return None

    try:
        webp_bytes = recompress_image(resp.content)
    except Exception as err:
        print(f"    (og:image recompress failed: {err})", file=sys.stderr)
        return None

    PREVIEWS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = PREVIEWS_DIR / f"{window_id}-og.webp"
    out_path.write_bytes(webp_bytes)
    return out_path


def clear_stale_previews(window_id: str) -> None:
    """Remove any previously-saved preview file for this window so a failed
    run today doesn't silently keep serving a stale image from a prior day."""
    if not PREVIEWS_DIR.exists():
        return
    for existing in PREVIEWS_DIR.glob(f"{window_id}.*"):
        existing.unlink(missing_ok=True)
    for existing in PREVIEWS_DIR.glob(f"{window_id}-og.*"):
        existing.unlink(missing_ok=True)


def _try_screenshot(url: str, window_id: str) -> tuple[str, str] | None:
    print(f"    [{window_id}] trying screenshot...", file=sys.stderr)
    path = capture_screenshot(url, window_id)
    if path:
        return path.relative_to(PROJECT_ROOT).as_posix(), "screenshot"
    print(f"    [{window_id}] screenshot did not yield an image", file=sys.stderr)
    return None


def _try_og_image(url: str, window_id: str) -> tuple[str, str] | None:
    print(f"    [{window_id}] trying og:image...", file=sys.stderr)
    og_url = fetch_og_image(url)
    if og_url:
        path = save_remote_image(og_url, window_id)
        if path:
            return path.relative_to(PROJECT_ROOT).as_posix(), "og:image"
    else:
        print(f"    [{window_id}] no og:image tag found on the page", file=sys.stderr)
    return None


def resolve_preview_image(url: str, window_id: str) -> tuple[str | None, str]:
    """The fallback chain: screenshot -> og:image -> card for most windows;
    og:image -> screenshot -> card for OG_IMAGE_FIRST_WINDOWS (see its
    comment). Returns (site-relative path or None, which source was used)."""
    clear_stale_previews(window_id)

    steps = (
        [_try_og_image, _try_screenshot]
        if window_id in OG_IMAGE_FIRST_WINDOWS
        else [_try_screenshot, _try_og_image]
    )
    for step in steps:
        result = step(url, window_id)
        if result:
            return result

    return None, "card"


def main():
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print(
            "ERROR: ANTHROPIC_API_KEY is not set in the environment.\n"
            "Set it for this session, e.g. (PowerShell):\n"
            '  $env:ANTHROPIC_API_KEY = "sk-ant-..."\n'
            "then re-run this script. The key is never read from a file in this repo.",
            file=sys.stderr,
        )
        sys.exit(1)

    client = anthropic.Anthropic(api_key=api_key)
    taste_prompt = load_taste_prompt()
    today = datetime.date.today()
    wildcard_window = pick_wildcard_window(today)
    if wildcard_window:
        print(f"Today is a wildcard day for {wildcard_window}.")

    state = load_state()
    day_rng = random.Random(f"seedpick-{today.isoformat()}")

    # Start from whatever's already live, so a window that fails today keeps
    # showing yesterday's (still-good) pick instead of going blank. A bad
    # run should mean "today didn't update" for that window, never "today
    # erased it."
    per_window = {}
    if OUTPUT_PATH.exists():
        try:
            previous = json.loads(OUTPUT_PATH.read_text(encoding="utf-8"))
            per_window = previous.get("perWindow", {})
        except Exception as err:
            print(f"(could not read previous {OUTPUT_PATH}, starting empty: {err})", file=sys.stderr)

    history_rows = []
    for window_id, info in WINDOWS.items():
        is_wildcard = window_id == wildcard_window
        mode = "wildcard" if is_wildcard else pick_mode(window_id, today)

        seed_row = None
        seed_rows_for_window = None
        if mode == "seed":
            seed_rows_for_window = parse_seed_rows(load_seed_csv_text(info["seed_file"]))
            seed_row = peek_next_seed_row(window_id, seed_rows_for_window, state, day_rng)
            print(f"[{window_id}] {info['label']} -- seed pick ({seed_row['source']}), searching...")
        elif mode == "wildcard":
            print(f"[{window_id}] {info['label']} -- wildcard, searching...")
        else:
            print(f"[{window_id}] {info['label']} -- fresh find, searching...")

        system, user, tool = build_messages(window_id, info, taste_prompt, is_wildcard, mode, seed_row)

        try:
            result = call_claude(client, system, user, tool)
        except Exception as err:
            print(f"[{window_id}] first attempt failed ({err}), retrying once...", file=sys.stderr)
            try:
                result = call_claude(client, system, user, tool)
            except Exception as err2:
                kept = "keeping yesterday's pick" if window_id in per_window else "no previous pick to fall back to"
                print(f"[{window_id}] FAILED after retry: {err2} -- {kept}", file=sys.stderr)
                continue

        # Only now -- after a real, successful pick -- does this seed row
        # actually count as served.
        if seed_row is not None:
            mark_seed_row_used(window_id, seed_row, seed_rows_for_window, state)

        print(f"[{window_id}] pick: {result['title']} -> {result['url']}")
        preview_image, image_source = resolve_preview_image(result["url"], window_id)
        print(f"[{window_id}] preview image: {preview_image or '(none -- falling back to card)'} ({image_source})")

        per_window[window_id] = {
            "title": result["title"],
            "url": result["url"],
            "blurb": result["blurb"],
            "isWildcard": is_wildcard,
            "previewImage": preview_image,
        }
        history_rows.append(
            {
                "date": today.isoformat(),
                "window": window_id,
                "category": info["label"],
                "title": result["title"],
                "blurb": result["blurb"],
                "url": result["url"],
                "source": "seed" if mode == "seed" else "web",
                "isWildcard": is_wildcard,
                "image_source": image_source,
            }
        )

    save_state(state)
    output = {"generatedAt": today.isoformat(), "perWindow": per_window}
    OUTPUT_PATH.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
    succeeded = len(history_rows)
    print(f"\nWrote {OUTPUT_PATH} ({succeeded}/5 windows updated today, {len(per_window)}/5 total populated)")
    print(f"Wrote {STATE_PATH}")

    if history_rows:
        append_history(history_rows)
        print(f"Appended {len(history_rows)} row(s) to {HISTORY_PATH}")
    else:
        print("No windows succeeded today -- history.csv left untouched.")


if __name__ == "__main__":
    main()
