const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const {page}=require('./test_data_quality_ui.js');
const ui=page();
const data=JSON.parse(fs.readFileSync(path.join(__dirname,'..','dist/data.json'),'utf8'));
const clone=x=>JSON.parse(JSON.stringify(x));
const base=clone(data.analysis.kosdaq150);
base.missing_review_data=[{code:'458650',name:'성우'}];
const make=(id,policy)=>{
 const value=clone(base); delete value.classification_scenarios;delete value.smallcap_scenarios;
 const row={...base.all[0],code:({'GICS:applied':'000010','GICS:not_applied':'000020','A:applied':'000030','A:not_applied':'000040','B:applied':'000050','B:not_applied':'000060'})[id+':'+policy],name:id+'_'+policy,status:'신규편입',is_cur:false};
 return {...value,all:[row],adds:[row],dels:[],watch_keep:[],watch_new:[],
  classification:{scheme:id==='GICS'?'GICS':'KRICS'},scenario_id:id,small_cap_policy:policy,
  smallcap_rule:policy==='applied'?{applied:true,threshold_rank:300,base_adds:0,base_dels:0,removed:[],replacements:[]}:null};
};
base.smallcap_scenarios={};
base.classification_scenarios={status:'ready',as_of:'20261023',scenarios:{},smallcap_scenarios:{},coverage:{additional_review_names:[{code:'389680',name:'유디엠텍'}]}};
const events={};
for(const policy of ['applied','not_applied']){
 base.smallcap_scenarios[policy]=make('GICS',policy);
 base.classification_scenarios.smallcap_scenarios[policy]={};
 for(const id of ['A','B'])base.classification_scenarios.smallcap_scenarios[policy][id]=make(id,policy);
 for(const id of ['GICS','A','B']){
  const value=id==='GICS'?base.smallcap_scenarios[policy]:base.classification_scenarios.smallcap_scenarios[policy][id];
  events[id+':'+policy]={key:'kosdaq150',notional_total:1000,availability:id==='GICS'?'estimated':'hold',hold_reasons:[],rows:[{code:value.adds[0].code,w_target:1,w_cur:0,flow_total:policy==='applied'?10:20,adv20:1e8}]};
 }
}
base.classification_scenarios.scenarios=base.classification_scenarios.smallcap_scenarios.applied;
base.missing_review_data=[{code:'458650',name:'성우'}];
data.analysis.kosdaq150=base;
data.flows.events=[{...events['GICS:applied'],scenario_flows:events}];
vm.runInContext(`Object.assign(A,${JSON.stringify(data.analysis)});Object.assign(KB,${JSON.stringify(data.kb.indexes)});Object.assign(D,${JSON.stringify({...data,analysis:undefined,kb:undefined})});`,ui.context);
const target=ui.elements.get('tab-kq150');
for(const policy of ['applied','not_applied'])for(const id of ['GICS','A','B']){
 ui.context.renderKrx('kosdaq150',target,id,policy);
 assert.match(target.innerHTML,/300위 재량 제외/);
 assert.match(target.innerHTML,/A\/B는 신규 선정조건/);
 assert.match(target.innerHTML,/CHECK 결측은 공식 결격 판정이 아닙니다/);
 assert.match(target.innerHTML,/유디엠텍/);
 assert.match(target.innerHTML,/성우/);
 const table=ui.elements.get('kosdaq150-tables').innerHTML;
 assert(table.includes(id+'_'+policy));
 if(id==='GICS'){
  assert(table.includes('패시브 매수'));
  const amount=vm.runInContext(`(()=>{const i=krxResult('kosdaq150','${id}','${policy}'),f=flowModel(i,2000);return f.flowAdd(i.adds[0]);})()`,ui.context);
  assert.equal(amount,policy==='applied'?20:40);
 }else{
  assert.match(target.innerHTML,/매매 추정은 HOLD/);
  assert(!table.includes('패시브 매수'));
 }
}
ui.context.renderKrx('kosdaq150',target,'A','not_applied');
let select=ui.elements.get('kosdaq150-scenario');select.value='B';select.onchange();
assert(ui.elements.get('kosdaq150-tables').innerHTML.includes('B_not_applied'));
select=ui.elements.get('kosdaq150-smallcap');select.value='applied';select.onchange();
assert(ui.elements.get('kosdaq150-tables').innerHTML.includes('B_applied'));
vm.runInContext(`A.kosdaq150.classification_scenarios.status='HOLD';A.kosdaq150.classification_scenarios.hold_reasons=['분류 기준일 불일치'];`,ui.context);
ui.context.renderKrx('kosdaq150',target,'GICS','not_applied');
assert.match(target.innerHTML,/KRICS 비교 HOLD/);
assert.match(target.innerHTML,/분류 기준일 불일치/);
assert(ui.elements.get('kosdaq150-tables').innerHTML.includes('GICS_not_applied'));
assert(!target.innerHTML.includes('<option value="A"'));
console.log('PASS: six independent classification/policy choices, selected flow scaling, HOLD, input warnings and future GICS fallback');
