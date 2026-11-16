#!/usr/bin/env node
'use strict';
/* usability-kit CLI. The dispatcher is one plain switch so merge conflicts
 * stay trivial; each future subcommand gets a case here and its logic in
 * src/. Exit codes: 0 ok, 1 critical problems, 2 tooling error. */

const { loadConfig } = require('../src/config');
const { runPreflight } = require('../src/preflight');
const { exitCodeFor } = require('../src/preflight/report');

const USAGE = `usage: usability-kit <command> [flags]

commands:
  preflight   crawl + check the site (writes DIR/report.json and DIR/shots/)
  ingest      not implemented in this build
  render      not implemented in this build
  serve-demo  not implemented in this build
  study       not implemented in this build
  compile     not implemented in this build
  dashboard   not implemented in this build
  drift       not implemented in this build
  sync        not implemented in this build

preflight flags:
  --config PATH         usability.config.json (default: usability.config.json)
  --base URL            override base_url
  --out DIR             report directory (default: usability-out)
  --only a,b,c          run only these checks
  --max-pages N         override crawl.max_pages
  --external            also check external links
  --no-lighthouse       skip the lighthouse check
  --no-walkthrough      skip the walkthrough check`;

function die(message, code = 2) {
  process.stderr.write(`usability-kit: ${message}\n`);
  process.exit(code);
}

function parsePreflightArgs(argv) {
  const opts = { config: 'usability.config.json', out: 'usability-out' };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === '--help' || a === '-h') {
      process.stdout.write(`${USAGE}\n`);
      process.exit(0);
    } else if (a === '--config') opts.config = argv[++i];
    else if (a === '--base') opts.baseUrl = argv[++i];
    else if (a === '--out') opts.out = argv[++i];
    else if (a === '--only') opts.only = String(argv[++i] || '').split(',').map((s) => s.trim()).filter(Boolean);
    else if (a === '--max-pages') opts.maxPages = Number(argv[++i]);
    else if (a === '--no-lighthouse') opts.noLighthouse = true;
    else if (a === '--no-walkthrough') opts.noWalkthrough = true;
    else if (a === '--external') opts.external = true;
    else die(`unknown flag for preflight: ${a} (see --help)`);
  }
  if (opts.maxPages !== undefined && (!Number.isInteger(opts.maxPages) || opts.maxPages < 1)) die('--max-pages must be a positive integer');
  return opts;
}

async function cmdPreflight(argv) {
  const opts = parsePreflightArgs(argv);
  const config = loadConfig(opts.config);
  const report = await runPreflight(config, opts);
  const s = report.summary;
  process.stdout.write(`${report.project}: ${s.critical} critical, ${s.serious} serious, ${s.moderate} moderate, ${s.minor} minor — ${opts.out}/report.json\n`);
  if (report.skipped.length) {
    for (const sk of report.skipped) process.stdout.write(`skipped ${sk.kind} ${sk.name}: ${sk.reason}\n`);
  }
  process.exit(exitCodeFor(report));
}

async function main() {
  const [cmd, ...rest] = process.argv.slice(2);
  switch (cmd) {
    case 'preflight':
      return cmdPreflight(rest);
    case 'ingest':
    case 'render':
    case 'serve-demo':
    case 'study':
    case 'compile':
    case 'dashboard':
    case 'drift':
    case 'sync':
    case 'runner':
    case 'push':
      process.stdout.write(`${cmd}: not implemented in this build\n`);
      process.exit(2);
    default:
      process.stdout.write(`${USAGE}\n`);
      process.exit(2);
  }
}

main().catch((e) => {
  process.stderr.write(`usability-kit: ${e && e.message ? e.message : String(e)}\n`);
  process.exit(2);
});
