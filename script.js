async function loadPicks() {
  const windows = document.querySelectorAll(".window");

  let data = null;
  try {
    const res = await fetch("today.json", { cache: "no-store" });
    if (res.ok) data = await res.json();
  } catch (err) {
    console.warn("Could not load today.json:", err);
  }

  const perWindow = (data && data.perWindow) || {};

  windows.forEach((el) => {
    const key = el.dataset.window;
    const kind = el.dataset.kind;
    const shape = el.dataset.shape; // "phone" needs a clipped shape, see below
    const pick = perWindow[key];
    const card = el.querySelector(".pick-card");

    // A window is a clean preview of the site, not a recommendation card --
    // no title, no blurb, no placeholder copy, ever. Title and blurb are
    // still generated and still land in today.json/history.csv as the
    // curation record; they just never render here. The chrome dots (a
    // browser-window visual, not text) are the only thing screen-style
    // windows show besides the image itself.
    const chrome =
      kind === "screen"
        ? `<div class="screen-chrome"><span></span><span></span><span></span></div>`
        : "";

    if (!pick || !pick.title || !pick.url) {
      card.className = "pick-card pick-card--empty";
      card.innerHTML = chrome;
      el.removeAttribute("href");
      el.style.cursor = "default";
      return;
    }

    el.href = pick.url;
    const hasImage = Boolean(pick.previewImage);
    card.className = `pick-card pick-card--${kind}${shape ? ` pick-card--${shape}` : ""}${
      hasImage ? " pick-card--has-image" : ""
    }`;

    // isWildcard is kept in the data (useful for the automation's own logic,
    // e.g. pacing how often it picks one) but deliberately not shown in the UI.

    // Preview images live at a fixed filename per window (e.g.
    // assets/previews/window-4.webp) that gets overwritten in place every
    // day, so browsers/CDNs can hold onto yesterday's cached bytes under
    // that same URL well past a normal refresh. Busting on generatedAt (the
    // one thing that reliably changes once a day) forces a fresh fetch
    // whenever today.json actually changes, without needing a real
    // cache-control change on the static host.
    const imageSrc = hasImage
      ? `${pick.previewImage}?v=${encodeURIComponent(data.generatedAt || "")}`
      : "";
    const image = hasImage
      ? `<img class="pick-image" src="${escapeHtml(imageSrc)}" alt="${escapeHtml(pick.title)}" loading="lazy">`
      : "";

    // The phone screen isn't actually a rectangle in the artwork (it's drawn
    // at a slight perspective skew), so the card is clipped to its true
    // quadrilateral via clip-path. Both the chrome dots AND the image go
    // inside the separately-rotated/skewed phone-content wrapper (captured
    // at a true phone aspect ratio -- see WINDOW_SCREENSHOT_VIEWPORT in the
    // generator), so the image lands at the phone's actual tilt instead of
    // showing through the clip path unrotated.
    card.innerHTML =
      shape === "phone" ? `<div class="phone-content">${image}${chrome}</div>` : `${image}${chrome}`;

    if (hasImage) {
      const img = card.querySelector(".pick-image");
      img.addEventListener("error", () => {
        // Share image URL didn't actually resolve to a loadable image --
        // drop back to the plain card instead of showing a broken img.
        card.classList.remove("pick-card--has-image");
        img.remove();
      });
    }
  });
}

function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

loadPicks();
