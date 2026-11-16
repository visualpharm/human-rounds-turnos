import test from 'node:test';
import assert from 'node:assert/strict';
import { runtime, storage } from './lineage/runtime.mjs';
for (const file of ['static/platform/account.mjs', 'static/platform/account-form.mjs']) {
  test(`lineage: ${file} mirror removes server-cleared identity`, () => {
    const localStorage = storage();
    const c = runtime(file, file.endsWith('/account.mjs') ? ['loadIdent', 'mirror'] : ['mirror'], {localStorage});
    const before = {dni:'30123456', nombre:'Ana', apellido:'Perez', nacimiento:'1990-01-02', sexo:'F', contacto:'+5491112345678', email:'ana@example.test'};
    c.mirror(before);
    assert.deepEqual(JSON.parse(localStorage.getItem('st_identity')), before);
    const after = {...before, nombre:'Anna', nacimiento:null, contacto:null, email:null};
    c.mirror(after);
    assert.deepEqual(JSON.parse(localStorage.getItem('st_identity')), after, `${file}: server clears must replace stored identity`);
  });
}
