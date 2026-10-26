const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {page} = require('./test_data_quality_ui.js');

const ui = page();
// The browser may only scale the frozen engine result, including current drift and ADV20.
vm.runInContext(`D.flows.events=[{key:'kospi200',notional_total:1000,
  rows:[{code:'0009K0',w_target:2,w_cur:1,flow_total:10,adv20:5e8}]}];`,ui.context);
let values=vm.runInContext(`(()=>{const f=flowModel(A.kospi200,2000),u={code:'0009K0',fif:.01,avg_trdval:1};
 return [f.weight(u),f.flowAdd(u),f.advDays(f.flowAdd(u),u)];})()`,ui.context);
assert.deepEqual(Array.from(values),[.02,20,4]);
vm.runInContext(`D.flows.events[0].availability='hold';`,ui.context);
assert.equal(vm.runInContext(`flowModel(A.kospi200,2000)`,ui.context),null);
assert.equal(vm.runInContext(`flowModel({classification_scenario:'A'},2000)`,ui.context),null);

const payload=JSON.parse(fs.readFileSync(path.join(__dirname,'..','dist','data.json'),'utf8'));
vm.runInContext(`Object.assign(A,${JSON.stringify(payload.analysis)});
 Object.assign(KB,${JSON.stringify(payload.kb.indexes)});
 Object.assign(D,${JSON.stringify({...payload,analysis:undefined,kb:undefined})});`,ui.context);
for(const key of ['kospi200','kosdaq150']){
  const target=ui.elements.get(key==='kospi200'?'tab-k200':'tab-kq150');
  const result=payload.analysis[key];
  assert.equal(result.classification_scenarios.status,'ready');
  ui.context.renderKrx(key,target);
  assert.match(target.innerHTML,/동일 입력 분류 비교/);
  assert.match(target.innerHTML,/KRICS-A/);
  assert.match(target.innerHTML,/KRICS-B/);
  assert.match(target.innerHTML,/공식 확정 아님/);
  assert.match(target.innerHTML,/시행일 HOLD/);
  ui.context.renderKrx(key,target,'A');
  assert.match(target.innerHTML,/매매 추정은 HOLD/);
  assert.match(target.innerHTML,/KRICS 과거 검증 성적 없음/);
  assert(!target.innerHTML.includes('편입 적중'));
  assert(!ui.elements.get(key+'-tables').innerHTML.includes('패시브 매수'));
  // The select handler must switch the full candidate and sector tables, not merely a badge.
  const select=ui.elements.get(key+'-scenario');
  select.value='B';select.onchange();
  assert.match(target.innerHTML,/KRICS-B · 모의 산출/);
}
ui.context.renderFlows();
const flows=ui.elements.get('tab-flows').innerHTML;
assert.match(flows,/HOLD는 계산 보류/);
assert.match(flows,/부분 합계/);
assert(!flows.includes('NaN'));
ui.context.renderAdhoc();
assert.match(ui.elements.get('tab-adhoc').innerHTML,/첫 15완료거래일/);
assert(!ui.elements.get('tab-adhoc').innerHTML.includes('해당 산업군 예비종목 1순위 편입'));
ui.context.renderDash();ui.context.renderQuality();
assert.match(ui.elements.get('tab-dash').innerHTML,/KRICS-A:/);
assert.match(ui.elements.get('krx-transition').innerHTML,/적용 시점 HOLD/);

const hold=payload.flows.events.find(e=>e.availability==='hold');
assert(hold,'actual output must hold unverified CAP/sector inputs');
// Force the held event to be the detail panel initially opened by renderFlows.
vm.runInContext(`D.flows.events=[${JSON.stringify(hold)}];`,ui.context);
ui.context.renderFlows();
const detail=ui.elements.get('flow-detail').innerHTML;
assert.match(detail,/HOLD · 목표비중/);
assert(!detail.includes('NaN'));
assert.match(detail,/공식 FFR \/ CAP계수/);
console.log('PASS: actual local build, KRICS scenarios, frozen engine scaling, HOLD and IPO UI contracts');
