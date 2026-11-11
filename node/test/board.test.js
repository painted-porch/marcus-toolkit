import test from 'node:test';
import assert from 'node:assert/strict';
import { createBoard, outcomeOf } from '../board.js';

/** A client that answers from a script and records every call. */
function fakeClient(script) {
    const calls = [];
    return {
        calls,
        async call(tool, args) {
            calls.push({ tool, args });
            const next = script[tool];
            const reply = Array.isArray(next) ? (next.length > 1 ? next.shift() : next[0]) : next;
            return reply ?? { success: true };
        },
    };
}

const ASSIGNED = { success: true, task: { id: 't1', name: 'Hold the north ford', instructions: 'x' }, lease_epoch: 7 };
const RETRY = { success: false, retry_after_seconds: 1, drain: { tasks_claimable: 0, tasks_in_flight: 2 } };
const DRAINED = { success: false, status: 'BOARD_DRAINED', should_exit: true, reason: 'complete' };

test('outcomes are classified the way the Minecraft runs count them', () => {
    assert.equal(outcomeOf('request_next_task', ASSIGNED), 'assigned');
    assert.equal(outcomeOf('request_next_task', RETRY), 'retry');
    assert.equal(outcomeOf('request_next_task', DRAINED), 'drained');
    assert.equal(outcomeOf('report_task_progress', { success: true }), 'ok');
    assert.equal(outcomeOf('report_task_progress', { success: false }), 'failed');
    assert.equal(outcomeOf('give_opinion', { success: false, transport_error: true }), 'transport_error');
});

test('registration carries the project id and reads the objective once', async () => {
    const client = fakeClient({
        register_agent: { success: true },
        query_project_history: { success: true, data: [{ subject: 'objective', opinions: [{ what: 'Hold the river line.' }] }] },
    });
    const b = createBoard({ agent: 'blue-1', projectId: 'p-blue', client });
    await b.register();
    assert.deepEqual(client.calls[0], { tool: 'register_agent', args: { agent_id: 'blue-1', name: 'blue-1', role: 'commander', skills: ['command'], project_id: 'p-blue' } });
    assert.equal(client.calls[1].args.kind, 'objective');
    assert.equal(b.state.objective, 'Hold the river line.');
});

test('a pull absorbs retries without a model turn, then holds the task and its lease epoch', async () => {
    const client = fakeClient({ register_agent: { success: true }, request_next_task: [RETRY, RETRY, ASSIGNED], query_project_history: { success: true, data: [] }, get_task_context: { success: true, context: {} } });
    const sleeps = [];
    const saved = [];
    const log = [];
    const b = createBoard({ agent: 'blue-1', projectId: 'p-blue', client, sleep: ms => sleeps.push(ms), save: s => saved.push(s), log: r => log.push(r) });
    const r = await b.pull();
    assert.equal(r.status, 'assigned');
    assert.equal(r.task.id, 't1');
    assert.equal(r.lease_epoch, 7);
    assert.deepEqual(b.state.task, { id: 't1', name: 'Hold the north ford' });
    assert.deepEqual(sleeps, [1000, 1000]);
    assert.deepEqual(saved.at(-1), { task: { id: 't1', name: 'Hold the north ford' }, leaseEpoch: 7 });
    const pulls = log.filter(x => x.tool === 'request_next_task').map(x => x.outcome);
    assert.deepEqual(pulls, ['retry', 'retry', 'assigned']);
    for (const k of ['at', 'agent', 'tool', 'args', 'outcome', 'ms', 'reply']) assert.ok(k in log[0], k);
    assert.ok(client.calls.some(c => c.tool === 'get_task_context' && c.args.task_id === 't1'));
});

test('a drained board ends the pull, and a pull without absorb returns the retry', async () => {
    const client = fakeClient({ register_agent: { success: true }, request_next_task: [DRAINED], query_project_history: { success: true, data: [] } });
    const b = createBoard({ agent: 'red-1', projectId: 'p-red', client });
    assert.equal((await b.pull()).status, 'drained');
    const client2 = fakeClient({ register_agent: { success: true }, request_next_task: [RETRY], query_project_history: { success: true, data: [] } });
    const b2 = createBoard({ agent: 'red-2', projectId: 'p-red', client: client2 });
    assert.equal((await b2.pull({ absorb: false })).status, 'retry');
});

test('a report carries the lease epoch and the evidence, and completion clears the hold', async () => {
    const client = fakeClient({ report_task_progress: { success: true } });
    const saved = [];
    const b = createBoard({ agent: 'blue-1', projectId: 'p-blue', client, load: () => ({ task: { id: 't1', name: 'n' }, leaseEpoch: 7 }), save: s => saved.push(s) });
    assert.deepEqual(b.state.task, { id: 't1', name: 'n' });
    await b.report({ status: 'in_progress', progress: 30, message: 'moving', subject: 'ford.north' });
    assert.equal(client.calls[0].args.lease_epoch, 7);
    assert.equal(client.calls[0].args.subject, 'ford.north');
    assert.equal(b.state.task.id, 't1');
    await b.report({ status: 'completed', message: 'held', evidence: { check: 'no_enemy_within', ok: true } });
    assert.deepEqual(client.calls[1].args.evidence, { check: 'no_enemy_within', ok: true });
    assert.equal(client.calls[1].args.progress, 100);
    assert.equal(b.state.task, null);
    assert.equal(saved.at(-1), null);
});

test('a decision is recorded as what because why, with an optional subject, never as support', async () => {
    const client = fakeClient({ log_decision: { success: true } });
    const b = createBoard({ agent: 'blue-1', projectId: 'p-blue', client });
    await b.decide({ what: 'withdrew block 3 across the ford', why: 'losses passed 15% in one turn', subject: 'ford.north' });
    assert.deepEqual(client.calls[0].args, { agent_id: 'blue-1', decision: 'DECISION: withdrew block 3 across the ford because losses passed 15% in one turn.', task_id: '', subject: 'ford.north' });
});

test('opinions and artifacts use the project and the task held', async () => {
    const client = fakeClient({ give_opinion: { success: true }, log_artifact: { success: true }, query_project_history: { success: true, data: [] } });
    const b = createBoard({ agent: 'blue-1', projectId: 'p-blue', client, projectRoot: '/w', load: () => ({ task: { id: 't1', name: 'n' }, leaseEpoch: 1 }) });
    await b.opinion({ subject: 'enemy.massing.north', what: 'red density rising north of the ford', why: 'umpire reports contact', aboutTask: true });
    assert.equal(client.calls[0].args.task_id, 't1');
    await b.artifact({ filename: 'orders-turn-3.txt', content: 'hold' });
    assert.equal(client.calls[1].args.project_root, '/w');
    await b.opinions({ limit: 5 });
    assert.deepEqual(client.calls[2].args, { project_id: 'p-blue', query_type: 'opinions', limit: 5 });
    await b.decisions();
    assert.equal(client.calls[3].args.kind, 'decision');
});
