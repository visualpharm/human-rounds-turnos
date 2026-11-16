import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { test } from 'node:test';
import vm from 'node:vm';

const html = execFileSync('python3', ['-c',
  'from app.auth.password_reset import page_html; print(page_html("synthetic-token").decode())'], {
  encoding: 'utf8', env: { ...process.env, TURNOS_ENV_FILE: '/dev/null', TURNOS_CONFIG_LOCK: '1',
    TURNOS_FAKE_AI: '1', TURNOS_SKIP_HSI: '1', MAIL_ENABLED: '0' },
});
const script = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].at(-1)[1];

function fixture(fetcher) {
  const button = { disabled: false };
  const message = { textContent: '', replaceChildren(link) { this.link = link; } };
  let submit;
  const form = { hidden: false, querySelector: () => button,
    addEventListener(type, handler) { assert.equal(type, 'submit'); submit = handler; } };
  const fields = { 'reset-form': form, 'reset-msg': message,
    'reset-pass': { value: 'Synthetic-pass-2' }, 'reset-confirm': { value: 'Synthetic-pass-2' },
    'reset-token': { value: 'synthetic-token' } };
  const destinations = [];
  vm.runInNewContext(script, {
    document: { getElementById: id => fields[id], createElement: () => ({}) }, fetch: fetcher,
    window: { location: { replace: url => destinations.push(url) } },
  });
  return { button, message, form, fields, destinations,
    submit: () => submit.call(form, { preventDefault() {} }) };
}

const response = (body, ok = true) => ({ ok, json: async () => body });

test('successful password proof directly continues to the server-owned entry or MFA screen', async () => {
  for (const redirect of ['/admin', '/admin/login?staff=1']) {
    const page = fixture(async () => response({ ok: true, redirect }));
    await page.submit();
    assert.deepEqual(page.destinations, [redirect]);
    assert.equal(page.form.hidden, true);
    assert.equal(page.message.link.href, redirect);
  }
});

test('missing, login-only and hostile redirects never trigger automatic navigation', async () => {
  for (const redirect of [undefined, '/admin/login', '//evil.example', '/\\evil.example', 'https://evil.example', '/admin?next=evil']) {
    const page = fixture(async () => response({ ok: true, redirect }));
    await page.submit();
    assert.deepEqual(page.destinations, []);
    assert.equal(page.message.link.href, '/admin/login');
  }
});

test('double submission is suppressed; an interrupted request permits a safe retry', async () => {
  let calls = 0;
  let fail;
  const page = fixture(() => { calls++; return new Promise((resolve, reject) => { fail = reject; }); });
  const first = page.submit();
  await page.submit();
  assert.equal(calls, 1);
  assert.equal(page.button.disabled, true);
  fail(new Error('synthetic network interruption'));
  await first;
  assert.equal(page.button.disabled, false);
  assert.equal(page.form.hidden, false);
  assert.deepEqual(page.destinations, []);
  assert.ok(page.message.textContent);
});

test('expired or consumed link stays on the page without a false success', async () => {
  const page = fixture(async () => response({ error: 'El enlace venció o ya fue usado.' }, false));
  await page.submit();
  assert.equal(page.form.hidden, false);
  assert.equal(page.button.disabled, false);
  assert.match(page.message.textContent, /venció/);
  assert.deepEqual(page.destinations, []);
});
