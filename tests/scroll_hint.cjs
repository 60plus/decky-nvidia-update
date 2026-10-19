const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const ts = require('typescript');
const compiled = ts.transpileModule(fs.readFileSync('src/useScrollHint.tsx', 'utf8'), {
  compilerOptions: {module: ts.ModuleKind.CommonJS, jsx: ts.JsxEmit.ReactJSX, target: ts.ScriptTarget.ES2020},
}).outputText;
let cleanup, last;
const ok = {label: 'Select'};
const original = function(map) { last = map; };
const store = {SetActionsFromMap: original};
const exported = {};
vm.runInNewContext(compiled, {
  exports: exported,
  window: {SteamUIStore: {GetFocusedWindowInstance: () => ({ActionDescriptionStore: store})}},
  require(name) {
    if (name === 'react') return {useLayoutEffect(fn) { cleanup = fn(); }};
    if (name === 'react/jsx-runtime') return {jsx: (type, props) => ({type, props})};
    if (name === '@decky/ui') return {
      findModuleExport: () => ({A: 0, RightStick: 18, RightStickClick: 19}),
      getFocusNavController: () => ({GetActiveNavTree: () => ({GetLastFocusedNode: () => ({GetActiveActionDescriptions: () => ({1: ok})})})}),
    };
    throw Error(name);
  },
});
exported.useScrollHint();
const unrelated = {0: ok, 1: {label: 'Back'}};
store.SetActionsFromMap(unrelated);
assert.equal(last, unrelated, 'Other Steam controls must remain untouched');
const scroll = {label: exported.scrollHint};
store.SetActionsFromMap({0: scroll, 1: unrelated[1]});
assert.equal(last[0], ok, 'Restore the actual primary action');
assert.equal(last[18], scroll, 'Use Steam native right-stick glyph, not A or stick-click');
assert.equal(last[1], unrelated[1]);
cleanup();
assert.equal(store.SetActionsFromMap, original, 'Closing the viewer must restore Steam');
Object.defineProperty(store, 'SetActionsFromMap', {value: original, configurable: false});
exported.useScrollHint();
assert.equal(cleanup, undefined, 'Leave a locked future Steam store unchanged');
console.log('Native scroll legend passed: marked action only, controller glyph enum, primary action preserved, cleanup, locked store.');
