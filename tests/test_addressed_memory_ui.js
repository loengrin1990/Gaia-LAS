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
async function fixture(selection, prepared = false, extraChanges = []) {
  const nodes = {};
  const get = id => nodes[id] || (nodes[id] = Object.assign(new Element(), {parentElement: new Element()}));
  let proposal = {id: 'request', version: 1, revision: 'v1', task: 'Проверить только бюджет.', evidence: ['Бюджет проекта подтверждён. Условия запуска не изменены.'], changes: [{action: 'Заменить фрагмент', before: 'Бюджет не подтверждён.', after: 'Бюджет подтверждён.', quote: 'Бюджет проекта подтверждён.'}, ...extraChanges]};
  if (prepared) proposal = null;
  const decisions = [], revisions = [];
  const api = async (action, payload) => {
    if (action === 'addressed/overview') return {version: 1, prepared: prepared ? [{id: 'prepared'}] : [], proposals: proposal ? [proposal] : []};
    if (action === 'addressed/inputs') return {id: 'prepared', task: 'Проверить бюджет.', before: 'Прежняя память.', evidence: ['Исходный факт.']};
    if (action === 'addressed/selection') return selection(payload);
    if (action === 'addressed/revise') { revisions.push(payload); proposal.changes[0].after = payload.texts[0]; proposal.revision = 'v2'; return {}; }
    if (action === 'addressed/decide') { decisions.push(payload); proposal = null; return {}; }
    throw Error('Unexpected request');
  };
  const context = {document: {createElement: tag => new Element(tag), createTextNode: text => Object.assign(new Element(), {textContent: text}), querySelector: () => null}, $: get, api, refreshMemory: async () => {}, selected: new Map(), setTimeout};
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
});

test('partial acceptance explicitly explains that unselected operations are not accepted', async () => {
  const state = await fixture(async payload => ({...payload, digest: 'one-of-two', after: 'Бюджет подтверждён.'}), false,
    [{action: 'Заменить фрагмент', before: 'Ждём отчёт.', after: 'Ждём подпись.', quote: 'Отчёт пока не подписан.'}]);
  state.checkbox.checked = true; state.checkbox.onchange(); await state.review.onclick(); await state.accept.onclick();
  assert.deepEqual([...state.decisions[0].selected], [0]);
  assert.ok(state.nodes.addressedProgress.textContent.includes('Сохранено изменений: 1 из 2'));
  assert.ok(state.nodes.addressedProgress.textContent.includes('Невыбранные изменения не приняты'));
});
