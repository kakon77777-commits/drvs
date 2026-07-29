// DRVS site — wires the real package to the page.
//
// There is deliberately no demo-specific search code here. The page imports
// the same ui/reveal.js the README tells you to import, pointed at the same
// prebuilt index that examples/demo-corpus ships. If this file needed
// special-case logic to make the demo look good, the demo would be a lie.
//
// One script serves both locales. Everything locale-dependent — where the
// corpus lives, which UI strings to use — is read off the demo root's data
// attributes, which the build writes per locale.

import { createRevealUI, STRINGS_EN } from "/drvs/ui/reveal.js";

const root = document.querySelector("[data-drvs-root]");
if (root) {
  const base = root.dataset.base || "/drvs/data";
  const locale = root.dataset.locale || "en";

  // zh-Hant is the package's built-in default, so it needs no override — the
  // English set is the one that has to be supplied explicitly.
  const strings = locale === "en" ? STRINGS_EN : undefined;

  const baseOptions = {
    root: ".demo",
    rowSelector: ".doc[data-doc-id]",
    reasonTarget: ".doc-title",
    workerUrl: "/drvs/ui/search-worker.js",
    configUrl: `${base}/search.config.json`,
    indexUrl: `${base}/index.json`,
    dictionaryUrl: `${base}/dictionary.json`,
    strings,
  };

  // The semantic channel starts OFF, and that is a demonstration rather than
  // a limitation: it costs a ~25MB model download, and this page has no
  // business spending a visitor's bandwidth on one before they have asked for
  // anything. The other four channels still rank — which is the "every
  // optional channel degrades structurally" claim, running live.
  const vectorOptions = {
    vectorsUrl: `${base}/vectors.bin`,
    vectorsMetaUrl: `${base}/vectors-meta.json`,
    chunksUrl: `${base}/chunks.bin`,
    chunksMetaUrl: `${base}/chunks-meta.json`,
  };

  const slot = document.querySelector(".panel-slot");
  const movePanel = () => {
    const panel = document.querySelector(".drvs-panel");
    if (slot && panel) slot.appendChild(panel);
  };

  let current = createRevealUI(baseOptions);
  movePanel();

  // --- semantic channel toggle --------------------------------------------
  // Rebuilding the whole UI is the honest way to do this: the Worker reads its
  // asset URLs once at init, so "turn the model on" genuinely is a fresh
  // start, not a flag flip. destroy() puts every row back as it found it.
  let semanticOn = false;
  const toggle = document.createElement("button");
  toggle.type = "button";
  toggle.className = "semantic-toggle";

  const LABEL = {
    off: root.dataset.semanticOff || "Enable semantic channel",
    on: root.dataset.semanticOn || "Semantic channel on — click to disable",
    loading: root.dataset.semanticLoading || "Loading model…",
  };

  function renderToggle(state) {
    toggle.textContent = state === "loading" ? LABEL.loading : semanticOn ? LABEL.on : LABEL.off;
    toggle.setAttribute("aria-pressed", String(semanticOn));
    toggle.disabled = state === "loading";
  }
  renderToggle();

  toggle.addEventListener("click", () => {
    semanticOn = !semanticOn;
    renderToggle(semanticOn ? "loading" : undefined);

    const query = document.querySelector(".drvs-input")?.value || "";
    current.destroy();
    document.querySelector(".drvs-panel")?.remove();

    current = createRevealUI({
      ...baseOptions,
      ...(semanticOn ? vectorOptions : {}),
      onResult: () => renderToggle(),
      onError: () => renderToggle(),
    });

    movePanel();
    semanticRow.appendChild(toggle);
    if (query) current.search(query);
    if (!semanticOn) renderToggle();
  });

  const semanticRow = document.createElement("div");
  semanticRow.className = "semantic-row";
  semanticRow.appendChild(toggle);
  slot?.appendChild(semanticRow);

  // --- example queries ----------------------------------------------------
  for (const chip of document.querySelectorAll(".chip[data-q]")) {
    chip.addEventListener("click", () => {
      current.search(chip.getAttribute("data-q"));
      document.querySelector(".drvs-input")?.focus();
    });
  }
}
