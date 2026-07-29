// DRVS test suite — core/scoring.js
//
// Runs against the demo corpus (examples/demo-corpus) — real, if tiny, data
// that has nothing to do with the project DRVS was distilled from, so a
// passing suite here is real evidence of genericity, not just "it imports
// without crashing." No external test framework — hand-rolled PASS/FAIL,
// same style as the reference deployment's own test suite, to keep DRVS's
// own test suite dependency-free too.
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import {
  normalizeQuery, tokenize, trigrams, expandQuery, prepareIndex,
  scoreDocument, assignTier, diversityRerank, titlePrefixKey, scoreCorpus,
  DEFAULT_CONFIG, DEFAULT_TIERS, DEFAULT_LABELS, LABELS_EN,
  DEFAULT_RELATION_TYPES, RELATION_TYPES_EN,
} from "../core/scoring.js";

const __dirname = dirname(fileURLToPath(import.meta.url));
const DEMO_DIST = join(__dirname, "..", "examples", "demo-corpus", "dist", "zh-Hant");
const DEMO_DIST_EN = join(__dirname, "..", "examples", "demo-corpus", "dist", "en");

let pass = 0, fail = 0;
function check(category, label, cond, detail) {
  if (cond) { pass++; console.log(`[PASS] (${category}) ${label}`); }
  else { fail++; console.log(`[FAIL] (${category}) ${label}${detail ? "\n       " + detail : ""}`); }
}

// --- normalizeQuery / tokenize / trigrams ---------------------------------
check("normalize", "NFKC-folds fullwidth to halfwidth", normalizeQuery("Ａ１") === "a1");
check("normalize", "lowercases", normalizeQuery("ABC") === "abc");
check("normalize", "collapses whitespace", normalizeQuery("a   b\n\tc") === "a b c");
check("normalize", "null/undefined -> empty string", normalizeQuery(null) === "" && normalizeQuery(undefined) === "");

check("tokenize", "CJK run becomes overlapping bigrams", JSON.stringify(tokenize("番茄")) === JSON.stringify(["番茄"]));
check("tokenize", "3-char CJK run becomes 2 overlapping bigrams", JSON.stringify(tokenize("咖啡豆")) === JSON.stringify(["咖啡", "啡豆"]));
check("tokenize", "Latin run becomes one whole token", JSON.stringify(tokenize("abc")) === JSON.stringify(["abc"]));
check("tokenize", "mixed CJK+Latin splits at the boundary", JSON.stringify(tokenize("abc番茄")) === JSON.stringify(["abc", "番茄"]));

check("trigrams", "short string (<3 chars) returns itself as one gram", JSON.stringify(trigrams("ab")) === JSON.stringify(["ab"]));
check("trigrams", "empty string returns no grams", trigrams("").length === 0);
check("trigrams", "4-char string returns 2 overlapping trigrams", trigrams("abcd").length === 2);

// --- expandQuery -----------------------------------------------------------
const DICT = [
  { concept_id: "c1", canonical: "酪梨", aliases: [{ term: "牛油果", weight: 0.95 }], related: [{ term: "水果", weight: 0.7 }] },
  { concept_id: "c2", canonical: "低信心詞", aliases: [{ term: "不該出現", weight: 0.3 }], related: [] },
];
{
  const byAlias = expandQuery(normalizeQuery("牛油果"), DICT, undefined);
  check("expandQuery", "alias query expands to canonical + itself excluded", byAlias.aliases.some((a) => a.term === "酪梨") && !byAlias.aliases.some((a) => a.term === "牛油果"));
  const byCanonical = expandQuery(normalizeQuery("酪梨"), DICT, undefined);
  check("expandQuery", "canonical query expands to its alias, not itself", byCanonical.aliases.some((a) => a.term === "牛油果"));
  check("expandQuery", "related terms come through with weight", byCanonical.related.some((r) => r.term === "水果"));
  const lowConfidence = expandQuery(normalizeQuery("低信心詞"), DICT, undefined);
  check("expandQuery", "below min_expansion_confidence alias is excluded", !lowConfidence.aliases.some((a) => a.term === "不該出現"));
  check("expandQuery", "empty dictionary never throws", JSON.stringify(expandQuery("x", [], undefined)) === JSON.stringify({ aliases: [], related: [] }));
  check("expandQuery", "empty query never throws", JSON.stringify(expandQuery("", DICT, undefined)) === JSON.stringify({ aliases: [], related: [] }));
}

// --- assignTier --------------------------------------------------------------
check("tiers", "score at tier_A boundary assigns A", assignTier(DEFAULT_TIERS.tier_A, DEFAULT_TIERS) === "A");
check("tiers", "score just below tier_A assigns B", assignTier(DEFAULT_TIERS.tier_A - 0.001, DEFAULT_TIERS) === "B");
check("tiers", "score below tier_D assigns null", assignTier(DEFAULT_TIERS.tier_D - 0.001, DEFAULT_TIERS) === null);

// --- titlePrefixKey ------------------------------------------------------
check("diversity", "shared lineage prefix groups near-duplicate titles", titlePrefixKey("陽台番茄種植入門：基礎篇") === titlePrefixKey("陽台番茄種植入門：進階篇"));
check("diversity", "unrelated titles never share a prefix key", titlePrefixKey("酪梨挑選") !== titlePrefixKey("咖啡保存"));

// --- diversityRerank (synthetic, isolates one quota behavior at a time) --
{
  const cfg = { diversity: { max_per_series_top_10: 2, max_same_title_prefix_top_10: 2 } };
  const mk = (id, score, series, title) => ({ id, score, doc: { p: series, t: title || id } });

  const sameSeries = [1, 2, 3, 4, 5, 6].map((n) => mk(`s${n}`, 1 - n * 0.01, "seriesA"));
  const reranked = diversityRerank(sameSeries, cfg);
  check("diversity", "series quota caps same-series docs in the head", reranked.slice(0, 2).every((r) => ["s1", "s2"].includes(r.id)));
  check("diversity", "demoted same-series docs are held, not dropped", reranked.length === sameSeries.length);

  const small = [1, 2, 3].map((n) => mk(`x${n}`, 1 - n * 0.01, "onlySeries"));
  check("diversity", "backfills past quota when too few candidates exist", diversityRerank(small, cfg).length === 3);

  check("diversity", "no diversity config -> results pass through unchanged", diversityRerank(sameSeries, {}).length === sameSeries.length);
}

// --- scoreCorpus integration, against the real (if tiny) demo corpus -----
const index = JSON.parse(readFileSync(join(DEMO_DIST, "index.json"), "utf-8"));
const dictionary = JSON.parse(readFileSync(join(DEMO_DIST, "dictionary.json"), "utf-8")).entries;
const documents = prepareIndex(index.documents.map((d) => ({ ...d }))); // fresh copy per run

check("scoreCorpus", "demo index loaded with the expected document count", documents.length === index.count && documents.length > 0);

{
  const exact = scoreCorpus(documents, "陽台番茄種植入門", DEFAULT_CONFIG, dictionary);
  check("scoreCorpus", "exact title query's top hit is the right document", exact.results[0]?.id === "balcony-tomato-basics");
  check("scoreCorpus", "exact title hit lands in tier A", exact.results[0]?.tier === "A");
  check("scoreCorpus", "exact match is not reported as low_confidence", exact.low_confidence === false);
}
{
  const alias = scoreCorpus(documents, "牛油果", DEFAULT_CONFIG, dictionary);
  check("scoreCorpus", "dictionary alias resolves to the correct document", alias.results[0]?.id === "avocado-ripeness");
  check("scoreCorpus", "alias hit is tagged with the alias channel", alias.results[0]?.channels.includes("alias"));
}
{
  const heading = scoreCorpus(documents, "支架與側芽", DEFAULT_CONFIG, dictionary);
  const pests = heading.results.find((r) => r.id === "balcony-tomato-pests");
  check("scoreCorpus", "same-series companion rides in via the relation channel, not a direct hit", !!pests && pests.channels.includes("relation") && !pests.channels.includes("exact"));
}
{
  const crossDomain = scoreCorpus(documents, "護照申請流程", DEFAULT_CONFIG, dictionary);
  check("scoreCorpus", "an unrelated query is honestly disclosed as low-confidence", crossDomain.low_confidence === true);
  check("scoreCorpus", "an unrelated query still returns the minimum result count (non-zero-results guarantee)", crossDomain.results.length >= DEFAULT_CONFIG.search.minimum_results);
}
check("scoreCorpus", "empty query returns empty results without throwing", scoreCorpus(documents, "", DEFAULT_CONFIG, dictionary).empty_query === true);
check("scoreCorpus", "empty document list returns empty_index without throwing", scoreCorpus([], "anything", DEFAULT_CONFIG, dictionary).empty_index === true);

// --- semantic channel fusion (synthetic Map, same contract client/vector- --
// client.js promises: Map<docIndex, {score, source, heading}>) ------------
{
  const targetIdx = 3; // arbitrary fixed index unrelated to the nonsense query below
  const targetId = documents[targetIdx].i;
  const nonsenseQuery = "殊塵朧霈闃";
  const docHit = new Map([[targetIdx, { score: 0.9, source: "doc", heading: null }]]);
  const boosted = scoreCorpus(documents, nonsenseQuery, DEFAULT_CONFIG, dictionary, docHit);
  const hit = boosted.results.find((r) => r.id === targetId);
  check("semantic-fusion", "doc-level synthetic hit promotes the right document", !!hit && hit.channels.includes("semantic"));
  check("semantic-fusion", "doc-level hit gets the generic summary label", hit?.reasons[0]?.label === "摘要語義近似");

  const chunkHit = new Map([[targetIdx, { score: 0.85, source: "chunk", heading: "測試章節" }]]);
  const chunkBoosted = scoreCorpus(documents, nonsenseQuery, DEFAULT_CONFIG, dictionary, chunkHit);
  const chunkResult = chunkBoosted.results.find((r) => r.id === targetId);
  check("semantic-fusion", "chunk-level hit with heading gets the distinct paragraph label", chunkResult?.reasons[0]?.label === "段落語義近似（測試章節）");

  const chunkNoHeading = new Map([[targetIdx, { score: 0.85, source: "chunk", heading: null }]]);
  const chunkNoHeadingBoosted = scoreCorpus(documents, nonsenseQuery, DEFAULT_CONFIG, dictionary, chunkNoHeading);
  const chunkNoHeadingResult = chunkNoHeadingBoosted.results.find((r) => r.id === targetId);
  check("semantic-fusion", "chunk-level hit with no heading falls back to the generic paragraph label", chunkNoHeadingResult?.reasons[0]?.label === "段落語義近似");

  const exactQuery = documents[0].t.slice(0, 4);
  const exactBaseline = scoreCorpus(documents, exactQuery, DEFAULT_CONFIG, dictionary, new Map());
  const exactTop = exactBaseline.results[0];
  const weak = new Map([[0, { score: 0.1, source: "doc", heading: null }]]);
  const stillTop = scoreCorpus(documents, exactQuery, DEFAULT_CONFIG, dictionary, weak).results.find((r) => r.id === exactTop.id);
  check("semantic-fusion", "a weak synthetic semantic score never downgrades an existing exact hit", stillTop?.score === exactTop.score);
}

// --- relation-type fallback (unrecognized type code must never crash) ----
{
  const cfg = { ...DEFAULT_CONFIG, relationTypes: { z: { label: "自訂關聯", relation: "custom", weightKey: "series_relation" } } };
  const docs = prepareIndex([
    { i: "d1", t: "第一篇文件的標題內容", s: "", h: [], k: [], r: [], p: null },
    { i: "d2", t: "完全無關的另一篇", s: "", h: [], k: [], r: [["d1", "unknown_code"]], p: null },
  ]);
  const strongQuery = docs[0].t;
  const result = scoreCorpus(docs, strongQuery, cfg, []);
  const weak = result.results.find((r) => r.id === "d2");
  check("relations", "an unrecognized relation type code never throws and still produces a result", !!weak);
  check("relations", "an unrecognized relation type code falls back to a generic (not misleadingly specific) label", weak?.reasons[0]?.relation === "related");
}

// --- reason labels are configuration, not constants ----------------------
// The hit-reason label is this package's headline honesty feature; if it were
// locked to one language the feature would only work for one audience.
{
  const docs = prepareIndex([{ i: "d1", t: "Balcony tomato basics", s: "", h: [], k: [], r: [], p: null }]);
  const q = "Balcony tomato basics";

  const zh = scoreCorpus(docs, q, DEFAULT_CONFIG, []);
  check("labels", "default labels are the zh-Hant set the package shipped with",
    zh.results[0]?.reasons[0]?.label === DEFAULT_LABELS.exact_title, `got=${zh.results[0]?.reasons[0]?.label}`);

  const en = scoreCorpus(docs, q, { ...DEFAULT_CONFIG, labels: LABELS_EN }, []);
  check("labels", "supplying LABELS_EN switches the reason text to English",
    en.results[0]?.reasons[0]?.label === "exact title match", `got=${en.results[0]?.reasons[0]?.label}`);

  // A partial override must not blank out the labels it doesn't mention.
  const partial = scoreCorpus(docs, q, { ...DEFAULT_CONFIG, labels: { lexical: "custom lexical" } }, []);
  check("labels", "a partial label override falls back to defaults for unspecified keys",
    partial.results[0]?.reasons[0]?.label === DEFAULT_LABELS.exact_title,
    `got=${partial.results[0]?.reasons[0]?.label}`);

  // Interpolating labels are functions so a translation can place the term
  // where its own grammar needs it, rather than in the original's word order.
  check("labels", "an interpolating label receives the matched term",
    LABELS_EN.alias("XIP").includes("XIP") && DEFAULT_LABELS.alias("XIP").includes("XIP"));
  check("labels", "the chunk label has a distinct form for a known heading vs none",
    LABELS_EN.semantic_chunk("Storage") !== LABELS_EN.semantic_chunk_generic);

  // A config file arrives as JSON, which cannot hold functions — so an
  // interpolating label has to be expressible as a "{}" template too, or
  // config-driven deployments silently can't translate half the labels.
  const chunkHit = new Map([[0, { score: 0.99, source: "chunk", heading: "Watering" }]]);
  const jsonish = scoreCorpus(docs, "zzz-no-lexical-match", {
    ...DEFAULT_CONFIG,
    labels: { semantic_chunk: "passage matched under {}" },
  }, [], chunkHit);
  check("labels", "a JSON-style {} template label interpolates without needing a function",
    jsonish.results[0]?.reasons[0]?.label === "passage matched under Watering",
    `got=${jsonish.results[0]?.reasons[0]?.label}`);
}

// --- the English edition of the same corpus ------------------------------
// The two editions are parallel by construction (same ids, same relations),
// so running the same scenarios against both is how "this engine is not
// script-specific" gets checked rather than asserted. The lexical channel in
// particular takes a completely different path for each: character bigrams
// for Chinese, whitespace tokens for English.
{
  const indexEn = JSON.parse(readFileSync(join(DEMO_DIST_EN, "index.json"), "utf-8"));
  const dictEn = JSON.parse(readFileSync(join(DEMO_DIST_EN, "dictionary.json"), "utf-8")).entries;
  const docsEn = prepareIndex(indexEn.documents.map((d) => ({ ...d })));
  const cfgEn = { ...DEFAULT_CONFIG, labels: LABELS_EN, relationTypes: RELATION_TYPES_EN };

  check("en-corpus", "the two editions hold the same document ids",
    JSON.stringify(indexEn.documents.map((d) => d.i).sort()) ===
    JSON.stringify(index.documents.map((d) => d.i).sort()));

  const exactEn = scoreCorpus(docsEn, "Growing tomatoes on a balcony", cfgEn, dictEn);
  check("en-corpus", "exact title query's top hit is the right document",
    exactEn.results[0]?.id === "balcony-tomato-basics");
  check("en-corpus", "exact title hit lands in tier A", exactEn.results[0]?.tier === "A");

  const aliasEn = scoreCorpus(docsEn, "alligator pear", cfgEn, dictEn);
  check("en-corpus", "an English dictionary alias resolves to the right document",
    aliasEn.results[0]?.id === "avocado-ripeness");

  const relEn = scoreCorpus(docsEn, "staking and side shoots", cfgEn, dictEn);
  const sibling = relEn.results.find((r) => r.id === "balcony-tomato-pests");
  check("en-corpus", "a same-series sibling rides in via the relation channel",
    !!sibling && sibling.channels.includes("relation"));

  const missEn = scoreCorpus(docsEn, "how to apply for a passport", cfgEn, dictEn);
  check("en-corpus", "an unrelated query is honestly disclosed as low-confidence",
    missEn.low_confidence === true);

  // Regression guard for a leak that is invisible in unit tests but glaring
  // on screen: relation labels live in cfg.relationTypes, NOT cfg.labels, so
  // translating only the latter leaves one foreign-language row sitting in an
  // otherwise-translated result list. This was shipped once.
  check("en-corpus", "relation label is translated when relationTypes is supplied",
    sibling?.reasons[0]?.label === RELATION_TYPES_EN.s.label,
    `got=${sibling?.reasons[0]?.label}`);

  const labelsOnly = scoreCorpus(docsEn, "staking and side shoots",
    { ...DEFAULT_CONFIG, labels: LABELS_EN }, dictEn);
  const leaked = labelsOnly.results.find((r) => r.id === "balcony-tomato-pests");
  check("en-corpus", "overriding labels alone leaves relation labels untranslated (documents the trap)",
    leaked?.reasons[0]?.label === DEFAULT_RELATION_TYPES.s.label,
    `got=${leaked?.reasons[0]?.label}`);
}

console.log(`\n--- ${pass}/${pass + fail} passed ---`);
process.exit(fail === 0 ? 0 : 1);
