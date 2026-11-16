// Actual home controller + canonical listing/nav/search modules, with only
// catalog/API boundaries replaced by synthetic data. No app server or DB.
import { test, ceil, hookOptions } from './helpers/ceiling.mjs';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { chromium } from 'playwright';

const root = new URL('../', import.meta.url);
const modules = new Map(readdirSync(new URL('static/platform/', root))
  .filter(name => name.endsWith('.mjs'))
  .map(name => ['/static/platform/' + name, readFileSync(new URL('static/platform/' + name, root), 'utf8')]));
const manifest = Object.fromEntries([...modules.keys()].map(path => [path.slice('/static/'.length), path]));
const homeTemplate = readFileSync(new URL('templates/index.html', root), 'utf8');
const homeScript = homeTemplate.match(/<script>\s*(\(function \(\) \{[\s\S]*?)<\/script>/)[1]
  .replace(/\{\{ static\('([^']+)'\) \}\}/g, (_, name) => '/static/' + name);
const rows = [
  { nombre: 'Ilya Belov', especialidad: 'Cardiología', centro: 'Centro Norte', horas: 80 },
  { nombre: 'Ibarra', especialidad: 'Cardiología', centro: 'Centro Norte', horas: 1 },
  { nombre: '\u0418\u043b\u044c\u044f \u0411\u0435\u043b\u043e\u0432', especialidad: 'Cardiología', centro: 'Centro Norte', horas: 80 },
  { nombre: '\u0418\u0431\u0440\u0430\u0433\u0438\u043c', especialidad: 'Pediatría', centro: 'Centro Sur', horas: 1 },
  { nombre: 'Éric Böhm', especialidad: 'Pediatría', centro: 'Centro Sur', horas: 5 },
  { nombre: 'Dario Estevez', especialidad: 'Clínica médica', centro: 'Centro Sur', horas: 4 },
  { nombre: 'De Luca', especialidad: 'Clínica médica', centro: 'Centro Norte', horas: 2 },
].map((row, i) => ({ ...row, href: '/doctor/synthetic-' + i, publico: 'todos',
  proximo: {kind:'info',v:'Catálogo sintético',sort:'2'}, palabras:['consulta sintética'] }));
const payload = { cols:['medico','especialidad','centro','proximo'], rows,
  options:{search:true,rank:'popularidad',urlState:true,profile:false,
    filters:[{t:'facet',key:'especialidad',label:'Especialidad',widget:'select'}]} };
let browser;
test.before(async () => { browser = await chromium.launch(); }, hookOptions());
test.after(async () => { await browser?.close(); });

function documentHtml(path, catalog = payload) {
  const body = path === '/doctors'
    ? '<div id="medicos-tabla" data-listado><script type="application/json" class="lst-data">' + JSON.stringify(catalog) + '</script></div>'
    : path === '/'
      ? '<input id="q" type="search" aria-label="Buscar"><div id="ejemplos"></div><div id="resultados"></div><div id="directorio-host"></div><section id="centros-sec"><div id="centros"></div></section>'
      : '<p>Información pública sintética</p>';
  return `<!doctype html><html lang="es"><head><meta charset="utf-8">
    <meta name="viewport" content="width=device-width,initial-scale=1">
    <script>window.SANA_STATIC=${JSON.stringify(manifest)};window.SANA_I18N={lang:'es',catalog:{}};</script>
    </head><body><header id="stznav"></header><main><button id="invoker">Buscar profesionales</button>${body}</main>
    ${path === '/' ? '<script>' + homeScript + '</script>' : ''}
    <script type="module">import * as nav from '/static/platform/nav.mjs';
      import * as listing from '/static/platform/listing.mjs';
      window.searchFixture={nav,listing};</script></body></html>`;
}
async function fixture(path = '/doctors', platform = 'MacIntel', catalog = payload) {
  const page = await browser.newPage({viewport:{width:1280,height:850}});
  ceil(page);
  await page.addInitScript(platform => {
    Object.defineProperty(navigator,'platform',{value:platform});
    Object.defineProperty(navigator,'userAgentData',{value:undefined});
  }, platform);
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/*', async route => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    if (modules.has(path)) return route.fulfill({contentType:'text/javascript',body:modules.get(path)});
    if (['/','/doctors','/guide'].includes(path)) return route.fulfill({contentType:'text/html',body:documentHtml(path,catalog)});
    if (path === '/api/doctors') return route.fulfill({json:{medicos:rows}});
    if (path === '/api/services') return route.fulfill({json:{servicios:[]}});
    if (path === '/api/centers') return route.fulfill({json:{centros:[]}});
    if (path === '/api/links') return route.fulfill({json:{enlaces:[]}});
    if (path === '/api/directory') return route.fulfill({json:{clusters:[]}});
    if (path === '/api/account/me') return route.fulfill({status:401,json:{}});
    if (path === '/api/account/config') return route.fulfill({json:{auth_mode:'password'}});
    if (path === '/api/ai-status') return route.fulfill({json:{disponible:true}});
    if (path === '/api/search-log') return route.fulfill({json:{ok:true}});
    return route.fulfill({status:404,body:'Outside synthetic public-search fixture'});
  });
  await page.goto('https://public-search.test' + path);
  await page.waitForFunction(() => Boolean(window.searchFixture));
  return {page,errors};
}
const names = async page => page.locator('tbody tr td:first-child .lst-name').allTextContents();
async function query(page, value, expected, selector = '.lst-search') {
  await page.locator(selector).fill(value);
  await page.waitForFunction(expected => {
    const found = [...document.querySelectorAll('tbody tr td:first-child .lst-name')].map(el=>el.textContent.trim());
    return JSON.stringify(found) === JSON.stringify(expected);
  }, expected).catch(async error => {
    throw new Error(`${value}: ${JSON.stringify(await names(page))}; ${error.message}`);
  });
  assert.deepEqual((await names(page)).map(name=>name.trim()), expected);
}

test('actual /doctors catalog: initials, relevance, facets, literal queries and URL reload', async () => {
  const {page,errors} = await fixture();
  try {
    await query(page,'IB',['Ibarra','Ilya Belov']);
    await page.reload();
    await page.locator('.lst-search').waitFor();
    assert.equal(await page.locator('.lst-search').inputValue(),'IB');
    assert.deepEqual((await names(page)).map(n=>n.trim()),['Ibarra','Ilya Belov']);
    await query(page,'\u0418\u0411',['\u0418\u0431\u0440\u0430\u0433\u0438\u043c','\u0418\u043b\u044c\u044f \u0411\u0435\u043b\u043e\u0432']);
    await page.locator('[data-facetk="especialidad"]').selectOption('Cardiología');
    assert.deepEqual((await names(page)).map(n=>n.trim()),['\u0418\u043b\u044c\u044f \u0411\u0435\u043b\u043e\u0432']);
    await page.locator('[data-facetk="especialidad"]').selectOption('');
    await query(page,'\u0431\u0435\u043b\u043e\u0432',['\u0418\u043b\u044c\u044f \u0411\u0435\u043b\u043e\u0432']);
    await query(page,'eb',['Éric Böhm']);
    await query(page,'consulta sintética',rows.slice().sort((a,b)=>b.horas-a.horas).map(r=>r.nombre));
    await query(page,'de',['De Luca']); // preserve literal matches for stopword-only queries
    await query(page,'IB cardiologo',['Ibarra','Ilya Belov']);
    await query(page,'sin coincidencia',[]);
    await query(page,'',rows.slice().sort((a,b)=>b.horas-a.horas).map(r=>r.nombre));
    await query(page,'IB',['Ibarra','Ilya Belov']);
    await page.locator('[data-sort="nombre"]').click();
    await page.locator('[data-sort="nombre"]').click();
    assert.deepEqual((await names(page)).map(n=>n.trim()),['Ilya Belov','Ibarra'],
      'explicit column sorting remains the user\'s choice');
    assert.deepEqual(errors,[]);
  } finally { await page.close(); }
});

test('non-catalog listings retain their original substring search contract', async () => {
  const catalog = {...payload,options:{...payload.options,rank:undefined,defaultSort:false}};
  const {page,errors} = await fixture('/doctors','MacIntel',catalog);
  try {
    await query(page,'IB',['Ibarra']);
    await query(page,'belov',['Ilya Belov']);
    await query(page,'',rows.map(r=>r.nombre));
    assert.deepEqual(errors,[]);
  } finally { await page.close(); }
});

test('actual home controller uses the same initials and prefix ranking', async () => {
  const {page,errors} = await fixture('/');
  try {
    await query(page,'IB',['Ibarra','Ilya Belov'],'#q');
    await query(page,'\u0418\u0411',['\u0418\u0431\u0440\u0430\u0433\u0438\u043c','\u0418\u043b\u044c\u044f \u0411\u0435\u043b\u043e\u0432'],'#q');
    await query(page,'eb',['Éric Böhm'],'#q');
    assert.deepEqual(errors,[]);
  } finally { await page.close(); }
});

test('browser keyboard: repeated shortcut, Escape, modal/editor/IME guards and disposal', async () => {
  const {page,errors} = await fixture();
  try {
    await page.locator('#invoker').focus();
    await page.keyboard.press('Meta+k');
    await page.keyboard.press('Meta+k');
    await query(page,'IB',['Ibarra','Ilya Belov']);
    await page.keyboard.press('Escape');
    assert.equal(await page.evaluate(()=>document.activeElement.id),'invoker');
    assert.equal(await page.locator('.lst-search').inputValue(),'IB');
    await page.evaluate(()=>{const d=document.createElement('dialog');d.id='other';d.open=true;d.textContent='Other task';document.body.appendChild(d);});
    await page.keyboard.press('Meta+k');
    assert.equal(await page.evaluate(()=>document.activeElement.id),'invoker');
    await page.evaluate(()=>document.getElementById('other').remove());
    await page.evaluate(()=>{const e=document.createElement('textarea');e.id='editor';document.body.appendChild(e);e.focus();});
    await page.keyboard.press('Meta+k');
    assert.equal(await page.evaluate(()=>document.activeElement.id),'editor');
    await page.locator('#invoker').focus();
    await page.evaluate(()=>document.dispatchEvent(new KeyboardEvent('keydown',{code:'KeyK',key:'k',metaKey:true,isComposing:true,bubbles:true})));
    assert.equal(await page.evaluate(()=>document.activeElement.id),'invoker');
    await page.evaluate(async()=>{const nav=window.searchFixture.nav.mount();await nav.ready;nav.dispose();});
    await page.keyboard.press('Meta+k');
    assert.equal(await page.evaluate(()=>document.activeElement.id),'invoker');
    assert.deepEqual(errors,[]);
  } finally { await page.close(); }
});

test('Ctrl-K off Mac and a page without a search reaches focused home search', async () => {
  const {page,errors} = await fixture('/guide','Win32');
  try {
    await page.locator('#invoker').focus();
    await page.keyboard.press('Control+k');
    await page.waitForURL('https://public-search.test/#q');
    await page.waitForFunction(()=>document.activeElement?.id==='q');
    await query(page,'IB',['Ibarra','Ilya Belov'],'#q');
    assert.deepEqual(errors,[]);
  } finally { await page.close(); }
});
