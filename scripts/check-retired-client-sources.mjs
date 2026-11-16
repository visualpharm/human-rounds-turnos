// Physical retirement includes ignored files and dangling symlinks.
import { lstatSync } from 'node:fs';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const retired = [
  'admin-system', 'admin-sistema', 'admin-settings', 'admin-roadmap', 'admin-alerts', 'admin-reports', 'admin-theme', 'admin-hsi',
  'admin-my-appointments', 'admin-doctor-patients',
  'admin-patients', 'admin-pacientes',
  'admin-preparations', 'admin-preparaciones',
  'my-requests', 'lab-results',
  'admin-charts', 'admin-combo', 'admin-control-fit', 'admin-hours-editor', 'admin-knowledge', 'admin-legal', 'admin-login', 'admin-patient-summary', 'admin-preferences', 'admin-prompts', 'admin-session', 'admin-solicitudes', 'admin-table', 'admin-users',
  'account-form', 'age', 'auth-test', 'calendar-grid', 'chat-ui', 'chat-widget', 'confirm', 'dates', 'dependents', 'directory', 'dni-barcode', 'dni-scan',
  'format', 'hours', 'islands', 'listing', 'offer', 'passkeys', 'photo-zoom', 'prep', 'search', 'tiles', 'turnstile', 'week',
];
const retiredFiles = [
  'ui-core.js', 'specialty-browser.js', 'stz-ui.js', 'rubricador.js',
  'app.html', 'panel.html', 'directorio.html',
];
const retiredRootMjs = ['age.mjs', 'dni-barcode.mjs', 'dni-scan.mjs', 'prep.mjs', 'passkeys.mjs', 'turnstile.mjs'];
const root = resolve(process.argv[2] || resolve(dirname(fileURLToPath(import.meta.url)), '..'));
let failures = 0;
for (const stem of retired) {
  const name = `${stem}.js`;
  try {
    lstatSync(resolve(root, name));
    console.error(`Retired client source remains: ${name}`);
    failures++;
  } catch (error) {
    if (error.code !== 'ENOENT') throw error;
  }
}
for (const name of retiredFiles) {
  try {
    lstatSync(resolve(root, name));
    console.error(`Retired client source remains: ${name}`);
    failures++;
  } catch (error) {
    if (error.code !== 'ENOENT') throw error;
  }
}
for (const name of retiredRootMjs) {
  try {
    lstatSync(resolve(root, name));
    console.error(`Retired client source remains: ${name}`);
    failures++;
  } catch (error) {
    if (error.code !== 'ENOENT') throw error;
  }
}
process.exitCode = failures ? 1 : 0;
