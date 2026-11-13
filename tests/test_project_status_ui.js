const assert = require('node:assert/strict');
const {test} = require('node:test');
const fs = require('node:fs');
const vm = require('node:vm');

class Element {
  constructor() { this.children = []; this.textContent = ''; this.value = ''; }
  appendChild(child) { this.children.push(child); return child; }
  set innerHTML(_value) { this.children = []; }
}
const html = fs.readFileSync(require.resolve('../gaia/static/index.html'), 'utf8');
const start = html.indexOf('    function projectStatusLabel(');
const end = html.indexOf('    function openSelectedProjectInDialog()', start);
assert.ok(start >= 0 && end > start);
function fixture(record) {
  const nodes = Object.fromEntries(['projectList', 'projectDetails', 'projectMeta', 'projectCode', 'projectTitle', 'projectGroup', 'projectStatus', 'project'].map(id => [id, new Element()]));
  const context = {document: {createElement: () => new Element(), getElementById: id => nodes[id]}, projectRecords: record ? [record] : [], selectedProjectRecord: () => record, emptyCard: (_title, text) => Object.assign(new Element(), {textContent: text})};
  vm.createContext(context);
  vm.runInContext(html.slice(start, end), context);
  return {context, nodes};
}
function text(node) { return [node.textContent, ...node.children.map(text)].join(' '); }

test('project rows show readable status and structure independently of model readiness', () => {
  for (const [status, label] of [['active', 'Активный'], ['draft', 'Черновик'], ['archived', 'Архив']]) {
    const record = {name: 'Север', title: 'Север', code: 'N', status, health: 'ok'};
    const {context, nodes} = fixture(record);
    context.renderProjectList();
    assert.ok(text(nodes.projectList).includes(label));
    assert.ok(text(nodes.projectList).includes('Структура в порядке'));
    assert.doesNotMatch(text(nodes.projectList), /active|draft|archived|health:|готов к работе/);
    nodes.projectList.children[0].onclick();
    assert.equal(nodes.project.value, 'Север');
    assert.equal(nodes.projectStatus.value, status, 'form keeps the API enum');
    assert.equal(record.status, status, 'rendering does not mutate stored status');
  }
});

test('details and dialogue metadata explain structure without raw status values', () => {
  const {context, nodes} = fixture({name: 'Север', status: 'draft', health: 'needs_attention'});
  context.syncProjectManagementForm();
  context.renderProjectDetails();
  assert.ok(text(nodes.projectDetails).includes('Черновик'));
  assert.ok(text(nodes.projectDetails).includes('Нужна проверка структуры'));
  assert.ok(text(nodes.projectDetails).includes('Индекс памяти'));
  assert.ok(text(nodes.projectMeta).includes('Нужна проверка структуры'));
  assert.doesNotMatch(text(nodes.projectDetails), /needs_attention|Health|Graph index/);
});

test('missing and unknown statuses never imply a healthy structure', () => {
  for (const value of [undefined, null, '', 'future_state', '__proto__']) {
    const {context, nodes} = fixture({name: 'Север', status: value, health: value});
    context.renderProjectList();
    context.renderProjectDetails();
    assert.ok(text(nodes.projectList).includes('Статус не указан'));
    assert.ok(text(nodes.projectList).includes('Структура не проверена'));
    assert.doesNotMatch(text(nodes.projectList), /Структура в порядке|future_state|__proto__/);
  }
});

test('empty project list still gives a next-step state', () => {
  const {context, nodes} = fixture(null);
  context.renderProjectList();
  context.renderProjectDetails();
  assert.ok(text(nodes.projectList).includes('Проекты не найдены'));
  assert.ok(text(nodes.projectDetails).includes('Выбери проект'));
});
