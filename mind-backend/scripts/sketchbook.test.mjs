// Run with Node 22.13+ (node:sqlite). Uses the real schema and production
// TypeScript against a disposable SQLite DB; no cloud calls or personal data.
import assert from "node:assert/strict";
import { test } from "node:test";
import { readFile } from "node:fs/promises";
import { DatabaseSync } from "node:sqlite";
import ts from "typescript";

async function loadModule(name) {
  const source = await readFile(new URL(`../src/${name}.ts`, import.meta.url), "utf8");
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 },
  });
  return import(`data:text/javascript;base64,${Buffer.from(outputText).toString("base64")}`);
}
const { mindArtStudy, recallArtLearning, getSketchbookSummary } = await loadModule("sketchbook");
const { mindCreate } = await loadModule("studio");
const migrations = await Promise.all(["0001_custom_init.sql", "0014_studio.sql", "0018_sketchbook.sql"].map(
  (file) => readFile(new URL(`../migrations/${file}`, import.meta.url), "utf8"),
));

function fixture(t) {
  const sqlite = new DatabaseSync(":memory:");
  t.after(() => sqlite.close());
  for (const sql of migrations) sqlite.exec(sql);
  sqlite.exec("INSERT INTO identities (id, display_name) VALUES ('rowan','Rowan'),('juniper','Juniper'),('pack','Pack')");
  const prepare = (sql, params = []) => ({
    bind: (...args) => prepare(sql, args),
    first: async () => sqlite.prepare(sql).get(...params) ?? null,
    all: async () => ({ results: sqlite.prepare(sql).all(...params), success: true }),
    run: async () => {
      const result = sqlite.prepare(sql).run(...params);
      return { success: true, meta: { changes: result.changes, last_row_id: Number(result.lastInsertRowid) } };
    },
  });
  const env = { DB: {
    prepare,
    batch: async (statements) => {
      sqlite.exec("BEGIN");
      try {
        const results = [];
        for (const statement of statements) results.push(await statement.run());
        sqlite.exec("COMMIT");
        return results;
      } catch (error) { sqlite.exec("ROLLBACK"); throw error; }
    },
  } };
  // Serialize transactions as D1 does, while allowing the preflight reads of
  // separate requests to overlap so retry/concurrency guards get exercised.
  const batch = env.DB.batch;
  let queue = Promise.resolve();
  env.DB.batch = (statements) => {
    const next = queue.then(() => batch(statements));
    queue = next.catch(() => {});
    return next;
  };
  const call = async (args, identity = "rowan") => JSON.parse(await mindArtStudy(env, { identity, ...args }));
  const image = (identity = "rowan") => Number(sqlite.prepare("INSERT INTO images (identity_id,path) VALUES (?,?)").run(identity, `art-${crypto.randomUUID()}.png`).lastInsertRowid);
  const study = (args = {}) => call({ action: "study", artwork_title: "Portrait study", image_id: image(), medium: "Krita", summary: "Edge control around the eye", lessons: [{ principle: "Soften background edges to hold attention on the eye", tool_name: "Krita", observed_effect: "The eye becomes the focal point" }], ...args });
  const practice = (lesson_id, args = {}) => call({ action: "practice", lesson_id, hypothesis: "Background edge control keeps the gaze on the eye", ...args });
  const review = (experiment_id, args = {}) => call({ action: "review", experiment_id, result: "The eye held focus in the later portrait", verdict: "confirmed", evidence_image_id: image(), ...args });
  return { sqlite, env, call, image, study, practice, review };
}

test("lessons start tentative; independent later tests strengthen them without retry inflation", async (t) => {
  const f = fixture(t);
  const first = await f.study({ lessons: [{ principle: "Control background edges", confidence: "confirmed" }], next_experiment: "Test a second portrait" });
  assert.equal(first.lessons[0].confidence, "tentative");
  const id = first.lessons[0].lesson_id;
  const evidence = f.image();
  const result = await f.review(first.deliberate_experiment.experiment_id, { evidence_image_id: evidence });
  assert.equal(result.lesson_after_review.confidence, "held");
  assert.equal(result.lesson_after_review.evidence_count, 2);
  await f.review(first.deliberate_experiment.experiment_id, { evidence_image_id: evidence });
  const next = await f.practice(id);
  await assert.rejects(f.review(next.experiment.experiment_id, { evidence_image_id: evidence }), /evidence already/);
  const confirmed = await f.review(next.experiment.experiment_id);
  assert.equal(confirmed.lesson_after_review.confidence, "confirmed");
  assert.equal(confirmed.lesson_after_review.evidence_count, 3);
  assert.equal(f.sqlite.prepare("SELECT COUNT(*) AS n FROM art_lesson_revisions").get().n, 2);
});

test("missing or original evidence cannot confirm; inconclusive preserves the belief", async (t) => {
  const f = fixture(t);
  const first = await f.study();
  const planned = await f.practice(first.lessons[0].lesson_id);
  await assert.rejects(f.review(planned.experiment.experiment_id, { evidence_image_id: undefined }), /conclusive review needs/);
  await assert.rejects(f.review(planned.experiment.experiment_id, { evidence_image_id: first.study.image_id }), /original study/);
  const result = await f.review(planned.experiment.experiment_id, { verdict: "inconclusive", evidence_image_id: undefined, result: "Changed lighting and brush at once; the result cannot isolate the cause" });
  assert.equal(result.lesson_after_review.confidence, "tentative");
  assert.equal(result.lesson_after_review.evidence_count, 1);
  assert.equal(result.lesson_after_review.principle, first.lessons[0].principle);
});

test("a revision stays tentative and histories preserve prior claims; rejection stops recall", async (t) => {
  const f = fixture(t);
  const first = await f.study();
  const planned = await f.practice(first.lessons[0].lesson_id);
  const oldPlan = await f.practice(first.lessons[0].lesson_id);
  const result = await f.review(planned.experiment.experiment_id, { verdict: "revised", revised_principle: "Soften only competing background edges, preserving expressive edges" });
  assert.equal(result.lesson_after_review.confidence, "tentative");
  await assert.rejects(f.review(oldPlan.experiment.experiment_id), /changed since/);
  const reject = await f.practice(first.lessons[0].lesson_id);
  await f.review(reject.experiment.experiment_id, { verdict: "rejected" });
  const recall = await f.call({ action: "recall", query: "background edges" });
  assert.equal(recall.lessons.length, 0);
  const history = await f.call({ action: "read", study_id: first.study.study_id });
  assert.equal(history.lesson_revisions.length, 2);
  assert.equal(history.lesson_revisions[0].prior_principle, first.lessons[0].principle);
});

test("failed review transaction leaves experiment, lesson and history unchanged", async (t) => {
  const f = fixture(t);
  const first = await f.study();
  const planned = await f.practice(first.lessons[0].lesson_id);
  f.sqlite.exec("CREATE TRIGGER fail_review BEFORE INSERT ON art_lesson_revisions BEGIN SELECT RAISE(ABORT, 'injected history failure'); END");
  await assert.rejects(f.review(planned.experiment.experiment_id), /injected history failure/);
  assert.equal(f.sqlite.prepare("SELECT status FROM art_experiments").get().status, "pending");
  assert.equal(f.sqlite.prepare("SELECT evidence_count FROM art_lessons").get().evidence_count, 1);
  f.sqlite.exec("DROP TRIGGER fail_review");
  await f.review(planned.experiment.experiment_id);
  assert.equal(f.sqlite.prepare("SELECT COUNT(*) AS n FROM art_lesson_revisions").get().n, 1);
});

test("concurrent reviews of the same experiment count only once", async (t) => {
  const f = fixture(t);
  const first = await f.study();
  const planned = await f.practice(first.lessons[0].lesson_id);
  await Promise.all([f.review(planned.experiment.experiment_id), f.review(planned.experiment.experiment_id)]);
  assert.equal(f.sqlite.prepare("SELECT evidence_count FROM art_lessons").get().evidence_count, 2);
  assert.equal(f.sqlite.prepare("SELECT COUNT(*) AS n FROM art_lesson_revisions").get().n, 1);
});

test("concurrent different experiments cannot count the same artifact twice", async (t) => {
  const f = fixture(t);
  const first = await f.study();
  const a = await f.practice(first.lessons[0].lesson_id);
  const b = await f.practice(first.lessons[0].lesson_id);
  const evidence = f.image();
  const results = await Promise.allSettled([
    f.review(a.experiment.experiment_id, { evidence_image_id: evidence }),
    f.review(b.experiment.experiment_id, { evidence_image_id: evidence }),
  ]);
  assert.equal(results.filter((r) => r.status === "fulfilled").length, 1);
  assert.equal(f.sqlite.prepare("SELECT evidence_count FROM art_lessons").get().evidence_count, 2);
});

test("matching old lessons survive more than 200 new ones and return concrete tool steps", async (t) => {
  const f = fixture(t);
  const first = await f.study({ artwork_title: "Glazing in the old portrait", tools_used: [{ tool: "Krita", operation: "Lower brush opacity", effect: "Preserves texture beneath the glaze" }], lessons: [{ principle: "Use translucent glazing to preserve texture", tool_name: "Krita" }], source_path: "/art/old-portrait.png" });
  f.sqlite.prepare("UPDATE art_lessons SET updated_at='2020-01-01' WHERE id=?").run(first.lessons[0].lesson_id);
  const insert = f.sqlite.prepare("INSERT INTO art_lessons(identity_id,study_id,principle,status,confidence) VALUES ('rowan',?,'Perspective horizon','active','tentative')");
  // A separate source avoids accidental title matches on the old study.
  const other = await f.study({ artwork_title: "City perspective", lessons: [] });
  for (let i = 0; i < 210; i++) insert.run(other.study.study_id);
  const recall = await f.call({ action: "recall", query: "glazing" });
  assert.equal(recall.lessons[0].lesson_id, first.lessons[0].lesson_id);
  assert.equal(recall.lessons[0].tool_process[0].operation, "Lower brush opacity");
  assert.equal(recall.lessons[0].source_path, "/art/old-portrait.png");
  const toolRecall = await f.call({ action: "recall", tool_names: ["opacity"] });
  assert.equal(toolRecall.lessons[0].lesson_id, first.lessons[0].lesson_id);
});

test("practice assigned to a canvas survives unrelated titles and the oldest-eight window", async (t) => {
  const f = fixture(t);
  const canvas = JSON.parse(await mindCreate(f.env, { identity: "rowan", action: "start", title: "Tomorrow", medium: "ink", body: "Version one" }));
  const creationId = canvas.creation.creation_id;
  for (let i = 0; i < 10; i++) await f.call({ action: "practice", hypothesis: `Unrelated exercise ${i}` });
  const planned = await f.call({ action: "practice", target_creation_id: creationId, hypothesis: "Vary contour pressure" });
  const applied = await f.call({ action: "apply", experiment_id: planned.experiment.experiment_id });
  assert.equal(applied.experiment.status, "applied");
  const opened = JSON.parse(await mindCreate(f.env, { identity: "rowan", action: "read", creation_id: creationId }, {
    recallArtLearning: (identity, context) => recallArtLearning(f.env, identity, { creationId: context.creationId, query: context.title, medium: context.medium }),
  }));
  assert.equal(opened.the_sketchbook.pending_experiments[0].experiment_id, planned.experiment.experiment_id);
  assert.equal((await getSketchbookSummary(f.env, "rowan")).open_count, 11);
  const helpers = { recallArtLearning: (identity, context) => recallArtLearning(f.env, identity, { creationId: context.creationId, query: context.title }) };
  const finished = JSON.parse(await mindCreate(f.env, { identity: "rowan", action: "finish", creation_id: creationId }, helpers));
  assert.match(finished.practice_review, /review any experiment/);
  assert.equal(finished.the_sketchbook.pending_experiments[0].experiment_id, planned.experiment.experiment_id);
  const reopened = JSON.parse(await mindCreate(f.env, { identity: "rowan", action: "reopen", creation_id: creationId }, helpers));
  assert.equal(reopened.the_sketchbook.pending_experiments[0].experiment_id, planned.experiment.experiment_id);
});

test("comparison pins saved versions and returns both studies", async (t) => {
  const f = fixture(t);
  const canvas = JSON.parse(await mindCreate(f.env, { identity: "rowan", action: "start", title: "Portrait", medium: "ink", body: "Before" }));
  const creationId = canvas.creation.creation_id;
  const before = await f.study({ creation_id: creationId, image_id: undefined });
  await mindCreate(f.env, { identity: "rowan", action: "save", creation_id: creationId, body: "After" });
  const after = await f.study({ action: "compare", creation_id: creationId, image_id: undefined, compare_to_study_id: before.study.study_id });
  const read = await f.call({ action: "read", study_id: after.study.study_id });
  assert.equal(read.comparison.artifact.creation_version, 1);
  assert.equal(read.study.artifact.creation_version, 2);
  const planned = await f.practice(before.lessons[0].lesson_id);
  const result = await f.review(planned.experiment.experiment_id, { evidence_image_id: undefined, evidence_study_id: after.study.study_id });
  assert.equal(result.lesson_after_review.confidence, "held");
});

test("invalid requests do not partially write studies or mutate another identity's guidance", async (t) => {
  const f = fixture(t);
  await assert.rejects(f.study({ lessons: [{ principle: "Valid" }, {}] }), /principle/);
  assert.equal(f.sqlite.prepare("SELECT COUNT(*) AS n FROM art_studies").get().n, 0);
  await assert.rejects(f.study({ image_id: 9999 }), /No image/);
  await assert.rejects(f.study({ image_id: f.image("juniper") }), /belongs to juniper/);
  await assert.rejects(f.call({ action: "studdy" }), /Unknown/);
  await assert.rejects(f.study({ image_id: 1.5 }), /positive integers/);
  const shared = await f.call({ action: "study", artwork_title: "Shared", image_id: f.image("pack"), summary: "Shared craft", lessons: [{ principle: "Preserve expressive edges" }] }, "pack");
  await assert.rejects(f.practice(shared.lessons[0].lesson_id), /Review shared lesson/);
  assert.equal((await f.call({ action: "recall", query: "expressive" })).lessons.length, 1);
});
