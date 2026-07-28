// DRVS site — wires the real package to the page.
//
// There is deliberately no demo-specific search code here. The page imports
// the same ui/reveal.js the README tells you to import, pointed at the same
// prebuilt demo index that examples/demo-corpus ships. If this file needed
// special-case logic to make the demo look good, the demo would be a lie.

import { createRevealUI, STRINGS_EN } from "/drvs/ui/reveal.js";

const ui = createRevealUI({
  root: ".demo",
  rowSelector: ".doc[data-doc-id]",
  reasonTarget: ".doc-title",
  workerUrl: "/drvs/ui/search-worker.js",
  configUrl: "/drvs/data/search.config.json",
  indexUrl: "/drvs/data/index.json",
  dictionaryUrl: "/drvs/data/dictionary.json",
  strings: STRINGS_EN,

  // The semantic channel is left OFF here, and that is a demonstration rather
  // than a limitation: it costs a ~25MB model download, and this page has no
  // business spending a visitor's bandwidth on one before they've asked for
  // anything. Everything below still ranks — which is exactly the "every
  // optional channel degrades structurally" claim, running live. The toggle
  // beneath turns it on.
});

// The control markup is created by createRevealUI() inside `root`; move it to
// the reserved slot so it sits above the corpus rather than at the end.
const slot = document.querySelector(".panel-slot");
const panel = document.querySelector(".drvs-panel");
if (slot && panel) slot.appendChild(panel);

// --- semantic channel toggle ------------------------------------------------
// Rebuilding the whole UI is the honest way to do this: the Worker reads its
// asset URLs once at init, so "turn the model on" genuinely is a fresh start,
// not a flag flip. destroy() puts every row back the way it found it first.
let current = ui;
let semanticOn = false;

const toggle = document.createElement("button");
toggle.type = "button";
toggle.className = "semantic-toggle";
toggle.setAttribute("aria-pressed", "false");

function renderToggle(state) {
  toggle.textContent =
    state === "loading" ? "Loading model…"
    : semanticOn ? "Semantic channel on — click to disable"
    : "Enable semantic channel (~25MB model, downloads once)";
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
    root: ".demo",
    rowSelector: ".doc[data-doc-id]",
    reasonTarget: ".doc-title",
    workerUrl: "/drvs/ui/search-worker.js",
    configUrl: "/drvs/data/search.config.json",
    indexUrl: "/drvs/data/index.json",
    dictionaryUrl: "/drvs/data/dictionary.json",
    strings: STRINGS_EN,
    ...(semanticOn ? {
      vectorsUrl: "/drvs/data/vectors.bin",
      vectorsMetaUrl: "/drvs/data/vectors-meta.json",
      chunksUrl: "/drvs/data/chunks.bin",
      chunksMetaUrl: "/drvs/data/chunks-meta.json",
    } : {}),
    onResult: () => renderToggle(),
    onError: () => renderToggle(),
  });

  const newPanel = document.querySelector(".drvs-panel");
  if (slot && newPanel) slot.appendChild(newPanel);
  document.querySelector(".semantic-row")?.appendChild(toggle);
  if (query) current.search(query);
  if (!semanticOn) renderToggle();
});

const semanticRow = document.createElement("div");
semanticRow.className = "semantic-row";
semanticRow.appendChild(toggle);
slot?.appendChild(semanticRow);

// --- example queries --------------------------------------------------------
for (const chip of document.querySelectorAll(".chip[data-q]")) {
  chip.addEventListener("click", () => {
    current.search(chip.getAttribute("data-q"));
    document.querySelector(".drvs-input")?.focus();
  });
}
