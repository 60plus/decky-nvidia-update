// Exercise the published dependency against Steam's separate execution/render windows.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const ts = require('typescript');
const source = fs.readFileSync('node_modules/@moi952/decky-ui-kit/dist/AnchoredDropdown.js', 'utf8');
const compiled = ts.transpileModule(source, {compilerOptions: {module: ts.ModuleKind.CommonJS}}).outputText;

function check({top, bottom, viewport=765, clipTop=56, clipBottom=725, scale=1, maxVisibleOptions, rowHeights=[]}, expected) {
  let index=0, effects=[], cleanups=[];
  const hooks=[], listeners=new Map(), exports={};
  const view = {innerHeight:viewport, getComputedStyle:()=>({overflowY:'auto'}),
    addEventListener:(name, fn)=>listeners.set(name,fn),
    removeEventListener(name, fn) { assert.equal(listeners.get(name),fn); listeners.delete(name); }};
  const body = {parentElement:null,getBoundingClientRect:()=>({top:0,bottom:0})};
  const wrapper = {ownerDocument:{defaultView:view,body}, offsetHeight:(bottom-top)/scale,
    getBoundingClientRect:()=>({top,bottom,height:bottom-top}),
    parentElement:{parentElement:body,getBoundingClientRect:()=>({top:clipTop,bottom:clipBottom})}};
  const react = {
    useState(initial) { const key=index++; if (!(key in hooks)) hooks[key]=typeof initial==='function'?initial():initial;
      return [hooks[key],v=>{hooks[key]=typeof v==='function'?v(hooks[key]):v;}]; },
    useRef(initial) {const key=index++; return hooks[key]??=( {current:initial} );},
    useEffect() {}, useLayoutEffect(fn) {effects.push(fn);},
  };
  vm.runInNewContext(compiled,{exports,window:{innerHeight:1},require(name) {
    if(name==='react') return react;
    if(name==='react/jsx-runtime') return {jsx:(type,props)=>({type,props}),jsxs:(type,props)=>({type,props})};
    if(name==='@decky/ui') return {Field:'field',DialogButton:'button',Focusable:'focusable',GamepadButton:{}};
    if(name==='react-icons/fa') return {};
    if(name==='./internal/theme') return {DEFAULT_PANEL_BACKGROUND:'#333',SIZE_STYLE:{medium:{fontSize:14,minHeight:40}}};
    throw Error(name);
  }});
  function render() {
    cleanups.forEach(fn=>fn()); cleanups=[];
    index=0; effects=[];
    const options=rowHeights.map((_,i)=>({value:String(i),label:'Option '+i}));
    const tree=exports.AnchoredDropdown({options,selectedValue:'',onChange(){},maxVisibleOptions,
      renderOptionLabel:option=>({type:'custom-row',props:{children:option.label}})});
    tree.props.ref.current=wrapper;
    let row=0;
    function attachRows(node) {
      if (!node || typeof node!=='object') return;
      if (Array.isArray(node)) { node.forEach(attachRows); return; }
      if (node.type==='button' && node.props?.children?.[0]?.props?.children?.type==='custom-row') {
        node.props.ref({offsetHeight:rowHeights[row++]});
      }
      attachRows(node.props?.children);
    }
    attachRows(tree);
    effects.forEach(fn=>{ const cleanup=fn(); if (cleanup) cleanups.push(cleanup); });
    return tree;
  }
  render().props.children[1].props.onActivate();
  render();
  let tree=render();
  const list=()=>tree.props.children.find(c=>c?.props?.style?.position==='absolute').props.style;
  assert.equal(list().bottom==='100%',expected.up);
  assert.equal(list().maxHeight,expected.height);
  assert(list().maxHeight*scale <= (expected.up?top-clipTop:clipBottom-bottom)-8);
  // Scrolling the containing panel repositions and clamps the open list.
  top=clipBottom-60; bottom=clipBottom-20;
  listeners.get('scroll')(); tree=render();
  assert.equal(list().bottom,'100%');
  listeners.get('resize')(); tree=render();
  tree.props.children[1].props.onActivate(); render();
  assert.equal(listeners.size,0,'closing the list releases resize/scroll listeners');
}
check({top:120,bottom:160},{up:false,height:220});
check({top:650,bottom:690},{up:true,height:220});
check({top:120,bottom:160,clipTop:100,clipBottom:260},{up:false,height:90});
check({top:120,bottom:160,maxVisibleOptions:0},{up:false,height:555});
check({top:120,bottom:200,scale:2},{up:false,height:220});
check({top:198,bottom:259,maxVisibleOptions:8},{up:false,height:352});
check({top:120,bottom:160,maxVisibleOptions:8,rowHeights:Array(10).fill(58)},{up:false,height:480});
check({top:198,bottom:259,maxVisibleOptions:8,rowHeights:Array(8).fill(58)},{up:false,height:456});
console.log('Dropdown geometry verified: owning window, placement, clipping, zoom, measured multiline rows, resize/scroll and listener cleanup.');
