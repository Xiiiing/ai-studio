import test from 'node:test';
import assert from 'node:assert/strict';
import { parseHTML } from 'linkedom';
import { applyTeamMode, supervisorSupport, teamModeFields, teamCockpit } from '../ui/factory/team-cockpit.js';
import { ingestInventory, modelsView, registryPayload } from '../ui/factory/models.js';
import { createForm } from '../ui/factory/views.js';
import { ingestProjectFlow, teamFlowView } from '../ui/factory/team.js';

globalThis.location = { origin: 'http://127.0.0.1:8787' };
const pid = '012345abcdef';
const dom = html => parseHTML('<html><body>' + html + '</body></html>').document;
function inventory(bound = false) {
  return { revision: 'fixture-1', editable_profiles: { writer: { enabled: true, model: 'mock-manager' } },
    role_assignments: { screenwriter: 'writer', ...(bound ? { executive_producer: 'writer' } : {}) },
    roles: [{ role: 'screenwriter', label: '编剧', profile: 'writer' },
      { role: 'executive_producer', label: '总制片代理', optional: true, profile: bound ? 'writer' : null, configured: bound, enabled: bound }] };
}
function flow() {
  return { project_id: pid, revision: 7, teams: [], nodes: [], edges: [],
    task_board: { schema: 'ai-studio.team-tasks.v1', tasks: [{ id: 'research', label: '选题研究', owner: 'researcher', reviewer: 'story_editor', inputs: ['brief'], outputs: ['research'], depends_on: [], current: true, status: 'succeeded', record: { attempt: 1 } }] },
    supervision: { mode: 'delegated', records: [{ kind: 'story', status: 'selected', current_gate_version: true, requested_model: 'request-model-only', proposal: { choice: 'B', rationale: '保留明确因果', risks: ['需要人工确认版权'] } }] },
    delivery_readiness: { schema: 'ai-studio.delivery-readiness.v1', project_id: pid, project_revision: 7,
      checks: [{ id: 'fresh_acceptance', label: '新鲜验收', status: 'not_run' }],
      artifacts: [{ name: 'episode.mp4', label: '带字幕成片', url: '/media/production/factory-' + pid + '/release/v2-aaaaaaaaaaaa/episode.mp4' }],
      platform_checklist: [{ id: 'rights', label: '核验授权', status: 'human_required' }] } };
}

test('old backend keeps the existing guided default and disables delegation', () => {
  assert.deepEqual(supervisorSupport(null), { supported: false, configured: false });
  const document = dom(teamModeFields(null));
  assert(document.querySelector('option[value="guided"]').hasAttribute('selected'));
  assert(document.querySelector('option[value="delegated"]').hasAttribute('disabled'));
  const payload = applyTeamMode({ brief: '原创故事' }, {}, null);
  assert.equal(payload.team_mode, 'guided');
  assert.throws(() => applyTeamMode({}, { team_mode: 'delegated' }, null), /先在模型/);
});
test('explicit enabled and saved matching binding is required', () => {
  assert.equal(supervisorSupport(inventory()).configured, false);
  const info = inventory(true);
  assert.equal(supervisorSupport(info).configured, true);
  assert.equal(applyTeamMode({}, { team_mode: 'delegated' }, info).team_mode, 'delegated');
  assert.throws(() => applyTeamMode({}, { team_mode: 'automatic_publish' }, info), /无效/);
  info.role_assignments.executive_producer = '';
  assert.equal(supervisorSupport(info).configured, false);
});
test('new-project form includes the real team mode input with the permission boundary', () => {
  const document = dom(createForm(null, inventory(true)));
  assert(document.querySelector('form [name="team_mode"]'));
  assert(!document.querySelector('option[value="delegated"]').hasAttribute('disabled'));
  assert.match(document.body.textContent, /付费视频授权/);
  assert.match(document.body.textContent, /文本请求可能/);
});
test('optional manager can be bound and unbound without creating an implicit fallback', () => {
  const state = {};
  ingestInventory(state, inventory());
  assert.equal(state.modelsDraft.roles.executive_producer, '');
  assert(!Object.hasOwn(registryPayload(state).roles, 'executive_producer'));
  const document = dom(modelsView(state));
  assert(document.querySelector('#role-executive_producer option[value=""]'));
  assert(!document.querySelector('#role-executive_producer option[value=""]').hasAttribute('disabled'));
  state.modelsDraft.roles.executive_producer = 'writer';
  assert.equal(registryPayload(state).roles.executive_producer, 'writer');
  state.modelsDraft.roles.executive_producer = '';
  assert(!Object.hasOwn(registryPayload(state).roles, 'executive_producer'));
  assert.equal(state.modelsDraft.roles.screenwriter, 'writer');
});
test('legacy inventories do not gain an unsupported editable role', () => {
  const info = inventory(); info.roles.pop();
  const state = {}; ingestInventory(state, info);
  assert.deepEqual(state.modelsDraft.roles, { screenwriter: 'writer' });
  assert.deepEqual(registryPayload(state).roles, info.role_assignments);
});
test('tasks distinguish returned execution from independent approval', () => {
  const document = dom(teamCockpit(flow()));
  assert.match(document.body.textContent, /节点返回 ≠ 审核通过/);
  assert.match(document.querySelector('[data-team-task="research"]').textContent, /执行已返回/);
  assert.match(document.querySelector('[data-team-task="research"]').textContent, /审核职责/);
});
test('stale-generation task records do not become current successes', () => {
  const data = flow(); data.task_board.tasks[0].current = false;
  data.task_board.tasks[0].previous_run = data.task_board.tasks[0].record;
  const document = dom(teamCockpit(data));
  const task = document.querySelector('[data-team-task]');
  assert.match(task.textContent, /待执行/);
  assert.match(task.textContent, /旧版本记录已保留/);
  assert.doesNotMatch(task.textContent, /执行已返回/);
});
test('unresolved supervisor request is not represented as live execution or approval', () => {
  const data = flow(); data.supervision.records[0].status = 'requested';
  const document = dom(teamCockpit(data));
  assert.match(document.body.textContent, /完成情况未确认/);
  assert.match(document.body.textContent, /不代表已观看成片/);
  assert.match(document.body.textContent, /请求模型/);
  assert(!document.querySelector('[data-action="decision"]'));
});
test('untrusted agent output is escaped and never creates executable elements', () => {
  const data = flow(); const evil = '<img src=x onerror=alert(1)>';
  data.task_board.tasks[0].label = evil;
  data.supervision.records[0].proposal.rationale = evil;
  data.supervision.records[0].proposal.risks = [evil];
  data.delivery_readiness.artifacts[0].label = evil;
  const document = dom(teamCockpit(data));
  assert.equal(document.querySelectorAll('img,script,[onerror]').length, 0);
  assert.match(document.body.textContent, /<img src=x onerror=alert\(1\)>/);
});
for (const url of ['javascript:alert(1)', 'https://outside.invalid/episode.mp4', '/media/production/factory-abcdef012345/release/v2-aaaaaaaaaaaa/episode.mp4', '/media/production/factory-' + pid + '/release/v2-aaaaaaaaaaaa/../private.txt']) {
  test('unsafe or foreign material link is not actionable: ' + url, () => {
    const data = flow(); data.delivery_readiness.artifacts[0].url = url;
    assert.equal(dom(teamCockpit(data)).querySelectorAll('.handoff-files a').length, 0);
  });
}
test('stale project projection disables formerly valid delivery links', () => {
  assert.equal(dom(teamCockpit(flow())).querySelectorAll('.handoff-files a').length, 1);
  const document = dom(teamCockpit(flow(), { stale: true }));
  assert.equal(document.querySelectorAll('.handoff-files a').length, 0);
  assert.match(document.body.textContent, /旧交付链接已停用/);
});
test('mismatched readiness revision is not displayed and platform flags cannot grant approval', () => {
  const data = flow(); data.delivery_readiness.platform_ready = true;
  data.delivery_readiness.platform_checklist[0].status = 'passed';
  let document = dom(teamCockpit(data));
  assert.match(document.body.textContent, /平台审核未核验/);
  assert.match(document.body.textContent, /需人工核验/);
  data.delivery_readiness.project_revision = 6;
  document = dom(teamCockpit(data));
  assert.match(document.body.textContent, /尚无与当前版本匹配/);
  assert.equal(document.querySelectorAll('.handoff-files a').length, 0);
});
test('real team-flow view mounts the cockpit and rejects cross-project or older responses', () => {
  const state = { projectId: pid, project: { id: pid, revision: 7 } };
  assert(ingestProjectFlow(state, flow(), pid));
  assert.match(teamFlowView(state), /aria-label="团队指挥台"/);
  const other = flow(); other.project_id = 'abcdef012345';
  assert.equal(ingestProjectFlow(state, other, pid), false);
  const older = flow(); older.revision = 6;
  assert.equal(ingestProjectFlow(state, older, pid), false);
  state.project.revision = 8;
  assert.match(teamFlowView(state), /旧交付链接已停用/);
});
test('task contracts use Chinese field labels and prototype names cannot crash badges', () => {
  const data = flow(); data.task_board.tasks[0].status = '__proto__';
  const document = dom(teamCockpit(data));
  const task = document.querySelector('[data-team-task]');
  assert.match(task.textContent, /创作需求/);
  assert.match(task.textContent, /状态未记录/);
  assert.doesNotMatch(task.textContent, /brief/);
});
test('legacy flow falls back to the existing member receipts rather than invented tasks', () => {
  const data = { project_id: pid, revision: 7 };
  const document = dom(teamCockpit(data));
  assert.match(document.body.textContent, /尚无任务工单记录/);
  assert.equal(document.querySelectorAll('[data-team-task]').length, 0);
});
