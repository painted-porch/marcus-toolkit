import test from 'node:test';
import assert from 'node:assert/strict';
import { bundle, candidateView, parseTaskLine, answerOf, POST_MARK, CLOSE_MARK } from '../candidates.js';

const spec = { name: 'Hold the north ford', description: 'd', completion_criteria: ['c'], because: ['enemy.north'] };
const op = (agent_id, what, timestamp) => ({ agent_id, what, timestamp });
const entry = (subject, opinions, agents) => ({ subject, agents: agents ?? [...new Set(opinions.map(o => o.agent_id))], opinions });

test('an open candidate shows its supporters, objectors and abstainers', () => {
    const e = entry('candidate.hold.the.north.ford', [
        op('planner', POST_MARK + JSON.stringify(spec), '1'),
        op('blue-1', 'my block is 60 px off', '2'),
        op('blue-2', 'OBJECT: already held', '3'),
        op('blue-3', 'NO EVIDENCE: I am south', '4'),
    ]);
    const v = candidateView(e, new Set(['planner']));
    assert.equal(v.name, 'Hold the north ford');
    assert.deepEqual(v.supporters, ['blue-1']);
    assert.deepEqual(v.objectors, ['blue-2']);
    assert.deepEqual(v.no_evidence, ['blue-3']);
    assert.deepEqual(v.because, ['enemy.north']);
});

test('a closed candidate is not shown, and its source subjects carry the task it became', () => {
    const e = entry('candidate.hold.the.north.ford', [
        op('planner', POST_MARK + JSON.stringify(spec), '1'),
        op('blue-1', 'yes', '2'),
        op('blue-2', 'yes', '3'),
        op('planner', `${CLOSE_MARK} promoted`, '4'),
        op('planner', 'TASK: held by blue-1', '5'),
    ]);
    const marker = entry('enemy.north', [op('blue-4', 'massing', '0')]);
    const b = bundle([e, marker], { controllerIds: ['planner'] });
    assert.deepEqual(b.candidates, []);
    assert.deepEqual(b.subjects, [{ subject: 'enemy.north', count: 1, task: 'Hold the north ford', task_status: 'held', held_by: 'blue-1' }]);
});

test('only the controller closes, and marks parse the way the Python rules read them', () => {
    const e = entry('candidate.x', [op('planner', POST_MARK + JSON.stringify({ ...spec, name: 'x' }), '1'), op('blue-1', `${CLOSE_MARK} evaporated`, '2')]);
    assert.ok(candidateView(e, new Set(['planner'])), 'an agent cannot close a candidate');
    assert.equal(answerOf('OBJECT: no'), 'object');
    assert.equal(answerOf('no evidence: nothing'), 'abstain');
    assert.equal(answerOf('I agree'), 'support');
    assert.deepEqual(parseTaskLine('TASK: blocked'), { status: 'blocked', heldBy: null });
    assert.equal(parseTaskLine('TASK: nonsense'), null);
});
