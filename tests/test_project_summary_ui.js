const assert = require('assert');
const fs = require('fs');
const vm = require('vm');

class Element {
  constructor() { this.children=[]; this.className=''; this.textContent=''; this.onclick=null; this.checked=false; this.value=''; this.open=false; }
  appendChild(child) { this.children.push(child); return child; }
  append(...children) { children.forEach(child=>this.appendChild(child)); }
  get childElementCount() { return this.children.length; }
  set innerHTML(_value) { this.children=[]; }
}

const overview = new Element();
const form = new Element();
const advanced = new Element();
const actorPresence = new Element();
const deadlinePresence = new Element();
const related = new Element();
const radios = Object.fromEntries(['requirement','decision','risk','open_question','action'].map(type=>[type,new Element()]));
form.reset = () => Object.values(radios).forEach(input=>{ input.checked=false; });
const document = {
  createElement: () => new Element(),
  getElementById: id => ({contextOverview:overview,contextSearchForm:form,contextActorPresence:actorPresence,contextDeadlinePresence:deadlinePresence,contextRelated:related}[id]),
  querySelector: selector => {
    if (selector === '.context-advanced') return advanced;
    const match = selector.match(/value="([^"]+)"/);
    return match ? radios[match[1]] : null;
  },
};
let searchRuns = 0;
const html = fs.readFileSync(require.resolve('../gaia/static/index.html'), 'utf8');
const start = html.indexOf('    const contextOverviewTypes=');
const end = html.indexOf('    async function loadJourneySummary()', start);
assert.ok(start >= 0 && end > start, 'production overview functions not found');
const context = {
  document,
  lastContextOverview: null,
  populateContextActors: () => {},
  renderContextSearchCard: () => new Element(),
  showScreen: () => {},
  runContextSearch: () => { searchRuns += 1; },
};
vm.createContext(context);
vm.runInContext(`${html.slice(start, end)};globalThis.renderContextOverview=renderContextOverview;`, context);

context.renderContextOverview({
  current_context_count: 28,
  workflow: {confirmed: 28,pending_total:3,conflicted:1},
  counts: {requirement:7,decision:5,risk:4,open_question:12,action:0},
  attention: {actions_without_actor:2,actions_without_deadline:3,related_items:1},
  actors: [], highlights: {},
});
const typeCards = overview.children[1].children;
assert.deepStrictEqual(typeCards.map(card=>card.textContent), ['Требования: 7','Решения: 5','Риски: 4','Открытые вопросы: 12','Действия: 0']);
for (const [index,type] of ['requirement','decision','risk','open_question','action'].entries()) {
  typeCards[index].onclick();
  assert.strictEqual(radios[type].checked, true, `card for ${type} sets its singular filter`);
  assert.strictEqual(Object.values(radios).filter(input=>input.checked).length, 1);
}
const attentionCards = overview.children[2].children.map(card=>card.textContent);
assert.ok(attentionCards.includes('Риски требуют внимания: 4'));
assert.ok(attentionCards.includes('Открытые вопросы: 12'));
assert.ok(attentionCards.includes('Действия без ответственного: 2'));
assert.ok(searchRuns >= 5);
console.log('project summary type counter checks passed');
