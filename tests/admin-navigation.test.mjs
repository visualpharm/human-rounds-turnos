// Executable direct-nav render contract.  The old suite inspected hash-shell
// templates and `entrar()` source; this exercises composed direct screens.
import test from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");

function renderedDirectNav() {
  const program = String.raw`
import json, os
from unittest.mock import patch
os.environ.update(DATABASE_URL='', TURNOS_FAKE_AI='1', TURNOS_SKIP_HSI='1',
                  TURNOS_ADMIN_SECRET='navrender-test1')
from app import main as app_module
from app.auth import admin_auth
from fastapi.testclient import TestClient
client = TestClient(app_module.app, follow_redirects=False)
cookie = {admin_auth.COOKIE_NAME: admin_auth._mint_token('navigation-render', 'admin')}
security = {'tiene_2fa': False, '2fa_deadline': None, '2fa_vencido': False}
with patch.object(admin_auth, 'account_security', return_value=security):
    queue = client.get('/admin/solicitudes', cookies=cookie)
    account = client.get('/admin/account', cookies=cookie)
print(json.dumps({'queue_status': queue.status_code, 'queue': queue.text,
                  'account_status': account.status_code, 'account': account.text}))
`;
  const result = spawnSync("python3", ["-c", program], {
    cwd: ROOT,
    encoding: "utf8",
    env: { ...process.env, TURNOS_FAKE_AI: "1" },
  });
  assert.equal(result.status, 0, result.stderr || result.stdout);
  return JSON.parse(result.stdout);
}

test("direct screen renders grouped server-owned navigation with real URLs", () => {
  const result = renderedDirectNav();
  assert.equal(result.queue_status, 200);
  assert.match(result.queue, /<nav class="adm5-nav"/);
  assert.equal((result.queue.match(/class="adm5-nav-group(?: |")/g) || []).length, 5);
  assert.match(result.queue, /id="adm-nav-group-care">Atención<\/span>/);
  assert.match(result.queue, /id="adm-nav-group-organization">Organización<\/span>/);
  assert.match(result.queue, /id="adm-nav-group-chat">Chat<\/span>/);
  assert.match(result.queue, /id="adm-nav-group-system">Sistema<\/span>/);
  assert.equal((result.queue.match(/adm5-nav-group-title/g) || []).length, 4);
  assert.match(result.queue, /adm5-nav-group--standalone[\s\S]*?data-nav="cuenta"/);
  assert.doesNotMatch(result.queue, /data-nav="consulta"/);
  assert.match(result.queue, /href="\/admin\/solicitudes"/);
  assert.doesNotMatch(result.queue, /href="#/);
  assert.match(result.queue, /data-nav="solicitudes" class="on"/);
});

test("account direct screen retains the accessible real sign-out control", () => {
  const result = renderedDirectNav();
  assert.equal(result.account_status, 200);
  assert.match(result.account, /id="adm-logout"/);
  assert.match(result.account, /aria-label="[^"]+"/);
});

test("grouped navigation keeps rows compact without reducing compact touch targets", () => {
  const css = readFileSync(join(ROOT, "static", "root", "admin-direct.css"), "utf8");
  assert.match(css, /\.adm5-nav-group\{display:flex;flex-direction:column;gap:0;\}/);
  assert.match(css, /\.adm5-nav a\{display:block;padding:6px 10px;border-radius:8px/);
  assert.match(css, /\.adm5-nav-group\{display:grid;grid-template-columns:repeat\(2,minmax\(0,1fr\)\);gap:0;\}/);
  assert.match(css, /\.adm5-nav a\{min-height:44px;display:flex;align-items:center;white-space:normal;padding:8px 10px;\}/);
});

test("the shell restores the native navigation disclosure when leaving compact mode", () => {
  const shell = readFileSync(join(ROOT, "templates/admin/base.html"), "utf8");
  // A doctor's row of tabs (adm5-side--tabs) is the one disclosure that never closes.
  assert.match(shell, /if \(compact\.matches && !tabs\) menu\.removeAttribute\("open"\);\s*else menu\.setAttribute\("open", ""\);/);
});
