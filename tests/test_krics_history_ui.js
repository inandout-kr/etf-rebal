const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const {page}=require('./test_data_quality_ui.js');
const clone=v=>JSON.parse(JSON.stringify(v));
const payload=JSON.parse(fs.readFileSync(path.join(__dirname,'..','dist/data.json'),'utf8'));
const saved=JSON.parse(fs.readFileSync(path.join(__dirname,'..','data/krics_last_good.json'),'utf8'));
const ui=page();
const key='kosdaq150';
const source=clone(payload.analysis[key]);
const prior=clone([...Object.values(saved.history?.[key]||{}),saved.indexes[key]].sort((a,b)=>a.as_of.localeCompare(b.as_of))[0]);
assert(prior,'a verified prior calculation must be packaged');
prior.krics.not_applied.A.adds[0].name='ONLY_HISTORICAL_CANDIDATE';
const current=clone(source);
const complete=(id,policy)=>({...clone(prior[id==='GICS'?'gics':'krics'][policy][id==='GICS'?undefined:id]||prior.gics[policy]),
  classification:{scheme:id==='GICS'?'GICS':'KRICS'},all:[],watch_keep:[],watch_new:[],largecap_candidates:[],
  small_cap_policy:policy,data_through:'20261110',adds:[{...prior.gics[policy].adds[0],name:'ONLY_CURRENT_'+id+'_'+policy}]});
current.smallcap_scenarios={applied:complete('GICS','applied'),not_applied:complete('GICS','not_applied')};
current.classification_scenarios={status:'ready',as_of:'20261110',current_snapshot_date:'20261111',review_ref_date:'20261110',
  classification_date_status:'current_snapshot_after_review',date_warning:'CLASSIFICATION_DATE_NOT_REVIEW_DATE',
  scenarios:{},smallcap_scenarios:{},coverage:{}};
for(const policy of ['applied','not_applied'])current.classification_scenarios.smallcap_scenarios[policy]={A:complete('A',policy),B:complete('B',policy)};
current.classification_scenarios.scenarios=current.classification_scenarios.smallcap_scenarios.applied;
const history={schema_version:1,indexes:{[key]:prior},history:{}};
const storage=new Map();
ui.context.window.localStorage={getItem:k=>storage.get(k)||null,setItem:(k,v)=>storage.set(k,v)};
vm.runInContext(`Object.assign(A,${JSON.stringify(payload.analysis)});A.last_daily='20261110';A.kosdaq150=${JSON.stringify(current)};
Object.assign(KB,${JSON.stringify(payload.kb.indexes)});Object.assign(D,${JSON.stringify({...payload,analysis:undefined,kb:undefined,krics_history:history})});`,ui.context);
const mount=ui.elements.get('tab-kq150');
ui.context.renderKrx(key,mount,'A','not_applied','current');
assert.match(mount.innerHTML,/CLASSIFICATION_DATE_NOT_REVIEW_DATE/);
assert(ui.elements.get(key+'-tables').innerHTML.includes('ONLY_CURRENT_A_not_applied'));
assert(!mount.innerHTML.includes('ONLY_HISTORICAL_CANDIDATE'));
assert.match(mount.innerHTML,/분류 관측 2026-11-11/);
// A stale or failed calculation retains both interpretation and policy options.
vm.runInContext(`A.kosdaq150.classification_scenarios.status='HOLD';A.kosdaq150.classification_scenarios.scenarios={};
A.kosdaq150.classification_scenarios.smallcap_scenarios={};A.kosdaq150.classification_scenarios.hold_reasons=['FRESH_COLLECTION_FAILED'];`,ui.context);
ui.context.renderKrx(key,mount);
assert.match(mount.innerHTML,/KRICS-A · 최신 계산 HOLD/);
assert.match(mount.innerHTML,/FRESH_COLLECTION_FAILED/);
assert(mount.innerHTML.includes('<option value="A" selected>'));
assert(mount.innerHTML.includes('<option value="B"'));
assert(mount.innerHTML.includes('<option value="not_applied" selected>'));
assert(!mount.innerHTML.includes('ONLY_CURRENT_A_not_applied'));
assert(!mount.innerHTML.includes('ONLY_HISTORICAL_CANDIDATE'));
assert(mount.innerHTML.includes(prior.as_of.slice(0,4)+'-'+prior.as_of.slice(4,6)+'-'+prior.as_of.slice(6)));
// Simulate page reinitialization while preserving the user's local selection.
vm.runInContext(`delete krxViewState.kosdaq150`,ui.context);
ui.context.renderKrx(key,mount);
assert.match(mount.innerHTML,/KRICS-A · 최신 계산 HOLD/);
assert(mount.innerHTML.includes('<option value="not_applied" selected>'));
// History is a separate view and never scales today's flow rows or AUM.
const date=ui.elements.get(key+'-reference');date.value='last_good';date.onchange();
assert.match(mount.innerHTML,/과거 보관 결과 · 현재 예측 아님/);
assert.match(mount.innerHTML,/ONLY_HISTORICAL_CANDIDATE/);
assert(!mount.innerHTML.includes('ONLY_CURRENT_'));
assert.match(mount.innerHTML,/이 과거 보기의 수급은 HOLD/);
assert(!mount.innerHTML.includes('패시브 매수'));
assert(!mount.innerHTML.includes('type="number"'));
assert(!mount.innerHTML.includes('NaN'));
assert.equal(vm.runInContext(`flowModel({historical_calculation:true},100000)`,ui.context),null);
// Recovery and last-good advancement must preserve a selected historical identity.
const historicalId=prior.snapshot_id||'last_good';
const recoveredSnapshot={...clone(prior),as_of:'20261110',snapshot_id:'20261110_recovered',
  generated:'2026-11-11 08:00',gics:clone(current.smallcap_scenarios),krics:clone(current.classification_scenarios.smallcap_scenarios)};
vm.runInContext(`A.kosdaq150=${JSON.stringify(current)};D.krics_history={schema_version:1,
  indexes:{kosdaq150:${JSON.stringify(recoveredSnapshot)}},history:{kosdaq150:{${JSON.stringify(historicalId)}:${JSON.stringify(prior)}}}};`,ui.context);
ui.context.renderKrx(key,mount);
assert.match(mount.innerHTML,/ONLY_HISTORICAL_CANDIDATE/);
ui.context.renderKrx(key,mount,'B','applied','current');
assert(ui.elements.get(key+'-tables').innerHTML.includes('ONLY_CURRENT_B_applied'));
assert(!mount.innerHTML.includes('ONLY_HISTORICAL_CANDIDATE'));
assert.match(mount.innerHTML,/매매 추정은 HOLD/);
console.log('PASS: ready/HOLD/recovery and reload retain A/B plus policy; historical candidates and null flows remain separate');
