import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {runtime, storage, flush} from './lineage/runtime.mjs';

// format.mjs needs the country block the server injects in production.
globalThis.SANA_COUNTRY_PHONE = JSON.parse(
  readFileSync(new URL('../packs/ar/country.json', import.meta.url), 'utf8')).phone;
const Format = await import('../static/platform/format.mjs');
for (const contact of [null, '+5491198765432']) test(`lineage: wizard submits server contact ${contact}`, async () => {
  const posts = [], button = {}, message = {style:{}};
  const c = runtime('app/modules/turnos/service.mjs', ['loadIdent','saveIdent','normPhone','onlyDigits','fetchSesion','telefonoDelTurno','casoChatListo','submit'], {
    Format, IDENT_KEY:'st_identity', localStorage:storage({st_identity:{contacto:'+5491112345678'}}),
    host:{}, $: selector => selector === '#wz-send' ? button : message,
    servicio:{id:'svc',centro:'center'}, wiz:{}, saveHoldToken(){}, olvidarOrdenHandoff(){}, renderSuccess(){}, evStep(){},
    fetch: async (url, options) => {
      if (url === '/api/account/me') return new Response(JSON.stringify({usuario:{dni:'30123456',contacto:contact}}));
      assert.equal(url, '/api/requests'); posts.push(JSON.parse(options.body));
      return new Response(JSON.stringify({ok:true}));
    }
  });
  const session = await c.fetchSesion(); c.submit(session.usuario, session.usuario);
  await flush();
  assert.equal(posts.length, 1, 'submit actually reaches the request endpoint');
  assert.equal(posts[0].contacto, contact || '', 'modules/turnos/service.mjs:2613 must not resurrect a cleared phone');
});
