/**
 * Reading candidates off the board, for an agent's pull bundle.
 *
 * Lifted from marcus-minecraft src/marcus/board-tools.js: the same marks the
 * Python rules use (CANDIDATE:, OBJECT:, NO EVIDENCE:, CLOSED:, TASK:), read
 * out of `query_project_history(..., "opinions")` entries. Pure functions,
 * no board call: the caller passes the entries and who the controllers are.
 */

export const CANDIDATE_PREFIX = 'candidate.';
export const POST_MARK = 'CANDIDATE: ';
export const OBJECT_MARK = 'OBJECT:';
export const ABSTAIN_MARK = 'NO EVIDENCE:';
export const CLOSE_MARK = 'CLOSED:';
export const TASK_MARK = 'TASK:';
const TASK_STATES = new Set(['open', 'held', 'blocked', 'done']);

/** The state a controller TASK: line carries, or null for any other text. */
export function parseTaskLine(text) {
    const body = String(text ?? '').trim();
    if (!body.toUpperCase().startsWith(TASK_MARK)) return null;
    const rest = body.slice(TASK_MARK.length).trim();
    if (/^held by /i.test(rest)) return { status: 'held', heldBy: rest.slice('held by '.length).trim() || null };
    const status = rest.toLowerCase();
    return TASK_STATES.has(status) ? { status, heldBy: null } : null;
}

/** How an agent's post on a candidate's subject reads: support, object or abstain. */
export function answerOf(what) {
    const said = String(what ?? '').trim().toUpperCase();
    if (said.startsWith(OBJECT_MARK)) return 'object';
    if (said.startsWith(ABSTAIN_MARK)) return 'abstain';
    return 'support';
}

/**
 * Read a candidate's parts out of one opinions entry.
 *
 * @param {object} entry  an opinions entry from the board
 * @param {Set<string>} controllerIds
 * @returns {{posts: Array, definition: object, spec: object, closedAs: string, task: object|null}|null}
 */
export function candidateParts(entry, controllerIds) {
    if (!String(entry?.subject ?? '').startsWith(CANDIDATE_PREFIX)) return null;
    const posts = [...(entry.opinions ?? [])].sort((a, b) => String(a.timestamp ?? '').localeCompare(String(b.timestamp ?? '')));
    const definition = posts.find(o => controllerIds.has(o.agent_id) && String(o.what ?? '').startsWith(POST_MARK));
    if (!definition) return null;
    const close = posts.find(o => controllerIds.has(o.agent_id) && String(o.what ?? '').trim().toUpperCase().startsWith(CLOSE_MARK));
    let spec;
    try {
        spec = JSON.parse(String(definition.what).slice(POST_MARK.length));
    } catch {
        return null;
    }
    const closedAs = close ? String(close.what).trim().slice(CLOSE_MARK.length).trim().toLowerCase() : '';
    let task = null;
    for (const o of posts) {
        if (!controllerIds.has(o.agent_id)) continue;
        const parsed = parseTaskLine(o.what);
        if (parsed) task = parsed;
    }
    return { posts, definition, spec, closedAs, task };
}

/** One open candidate as the bundle shows it, or null when closed or not a candidate. */
export function candidateView(entry, controllerIds) {
    const parts = candidateParts(entry, controllerIds);
    if (!parts || parts.closedAs) return null;
    const { posts, definition, spec } = parts;
    const latest = new Map();
    for (const o of posts) if (o.agent_id && !controllerIds.has(o.agent_id)) latest.set(o.agent_id, String(o.what ?? '').trim());
    const by = kind => [...latest].filter(([, w]) => answerOf(w) === kind).map(([a]) => a);
    const posted = by('support'), objectors = by('object'), abstainers = by('abstain');
    const observers = (Array.isArray(spec.observers) ? spec.observers : []).filter(a => typeof a === 'string' && a && !controllerIds.has(a));
    const supporters = [...posted, ...observers.filter(a => !posted.includes(a) && !objectors.includes(a) && !abstainers.includes(a))];
    return {
        subject: entry.subject,
        name: spec.name ?? entry.subject,
        description: spec.description ?? '',
        completion_criteria: spec.completion_criteria ?? [],
        because: spec.because ?? [],
        posted_at: definition.timestamp ?? null,
        supporters,
        objectors,
        ...(abstainers.length ? { no_evidence: abstainers } : {}),
        ...(observers.length ? { observers, opens_unless_objected: true } : {}),
    };
}

/** Source subject -> the task a promoted candidate became, with its latest controller-posted state. */
export function taskedSubjects(entries, controllerIds) {
    const tasked = new Map();
    for (const entry of entries) {
        const parts = candidateParts(entry, controllerIds);
        if (!parts || parts.closedAs !== 'promoted') continue;
        const name = String(parts.spec.name ?? entry.subject);
        const status = parts.task?.status ?? 'created';
        const heldBy = status === 'held' ? (parts.task?.heldBy ?? null) : null;
        for (const source of parts.spec.because ?? []) if (source) tasked.set(String(source), { name, status, heldBy });
    }
    return tasked;
}

/**
 * The pull bundle's view of the board: every open candidate, and every other
 * subject with its count and the task it already became.
 *
 * @param {Array<object>} entries  opinions entries
 * @param {{controllerIds?: Iterable<string>}} [opts]
 * @returns {{candidates: Array<object>, subjects: Array<object>}}
 */
export function bundle(entries, { controllerIds = ['planner', 'curator'] } = {}) {
    const ids = new Set(controllerIds);
    const candidates = entries.map(e => candidateView(e, ids)).filter(Boolean);
    const tasked = taskedSubjects(entries, ids);
    const subjects = [];
    for (const entry of entries) {
        if (!entry?.subject || String(entry.subject).startsWith(CANDIDATE_PREFIX)) continue;
        const task = tasked.get(String(entry.subject));
        subjects.push({
            subject: entry.subject,
            count: entry.count ?? (Array.isArray(entry.agents) ? entry.agents.length : 0),
            ...(task ? { task: task.name, task_status: task.status } : {}),
            ...(task?.heldBy ? { held_by: task.heldBy } : {}),
        });
    }
    return { candidates, subjects };
}
