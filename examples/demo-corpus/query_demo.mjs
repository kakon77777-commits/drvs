// DRVS demo-corpus query script — proves core/scoring.js works standalone,
// on a real (if tiny) corpus that has nothing to do with the project DRVS was
// distilled from. Runs entirely in plain Node: exact + lexical + dictionary +
// relation channels only (no semantic vectors — that channel needs a
// browser/WASM model, see client/vector-client.js, and is out of scope for a
// Node script by design, same as the reference deployment only ever verifies
// that channel in-browser).
//
// It runs the SAME five scenarios against both language editions of the
// corpus. The two are parallel by construction — same ids, same relations,
// same structure — so if a scenario passes in one language and fails in the
// other, that is a real asymmetry in the engine rather than a difference in
// the test data. The lexical channel especially takes very different paths
// for the two (character bigrams for Chinese, whitespace tokens for English),
// and this is where that gets checked instead of assumed.
//
// Usage: node examples/demo-corpus/query_demo.mjs
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { scoreCorpus, prepareIndex, DEFAULT_CONFIG, LABELS_EN, RELATION_TYPES_EN } from "../../core/scoring.js";

const __dirname = dirname(fileURLToPath(import.meta.url));

const EDITIONS = [
  {
    locale: "zh-Hant",
    config: DEFAULT_CONFIG,
    scenarios: [
      { label: "exact title match", query: "陽台番茄種植入門", expectTop: "balcony-tomato-basics" },
      { label: "dictionary alias (牛油果 -> 酪梨)", query: "牛油果", expectTop: "avocado-ripeness" },
      { label: "lexical/summary overlap, no title match", query: "咖啡豆冰箱保存" },
      { label: "series relation (query hits the basics doc; the pests doc should ride in via same-series Tier D)",
        query: "支架與側芽", expectRelation: "balcony-tomato-pests" },
      { label: "cross-domain / zero-data (nothing here is about passports)",
        query: "護照申請流程", expectLowConfidence: true },
    ],
  },
  {
    locale: "en",
    // Both are needed: `labels` alone leaves relation labels in the default
    // language, which shows up as a single foreign-language row in an
    // otherwise-translated result list.
    config: { ...DEFAULT_CONFIG, labels: LABELS_EN, relationTypes: RELATION_TYPES_EN },
    scenarios: [
      { label: "exact title match", query: "Growing tomatoes on a balcony", expectTop: "balcony-tomato-basics" },
      { label: "dictionary alias (alligator pear -> avocado)", query: "alligator pear", expectTop: "avocado-ripeness" },
      { label: "lexical/summary overlap, no title match", query: "storing coffee beans in the fridge" },
      { label: "series relation (query hits the basics doc; the pests doc should ride in via same-series Tier D)",
        query: "staking and side shoots", expectRelation: "balcony-tomato-pests" },
      { label: "cross-domain / zero-data (nothing here is about passports)",
        query: "how to apply for a passport", expectLowConfidence: true },
    ],
  },
];

let allOk = true;
function check(label, cond) {
  console.log(`  [${cond ? "PASS" : "FAIL"}] ${label}`);
  if (!cond) allOk = false;
}

for (const { locale, config, scenarios } of EDITIONS) {
  const distDir = join(__dirname, "dist", locale);
  const index = JSON.parse(readFileSync(join(distDir, "index.json"), "utf-8"));
  const dictionary = JSON.parse(readFileSync(join(distDir, "dictionary.json"), "utf-8")).entries;
  const documents = prepareIndex(index.documents);

  console.log(`\n${"=".repeat(70)}\n${locale} — ${documents.length} documents\n${"=".repeat(70)}`);

  for (const s of scenarios) {
    const result = scoreCorpus(documents, s.query, config, dictionary);
    console.log(`\n--- ${s.label}`);
    console.log(`query: ${JSON.stringify(s.query)}  low_confidence=${result.low_confidence}  relaxed=${result.relaxed}`);
    for (const r of result.results.slice(0, 4)) {
      console.log(`  ${r.doc.t} [${r.tier}] ${r.score.toFixed(2)} — ${r.reasons[0]?.label || "?"}`);
    }

    if (s.expectTop) check(`top hit is ${s.expectTop}`, result.results[0]?.id === s.expectTop);
    if (s.expectRelation) {
      const hit = result.results.find((r) => r.id === s.expectRelation);
      check(`${s.expectRelation} rides in via a relation, not a direct match`,
        !!hit && hit.channels.includes("relation"));
    }
    if (s.expectLowConfidence) check("honestly disclosed as low-confidence", result.low_confidence === true);
  }
}

console.log(`\n${allOk ? "All demo checks passed in both languages." : "Some demo checks FAILED — see above."}`);
process.exit(allOk ? 0 : 1);
