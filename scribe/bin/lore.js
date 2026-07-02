#!/usr/bin/env node
'use strict';

/**
 * Lore — the Scribe.
 *   lore init      install the post-commit hook (Husky-style)
 *   lore capture   (internal) called by the hook: inscribe HEAD's Why
 *   lore recall    ask /why from the terminal
 *   lore canon     show this repo's Canon
 *   lore log       recent Whys
 *
 * Zero dependencies — uses Node 18+ globals (fetch) and built-ins.
 */

const fs = require('fs');
const path = require('path');
const { execSync } = require('child_process');

const CONFIG_FILE = '.lore.json';

// ---------------------------------------------------------------- helpers
function git(args) {
  return execSync('git ' + args, { encoding: 'utf8', stdio: ['pipe', 'pipe', 'ignore'] }).trim();
}
function repoRoot() {
  try { return git('rev-parse --show-toplevel'); } catch { return null; }
}
function readConfig() {
  try {
    return JSON.parse(fs.readFileSync(CONFIG_FILE, 'utf8'));
  } catch {
    return { backendUrl: 'http://localhost:8000', canon: path.basename(process.cwd()) };
  }
}
function writeConfig(cfg) {
  fs.writeFileSync(CONFIG_FILE, JSON.stringify(cfg, null, 2) + '\n');
}
async function api(url, method, body) {
  const res = await fetch(url, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) throw new Error(`${method} ${url} -> ${res.status}`);
  return res.json();
}
function wrap(s, w = 76) {
  return String(s).replace(new RegExp(`(.{1,${w}})(\\s|$)`, 'g'), '$1\n  ').trim();
}
function arg(args, flag) {
  const i = args.indexOf(flag);
  return i >= 0 ? args[i + 1] : undefined;
}

// ---------------------------------------------------------------- commands
function cmdInit(args) {
  const root = repoRoot();
  if (!root) {
    console.error('lore: not a git repository — run this inside a repo.');
    process.exit(1);
  }
  const cfg = readConfig();
  cfg.backendUrl = arg(args, '--url') || cfg.backendUrl || 'http://localhost:8000';
  cfg.canon = arg(args, '--canon') || cfg.canon || path.basename(root);
  writeConfig(cfg);

  const hookPath = path.join(root, '.git', 'hooks', 'post-commit');
  const hook =
    '#!/bin/sh\n' +
    '# Lore Scribe — inscribe this commit\'s Why into the Canon\n' +
    'npx --no-install lore capture >/dev/null 2>&1 || true\n';

  if (fs.existsSync(hookPath) && !fs.readFileSync(hookPath, 'utf8').includes('Lore Scribe')) {
    fs.copyFileSync(hookPath, hookPath + '.backup');
    console.log('lore: existing post-commit hook backed up -> post-commit.backup');
  }
  fs.writeFileSync(hookPath, hook);
  try { fs.chmodSync(hookPath, 0o755); } catch { /* windows */ }

  console.log('\n  📖  Lore Scribe installed.');
  console.log('      Canon:   ' + cfg.canon);
  console.log('      Backend: ' + cfg.backendUrl + '\n');
  console.log('  Add a "Why:" line to a commit and it gets inscribed:\n');
  console.log('      git commit -m "feat(auth): short-lived JWTs" -m "Why: Redis failover logged everyone out; stateless tokens remove that SPOF."\n');
  console.log('  Then:  lore recall "why is auth stateless?"\n');
}

function parseHead() {
  const S = '<<<LORE>>>';
  const raw = git(`log -1 --pretty=%H${S}%an${S}%s${S}%b`);
  const [hash, author, subject, body] = raw.split(S);
  let why = '';
  const m = (body || '').match(/^\s*Why:\s*([\s\S]+?)\s*$/im);
  if (m) why = m[1].trim();
  return { hash, author, subject, body, why };
}

async function cmdCapture() {
  // Invoked by the git hook. Must NEVER block or noisily fail a commit.
  try {
    const cfg = readConfig();
    const c = parseHead();
    if (!c.why) return;             // only inscribe commits that declared a Why
    let branch = '';
    try { branch = git('rev-parse --abbrev-ref HEAD'); } catch {}
    await api(cfg.backendUrl + '/inscribe', 'POST', {
      hash: c.hash, message: c.subject, why: c.why,
      author: c.author, repo: cfg.canon, branch,
    });
  } catch { /* silent by design */ }
}

async function cmdRecall(args) {
  const cfg = readConfig();
  const q = args.filter((a) => !a.startsWith('--')).join(' ').trim();
  if (!q) { console.error('Usage: lore recall "<question>"'); process.exit(1); }
  try {
    const data = await api(cfg.backendUrl + '/why', 'POST', { question: q });
    console.log('\n  ' + wrap(data.answer) + '\n');
    if (data.sources && data.sources.length) {
      const prov = data.sources.map((s) => (Array.isArray(s) ? s[1] : s)).join('  ·  ');
      console.log('  Provenance:  ' + prov);
    }
    console.log(`  (${data.mode} · ${data.latency_s}s)\n`);
  } catch (e) {
    console.error('lore: ' + e.message + ' — is the backend running?');
    process.exit(1);
  }
}

async function cmdCanon() {
  const cfg = readConfig();
  try {
    const data = await api(cfg.backendUrl + '/canon', 'GET');
    console.log(`\n  📖  Canon "${cfg.canon}" — ${data.count} Whys (${data.mode})\n`);
    (data.memories || []).slice(0, 25).forEach((m) => {
      console.log('   •  ' + (m.memory || '').slice(0, 88) + '   [' + (m.source || '?') + ']');
    });
    console.log('');
  } catch (e) {
    console.error('lore: ' + e.message + ' — is the backend running?');
    process.exit(1);
  }
}

function usage() {
  console.log('\n  Lore — the Scribe\n');
  console.log('    lore init [--url <backend>] [--canon <name>]   install the commit hook');
  console.log('    lore recall "<question>"                        recall a decision (/why)');
  console.log('    lore canon                                      show this repo\'s Canon');
  console.log('    lore log                                        recent Whys');
  console.log('\n  Add a "Why: ..." line to a commit message to inscribe it.\n');
}

// ---------------------------------------------------------------- dispatch
const [cmd, ...rest] = process.argv.slice(2);
(async () => {
  switch (cmd) {
    case 'init': cmdInit(rest); break;
    case 'capture': await cmdCapture(); break;
    case 'recall': await cmdRecall(rest); break;
    case 'canon': await cmdCanon(); break;
    case 'log': await cmdCanon(); break;
    default: usage();
  }
})();
