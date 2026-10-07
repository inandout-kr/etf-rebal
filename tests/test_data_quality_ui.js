const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const template = fs.readFileSync(path.join(__dirname, '..', 'template.html'), 'utf8');
const script = template.match(/<script>\s*([\s\S]*?)<\/script>/)[1];
new vm.Script(script);
assert(!/data-t="semi"|id="tab-semi"|nav-semi|renderSemi\(/.test(template));
const nav = [...template.matchAll(/<button data-t="([^"]+)"[^>]*>([^<]+)<\/button>/g)];
const tabs = [...template.matchAll(/<section class="tab(?: on)?" id="tab-([^"]+)"/g)];
assert.deepEqual(nav.map(x => x[1]).sort(), tabs.map(x => x[1]).sort());

function fixture() {
  return {
    analysis: {
      generated: '2026-10-01 08:12', last_daily: '20260930', usdkrw: 1380,
      data_quality: {status: 'warning', as_of: '2026-09-30', warnings: [],
        universe: {scope: 'current_snapshot', observed_at: '2026-10-01 08:10'},
        fx: {status: 'unverified_fallback', as_of: null}},
      stock_data: {'084180': {name: '수성웹툰', price_as_of: '20260930', review_ready: false,
        universe_active: false, universe_as_of: '2026-10-01 08:10'}},
      kospi200: {all: [], adds: [], dels: [], n_current: 200, data_through: '20260930',
        window: {apply: '2026-12-11', period_start: '20260501', period_end: '20261031'}},
      kosdaq150: {all: [], adds: [], dels: [], n_current: 150, window: {apply: '2026-12-11'}},
      kospi100: {adds: [], dels: []}, cap_monitor: [],
      fn_top10: {top: [], adds: [], dels: [], window: {apply: '2026-12-11'}},
      fn_semitop10: {top: [], adds: [], dels: [], window: {apply: '2027-03-12'}},
      ipo_monitor: [], events: [], msci: {availability: 'unavailable', reason: '환율 검증 필요 · MSCI 계산 보류',
        candidates: [], smallest_current: [], em_min_full_usd_bn: 3.94, n_current: 1, proxy_date: '2026-09-30'},
    },
    proxy: {kospi200: {date: '2026-09-30'}, krx_semi: {date: '2026-09-30'}},
    kb: {indexes: {krx_semi: {name: 'KRX 반도체', doc: '방법론', universe: '반도체 유니버스',
      selection: [], adhoc: [], regular: '9월 정기변경', cap: '20%'}}, etf_index_key: {'KRX 반도체': 'krx_semi'}},
    members: {krx_semi: ['005930']},
    etfs: [{code: '091160', name: 'KODEX 반도체', bm: 'KRX 반도체', provider: 'KRX',
      aum_eok: 1000, nav: 10000, cycle: '매년 9월', apply_rule: '정기변경', cap: '20%', confidence: '높음'}],
    flows: {events: [], by_stock: {'005930': {name: '삼성전자', total: 10,
      events: [{index: 'KRX 반도체', apply: '2027-09-10', flow: 10, adv_mult: 1}]}}},
    semi: {apply: '2027-09-10', scenarios: {
      current: {total_sell_eok: -4711, total_buy_eok: 4711,
        rows: [{code: '005930', name: '삼성전자', flow_total: 10, adv_mult: 1}]},
      cap10: {adds: [], dels: [], rows: []},
    }},
  };
}

// Exercise the actual page helpers, tab handlers and selected renderers without a browser or network.
function page(hash = '') {
  const elements = new Map();
  class Element {
    constructor(id, classes = []) {
      this.id = id; this.dataset = {}; this.textContent = ''; this.value = ''; this.checked = false;
      const names = new Set(classes);
      this.classList = {contains: name => names.has(name), remove: name => names.delete(name),
        toggle: (name, force) => {const on = force ?? !names.has(name); on ? names.add(name) : names.delete(name); return on;}};
    }
    set innerHTML(html) {
      this.html = html;
      for (const match of html.matchAll(/<[^>]+\bid="([^"]+)"[^>]*>/g)) {
        const el = new Element(match[1]); el.checked = /\bchecked\b/.test(match[0]); elements.set(el.id, el);
      }
    }
    get innerHTML() {return this.html || '';}
    click() {if (this.onclick) this.onclick();}
    setAttribute(name, value) {this[name] = value;}
    scrollIntoView() {}
    focus() {}
  }
  for (const match of template.slice(0, template.indexOf('<script>')).matchAll(/\bid="([^"]+)"/g)) {
    elements.set(match[1], new Element(match[1]));
  }
  const buttons = nav.map((match, i) => {
    const el = new Element('nav-' + match[1], i ? [] : ['on']); el.dataset.t = match[1]; el.textContent = match[2]; return el;
  });
  const sections = tabs.map(match => {const el = elements.get('tab-' + match[1]); if (match[1] === 'dash') el.classList.toggle('on', true); return el;});
  const location = {hash};
  const document = {
    getElementById: id => elements.get(id), addEventListener() {},
    querySelector(selector) {
      const button = selector.match(/^#nav button\[data-t="([^"]+)"\]$/);
      return button ? buttons.find(b => b.dataset.t === button[1]) : elements.get(selector.slice(1)) || null;
    },
    querySelectorAll: selector => selector === '#nav button' ? buttons : selector === 'section.tab' ? sections : [],
  };
  elements.get('data').textContent = JSON.stringify(fixture());
  const context = vm.createContext({document, location, window: {scrollTo() {}},
    history: {replaceState: (_state, _title, url) => location.hash = url},
    MutationObserver: class {observe() {} disconnect() {}}, Event: class {}});
  // Render relevant panels explicitly below, including the dashboard regression.
  const boot = script.replace(/^renderDash\(\);[^\n]+\n/m, '');
  assert.notEqual(boot, script);
  vm.runInContext(boot, context);
  return {context, elements, buttons, sections, location};
}

for (const entry of ['', '#semi', ...nav.map(x => '#' + x[1])]) {
  const ui = page(entry);
  const expected = entry === '#semi' ? 'flows' : entry.slice(1) || 'dash';
  assert.equal(ui.sections.find(s => s.classList.contains('on')).id, 'tab-' + expected);
  if (entry === '#semi') assert.equal(ui.location.hash, '#flows');
  for (const button of ui.buttons) {
    button.click();
    assert.equal(ui.sections.filter(s => s.classList.contains('on')).length, 1);
    assert.equal(ui.sections.find(s => s.classList.contains('on')).id, 'tab-' + button.dataset.t);
    assert.equal(ui.location.hash, '#' + button.dataset.t);
  }
}

const ui = page('#etf');
ui.context.renderDash();
const dashboard = ui.elements.get('tab-dash').innerHTML;
const dashboardKpis = dashboard.match(/<div class="grid g4 dashboard-kpis">([\s\S]*?)\n  <\/div>/)[1];
assert.equal((dashboardKpis.match(/class="card kpi"/g) || []).length, 3);
assert(!dashboardKpis.includes('KRX 반도체'));
assert(!dashboard.includes('패시브 매도 / 매수 (A:'));
assert.match(dashboardKpis, /코스피 200/);
assert.match(dashboardKpis, /코스닥 150/);
assert(!/semi-kpi|semiA\.total_sell_eok|semiA\.total_buy_eok/.test(template));
assert.match(template, /\.g4\{grid-template-columns:repeat\(auto-fit,minmax\(230px,1fr\)\)\}/);
assert.match(template, /\.dashboard-kpis>\.kpi:first-child\{grid-column:1\/-1\}/);
assert.match(dashboard, /KRX 반도체.*시나리오 B/);
assert.match(dashboard, /삼성전자.*KRX 반도체/);
vm.runInContext('D.semi = null', ui.context);
ui.context.renderDash();
assert.equal((ui.elements.get('tab-dash').innerHTML.match(/class="card kpi"/g) || []).length, 3);
vm.runInContext('D.semi = ' + JSON.stringify(fixture().semi), ui.context);
assert.match(ui.elements.get('hdr-sub').textContent, /미확인.*MSCI 계산 보류/);
assert.match(ui.elements.get('data-quality').innerHTML, /시세 기준 2026-09-30/);
assert.match(ui.elements.get('data-quality').innerHTML, /현재 유니버스 관측 2026-10-01 08:10/);
assert.match(ui.elements.get('data-quality').innerHTML, /당시 상장 목록을 재구성한 자료는 아닙니다/);
ui.context.renderAdhoc();
assert.match(ui.elements.get('tab-adhoc').innerHTML, /환율 검증 필요.*MSCI 계산 보류/);
ui.context.renderEtf();
assert.match(ui.elements.get('etf-tbl').innerHTML, /KODEX 반도체/);
ui.context.showEtf('091160');
assert.match(ui.elements.get('etf-detail').innerHTML, /KRX 반도체/);
assert.match(ui.elements.get('etf-detail').innerHTML, /프록시 ETF 구성종목 1개/);
ui.context.renderFlows();
assert(!ui.elements.get('tab-flows').innerHTML.includes('별도 탭'));
assert.match(ui.elements.get('flow-stock').innerHTML, /KRX 반도체/);
assert.match(vm.runInContext("priceInfo(A.stock_data['084180'])", ui.context), /가격 기준 2026-09-30.*심사 보류.*현재 유니버스 밖/);

vm.runInContext("A.data_quality.fx = {status:'fallback', source:'Naver FX_USDKRW', as_of:'2026-09-30T16:00:00+09:00'}; A.msci.availability='ready';", ui.context);
ui.context.renderQuality();
assert.match(ui.elements.get('data-quality').innerHTML, /이전 정상 환율 사용.*2026-09-30T16:00:00\+09:00.*Naver FX_USDKRW/);
console.log('PASS: dashboard dedicated card removed; remaining KPIs/common data retained; tab entry/reload, ETF/flow access, date scope and FX unavailable UI');
