// DRVS — Dynamic Revealing Vector Search
// ui/reveal.js: the *revealing* half of the system — the main-thread
// controller that turns a scored result set into a visible state change on a
// page that already exists.
//
// This is the part the package is named after, and it is deliberately NOT a
// "results page". The defining behavior is REVERSE MASKING: the host's own
// list (a timeline, an archive, a table of contents) stays exactly where it
// is, in its own order, and a query only changes how visible each row is.
// Nothing is moved, re-sorted into a detached result list, or removed from
// the DOM. Clearing the query restores the original page byte-for-byte.
//
// Everything here is additive and structurally optional:
//   - If this module never loads, the host page is untouched and still fully
//     readable — no URL changes, no row removed, no dependency on JS to read
//     the underlying content.
//   - If the Worker can't start (blocked, unsupported, 404), the control
//     panel hides itself and the page underneath keeps working.
//   - If the config file is missing, built-in defaults apply.
//   - A single config flag (`drvs_enabled: false`) is a full kill switch that
//     returns the page to its original state with no code change.
//
// createRevealUI(options) returns an independent controller — no module-level
// singleton state — so one page can drive more than one index if it needs to.

import { DEFAULT_CONFIG } from "../core/scoring.js";

// Per-tier quality descriptor. Deliberately mechanism-agnostic: the specific
// WHY (title match, lexical overlap, same-series relation, matched paragraph)
// comes from the reason's own label rendered right after this prefix. A
// document can land in Tier D via weak lexical similarity OR a confirmed
// relation match, so this prefix must not presume which one it was.
export const STRINGS_ZH_HANT = {
  placeholder: "動態顯影搜尋 — 標題、章節、關鍵詞…",
  inputLabel: "搜尋語料庫",
  reveal: "顯影",
  focus: "只看結果",
  reset: "重設",
  clear: "清除",
  tierA: "A｜精確",
  tierB: "B｜近似",
  tierC: "C｜弱關聯",
  tierD: "D｜低相關",
  reasonSeparator: "：",
  // Shown when a row is only present to satisfy the minimum-results guarantee
  // and has no matching reason of its own. Without this, such a row renders
  // with a tier badge and no explanation — which is precisely the opaque
  // "trust me" result this package exists to avoid.
  paddingReason: "無直接命中，為維持最低結果數而列出",
  indexed: (n) => `${n} 篇索引`,
  latency: (ms) => `${ms}ms`,
  expansionsPrefix: "查詢展開",
  aliases: "別名",
  relatedTerms: "相關詞",
  termJoin: "、",
  partJoin: " ｜ ",
  valueSeparator: "：",
  relaxed: "直接命中較少，已加入詞彙與語義近似結果。",
  lowConfidence: "沒有找到高可信度直接結果。以下僅列出語義上最接近的文件。",
  emptyIndex: "索引中沒有可用結果。請嘗試較短詞組、別名或移除限制條件。",
  loadError: "索引載入失敗，搜尋暫時無法使用（原始內容不受影響）。",
};

export const STRINGS_EN = {
  placeholder: "Search — titles, sections, keywords…",
  inputLabel: "Search the corpus",
  reveal: "Reveal",
  focus: "Results only",
  reset: "Reset",
  clear: "Clear",
  tierA: "A · exact",
  tierB: "B · close",
  tierC: "C · weak",
  tierD: "D · distant",
  reasonSeparator: " — ",
  paddingReason: "no direct match; listed to meet the minimum result count",
  indexed: (n) => `${n} indexed`,
  latency: (ms) => `${ms}ms`,
  expansionsPrefix: "Query expanded",
  aliases: "aliases",
  relatedTerms: "related",
  termJoin: ", ",
  partJoin: "  ·  ",
  valueSeparator: ": ",
  relaxed: "Few direct hits — lexical and semantic matches were included.",
  lowConfidence: "No high-confidence direct match. Showing the semantically closest documents only.",
  emptyIndex: "No results available in the index. Try a shorter phrase, an alias, or fewer constraints.",
  loadError: "Index failed to load; search is unavailable (the page content itself is unaffected).",
};

const DEFAULTS = {
  root: "[data-drvs-root]",
  rowSelector: "[data-doc-id]",
  rowScope: null,          // defaults to `document`
  groupSelector: null,     // e.g. "section" — hidden in focus mode once every row inside is hidden
  reasonTarget: null,      // selector *within* a row; defaults to the row itself
  workerUrl: "/drvs/search-worker.js",
  configUrl: "/drvs/search.config.json",
  // Asset URLs forwarded to the Worker on init (a Worker script can't take
  // constructor arguments). Only `indexUrl` is required; every other channel's
  // assets are optional and their absence disables just that channel.
  indexUrl: "/drvs/index.json",
  dictionaryUrl: "/drvs/dictionary.json",
  vectorsUrl: null,
  vectorsMetaUrl: null,
  chunksUrl: null,
  chunksMetaUrl: null,
  modelName: undefined,
  libraryUrl: undefined,
  rawFloor: undefined,
  rawCeiling: undefined,
  createWorker: null,      // (url) => Worker — override for bundlers with their own worker syntax
  searchFn: null,          // async (query) => result — bypasses the Worker entirely (SSR/tests/no-worker hosts)
  strings: null,
  renderControls: true,    // build the control markup if the root doesn't already contain it
  onResult: null,
  onError: null,
};

const TIER_KEYS = { A: "tierA", B: "tierB", C: "tierC", D: "tierD" };

function textNode(s) {
  return document.createTextNode(s == null ? "" : String(s));
}

// All host-supplied and index-supplied text goes through textNode/textContent,
// never innerHTML. A document title in the index is untrusted input as far as
// this module is concerned — an index built from arbitrary markdown can carry
// anything, and a search UI that injects it as HTML is an XSS hole.
function clearNode(el) {
  while (el && el.firstChild) el.removeChild(el.firstChild);
}

function el(tag, className, attrs) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (attrs) for (const k of Object.keys(attrs)) node.setAttribute(k, attrs[k]);
  return node;
}

function mergeConfig(base, loaded) {
  if (!loaded) return base;
  const out = { ...base, ...loaded };
  for (const key of ["search", "tiers", "weights", "channels", "expansion_limits", "diversity", "display"]) {
    out[key] = { ...(base[key] || {}), ...(loaded[key] || {}) };
  }
  return out;
}

/**
 * Build the control markup inside `root` when the host hasn't hand-authored
 * it. Keeps the package drop-in for a plain static site while still letting a
 * host that wants full control over its own markup supply the elements itself
 * (matching class names) and skip this entirely.
 */
function buildControls(root, S) {
  const panel = el("div", "drvs-panel");

  const row = el("div", "drvs-row");
  const wrap = el("div", "drvs-input-wrap");
  const input = el("input", "drvs-input", {
    type: "text",
    placeholder: S.placeholder,
    "aria-label": S.inputLabel,
    autocomplete: "off",
    spellcheck: "false",
  });
  const clear = el("button", "drvs-clear", { type: "button", "aria-label": S.clear });
  clear.appendChild(textNode("×"));
  wrap.appendChild(input);
  wrap.appendChild(clear);
  row.appendChild(wrap);

  const revealBtn = el("button", "drvs-mode-btn", { type: "button", "data-mode": "reveal", "aria-pressed": "true" });
  revealBtn.appendChild(textNode(S.reveal));
  const focusBtn = el("button", "drvs-mode-btn", { type: "button", "data-mode": "focus", "aria-pressed": "false" });
  focusBtn.appendChild(textNode(S.focus));
  const resetBtn = el("button", "drvs-reset-btn", { type: "button" });
  resetBtn.appendChild(textNode(S.reset));
  row.appendChild(revealBtn);
  row.appendChild(focusBtn);
  row.appendChild(resetBtn);

  panel.appendChild(row);
  panel.appendChild(el("div", "drvs-meta", { "aria-live": "polite" }));
  panel.appendChild(el("div", "drvs-expansions"));
  panel.appendChild(el("div", "drvs-banner", { role: "status" }));

  root.appendChild(panel);
}

export function createRevealUI(userOptions) {
  const opts = { ...DEFAULTS, ...(userOptions || {}) };
  const S = { ...STRINGS_ZH_HANT, ...(opts.strings || {}) };

  const root = typeof opts.root === "string" ? document.querySelector(opts.root) : opts.root;
  if (!root) return null; // this page doesn't opt in to the search layer

  const scope = opts.rowScope
    ? (typeof opts.rowScope === "string" ? document.querySelector(opts.rowScope) : opts.rowScope)
    : document;
  const rows = Array.prototype.slice.call((scope || document).querySelectorAll(opts.rowSelector));
  if (!rows.length) return null; // nothing to reveal — leave the page alone

  if (opts.renderControls && !root.querySelector(".drvs-input")) buildControls(root, S);

  const input = root.querySelector(".drvs-input");
  if (!input) return null;
  const inputWrap = root.querySelector(".drvs-input-wrap");
  const clearBtn = root.querySelector(".drvs-clear");
  const resetBtn = root.querySelector(".drvs-reset-btn");
  const modeBtns = Array.prototype.slice.call(root.querySelectorAll(".drvs-mode-btn"));
  const metaEl = root.querySelector(".drvs-meta");
  const expansionsEl = root.querySelector(".drvs-expansions");
  const bannerEl = root.querySelector(".drvs-banner");

  const groups = opts.groupSelector
    ? Array.prototype.slice.call(document.querySelectorAll(opts.groupSelector))
    : [];

  let config = DEFAULT_CONFIG;
  let debounceMs = DEFAULT_CONFIG.debounce_ms || 300;
  let worker = null;
  let reqSeq = 0;
  let latestReqId = 0;
  let ready = false;
  let pendingQuery = null;
  let destroyed = false;
  let debounceTimer = null;

  // ---------------------------------------------------------------- reveal

  function resetRows() {
    for (const r of rows) {
      r.classList.remove("drvs-tier-a", "drvs-tier-b", "drvs-tier-c", "drvs-tier-d", "drvs-hidden");
      r.removeAttribute("data-drvs-score");
      r.removeAttribute("data-drvs-tier");
      r.removeAttribute("data-drvs-visible");
      const reason = r.querySelector(".drvs-reason");
      if (reason) reason.remove();
    }
    for (const g of groups) g.classList.remove("drvs-empty-group");
  }

  function showBanner(text, kind) {
    if (!bannerEl) return;
    clearNode(bannerEl);
    if (!text) {
      bannerEl.classList.remove("show", "low");
      return;
    }
    bannerEl.appendChild(textNode(text));
    bannerEl.classList.add("show");
    bannerEl.classList.toggle("low", kind === "low");
  }

  // A query that names a known concept shows what it was ALSO expanded to
  // search for, so a hit that only matched via an alias or a related term is
  // never a silent surprise to the reader.
  function renderExpansions(expansions) {
    if (!expansionsEl) return;
    clearNode(expansionsEl);
    const aliases = (expansions && expansions.aliases) || [];
    const related = (expansions && expansions.related) || [];
    if (!aliases.length && !related.length) {
      expansionsEl.classList.remove("show");
      return;
    }
    const parts = [];
    if (aliases.length) parts.push(`${S.aliases}${S.valueSeparator}${aliases.map((a) => a.term).join(S.termJoin)}`);
    if (related.length) parts.push(`${S.relatedTerms}${S.valueSeparator}${related.map((r) => r.term).join(S.termJoin)}`);
    expansionsEl.appendChild(textNode(`${S.expansionsPrefix} — ${parts.join(S.partJoin)}`));
    expansionsEl.classList.add("show");
  }

  function applyResult(result, latencyMs) {
    resetRows();

    if (!result || result.empty_query) {
      showBanner("");
      renderExpansions(null);
      if (metaEl) metaEl.textContent = "";
      return;
    }

    const byId = new Map();
    for (const r of result.results || []) byId.set(String(r.id), r);

    const tierCounts = { A: 0, B: 0, C: 0, D: 0 };
    for (const row of rows) {
      const id = row.getAttribute("data-doc-id");
      const hit = byId.get(String(id));
      if (!hit) {
        row.classList.add("drvs-hidden");
        row.setAttribute("data-drvs-visible", "false");
        continue;
      }
      row.classList.add("drvs-tier-" + String(hit.tier).toLowerCase());
      row.setAttribute("data-drvs-score", hit.score.toFixed(2));
      row.setAttribute("data-drvs-tier", hit.tier);
      row.setAttribute("data-drvs-visible", "true");
      tierCounts[hit.tier] = (tierCounts[hit.tier] || 0) + 1;

      // Label with the document's REAL final tier (from the scorer's own
      // threshold assignment), not the reason object's own `tier` field. A
      // reason records a hardcoded guess at its tier that predates any
      // document-level score adjustment (a relation floor, a semantic channel
      // win), so the two genuinely can disagree — and when they do, the badge
      // the reader sees must match the row's actual ranking, not the guess.
      // The descriptive text still comes from the reason itself; only the tier
      // badge is corrected.
      //
      // A row with NO reason is not skipped: that happens when the
      // minimum-results guarantee pads the list with the best of a bad bunch,
      // and silently rendering it with a tier and no explanation would be the
      // exact opaque result this package exists to avoid. It gets an explicit
      // "this is padding" label instead.
      const top = (hit.reasons && hit.reasons.length) ? hit.reasons[0] : null;
      const span = el("span", "drvs-reason");
      if (!top) span.classList.add("drvs-reason-padding");
      span.appendChild(textNode(
        `· ${S[TIER_KEYS[hit.tier]] || hit.tier}${S.reasonSeparator}${top ? top.label : S.paddingReason}`
      ));
      const target = opts.reasonTarget ? row.querySelector(opts.reasonTarget) : null;
      (target || row).appendChild(span);
    }

    for (const g of groups) {
      const groupRows = g.querySelectorAll(opts.rowSelector);
      if (!groupRows.length) continue;
      let allHidden = true;
      for (const gr of groupRows) {
        if (!gr.classList.contains("drvs-hidden")) { allHidden = false; break; }
      }
      g.classList.toggle("drvs-empty-group", allHidden);
    }

    if (metaEl) {
      const stats = [
        S.indexed(result.total_candidates || 0),
        `A:${tierCounts.A} B:${tierCounts.B} C:${tierCounts.C} D:${tierCounts.D}`,
      ];
      if (typeof latencyMs === "number") stats.push(S.latency(latencyMs));
      metaEl.textContent = stats.join(" · ");
    }
    renderExpansions(result.expansions);

    // Order matters: an honestly-empty index is a different statement from a
    // low-confidence match, which is different again from a relaxed threshold.
    // Never collapse these into one vague "no good results" message — the
    // whole contract of this UI is that the reader can tell which situation
    // they're actually in.
    if (result.empty_index) showBanner(S.emptyIndex, "low");
    else if (result.low_confidence) showBanner(S.lowConfidence, "low");
    else if (result.relaxed) showBanner(S.relaxed, "");
    else showBanner("");

    if (typeof opts.onResult === "function") opts.onResult(result);
  }

  // ---------------------------------------------------------------- worker

  function startWorker() {
    if (typeof opts.searchFn === "function") { ready = true; return; }
    try {
      worker = opts.createWorker
        ? opts.createWorker(opts.workerUrl)
        : new Worker(opts.workerUrl, { type: "module" });
    } catch (e) {
      // No worker => no search. Hide the control panel rather than leave a
      // dead input on the page; the host content itself stays fully intact.
      root.style.display = "none";
      if (typeof opts.onError === "function") opts.onError(e);
      return;
    }
    worker.onmessage = (e) => {
      const msg = e.data || {};
      if (msg.type === "ready") {
        ready = true;
        if (msg.meta && typeof msg.meta.count === "number") {
          root.setAttribute("data-doc-count", String(msg.meta.count));
        }
        if (pendingQuery !== null) {
          const q = pendingQuery;
          pendingQuery = null;
          runSearch(q);
        }
        return;
      }
      if (msg.type === "result") {
        // Stale-reply guard: a newer keystroke already superseded this
        // request, so its (slower) answer must not overwrite the fresher one.
        if (msg.reqId !== latestReqId) return;
        applyResult(msg.result, msg.latency_ms);
        return;
      }
      if (msg.type === "error") {
        if (metaEl) metaEl.textContent = "";
        showBanner(S.loadError, "low");
        if (typeof opts.onError === "function") opts.onError(new Error(msg.error));
      }
    };
    worker.postMessage({
      type: "init",
      urls: {
        indexUrl: opts.indexUrl,
        configUrl: opts.configUrl,
        dictionaryUrl: opts.dictionaryUrl,
        vectorsUrl: opts.vectorsUrl,
        vectorsMetaUrl: opts.vectorsMetaUrl,
        chunksUrl: opts.chunksUrl,
        chunksMetaUrl: opts.chunksMetaUrl,
        modelName: opts.modelName,
        libraryUrl: opts.libraryUrl,
        rawFloor: opts.rawFloor,
        rawCeiling: opts.rawCeiling,
      },
    });
  }

  async function runSearch(query) {
    if (destroyed) return;
    if (typeof opts.searchFn === "function") {
      const reqId = ++reqSeq;
      latestReqId = reqId;
      const t0 = Date.now();
      try {
        const result = await opts.searchFn(query);
        if (reqId !== latestReqId) return; // superseded while awaiting
        applyResult(result, Date.now() - t0);
      } catch (e) {
        showBanner(S.loadError, "low");
        if (typeof opts.onError === "function") opts.onError(e);
      }
      return;
    }
    if (!ready) { pendingQuery = query; return; }
    latestReqId = ++reqSeq;
    worker.postMessage({ type: "search", query, reqId: latestReqId });
  }

  function fullReset() {
    input.value = "";
    if (inputWrap) inputWrap.classList.remove("has-query");
    resetRows();
    showBanner("");
    renderExpansions(null);
    if (metaEl) metaEl.textContent = "";
    latestReqId = ++reqSeq; // invalidate any in-flight reply
  }

  function setMode(mode) {
    for (const b of modeBtns) {
      b.setAttribute("aria-pressed", String(b.getAttribute("data-mode") === mode));
    }
    document.body.classList.toggle("drvs-focus", mode === "focus");
  }

  // ----------------------------------------------------------------- wiring

  function scheduleSearch() {
    const q = input.value;
    if (inputWrap) inputWrap.classList.toggle("has-query", q.length > 0);
    if (debounceTimer) clearTimeout(debounceTimer);
    if (!q.trim()) { fullReset(); return; }
    debounceTimer = setTimeout(() => runSearch(q), debounceMs);
  }

  function onKeydown(e) {
    if (e.key === "Enter") {
      if (debounceTimer) clearTimeout(debounceTimer);
      const q = input.value;
      if (q.trim()) runSearch(q); else fullReset();
    } else if (e.key === "Escape") {
      fullReset();
      input.blur();
    }
  }

  const onClearClick = () => { fullReset(); input.focus(); };
  const modeHandlers = [];

  input.addEventListener("input", scheduleSearch);
  input.addEventListener("keydown", onKeydown);
  if (clearBtn) clearBtn.addEventListener("click", onClearClick);
  if (resetBtn) resetBtn.addEventListener("click", fullReset);
  for (const btn of modeBtns) {
    const handler = () => setMode(btn.getAttribute("data-mode"));
    btn.addEventListener("click", handler);
    modeHandlers.push([btn, handler]);
  }

  // Boot: load config (for the kill switch, debounce, and max query length),
  // then start. A missing/broken config is never fatal — defaults still work.
  const boot = opts.configUrl
    ? fetch(opts.configUrl).then((r) => (r.ok ? r.json() : null)).catch(() => null)
    : Promise.resolve(null);

  boot.then((loaded) => {
    if (destroyed) return;
    if (loaded && loaded.drvs_enabled === false) {
      root.style.display = "none"; // single-flag rollback: page returns to its original state
      return;
    }
    config = mergeConfig(DEFAULT_CONFIG, loaded);
    if (config.debounce_ms) debounceMs = config.debounce_ms;
    if (config.max_query_length) input.setAttribute("maxlength", String(config.max_query_length));
    setMode((config.display && config.display.default_mode) || "reveal");
    startWorker();
  });

  return {
    element: root,
    get config() { return config; },
    search: (q) => { input.value = q; if (inputWrap) inputWrap.classList.toggle("has-query", !!q); return runSearch(q); },
    reset: fullReset,
    setMode,
    destroy() {
      destroyed = true;
      if (debounceTimer) clearTimeout(debounceTimer);
      input.removeEventListener("input", scheduleSearch);
      input.removeEventListener("keydown", onKeydown);
      if (clearBtn) clearBtn.removeEventListener("click", onClearClick);
      if (resetBtn) resetBtn.removeEventListener("click", fullReset);
      for (const [btn, handler] of modeHandlers) btn.removeEventListener("click", handler);
      if (worker) { worker.terminate(); worker = null; }
      resetRows();
      document.body.classList.remove("drvs-focus");
    },
  };
}
