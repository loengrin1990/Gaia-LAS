const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
class Element {
  constructor(tag = '') { this.tag = tag; this.children = []; this.textContent = ''; this.disabled = false; this.checked = false; this.value = ''; this.hidden = false; this.style = {}; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  closest() { return this.parentElement; }
}
const tick = () => new Promise(resolve => setImmediate(resolve));
function descendants(node) { return [node, ...node.children.flatMap(descendants)]; }
async function fixture(selection, prepared = false, extraChanges = [], lifecycle = {}) {
  const nodes = {};
  const get = id => nodes[id] || (nodes[id] = Object.assign(new Element(), {parentElement: new Element()}));
  let proposal = {id: 'request', version: 1, revision: 'v1', task: 'Проверить только бюджет.', evidence: ['Бюджет проекта подтверждён. Условия запуска не изменены.'], changes: [{action: 'Заменить фрагмент', before: 'Бюджет не подтверждён.', after: 'Бюджет подтверждён.', quote: 'Бюджет проекта подтверждён.'}, ...extraChanges]};
  if (prepared) proposal = null;
  let pending = prepared;
  const decisions = [], revisions = [];
  const api = async (action, payload) => {
    if (action === 'addressed/overview') return {version: 1, analysis_busy: Boolean(lifecycle.busy), prepared: pending ? [{id: 'prepared'}] : [], proposals: proposal ? [proposal] : []};
    if (action === 'addressed/inputs') return {id: 'prepared', task: 'Проверить бюджет.', before: 'Прежняя память.', evidence: ['Исходный факт.']};
    if (action === 'addressed/prepare') { pending = true; return {id: 'prepared', before: 'Прежняя память.', evidence: ['Исходный факт.']}; }
    if (action === 'addressed/analyze') { lifecycle.busy = true; return {state: 'running', analysis_busy: true}; }
    if (action === 'addressed/job') return {state: lifecycle.cancelled ? 'cancelled' : (lifecycle.busy ? 'running' : 'done'), analysis_busy: Boolean(lifecycle.busy), message: (lifecycle.cancelled ? 'Проход отменён. ' : '') + (lifecycle.busy ? 'Локальный запрос ещё выполняется, новый анализ пока недоступен.' : 'Локальный запрос завершён; можно подготовить новый проход.')};
    if (action === 'addressed/selection') return selection(payload);
    if (action === 'addressed/revise') { revisions.push(payload); proposal.changes[0].after = payload.texts[0]; proposal.revision = 'v2'; return {}; }
    if (action === 'addressed/decide') { decisions.push(payload); proposal = null; pending = false; if (payload.decision === 'cancel') lifecycle.cancelled = true; return {}; }
    throw Error('Unexpected request');
  };
  const context = {document: {createElement: tag => new Element(tag), createTextNode: text => Object.assign(new Element(), {textContent: text}), querySelector: () => null}, $: get, api, refreshMemory: async () => {}, selected: new Map(), setTimeout: lifecycle.timer || setTimeout};
  vm.runInNewContext(fs.readFileSync(require.resolve('../gaia/static/addressed_memory.js'), 'utf8'), context);
  await tick();
  const tree = () => descendants(get('addressedProposals'));
  return {nodes, decisions, revisions, tree, checkbox: tree().find(n => n.tag === 'input'), review: tree().find(n => n.textContent === 'Просмотреть выбранные изменения'), accept: tree().find(n => n.textContent === 'Подтвердить показанные изменения')};
}

test('explicit preview enables confirmation of the exact reviewed selection', async () => {
  const fixtureState = await fixture(async payload => ({...payload, digest: 'reviewed', after: 'Бюджет подтверждён.'}));
  assert.equal(fixtureState.accept.disabled, true);
  assert.equal(fixtureState.checkbox.checked, false);
  assert.equal(fixtureState.nodes.prepareMemory.parentElement.style.display, 'none');
  fixtureState.checkbox.checked = true; fixtureState.checkbox.onchange();
  await fixtureState.review.onclick();
  assert.equal(fixtureState.accept.disabled, false);
  assert.ok(fixtureState.tree().some(n => n.textContent.includes('Полная память после выбранных изменений')));
  await fixtureState.accept.onclick();
  assert.equal(fixtureState.decisions[0].digest, 'reviewed');
  assert.deepEqual([...fixtureState.decisions[0].selected], [0]);
  assert.equal(fixtureState.decisions[0].decision, 'accept');
});

test('late preview cannot confirm a selection changed while loading', async () => {
  let resolve;
  const state = await fixture(payload => new Promise(done => { resolve = () => done({...payload, digest: 'old', after: 'Late result'}); }));
  state.checkbox.checked = true; state.checkbox.onchange();
  const loading = state.review.onclick();
  state.checkbox.checked = false; state.checkbox.onchange();
  resolve(); await loading;
  assert.equal(state.accept.disabled, true);
  assert.ok(state.tree().every(n => !n.textContent.includes('Late result')));
  await state.accept.onclick();
  assert.equal(state.decisions.length, 0);
});

test('editing a replacement requires saving and a fresh preview', async () => {
  const state = await fixture(async payload => ({...payload, digest: 'before-edit', after: 'Бюджет подтверждён.'}));
  state.checkbox.checked = true; state.checkbox.onchange(); await state.review.onclick();
  const editor = state.tree().find(n => n.tag === 'textarea');
  editor.value = 'Уточнённое предложение.'; editor.oninput();
  assert.equal(state.accept.disabled, true); assert.equal(state.review.disabled, true);
  await state.tree().find(n => n.textContent === 'Сохранить правку предложения').onclick();
  assert.equal(state.revisions[0].revision, 'v1');
  assert.equal(state.revisions[0].texts[0], 'Уточнённое предложение.');
  assert.equal(state.decisions.length, 0);
  assert.equal(state.tree().find(n => n.textContent === 'Подтвердить показанные изменения').disabled, true);
});

test('a restored prepared request displays its frozen task and sources', async () => {
  const state = await fixture(async () => {}, true);
  assert.ok(state.nodes.addressedInputs.textContent.includes('Проверить бюджет.'));
  assert.ok(state.nodes.addressedInputs.textContent.includes('Исходный факт.'));
  assert.equal(state.nodes.addressedAnalyze.disabled, false);
  assert.equal(state.nodes.addressedCancel.disabled, false);
});

test('a restored proposal shows its task, source number and semantic review cue', async () => {
  const state = await fixture(async () => {});
  assert.ok(state.tree().some(n => n.textContent.includes('Задача этого прохода\nПроверить только бюджет.')));
  assert.ok(state.tree().some(n => n.textContent.includes('дословная цитата, источник 1')));
  assert.ok(state.tree().some(n => n.textContent.includes('контекст цитаты') && n.textContent.includes('Условия запуска не изменены.')));
  assert.ok(state.tree().some(n => n.textContent.includes('именно эту замену')));
  assert.ok(state.tree().some(n => n.textContent.includes('постоянная обязанность') && n.textContent.includes('оставьте её невыбранной')));
});

test('partial acceptance explicitly explains that unselected operations are not accepted', async () => {
  const state = await fixture(async payload => ({...payload, digest: 'one-of-two', after: 'Бюджет подтверждён.'}), false,
    [{action: 'Заменить фрагмент', before: 'Ждём отчёт.', after: 'Ждём подпись.', quote: 'Отчёт пока не подписан.'}]);
  state.checkbox.checked = true; state.checkbox.onchange(); await state.review.onclick(); await state.accept.onclick();
  assert.deepEqual([...state.decisions[0].selected], [0]);
  assert.ok(state.nodes.addressedProgress.textContent.includes('Сохранено изменений: 1 из 2'));
  assert.ok(state.nodes.addressedProgress.textContent.includes('Невыбранные изменения не приняты'));
});

test('logical cancellation keeps analysis disabled until the worker actually releases its slot', async () => {
  const timers = [], lifecycle = {busy: false, timer: callback => timers.push(callback)};
  const state = await fixture(async () => {}, true, [], lifecycle);
  const analyzing = state.nodes.addressedAnalyze.onclick(); await tick();
  await state.nodes.addressedRefresh.onclick(); // Refresh must not disable cancellation.
  assert.equal(state.nodes.addressedCancel.disabled, false);
  await state.nodes.addressedCancel.onclick();
  timers.shift()(); await tick();
  assert.equal(state.nodes.addressedAnalyze.disabled, true);
  assert.equal(state.nodes.addressedPrepare.disabled, true);
  assert.ok(state.nodes.addressedCompute.textContent.includes('новый анализ пока недоступен'));
  assert.ok(state.nodes.addressedProgress.textContent.includes('Проход отменён'));
  lifecycle.busy = false; timers.shift()(); await analyzing;
  assert.equal(state.nodes.addressedPrepare.disabled, false);
  assert.ok(state.nodes.addressedCompute.textContent.includes('завершён'));
  await state.nodes.addressedPrepare.onclick();
  assert.equal(state.nodes.addressedAnalyze.disabled, false);
});

test('a restored page polls a busy compute slot and enables the prepared analysis after release', async () => {
  const timers = [], lifecycle = {busy: true, timer: callback => timers.push(callback)};
  const state = await fixture(async () => {}, true, [], lifecycle);
  assert.equal(state.nodes.addressedAnalyze.disabled, true);
  assert.ok(state.nodes.addressedCompute.textContent.includes('новый анализ пока недоступен'));
  lifecycle.busy = false; timers.shift()(); await tick();
  assert.equal(state.nodes.addressedAnalyze.disabled, false);
  assert.ok(state.nodes.addressedCompute.textContent.includes('завершён'));
});

test('normal completion clears the compute indicator when the worker finishes before the first refresh', async () => {
  const timers = [], lifecycle = {busy: false, timer: callback => timers.push(callback)};
  const state = await fixture(async () => {}, true, [], lifecycle);
  const analyzing = state.nodes.addressedAnalyze.onclick(); await tick();
  lifecycle.busy = false; timers.shift()(); await analyzing;
  assert.equal(state.nodes.addressedPrepare.disabled, false);
  assert.ok(state.nodes.addressedCompute.textContent.includes('завершён'));
  assert.ok(!state.nodes.addressedCompute.textContent.includes('пока недоступен'));
});

test('automatic compute polling preserves an unsaved proposal edit', async () => {
  const timers = [], lifecycle = {busy: true, timer: callback => timers.push(callback)};
  const state = await fixture(async () => {}, false, [], lifecycle);
  const editor = state.tree().find(n => n.tag === 'textarea');
  editor.value = 'Ещё не сохранённая правка.'; editor.oninput();
  lifecycle.busy = false; timers.shift()(); await tick();
  assert.equal(state.tree().find(n => n.tag === 'textarea'), editor);
  assert.equal(editor.value, 'Ещё не сохранённая правка.');
  assert.equal(state.accept.disabled, true);
  assert.equal(state.review.disabled, true);
  assert.equal(state.revisions.length, 0);
});
