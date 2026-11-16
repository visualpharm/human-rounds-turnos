'use strict';
/* Config loader for usability.config.json (schema/usability-config.v1.json).
 *
 * Validates with a small hand-written walker (no new dependencies) and fills
 * defaults. Credential resolution happens at runtime only: env vars named by
 * roles.<r>.login.env only (the hub broker or the caller provides them; no vault CLI).
 * Values never reach a log, a report or an error message.
 */

const fs = require('node:fs');
const path = require('node:path');

const SEVERITIES = ['critical', 'serious', 'moderate', 'minor'];
const CHECK_IDS = ['links', 'console', 'requests', 'images', 'overflow', 'axe', 'contrast',
  'text_density', 'tap_targets', 'overlays', 'menu_style', 'lighthouse', 'html', 'walkthrough'];
const DEFAULT_VIEWPORTS = [360, 390, 834, 1440];

class ConfigError extends Error {
  constructor(errors) {
    super(`invalid usability config: ${errors.join('; ')}`);
    this.name = 'ConfigError';
    this.errors = errors;
  }
}

function isPlainObject(v) { return v !== null && typeof v === 'object' && !Array.isArray(v); }
function isHttpUrl(v) { return typeof v === 'string' && /^https?:\/\/.{2,}/.test(v); }
function isPagePath(v) { return typeof v === 'string' && v.startsWith('/'); }

/* Each check returns a list of error strings (empty when the value passes). */
function checkString(field, v, { min = 1 } = {}) {
  return typeof v === 'string' && v.trim().length >= min ? [] : [`${field} must be a non-empty string`];
}
function checkStringArray(field, v, { min = 0, itemCheck } = {}) {
  if (!Array.isArray(v) || v.length < min) return [`${field} must be an array with at least ${min} item(s)`];
  return v.flatMap((item, i) => itemCheck(field, item, i));
}
function pathArray(field, v, { min = 0 } = {}) {
  return checkStringArray(field, v, { min, itemCheck: (f, item, i) => (isPagePath(item) ? [] : [`${f}[${i}] must be a path starting with "/"`]) });
}

function validateLoginStep(field, step, i) {
  const where = `${field}[${i}]`;
  if (!isPlainObject(step)) return [`${where} must be an object`];
  const keys = Object.keys(step).filter((k) => !['fill', 'click', 'wait_url', 'secret', 'value'].includes(k));
  if (keys.length) return [`${where} has unknown key(s): ${keys.join(', ')}`];
  const actions = ['fill', 'click', 'wait_url'].filter((k) => step[k] !== undefined);
  if (actions.length !== 1) return [`${where} must have exactly one of fill, click, wait_url`];
  const errs = checkString(`${where}.${actions[0]}`, step[actions[0]]);
  if (step.secret !== undefined) errs.push(...checkString(`${where}.secret`, step.secret));
  if (step.value !== undefined && typeof step.value !== 'string') errs.push(`${where}.value must be a string`);
  if (step.fill !== undefined && step.value !== undefined && step.secret !== undefined) errs.push(`${where} cannot set both secret and value`);
  return errs;
}

function validateRole(field, role) {
  if (!isPlainObject(role)) return [`${field} must be an object`];
  let errs = [];
  if (!isPlainObject(role.login)) return [`${field}.login must be an object`];
  if (!isPagePath(role.login.url)) errs.push(`${field}.login.url must be a path starting with "/"`);
  if (!Array.isArray(role.login.steps) || !role.login.steps.length) {
    errs.push(`${field}.login.steps must be a non-empty array`);
  } else {
    role.login.steps.forEach((step, i) => { errs = errs.concat(validateLoginStep(`${field}.login.steps`, step, i)); });
  }
  if (role.login.env !== undefined) {
    if (!isPlainObject(role.login.env)) errs.push(`${field}.login.env must be an object`);
    else for (const [secret, envName] of Object.entries(role.login.env)) errs.push(...checkString(`${field}.login.env.${secret}`, envName));
  }
  if (role.login.op_item !== undefined) errs.push(...checkString(`${field}.login.op_item`, role.login.op_item));
  if (role.login.op_vault !== undefined) errs.push(...checkString(`${field}.login.op_vault`, role.login.op_vault));
  errs.push(...pathArray(`${field}.pages`, role.pages, { min: 1 }));
  return errs;
}

/* Returns {errors} — unknown keys are left alone so v1 configs can grow. */
function validateConfig(cfg) {
  const errors = [];
  if (!isPlainObject(cfg)) return { errors: ['config root must be an object'] };
  if (cfg.version !== 1) errors.push('version must be 1');
  errors.push(...checkString('project', cfg.project));
  if (!isHttpUrl(cfg.base_url)) errors.push('base_url must be an http(s) URL');
  if (cfg.local_url !== undefined && !isHttpUrl(cfg.local_url)) errors.push('local_url must be an http(s) URL');
  if (cfg.viewports !== undefined) {
    if (!Array.isArray(cfg.viewports) || !cfg.viewports.length) errors.push('viewports must be a non-empty array');
    else cfg.viewports.forEach((v, i) => {
      if (!Number.isInteger(v) || v < 200 || v > 4000) errors.push(`viewports[${i}] must be an integer between 200 and 4000`);
    });
  }
  errors.push(...pathArray('public_pages', cfg.public_pages, { min: 1 }));
  if (cfg.roles !== undefined) {
    if (!isPlainObject(cfg.roles)) errors.push('roles must be an object');
    else for (const [name, role] of Object.entries(cfg.roles)) errors.push(...validateRole(`roles.${name}`, role));
  }
  if (cfg.crawl !== undefined) {
    if (!isPlainObject(cfg.crawl)) errors.push('crawl must be an object');
    else {
      if (cfg.crawl.max_pages !== undefined && (!Number.isInteger(cfg.crawl.max_pages) || cfg.crawl.max_pages < 1)) errors.push('crawl.max_pages must be a positive integer');
      if (cfg.crawl.exclude !== undefined) errors.push(...checkStringArray('crawl.exclude', cfg.crawl.exclude, { itemCheck: (f, item, i) => checkString(`${f}[${i}]`, item) }));
    }
  }
  if (cfg.lighthouse !== undefined) {
    if (!isPlainObject(cfg.lighthouse)) errors.push('lighthouse must be an object');
    else if (cfg.lighthouse.pages !== undefined) errors.push(...pathArray('lighthouse.pages', cfg.lighthouse.pages));
  }
  if (cfg.checks !== undefined) {
    if (!isPlainObject(cfg.checks)) errors.push('checks must be an object');
    else {
      if (cfg.checks.disable !== undefined) {
        errors.push(...checkStringArray('checks.disable', cfg.checks.disable, {
          itemCheck: (f, item, i) => (CHECK_IDS.includes(item) ? [] : [`${f}[${i}] must be one of: ${CHECK_IDS.join(', ')}`]),
        }));
      }
      if (cfg.checks.severity_overrides !== undefined) {
        if (!isPlainObject(cfg.checks.severity_overrides)) errors.push('checks.severity_overrides must be an object');
        else for (const [id, sev] of Object.entries(cfg.checks.severity_overrides)) {
          if (!CHECK_IDS.includes(id)) errors.push(`checks.severity_overrides.${id} must be one of: ${CHECK_IDS.join(', ')}`);
          if (!SEVERITIES.includes(sev)) errors.push(`checks.severity_overrides.${id} must be one of: ${SEVERITIES.join(', ')}`);
        }
      }
    }
  }
  if (cfg.walkthroughs !== undefined) {
    errors.push(...checkStringArray('walkthroughs', cfg.walkthroughs, {
      min: 0,
      itemCheck: (f, w, i) => {
        if (!isPlainObject(w)) return [`${f}[${i}] must be an object`];
        let e = [];
        e.push(...checkString(`${f}[${i}].name`, w.name));
        e.push(...checkString(`${f}[${i}].role`, w.role));
        if(w.driver!==undefined&&!['stagehand','playwright'].includes(w.driver))e.push(`${f}[${i}].driver must be stagehand or playwright`);
        if (w.media !== undefined && w.media !== 'fake') e.push(`${f}[${i}].media must be fake`);
        if (!isPagePath(w.start)) e.push(`${f}[${i}].start must be a path starting with "/"`);
        e.push(...checkStringArray(`${f}[${i}].steps`, w.steps, { min: 1, itemCheck: (ff, s, j) => checkString(`${ff}[${j}]`, s) }));
        return e;
      },
    }));
  }
  if (cfg.llm !== undefined) {
    if (!isPlainObject(cfg.llm)) errors.push('llm must be an object');
    else {
      if (cfg.llm.model !== undefined) errors.push(...checkString('llm.model', cfg.llm.model));
      if (cfg.llm.openrouter_op_item !== undefined) errors.push(...checkString('llm.openrouter_op_item', cfg.llm.openrouter_op_item));
    }
  }
  return { errors };
}

/* Applies defaults; returns a normalized copy. */
function normalizeConfig(cfg) {
  return {
    ...cfg,
    viewports: cfg.viewports || DEFAULT_VIEWPORTS.slice(),
    roles: cfg.roles || {},
    crawl: { max_pages: 40, exclude: [], ...(cfg.crawl || {}) },
    lighthouse: { pages: [], ...(cfg.lighthouse || {}) },
    checks: { disable: [], severity_overrides: {}, ...(cfg.checks || {}) },
    walkthroughs: cfg.walkthroughs || [],
    llm: cfg.llm || {},
  };
}

function loadConfig(file) {
  let raw;
  try {
    raw = fs.readFileSync(path.resolve(file), 'utf8');
  } catch (e) {
    throw new ConfigError([`cannot read ${file}: ${e.code || e.message}`]);
  }
  let cfg;
  try {
    cfg = JSON.parse(raw);
  } catch (e) {
    throw new ConfigError([`${file} is not valid JSON: ${e.message}`]);
  }
  const { errors } = validateConfig(cfg);
  if (errors.length) throw new ConfigError(errors);
  return normalizeConfig(cfg);
}

/* ---- credential resolution: environment variables only; values never logged ----
 * The kit never starts a vault CLI and never prompts: the hub's broker (or the
 * caller) puts the values in the environment. A missing value is reported, and
 * the step lands in skipped[] — no window, no crash. Config keys `op_item` /
 * `op_vault` / `llm.openrouter_op_item` from older configs are accepted and ignored. */

/* secrets: names used by the login steps, e.g. ['username', 'password'].
 * Resolves each from process.env through login.env.
 * Returns {ok: true, creds} or {ok: false, reason}. */
async function resolveCredentials(login, secrets) {
  const creds = {};
  for (const secret of secrets) {
    const envName = login.env ? login.env[secret] : undefined;
    if (envName && process.env[envName]) creds[secret] = process.env[envName];
  }
  const missing = secrets.filter((s) => creds[s] === undefined);
  if (missing.length) {
    return { ok: false, reason: `no resolvable credential(s): ${missing.join(', ')} (environment variable unset)` };
  }
  return { ok: true, creds };
}

/* OpenRouter key for walkthroughs: OPENROUTER_API_KEY only; null when unset. */
async function resolveOpenRouterKey() {
  return process.env.OPENROUTER_API_KEY || null;
}

module.exports = {
  ConfigError,
  DEFAULT_VIEWPORTS,
  CHECK_IDS,
  SEVERITIES,
  loadConfig,
  validateConfig,
  normalizeConfig,
  resolveCredentials,
  resolveOpenRouterKey,
};
