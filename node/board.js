/**
 * A commander's board client: the pull loop as a plain object, no model.
 *
 * The behaviour is the Minecraft harness's src/marcus/board-tools.js reduced
 * to what a commander whose body is an umpire needs: register with the
 * project (Marcus hands work only to a registered agent), pull, absorb
 * "ask again" replies without a model turn, hold the task and its lease
 * epoch across a restart, report with the lease epoch and the umpire's
 * evidence, leave markers, record decisions, read the board. Every call is
 * one record in board-calls.jsonl with the same six fields the Minecraft
 * runs measure: at, agent, tool, args, outcome, ms, reply.
 *
 * Nothing here talks to another commander. The board is the only channel.
 */

const sleepMs = ms => new Promise(r => setTimeout(r, ms));

/** Append one JSON line per record to `path`, synchronously, so a kill loses nothing. */
export function jsonlLogger(path, fs) {
    return record => fs.appendFileSync(path, JSON.stringify(record) + '\n');
}

/** Classify a reply the way the Minecraft harness does, so idle polls are counted where they always were. */
export function outcomeOf(tool, reply) {
    if (reply?.transport_error || reply?.rpc_error) return 'transport_error';
    if (reply?.status === 'BOARD_DRAINED') return 'drained';
    if (tool === 'request_next_task') return reply?.success === true ? 'assigned' : 'retry';
    return reply?.success === false ? 'failed' : 'ok';
}

/**
 * @param {object} deps
 * @param {string} deps.agent          the commander's id on the board
 * @param {string} deps.projectId      its side's project
 * @param {object} deps.client         something with `call(tool, args)` that never throws
 * @param {string} [deps.projectRoot]  where artifacts are written (not a repository)
 * @param {Function} [deps.log]        one record per board call
 * @param {Function} [deps.load]       read the held task back after a restart
 * @param {Function} [deps.save]       persist the held task (null clears it)
 * @param {Function} [deps.sleep]      for tests
 * @param {Function} [deps.now]        clock, for tests
 * @param {string} [deps.role]
 * @param {string[]} [deps.skills]
 */
export function createBoard({
    agent,
    projectId,
    client,
    projectRoot = '',
    log,
    load,
    save,
    sleep = sleepMs,
    now = Date.now,
    role = 'commander',
    skills = ['command'],
}) {
    const held = load?.() ?? null;
    const state = {
        registered: false,
        task: held?.task ?? null,
        leaseEpoch: held?.leaseEpoch ?? null,
        objective: null,
    };
    const remember = () => save?.(state.task ? { task: state.task, leaseEpoch: state.leaseEpoch } : null);

    const board = async (tool, args) => {
        const started = now();
        const reply = (await client.call(tool, args)) ?? { success: false, error: 'no reply' };
        log?.({ at: new Date(now()).toISOString(), agent, tool, args, outcome: outcomeOf(tool, reply), ms: now() - started, reply });
        return reply;
    };

    const register = async () => {
        const reply = await board('register_agent', { agent_id: agent, name: agent, role, skills, project_id: projectId });
        if (reply.success === false) return reply;
        state.registered = true;
        const primer = await board('query_project_history', { project_id: projectId, query_type: 'opinions', kind: 'objective', limit: 1 });
        const what = primer?.success !== false && Array.isArray(primer?.data) ? primer.data[0]?.opinions?.[0]?.what : undefined;
        if (typeof what === 'string' && what.trim()) state.objective = what.trim();
        return reply;
    };

    const ensureRegistered = async () => {
        if (!state.registered) {
            const reply = await register();
            if (reply.success === false) return reply;
        }
        return null;
    };

    /**
     * Ask for work. With `absorb`, an "ask again" reply is waited out here, one
     * board call per ask, so idle time costs wall-clock and no model turn.
     *
     * With `quiet`, an assignment fetches neither the task context nor the board's
     * opinions: the caller is blind to the board by design and must not read it.
     *
     * @param {{absorb?: boolean, maxAsks?: number, waitMs?: number, quiet?: boolean}} [opts]
     * @returns {Promise<object>} {status:'assigned', task, context, opinions, objective?}
     *     | {status:'retry', reply} | {status:'drained', reply} | {status:'error', reply}
     */
    const pull = async ({ absorb = true, maxAsks = Infinity, waitMs = null, quiet = false } = {}) => {
        const refused = await ensureRegistered();
        if (refused) return { status: 'error', reply: refused };
        if (state.task) return { status: 'holding', task: state.task };
        let asks = 0;
        for (;;) {
            const reply = await board('request_next_task', { agent_id: agent });
            asks++;
            if (reply.success === true && reply.task) {
                state.task = { id: reply.task.id, name: reply.task.name };
                state.leaseEpoch = reply.lease_epoch ?? null;
                remember();
                const [context, opinions] = quiet ? [null, null] : await Promise.all([
                    board('get_task_context', { task_id: reply.task.id }),
                    board('query_project_history', { project_id: projectId, query_type: 'opinions', limit: 20 }),
                ]);
                const out = { status: 'assigned', task: reply.task, lease_epoch: state.leaseEpoch, context, opinions };
                if (state.objective) out.objective = state.objective;
                return out;
            }
            if (reply.status === 'BOARD_DRAINED') return { status: 'drained', reply };
            if (reply.transport_error || reply.rpc_error) return { status: 'error', reply };
            if (!absorb || asks >= maxAsks) return { status: 'retry', reply };
            const wait = waitMs ?? Math.max(1, Number(reply.retry_after_seconds ?? 30)) * 1000;
            await sleep(wait);
        }
    };

    /**
     * Report on the held task. Completion or blocking clears the hold.
     *
     * @param {{status: string, progress?: number, message: string, subject?: string, observation?: string, evidence?: object}} args
     */
    const report = async args => {
        if (!state.task) return { success: false, error: 'You hold no task.' };
        const reply = await board('report_task_progress', {
            agent_id: agent,
            task_id: state.task.id,
            status: args.status,
            progress: args.progress ?? (args.status === 'completed' ? 100 : 50),
            message: args.message,
            ...(args.subject?.trim() ? { subject: args.subject.trim() } : {}),
            ...(args.observation?.trim() ? { observation: args.observation.trim() } : {}),
            ...(args.evidence ? { evidence: args.evidence } : {}),
            ...(state.leaseEpoch ? { lease_epoch: state.leaseEpoch } : {}),
        });
        if (reply.success !== false && args.status !== 'in_progress') {
            state.task = null;
            state.leaseEpoch = null;
            remember();
        }
        return reply;
    };

    // Every write needs a registered agent: the kernel records an opinion or a
    // decision against the project the agent registered to (ADR-0013 K6), and
    // refuses one from an agent it has not seen. A commander that answers a
    // candidate before its first pull was refused for exactly this
    // (no-hand rehearsal 20261111T111205), so the registration is ensured here
    // rather than left to the pull.
    const registered = async call => {
        const refused = await ensureRegistered();
        return refused ?? call();
    };

    const opinion = ({ subject, what, why = '', impact = '', aboutTask = false }) =>
        registered(() => board('give_opinion', { agent_id: agent, subject, what, why, impact, ...(aboutTask && state.task ? { task_id: state.task.id } : {}) }));

    /** A decision is what the commander did and why; it never counts toward a candidate. */
    const decide = ({ what, why, subject, kind = 'decision' }) =>
        registered(() => board('log_decision', {
            agent_id: agent,
            decision: `${kind === 'decision' ? 'DECISION' : 'THOUGHT'}: ${String(what).trim().replace(/\.+$/, '')} because ${String(why).trim().replace(/\.+$/, '')}.`,
            task_id: state.task?.id ?? '',
            ...(subject?.trim() ? { subject: subject.trim() } : {}),
        }));

    const artifact = ({ filename, content, description = '', artifact_type = 'world-state' }) =>
        state.task
            ? registered(() => board('log_artifact', { task_id: state.task.id, filename, content, artifact_type, project_root: projectRoot, description }))
            : Promise.resolve({ success: false, error: 'You hold no task.' });

    const opinions = ({ limit = 50, windowMinutes = null, kind = null } = {}) =>
        board('query_project_history', {
            project_id: projectId,
            query_type: 'opinions',
            limit,
            ...(windowMinutes ? { window_minutes: windowMinutes } : {}),
            ...(kind ? { kind } : {}),
        });

    const decisions = ({ limit = 20 } = {}) =>
        board('query_project_history', { project_id: projectId, query_type: 'decisions', kind: 'decision', limit });

    const status = () => board('get_project_status', {});

    return { state, register, pull, report, opinion, decide, artifact, opinions, decisions, status };
}
