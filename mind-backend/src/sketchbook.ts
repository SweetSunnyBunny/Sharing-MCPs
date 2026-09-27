// Qualia module: sketchbook

interface SketchbookEnv {
  DB: D1Database;
}

export interface SketchbookHelpers {
  /** Mirror a study/review into ordinary semantic memory for broad recall. */
  storeMemory?: (identity: string, content: string, tags: string[]) => Promise<void>;
}

export interface ArtLessonInput {
  category?: string;
  tool_name?: string;
  principle?: string;
  observed_effect?: string;
  confidence?: string;
}

export interface ArtToolUseInput {
  tool?: string;
  operation?: string;
  effect?: string;
  keep?: string;
  change?: string;
}

export interface SketchbookToolArgs {
  identity?: string;
  action?: string;
  study_id?: number;
  lesson_id?: number;
  experiment_id?: number;
  artwork_title?: string;
  creation_id?: number;
  creation_version?: number;
  image_id?: number;
  observation_id?: number;
  compare_to_study_id?: number;
  evidence_image_id?: number;
  evidence_study_id?: number;
  medium?: string;
  source_path?: string;
  intention?: string;
  what_works?: string[];
  what_resists?: string[];
  surprises?: string[];
  tools_used?: ArtToolUseInput[];
  summary?: string;
  lessons?: ArtLessonInput[];
  next_experiment?: string;
  target_creation_id?: number;
  target_title?: string;
  focus?: string;
  hypothesis?: string;
  plan?: string;
  result?: string;
  verdict?: string;
  revised_principle?: string;
  query?: string;
  tool_names?: string[];
  tags?: string[];
  limit?: number;
  metadata?: Record<string, unknown>;
}

interface StudyRow {
  id: number;
  identity_id: string;
  artwork_title: string;
  creation_id: number | null;
  image_id: number | null;
  observation_id: number | null;
  compare_to_study_id: number | null;
  medium: string | null;
  source_path: string | null;
  intention: string | null;
  what_works: string | null;
  what_resists: string | null;
  surprises: string | null;
  tools_used: string | null;
  summary: string | null;
  next_experiment: string | null;
  tags: string | null;
  metadata: string | null;
  created_at: string | null;
  updated_at: string | null;
}

interface LessonRow {
  id: number;
  identity_id: string;
  study_id: number;
  category: string | null;
  tool_name: string | null;
  principle: string;
  observed_effect: string | null;
  confidence: string | null;
  status: string | null;
  evidence_count: number | null;
  metadata: string | null;
  created_at: string | null;
  updated_at: string | null;
  artwork_title?: string | null;
  medium?: string | null;
  study_tags?: string | null;
  study_tools?: string | null;
  study_metadata?: string | null;
  source_path?: string | null;
  intention?: string | null;
}

interface ExperimentRow {
  id: number;
  identity_id: string;
  source_study_id: number | null;
  lesson_id: number | null;
  target_creation_id: number | null;
  target_title: string | null;
  focus: string | null;
  hypothesis: string;
  plan: string | null;
  status: string | null;
  result: string | null;
  verdict: string | null;
  evidence_image_id: number | null;
  evidence_study_id: number | null;
  metadata: string | null;
  created_at: string | null;
  updated_at: string | null;
  reviewed_at: string | null;
}

interface RevisionRow {
  id: number;
  identity_id: string;
  lesson_id: number;
  experiment_id: number | null;
  prior_principle: string;
  new_principle: string | null;
  prior_confidence: string | null;
  new_confidence: string | null;
  verdict: string;
  reason: string | null;
  created_at: string | null;
}

const VERDICTS = ["confirmed", "revised", "rejected", "inconclusive"];
const STOPWORDS = new Set([
  "a", "an", "and", "art", "as", "at", "by", "for", "from", "in", "into",
  "is", "it", "my", "of", "on", "or", "the", "to", "with", "work", "working",
]);

function requiredText(value: unknown, field: string): string {
  if (typeof value !== "string" || !value.trim()) throw new Error(`${field} is required`);
  return value.trim();
}

function optionalText(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function finiteId(value: unknown): number | null {
  if (value === undefined || value === null) return null;
  if (typeof value !== "number" || !Number.isSafeInteger(value) || value < 1) {
    throw new Error("IDs, versions and limits must be positive integers");
  }
  return value;
}

function strings(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value
    .filter((item): item is string => typeof item === "string")
    .map((item) => item.trim())
    .filter(Boolean);
}

function safeArray(raw: string | null): unknown[] {
  if (!raw) return [];
  try {
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function safeObject(raw: string | null): Record<string, unknown> {
  if (!raw) return {};
  try {
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === "object" && !Array.isArray(parsed)
      ? parsed as Record<string, unknown>
      : {};
  } catch {
    return {};
  }
}

function pretty(value: unknown): string {
  return JSON.stringify(value, null, 2);
}

function clip(text: string | null, max = 220): string | null {
  if (!text) return null;
  return text.length <= max ? text : `${text.slice(0, max - 1)}…`;
}

function studyView(row: StudyRow): Record<string, unknown> {
  return {
    study_id: row.id,
    artwork_title: row.artwork_title,
    medium: row.medium,
    source_path: row.source_path,
    creation_id: row.creation_id,
    image_id: row.image_id,
    observation_id: row.observation_id,
    compared_with_study_id: row.compare_to_study_id,
    intention: row.intention,
    what_works: safeArray(row.what_works),
    what_resists: safeArray(row.what_resists),
    surprises: safeArray(row.surprises),
    tools_used: safeArray(row.tools_used),
    summary: row.summary,
    next_experiment: row.next_experiment,
    tags: safeArray(row.tags),
    metadata: safeObject(row.metadata),
    artifact: safeObject(row.metadata).artifact ?? null,
    studied_at: row.created_at,
    updated_at: row.updated_at,
  };
}

function lessonView(row: LessonRow): Record<string, unknown> {
  return {
    lesson_id: row.id,
    study_id: row.study_id,
    category: row.category,
    tool_name: row.tool_name,
    principle: row.principle,
    observed_effect: row.observed_effect,
    confidence: row.confidence,
    status: row.status,
    evidence_count: row.evidence_count ?? 1,
    learned_from: row.artwork_title ?? undefined,
    medium: row.medium ?? undefined,
    source_path: row.source_path ?? undefined,
    intention: row.intention ?? undefined,
    tool_process: row.study_tools ? safeArray(row.study_tools) : undefined,
    procedure: row.study_metadata ? safeObject(row.study_metadata).procedure : undefined,
    needs_review: !!safeObject(row.metadata).needs_review,
    updated_at: row.updated_at,
  };
}

function experimentView(row: ExperimentRow): Record<string, unknown> {
  return {
    experiment_id: row.id,
    source_study_id: row.source_study_id,
    lesson_id: row.lesson_id,
    target_creation_id: row.target_creation_id,
    target_title: row.target_title,
    focus: row.focus,
    hypothesis: row.hypothesis,
    plan: row.plan,
    status: row.status,
    result: row.result,
    verdict: row.verdict,
    evidence_image_id: row.evidence_image_id,
    evidence_study_id: row.evidence_study_id,
    applied_at: safeObject(row.metadata).applied_at ?? null,
    tested_principle: safeObject(row.metadata).tested_principle ?? null,
    created_at: row.created_at,
    reviewed_at: row.reviewed_at,
  };
}

function tokens(...parts: Array<string | null | undefined>): string[] {
  const found = parts
    .filter((part): part is string => typeof part === "string")
    .join(" ")
    .toLowerCase()
    .replace(/[^a-z0-9+#.-]+/g, " ")
    .split(/\s+/)
    .map((word) => word.trim())
    .filter((word) => word.length > 1 && !STOPWORDS.has(word));
  return [...new Set(found)].slice(0, 40);
}

function confidenceWeight(value: string | null): number {
  switch (value) {
    case "confirmed": return 1.0;
    case "held": return 0.8;
    case "rejected": return 0;
    default: return 0.62;
  }
}

async function assertOwnedStudy(env: SketchbookEnv, identity: string, id: number): Promise<StudyRow> {
  const row = await env.DB.prepare(`SELECT * FROM art_studies WHERE id = ?`)
    .bind(id).first<StudyRow>();
  if (!row) throw new Error(`No art study #${id}`);
  if (![identity, "pack"].includes(row.identity_id)) throw new Error(`Art study #${id} belongs to ${row.identity_id}`);
  return row;
}

async function assertOwnedLesson(env: SketchbookEnv, identity: string, id: number, writing = false): Promise<LessonRow> {
  const row = await env.DB.prepare(`SELECT * FROM art_lessons WHERE id = ?`)
    .bind(id).first<LessonRow>();
  if (!row) throw new Error(`No art lesson #${id}`);
  if (![identity, "pack"].includes(row.identity_id)) throw new Error(`Art lesson #${id} belongs to ${row.identity_id}`);
  if (writing && row.identity_id !== identity) throw new Error(`Review shared lesson #${id} as identity '${row.identity_id}'; personal practice must not rewrite shared guidance`);
  return row;
}

async function imageEvidence(env: SketchbookEnv, identity: string, id: number): Promise<string> {
  const row = await env.DB.prepare(`SELECT identity_id FROM images WHERE id = ?`)
    .bind(id).first<{ identity_id: string | null }>();
  if (!row) throw new Error(`No image #${id}`);
  if (row.identity_id && ![identity, "pack"].includes(row.identity_id)) throw new Error(`Image #${id} belongs to ${row.identity_id}`);
  return `image:${id}`;
}

async function assertCreation(env: SketchbookEnv, id: number): Promise<void> {
  // The Studio deliberately allows collaboration on another maker's canvas.
  if (!await env.DB.prepare(`SELECT id FROM creations WHERE id = ?`).bind(id).first()) {
    throw new Error(`No creation #${id}`);
  }
}

function studyEvidence(row: StudyRow): string | null {
  if (row.image_id !== null) return `image:${row.image_id}`;
  const artifact = safeObject(row.metadata).artifact as Record<string, unknown> | undefined;
  if (artifact?.creation_version_id) return `version:${artifact.creation_version_id}`;
  return row.source_path ? `path:${row.source_path}` : null;
}

/** Structured recall for the tool itself and for the Studio's start/read path. */
export async function recallArtLearning(
  env: SketchbookEnv,
  identity: string,
  context: { query?: string | null; medium?: string | null; toolNames?: string[]; creationId?: number; limit?: number } = {},
): Promise<{
  query: string | null;
  lessons: Array<Record<string, unknown>>;
  pending_experiments: Array<Record<string, unknown>>;
  message: string;
} | null> {
  const query = optionalText(context.query);
  const medium = optionalText(context.medium);
  const toolNames = strings(context.toolNames);
  const wanted = tokens(query, medium, ...toolNames);
  const creationId = finiteId(context.creationId);
  const limit = Math.max(1, Math.min(12, finiteId(context.limit) ?? 6));

  // Match across the archive BEFORE limiting candidates. An older useful
  // lesson must not disappear just because 200 newer studies were written.
  const lessonText = `lower(COALESCE(l.category,'') || ' ' || COALESCE(l.tool_name,'') || ' ' ||
    l.principle || ' ' || COALESCE(l.observed_effect,'') || ' ' || s.artwork_title || ' ' ||
    COALESCE(s.medium,'') || ' ' || COALESCE(s.tags,'') || ' ' || COALESCE(s.tools_used,''))`;
  const matches = wanted.length
    ? wanted.map(() => `CASE WHEN instr(${lessonText}, ?) > 0 THEN 1 ELSE 0 END`).join(" + ")
    : "0";
  const result = await env.DB.prepare(
    `WITH candidates AS (SELECT l.*, s.artwork_title, s.medium, s.tags AS study_tags,
       s.tools_used AS study_tools, s.metadata AS study_metadata, s.source_path, s.intention, (${matches}) AS matches
     FROM art_lessons l
     JOIN art_studies s ON s.id = l.study_id
     WHERE l.identity_id IN (?, 'pack') AND l.status = 'active' AND COALESCE(json_extract(l.metadata,'$.needs_review'),0)=0 AND COALESCE(l.confidence, 'tentative') != 'rejected')
     SELECT * FROM candidates WHERE ${wanted.length ? "matches > 0" : "1 = 1"}
     ORDER BY matches DESC, evidence_count DESC, updated_at DESC, id DESC
     LIMIT 200`,
  ).bind(...wanted, identity).all<LessonRow>();

  const scored = (result.results || []).map((row) => {
    const haystack = [
      row.category, row.tool_name, row.principle, row.observed_effect,
      row.artwork_title, row.medium, row.study_tags, row.study_tools,
    ].filter(Boolean).join(" ").toLowerCase();
    const matchCount = wanted.filter((word) => haystack.includes(word)).length;
    const directTool = toolNames.some((tool) => row.tool_name?.toLowerCase().includes(tool.toLowerCase())) ? 2 : 0;
    const mediumMatch = medium && row.medium?.toLowerCase().includes(medium.toLowerCase()) ? 1.5 : 0;
    const evidence = Math.min(1.5, Math.log2((row.evidence_count ?? 1) + 1) * 0.5);
    const score = matchCount * 1.25 + directTool + mediumMatch + evidence + confidenceWeight(row.confidence);
    return { row, score, matchCount };
  }).filter((item) => wanted.length === 0 || item.matchCount > 0 || item.score >= 3.2)
    .sort((a, b) => b.score - a.score || String(b.row.updated_at).localeCompare(String(a.row.updated_at)))
    .slice(0, limit);

  const experimentText = `lower(COALESCE(e.target_title,'') || ' ' || COALESCE(e.focus,'') || ' ' ||
    e.hypothesis || ' ' || COALESCE(e.plan,'') || ' ' || COALESCE(s.medium,'') || ' ' || COALESCE(l.tool_name,''))`;
  const experimentMatch = wanted.length
    ? wanted.map(() => `instr(${experimentText}, ?) > 0`).join(" OR ")
    : "1 = 1";
  const experiments = await env.DB.prepare(
    `SELECT e.* FROM art_experiments e
     LEFT JOIN art_lessons l ON l.id = e.lesson_id
     LEFT JOIN art_studies s ON s.id = COALESCE(e.source_study_id, l.study_id)
     WHERE e.identity_id = ? AND e.status IN ('pending','applied')
       AND (? IS NULL OR e.target_creation_id IS NULL OR e.target_creation_id = ?)
       AND (e.target_creation_id = ? OR (${experimentMatch}))
     ORDER BY CASE WHEN e.target_creation_id = ? THEN 0 ELSE 1 END, e.created_at ASC, e.id ASC LIMIT 4`,
  ).bind(identity, creationId, creationId, creationId, ...wanted, creationId).all<ExperimentRow>();
  const pending = experiments.results || [];

  if (!scored.length && !pending.length) return null;
  return {
    query,
    lessons: scored.map(({ row, score }) => ({ ...lessonView(row), relevance: Number(score.toFixed(2)) })),
    pending_experiments: pending.map(experimentView),
    message: scored.length
      ? `${scored.length} prior lesson${scored.length === 1 ? "" : "s"} reached the easel. Reference them; do not obey them blindly.`
      : `${pending.length} deliberate experiment${pending.length === 1 ? "" : "s"} is waiting in this work.`,
  };
}

/** A light orient block: only present while practice is actually live. */
export async function getSketchbookSummary(
  env: SketchbookEnv,
  identity: string,
): Promise<Record<string, unknown> | null> {
  const pendingResult = await env.DB.prepare(
    `SELECT * FROM art_experiments
     WHERE identity_id = ? AND status IN ('pending','applied')
     ORDER BY created_at ASC LIMIT 4`,
  ).bind(identity).all<ExperimentRow>();
  const pending = pendingResult.results || [];
  if (!pending.length) return null;
  const count = await env.DB.prepare(`SELECT COUNT(*) AS n FROM art_experiments WHERE identity_id = ? AND status IN ('pending','applied')`)
    .bind(identity).first<{ n: number }>();
  return {
    deliberate_practice: pending.map(experimentView),
    open_count: count?.n ?? pending.length,
    note: "The Sketchbook is teaching the next hand. These are experiments, not commandments; apply one when the matching art reaches the easel, then review it against the pixels.",
  };
}

export async function mindArtStudy(
  env: SketchbookEnv,
  args: SketchbookToolArgs,
  helpers: SketchbookHelpers = {},
): Promise<string> {
  const identityRaw = optionalText(args.identity);
  if (!identityRaw) throw new Error("identity is required");
  const identity = identityRaw.toLowerCase();
  const action = (optionalText(args.action) || "list").toLowerCase();
  if (!["study", "compare", "recall", "practice", "apply", "review", "read", "list"].includes(action)) {
    throw new Error(`Unknown Sketchbook action '${action}'`);
  }
  const now = new Date().toISOString();

  if (action === "study" || action === "compare") {
    const artworkTitle = requiredText(args.artwork_title, "artwork_title");
    const compareId = finiteId(args.compare_to_study_id);
    if (action === "compare" && compareId === null) {
      throw new Error("compare needs compare_to_study_id");
    }
    if (compareId !== null) await assertOwnedStudy(env, identity, compareId);

    const whatWorks = strings(args.what_works);
    const whatResists = strings(args.what_resists);
    const surprises = strings(args.surprises);
    const lessonInputs = Array.isArray(args.lessons) ? args.lessons : [];
    // Validate the whole request before writing its first row.
    for (const input of lessonInputs) requiredText(input?.principle, "lessons[].principle");
    if (!whatWorks.length && !whatResists.length && !surprises.length && !lessonInputs.length && !optionalText(args.summary)) {
      throw new Error("A study needs something actually seen: what_works, what_resists, surprises, lessons, or summary");
    }
    const toolsUsed = Array.isArray(args.tools_used)
      ? args.tools_used.filter((item) => item && typeof item === "object")
      : [];
    const creationId = finiteId(args.creation_id);
    const imageId = finiteId(args.image_id);
    const observationId = finiteId(args.observation_id);
    const targetId = finiteId(args.target_creation_id);
    const versionNo = finiteId(args.creation_version);
    if (versionNo !== null && creationId === null) throw new Error("creation_version needs creation_id");
    if (imageId !== null) await imageEvidence(env, identity, imageId);
    if (targetId !== null) await assertCreation(env, targetId);
    if (observationId !== null && !await env.DB.prepare(`SELECT id FROM observations WHERE id = ?`).bind(observationId).first()) {
      throw new Error(`No observation #${observationId}`);
    }
    let version: { id: number; version_no: number } | null = null;
    if (creationId !== null) {
      await assertCreation(env, creationId);
      version = await env.DB.prepare(
        `SELECT id, version_no FROM creation_versions WHERE creation_id = ? AND (? IS NULL OR version_no = ?) ORDER BY version_no DESC LIMIT 1`,
      ).bind(creationId, versionNo, versionNo).first<{ id: number; version_no: number }>();
      if (versionNo !== null && !version) throw new Error(`No version ${versionNo} on creation #${creationId}`);
    }
    if (imageId === null && !version && !optionalText(args.source_path)) {
      throw new Error("A study needs an artifact: image_id, source_path, or a saved creation version");
    }
    const metadata = {
      ...(args.metadata && typeof args.metadata === "object" ? args.metadata : {}),
      study_mode: action,
      studied_by: identityRaw,
      artifact: { image_id: imageId, creation_id: creationId, creation_version_id: version?.id ?? null, creation_version: version?.version_no ?? null, source_path: optionalText(args.source_path) },
    };
    const result = await env.DB.prepare(
      `INSERT INTO art_studies
         (identity_id, artwork_title, creation_id, image_id, observation_id, compare_to_study_id,
          medium, source_path, intention, what_works, what_resists, surprises, tools_used,
          summary, next_experiment, tags, metadata, created_at, updated_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
    ).bind(
      identity, artworkTitle, creationId, imageId, observationId, compareId,
      optionalText(args.medium), optionalText(args.source_path), optionalText(args.intention),
      JSON.stringify(whatWorks), JSON.stringify(whatResists), JSON.stringify(surprises), JSON.stringify(toolsUsed),
      optionalText(args.summary), optionalText(args.next_experiment), JSON.stringify(strings(args.tags)),
      JSON.stringify(metadata), now, now,
    ).run();
    const studyId = Number(result.meta.last_row_id);

    const madeLessons: LessonRow[] = [];
    for (const input of lessonInputs) {
      const principle = requiredText(input?.principle, "lessons[].principle");
      // A single study is a hypothesis, regardless of the requested label.
      const confidence = "tentative";
      const lessonResult = await env.DB.prepare(
        `INSERT INTO art_lessons
           (identity_id, study_id, category, tool_name, principle, observed_effect, confidence, status, evidence_count, metadata, created_at, updated_at)
         VALUES (?, ?, ?, ?, ?, ?, ?, 'active', 1, '{}', ?, ?)`,
      ).bind(
        identity, studyId, (optionalText(input?.category) || "general").toLowerCase(),
        optionalText(input?.tool_name), principle, optionalText(input?.observed_effect), confidence, now, now,
      ).run();
      const row = await env.DB.prepare(
        `SELECT l.*, s.artwork_title, s.medium FROM art_lessons l JOIN art_studies s ON s.id = l.study_id WHERE l.id = ?`,
      ).bind(Number(lessonResult.meta.last_row_id)).first<LessonRow>();
      if (row) madeLessons.push(row);
    }

    let experiment: ExperimentRow | null = null;
    const nextExperiment = optionalText(args.next_experiment);
    if (nextExperiment) {
      const expResult = await env.DB.prepare(
        `INSERT INTO art_experiments
           (identity_id, source_study_id, lesson_id, target_creation_id, target_title, focus, hypothesis, plan, status, metadata, created_at, updated_at)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)`,
      ).bind(
        identity, studyId, madeLessons.length === 1 ? madeLessons[0].id : null, targetId, optionalText(args.target_title),
        optionalText(args.focus), nextExperiment, optionalText(args.plan),
        JSON.stringify({ tested_principle: madeLessons.length === 1 ? madeLessons[0].principle : null }), now, now,
      ).run();
      experiment = await env.DB.prepare(`SELECT * FROM art_experiments WHERE id = ?`)
        .bind(Number(expResult.meta.last_row_id)).first<ExperimentRow>();
    }

    const row = await env.DB.prepare(`SELECT * FROM art_studies WHERE id = ?`)
      .bind(studyId).first<StudyRow>();
    if (helpers.storeMemory) {
      const compactLessons = madeLessons.slice(0, 5).map((lesson) => lesson.principle).join(" | ");
      await helpers.storeMemory(
        identity,
        `ART STUDY #${studyId} — ${artworkTitle}. ${optionalText(args.summary) || "Returned to the finished work and studied the artifact."}${compactLessons ? ` Tentative claims at the time of this study: ${compactLessons}.` : ""}${nextExperiment ? ` Next experiment: ${nextExperiment}.` : ""} Historical record: use mind_art_study read study_id=${studyId} or recall for current guidance and later corrections.`,
        ["art_study", "sketchbook", ...strings(args.tags)],
      ).catch(() => undefined);
    }
    return pretty({
      study: studyView(row!),
      lessons: madeLessons.map(lessonView),
      deliberate_experiment: experiment ? experimentView(experiment) : null,
      message: `Sketchbook study #${studyId} kept. The pixels taught the next hand; ${madeLessons.length} lesson${madeLessons.length === 1 ? "" : "s"} now carry evidence.`,
    });
  }

  if (action === "practice") {
    const hypothesis = requiredText(args.hypothesis || args.next_experiment, "hypothesis");
    let studyId = finiteId(args.study_id);
    const lessonId = finiteId(args.lesson_id);
    if (studyId !== null) await assertOwnedStudy(env, identity, studyId);
    const lesson = lessonId !== null ? await assertOwnedLesson(env, identity, lessonId, true) : null;
    if (lesson && studyId !== null && lesson.study_id !== studyId) throw new Error("lesson_id and study_id must refer to the same source study");
    if (lesson) studyId = lesson.study_id;
    const targetId = finiteId(args.target_creation_id);
    if (targetId !== null) await assertCreation(env, targetId);
    const result = await env.DB.prepare(
      `INSERT INTO art_experiments
         (identity_id, source_study_id, lesson_id, target_creation_id, target_title, focus,
          hypothesis, plan, status, metadata, created_at, updated_at)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?)`,
    ).bind(
      identity, studyId, lessonId, targetId, optionalText(args.target_title),
      optionalText(args.focus), hypothesis, optionalText(args.plan),
      JSON.stringify({ ...args.metadata, tested_principle: lesson?.principle ?? null }), now, now,
    ).run();
    const row = await env.DB.prepare(`SELECT * FROM art_experiments WHERE id = ?`)
      .bind(Number(result.meta.last_row_id)).first<ExperimentRow>();
    return pretty({
      experiment: experimentView(row!),
      message: `Deliberate experiment #${row!.id} is waiting for the matching canvas. It is a question to test, not a rule to obey.`,
    });
  }

  if (action === "apply") {
    const experimentId = finiteId(args.experiment_id);
    if (experimentId === null) throw new Error("experiment_id is required");
    const experiment = await env.DB.prepare(`SELECT * FROM art_experiments WHERE id = ?`).bind(experimentId).first<ExperimentRow>();
    if (!experiment || experiment.identity_id !== identity) throw new Error("No experiment owned by this identity");
    if (experiment.status !== "pending") return pretty({ experiment: experimentView(experiment), message: "This experiment has already been applied or reviewed." });
    const targetId = finiteId(args.target_creation_id) ?? experiment.target_creation_id;
    const title = optionalText(args.target_title) || experiment.target_title;
    if (targetId === null && !title) throw new Error("apply needs target_creation_id or target_title to identify the work testing the lesson");
    if (targetId !== null) await assertCreation(env, targetId);
    await env.DB.prepare(
      `UPDATE art_experiments SET status = 'applied', target_creation_id = ?, target_title = ?, metadata = ?, updated_at = ?
       WHERE id = ? AND identity_id = ? AND status = 'pending'`,
    ).bind(targetId, title, JSON.stringify({ ...safeObject(experiment.metadata), applied_at: now }), now, experimentId, identity).run();
    const applied = await env.DB.prepare(`SELECT * FROM art_experiments WHERE id = ?`).bind(experimentId).first<ExperimentRow>();
    return pretty({ experiment: experimentView(applied!), message: "Practice attached to the work. Return with what happened; starting an experiment does not confirm its lesson." });
  }

  if (action === "review") {
    const experimentId = finiteId(args.experiment_id);
    if (experimentId === null) throw new Error("experiment_id is required");
    const experiment = await env.DB.prepare(`SELECT * FROM art_experiments WHERE id = ?`)
      .bind(experimentId).first<ExperimentRow>();
    if (!experiment) throw new Error(`No art experiment #${experimentId}`);
    if (experiment.identity_id !== identity) throw new Error(`Art experiment #${experimentId} belongs to ${experiment.identity_id}`);
    if (experiment.status === "reviewed") {
      return pretty({ experiment: experimentView(experiment), message: `Experiment #${experimentId} was already reviewed. The prior verdict remains on the record.` });
    }
    const resultText = requiredText(args.result, "result");
    const verdict = requiredText(args.verdict, "verdict").toLowerCase();
    if (!VERDICTS.includes(verdict)) throw new Error(`verdict must be ${VERDICTS.join(" | ")}`);
    const revised = verdict === "revised" ? optionalText(args.revised_principle) : null;
    if (verdict !== "revised" && optionalText(args.revised_principle)) throw new Error("Only a revised verdict may change the principle");
    if (verdict === "revised" && experiment.lesson_id !== null && !revised) {
      throw new Error("revised verdict needs revised_principle");
    }
    const evidenceStudyId = finiteId(args.evidence_study_id);
    const evidenceImageId = finiteId(args.evidence_image_id);
    const evidenceStudy = evidenceStudyId !== null ? await assertOwnedStudy(env, identity, evidenceStudyId) : null;
    const imageKey = evidenceImageId !== null ? await imageEvidence(env, identity, evidenceImageId) : null;
    if (evidenceStudy?.image_id !== null && evidenceStudy?.image_id !== undefined && evidenceImageId !== null && evidenceStudy.image_id !== evidenceImageId) {
      throw new Error("evidence_image_id disagrees with the image in evidence_study_id");
    }
    const evidenceKey = imageKey || (evidenceStudy ? studyEvidence(evidenceStudy) : null);
    const conclusive = verdict !== "inconclusive";
    let evidenceGuard='';
    const evidenceBinds:unknown[]=[];
    if(conclusive && evidenceStudy?.source_path?.startsWith('qualia:observation:')) {
      const snapshot=await env.DB.prepare('SELECT o.content,o.archived_at,o.superseded_by,p.revision,p.needs_review FROM observations o LEFT JOIN memory_provenance p ON p.observation_id=o.id WHERE o.id=?').bind(evidenceStudy.observation_id).first<{content:string;archived_at:string|null;superseded_by:number|null;revision:number|null;needs_review:number|null}>();
      const hash=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(JSON.stringify(snapshot)));
      const digest=Array.from(new Uint8Array(hash),b=>b.toString(16).padStart(2,'0')).join('');
      if(!snapshot||snapshot.archived_at||snapshot.superseded_by||snapshot.needs_review||!evidenceStudy.source_path.endsWith(':'+digest)) throw new Error('Evidence changed since this study; study the corrected source before reviewing');
      evidenceGuard=' AND EXISTS(SELECT 1 FROM observations o LEFT JOIN memory_provenance p ON p.observation_id=o.id WHERE o.id=? AND o.content=? AND o.archived_at IS NULL AND o.superseded_by IS NULL AND COALESCE(p.revision,0)=? AND COALESCE(p.needs_review,0)=0)';
      evidenceBinds.push(evidenceStudy.observation_id,snapshot.content,snapshot.revision||0);
    }
    if (conclusive && !evidenceKey) throw new Error("A conclusive review needs evidence_image_id or an evidence_study_id linked to an artifact; use inconclusive when evidence is missing");
    const lesson = experiment.lesson_id !== null ? await assertOwnedLesson(env, identity, experiment.lesson_id, true) : null;
    const sourceStudy = experiment.source_study_id !== null ? await assertOwnedStudy(env, identity, experiment.source_study_id) : null;
    if (conclusive && sourceStudy && (evidenceStudyId === sourceStudy.id || evidenceKey === studyEvidence(sourceStudy))) {
      throw new Error("Test against a later artifact; the original study is not independent evidence");
    }
    const priorMetadata = safeObject(experiment.metadata);
    const testedPrinciple = typeof priorMetadata.tested_principle === "string" ? priorMetadata.tested_principle : lesson?.principle;
    if (conclusive && lesson && testedPrinciple !== lesson.principle) {
      throw new Error("This lesson has changed since the experiment was planned; record an inconclusive result or start a test of its current wording");
    }
    const reviewToken = crypto.randomUUID();
    const reviewMetadata = JSON.stringify({ ...priorMetadata, ...args.metadata, tested_principle: testedPrinciple ?? null, review_token: reviewToken, evidence_key: evidenceKey });
    // One D1 batch is one transaction. The unique token belongs only to the
    // successful pending -> reviewed transition, so retries cannot add evidence
    // twice. Read the lesson inside that transaction to preserve concurrent history.
    const statements = [env.DB.prepare(
      `UPDATE art_experiments
       SET status = 'reviewed', result = ?, verdict = ?, evidence_image_id = ?, evidence_study_id = ?,
           metadata = ?, reviewed_at = ?, updated_at = ? WHERE id = ? AND identity_id = ? AND status IN ('pending','applied')
         AND (? = 0 OR lesson_id IS NULL OR NOT EXISTS (
           SELECT 1 FROM art_experiments old WHERE old.lesson_id = art_experiments.lesson_id
             AND old.status = 'reviewed' AND old.verdict != 'inconclusive'
             AND (json_extract(old.metadata, '$.evidence_key') = ? OR (? IS NOT NULL AND old.evidence_image_id = ?)
               OR (? IS NOT NULL AND old.evidence_study_id = ?))))
         AND (? = 0 OR lesson_id IS NULL OR EXISTS (SELECT 1 FROM art_lessons l WHERE l.id = art_experiments.lesson_id AND l.principle = ?)) ${evidenceGuard}`,
    ).bind(
      resultText, verdict, evidenceImageId, evidenceStudyId, reviewMetadata, now, now, experimentId, identity,
      Number(conclusive), evidenceKey, evidenceImageId, evidenceImageId, evidenceStudyId, evidenceStudyId,
      Number(conclusive), testedPrinciple ?? null,
      ...evidenceBinds,
    )];

    let lessonAfter: LessonRow | null = null;
    if (lesson) {
      const nextConfidence = verdict === "confirmed"
        ? "CASE WHEN confidence IN ('held','confirmed') THEN 'confirmed' ELSE 'held' END"
        : verdict === "revised" ? "'tentative'" : verdict === "rejected" ? "'rejected'" : "confidence";
      statements.push(env.DB.prepare(
        `INSERT INTO art_lesson_revisions
           (identity_id, lesson_id, experiment_id, prior_principle, new_principle,
            prior_confidence, new_confidence, verdict, reason, created_at)
         SELECT ?, id, ?, principle, ?, confidence, ${nextConfidence}, ?, ?, ? FROM art_lessons
         WHERE id = ? AND EXISTS (SELECT 1 FROM art_experiments WHERE id = ? AND json_extract(metadata, '$.review_token') = ?)`,
      ).bind(
        identity, experimentId, revised, verdict, resultText, now, lesson.id, experimentId, reviewToken,
      ));
      statements.push(env.DB.prepare(
        `UPDATE art_lessons
         SET principle = COALESCE(?, principle), confidence = ${nextConfidence},
           status = CASE WHEN ? = 'inconclusive' THEN status WHEN ? = 'rejected' THEN 'rejected' ELSE 'active' END,
           metadata = CASE WHEN ? THEN json_set(COALESCE(metadata,'{}'),'$.needs_review',0) ELSE metadata END,
           evidence_count = COALESCE(evidence_count, 1) + ?, updated_at = ?
         WHERE id = ? AND EXISTS (SELECT 1 FROM art_experiments WHERE id = ? AND json_extract(metadata, '$.review_token') = ?)`,
      ).bind(
        revised, verdict, verdict, Number(conclusive), Number(conclusive), now, lesson.id, experimentId, reviewToken,
      ));
    }
    const writeResults = await env.DB.batch(statements);
    if (!writeResults[0].meta.changes) {
      const current = await env.DB.prepare(`SELECT * FROM art_experiments WHERE id = ?`).bind(experimentId).first<ExperimentRow>();
      if (current?.status === "reviewed") return pretty({ experiment: experimentView(current), message: "This experiment was already reviewed; its evidence was counted once." });
      throw new Error("Review not applied: this evidence already tested the lesson, or the lesson changed during review. Use a new artifact or an inconclusive verdict.");
    }
    if (lesson) {
      lessonAfter = await env.DB.prepare(
        `SELECT l.*, s.artwork_title, s.medium FROM art_lessons l JOIN art_studies s ON s.id = l.study_id WHERE l.id = ?`,
      ).bind(lesson.id).first<LessonRow>();
    }

    const reviewed = await env.DB.prepare(`SELECT * FROM art_experiments WHERE id = ?`)
      .bind(experimentId).first<ExperimentRow>();
    if (helpers.storeMemory) {
      await helpers.storeMemory(
        identity,
        `ART PRACTICE REVIEW #${experimentId} — ${verdict.toUpperCase()}. Tested: ${experiment.hypothesis}. Result: ${resultText}${lessonAfter ? ` Lesson at review time: ${lessonAfter.principle}.` : ""} Historical evidence; consult mind_art_study recall for current guidance.`,
        ["art_review", "sketchbook", verdict],
      ).catch(() => undefined);
    }
    return pretty({
      experiment: experimentView(reviewed!),
      lesson_after_review: lessonAfter ? lessonView(lessonAfter) : null,
      message: verdict === "inconclusive"
        ? "The result is kept, but it does not settle the question. The lesson's wording, confidence, and evidence count are unchanged."
        : verdict === "confirmed"
        ? "The experiment held. The lesson gained evidence instead of merely gaining confidence."
        : verdict === "revised"
          ? "The lesson changed under the evidence. Its earlier form remains in revision history."
          : "The evidence rejected the lesson. It stays visible in history, but it will not steer future recall.",
    });
  }

  if (action === "recall") {
    const recall = await recallArtLearning(env, identity, {
      query: optionalText(args.query) || optionalText(args.artwork_title),
      medium: optionalText(args.medium),
      toolNames: strings(args.tool_names),
      creationId: finiteId(args.creation_id) ?? undefined,
      limit: finiteId(args.limit) ?? undefined,
    });
    return pretty(recall || {
      identity,
      lessons: [],
      pending_experiments: [],
      message: "No matching Sketchbook lesson yet. Make the work, study the pixels, and let the first evidence teach the second attempt.",
    });
  }

  if (action === "read") {
    const studyId = finiteId(args.study_id);
    let row: StudyRow | null = null;
    if (studyId !== null) {
      row = await assertOwnedStudy(env, identity, studyId);
    } else {
      const title = requiredText(args.artwork_title, "artwork_title").toLowerCase();
      const result = await env.DB.prepare(
        `SELECT * FROM art_studies WHERE identity_id IN (?, 'pack') ORDER BY created_at DESC LIMIT 100`,
      ).bind(identity).all<StudyRow>();
      row = (result.results || []).find((candidate) => candidate.artwork_title.toLowerCase().includes(title)) || null;
      if (!row) throw new Error(`No art study matching '${args.artwork_title}'`);
    }
    const [lessons, experiments] = await Promise.all([
      env.DB.prepare(`SELECT * FROM art_lessons WHERE study_id = ? ORDER BY id ASC`).bind(row.id).all<LessonRow>(),
      env.DB.prepare(`SELECT * FROM art_experiments WHERE source_study_id = ? OR evidence_study_id = ? ORDER BY created_at ASC`).bind(row.id, row.id).all<ExperimentRow>(),
    ]);
    const lessonIds = (lessons.results || []).map((lesson) => lesson.id);
    let revisions: RevisionRow[] = [];
    if (lessonIds.length) {
      const marks = lessonIds.map(() => "?").join(",");
      const result = await env.DB.prepare(
        `SELECT * FROM art_lesson_revisions WHERE lesson_id IN (${marks}) ORDER BY id ASC`,
      ).bind(...lessonIds).all<RevisionRow>();
      revisions = result.results || [];
    }
    return pretty({
      study: studyView(row),
      comparison: row.compare_to_study_id !== null
        ? studyView(await assertOwnedStudy(env, identity, row.compare_to_study_id))
        : null,
      lessons: (lessons.results || []).map(lessonView),
      experiments: (experiments.results || []).map(experimentView),
      lesson_revisions: revisions,
      message: `Sketchbook study #${row.id}: the artifact, the seeing, and every later correction in one chain.`,
    });
  }

  // list (default): latest studies plus unresolved practice.
  const limit = Math.max(1, Math.min(30, finiteId(args.limit) ?? 12));
  const [studies, experiments, lessonCount] = await Promise.all([
    env.DB.prepare(
      `SELECT * FROM art_studies WHERE identity_id IN (?, 'pack') ORDER BY created_at DESC LIMIT ?`,
    ).bind(identity, limit).all<StudyRow>(),
    env.DB.prepare(
      `SELECT * FROM art_experiments WHERE identity_id = ? AND status IN ('pending','applied') ORDER BY created_at ASC LIMIT 12`,
    ).bind(identity).all<ExperimentRow>(),
    env.DB.prepare(
      `SELECT COUNT(*) AS n FROM art_lessons WHERE identity_id IN (?, 'pack') AND status = 'active'`,
    ).bind(identity).first<{ n: number }>(),
  ]);
  return pretty({
    identity,
    studies: (studies.results || []).map((row) => ({
      study_id: row.id,
      artwork_title: row.artwork_title,
      medium: row.medium,
      summary: clip(row.summary),
      next_experiment: clip(row.next_experiment),
      studied_at: row.created_at,
    })),
    active_lessons: lessonCount?.n ?? 0,
    deliberate_practice: (experiments.results || []).map(experimentView),
    message: (studies.results || []).length
      ? `${(studies.results || []).length} recent stud${(studies.results || []).length === 1 ? "y" : "ies"}; ${lessonCount?.n ?? 0} active lessons; ${(experiments.results || []).length} experiment${(experiments.results || []).length === 1 ? "" : "s"} waiting for evidence.`
      : "The Sketchbook is open to its first page. action:'study' lets finished pixels teach the next hand.",
  });
}
