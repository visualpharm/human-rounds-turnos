// Direct-navigation runtime contract.  The retired hash-shell preference
// code had no surviving product surface, so this executes the real Python
// resolver through the composed ASGI app instead of asserting source text.
import test from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");

function resolverRuntime() {
  const program = String.raw`
import json, os
from unittest.mock import patch
os.environ.update(DATABASE_URL='', TURNOS_FAKE_AI='1', TURNOS_SKIP_HSI='1',
                  TURNOS_ADMIN_SECRET='nav-node-test1')
from app import main as app_module
from app.auth import admin_auth
from app.admin_panel import admin_navigation
from fastapi.testclient import TestClient
client = TestClient(app_module.app, follow_redirects=False)
cookie = {admin_auth.COOKIE_NAME: admin_auth._mint_token('navigation-node', 'admin')}
security = {'tiene_2fa': False, '2fa_deadline': None, '2fa_vencido': False}
with patch.object(admin_navigation.admin_preferences, 'get_items',
                  return_value={'nav.last_screen': '/admin/cola'}), \
     patch.object(admin_auth, 'account_security', return_value=security):
    saved = client.get('/admin', cookies=cookie)
    explicit = client.get('/admin?next=/admin/legal?doc=privacidad', cookies=cookie)
    invalid = client.get('/admin?next=/admin/legal&next=/admin/account', cookies=cookie)
print(json.dumps({'saved': saved.headers.get('location'),
                  'explicit': explicit.headers.get('location'),
                  'invalid': invalid.headers.get('location')}))
`;
  const result = spawnSync("python3", ["-c", program], {
    cwd: ROOT,
    encoding: "utf8",
    env: { ...process.env, TURNOS_FAKE_AI: "1" },
  });
  assert.equal(result.status, 0, result.stderr || result.stdout);
  return JSON.parse(result.stdout);
}

test("server resolver restores only a saved bare direct path", () => {
  assert.equal(resolverRuntime().saved, "/admin/cola");
});

test("server resolver gives a valid explicit direct target precedence", () => {
  assert.equal(resolverRuntime().explicit, "/admin/legal?doc=privacidad");
});

test("duplicate outer next is invalid and cannot revive saved state", () => {
  assert.equal(resolverRuntime().invalid, "/admin/solicitudes");
});
