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

    if (!pick || !pick.title || !pick.url) {
      card.className = "pick-card pick-card--empty";
      card.innerHTML = `<p class="pick-placeholder">✨ new pick coming soon</p>`;
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

    const chrome =
      kind === "screen"
        ? `<div class="screen-chrome"><span></span><span></span><span></span></div>`
        : "";

    const image = hasImage
      ? `<img class="pick-image" src="${escapeHtml(pick.previewImage)}" alt="" loading="lazy">
         <div class="pick-scrim"></div>`
      : "";

    const content = `
      ${chrome}
      <div class="pick-body">
        <p class="pick-title">${escapeHtml(pick.title)}</p>
        <p class="pick-blurb">${escapeHtml(pick.blurb || "")}</p>
      </div>
    `;

    // The phone screen isn't actually a rectangle in the artwork (it's drawn
    // at a slight perspective skew), so the card is clipped to its true
    // quadrilateral via clip-path. The image fills that full clipped shape;
    // only the text (chrome + body) goes inside the separately-rotated
    // phone-content wrapper, so the photo isn't squeezed into that smaller box.
    card.innerHTML =
      shape === "phone" ? `${image}<div class="phone-content">${content}</div>` : `${image}${content}`;

    if (hasImage) {
      const img = card.querySelector(".pick-image");
      img.addEventListener("error", () => {
        // Share image URL didn't actually resolve to a loadable image --
        // drop back to the plain step-2 card instead of showing a broken img.
        card.classList.remove("pick-card--has-image");
        img.remove();
        card.querySelector(".pick-scrim")?.remove();
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
