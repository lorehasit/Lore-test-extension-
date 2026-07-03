#!/usr/bin/env node
'use strict';

/**
 * Lore CLI.
 *   lore              launch the interactive REPL (banner + /why prompt)
 *   lore start        same as above (alias: chat)
 *   lore recall "q"   one-off question
 *   lore canon        show the Canon
 *   lore init         install the git commit hook (Scribe)
 *   lore capture      (internal) called by the hook
 *
 * Zero dependencies — Node 18+ globals (fetch) + built-ins only.
 */

const fs = require('fs');
const os = require('os');
const path = require('path');
const readline = require('readline');
const { execSync } = require('child_process');

const CONFIG_FILE = '.lore.json';

// ---------------------------------------------------------------- ansi
const O = '\x1b[38;2;252;145;23m'; // Lore orange
const CY = '\x1b[38;2;125;211;252m';
const B = '\x1b[1m';
const D = '\x1b[2m';
const R = '\x1b[0m';
const RED = '\x1b[31m';

const BANNER = `
                       %%%%%%%
                  %#+++=.  .:+*+*#%%
               %#+=====:       :++=+#%
             %#+======-          .++==#%
            %*=======+.            .+==+%
           %#========+               *==+%
           #+========+              = *==*%
           #=========+           .. : .*=+#
           #=========*    ==      .:   *==*%
           #+========+-             :. +==*%
           #*=========+              :-*==*%         %@
           %#**+======+-           .-. #==*%      @%%%%@
           %%#++=======+:      .::.   :*=+%       @%
           %%%#*++=====++.            #=+#%      @%@
          %%  %%#*++++++*+.          **+#@       %@
         %%     @%#***+++*+         =*+#%%      %%
        @@        @%%##*****       :*+%@%%%    %%
        @@        @@@@%#***##     .**%%   %% %%%
        @      @@@@  @@@%%#**%   .+#%@     @%%
        @@@@@@@           @%#*#.:*%%
                            @%#**%@
                             @@%@@
                            %%#%%%
                          @#***+**%
                          %**+***#%
                          @@%#+*#%`;

// ---------------------------------------------------------------- helpers
function git(args) {
  return execSync('git ' + args, { encoding: 'utf8', stdio: ['pipe', 'pipe', 'ignore'] }).trim();
}
function repoRoot() {
  try { return git('rev-parse --show-toplevel'); } catch { return null; }
}
function readConfig() {
  for (const p of [CONFIG_FILE, path.join(os.homedir(), '.lore.json')]) {
    try { return JSON.parse(fs.readFileSync(p, 'utf8')); } catch { /* next */ }
  }
  return {
    backendUrl: process.env.LORE_BACKEND_URL || 'http://localhost:8000',
    canon: path.basename(process.cwd()),
  };
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
function printAnswer(data) {
  console.log('');
  console.log('  ' + wrap(data.answer || ''));
  if (data.sources && data.sources.length) {
    const prov = data.sources.map((s) => (Array.isArray(s) ? s[1] : s)).join('  ·  ');
    console.log('  ' + D + 'provenance  ' + R + O + prov + R);
  }
  console.log('  ' + D + `(${data.mode} · ${data.latency_s}s)` + R);
  console.log('');
}

// ---------------------------------------------------------------- interactive REPL
async function interactive(args) {
  const cfg = readConfig();
  const override = arg(args, '--url');
  if (override) cfg.backendUrl = override;
  cfg.backendUrl = (cfg.backendUrl || 'http://localhost:8000').replace(/\/+$/, '');

  console.clear();
  console.log(O + BANNER + R);

  let count = '·';
  try { count = (await api(cfg.backendUrl + '/canon', 'GET')).count; } catch { /* offline */ }

  console.log('');
  console.log('   ' + B + O + 'LORE' + R + D + '  — your team’s decisions, remembered' + R);
  console.log('   ' + D + 'backend ' + R + cfg.backendUrl);
  console.log('   ' + D + 'canon ' + R + cfg.canon + D + '   whys ' + R + count);
  console.log('');
  console.log('   ' + D + 'Ask why any decision was made. Type ' + R + O + '/help' + R + D +
              ' for commands, ' + R + O + '/quit' + R + D + ' to exit.' + R);
  console.log('');

  const rl = readline.createInterface({
    input: process.stdin,
    output: process.stdout,
    prompt: O + '/why ' + R,
  });
  let busy = false;         // a recall is in flight
  let exitRequested = false; // quit was hit mid-recall; exit once it finishes
  const doExit = () => {
    console.log('\n' + D + '  Canon closed. See you next decision.' + R + '\n');
    process.exit(0);
  };

  rl.prompt();

  rl.on('line', async (line) => {
    if (busy) return; // ignore input while a recall is running
    const raw = line.trim();
    if (!raw) return rl.prompt();

    if (raw === '/quit' || raw === '/exit' || raw === 'exit' || raw === 'quit') return rl.close();
    if (raw === '/help') { helpInline(); return rl.prompt(); }
    if (raw === '/clear') { console.clear(); console.log(O + BANNER + R + '\n'); return rl.prompt(); }
    if (raw === '/canon') {
      busy = true; await canonInline(cfg); busy = false;
      return exitRequested ? doExit() : rl.prompt();
    }

    const question = raw.startsWith('/why') ? raw.slice(4).trim() : raw;
    if (!question) return rl.prompt();

    busy = true;
    process.stdout.write('  ' + D + 'recalling…' + R);
    try {
      const data = await api(cfg.backendUrl + '/why', 'POST', { question });
      process.stdout.write('\r\x1b[2K'); // clear the "recalling" line
      printAnswer(data);
    } catch (e) {
      process.stdout.write('\r\x1b[2K');
      console.log('  ' + RED + 'Couldn’t reach the backend (' + e.message + '). Is it up at ' +
                  cfg.backendUrl + '?' + R + '\n');
    }
    busy = false;
    return exitRequested ? doExit() : rl.prompt();
  });

  rl.on('SIGINT', () => rl.close());
  rl.on('close', () => { if (busy) { exitRequested = true; return; } doExit(); });
}

function helpInline() {
  console.log('');
  console.log('   ' + O + '/why <question>' + R + D + '   recall a decision (or just type the question)' + R);
  console.log('   ' + O + '/canon' + R + D + '            list what’s in the Canon' + R);
  console.log('   ' + O + '/clear' + R + D + '            redraw the screen' + R);
  console.log('   ' + O + '/quit' + R + D + '             exit' + R);
  console.log('');
}

async function canonInline(cfg) {
  try {
    const d = await api(cfg.backendUrl + '/canon', 'GET');
    console.log('\n   ' + B + 'Canon' + R + D + ` — ${d.count} Whys` + R);
    (d.memories || []).slice(0, 20).forEach((m) => {
      console.log('   ' + D + '·' + R + ' ' + (m.memory || '').slice(0, 84) + '  ' + O + '[' + (m.source || '?') + ']' + R);
    });
    console.log('');
  } catch (e) {
    console.log('  ' + RED + e.message + R + '\n');
  }
}

// ---------------------------------------------------------------- one-off commands
function cmdInit(args) {
  const root = repoRoot();
  if (!root) { console.error('lore: not a git repository.'); process.exit(1); }
  const cfg = readConfig();
  cfg.backendUrl = arg(args, '--url') || cfg.backendUrl || 'http://localhost:8000';
  cfg.canon = arg(args, '--canon') || cfg.canon || path.basename(root);
  writeConfig(cfg);
  const hookPath = path.join(root, '.git', 'hooks', 'post-commit');
  const hook = '#!/bin/sh\n# Lore Scribe\nnpx --no-install lore capture >/dev/null 2>&1 || true\n';
  if (fs.existsSync(hookPath) && !fs.readFileSync(hookPath, 'utf8').includes('Lore Scribe')) {
    fs.copyFileSync(hookPath, hookPath + '.backup');
  }
  fs.writeFileSync(hookPath, hook);
  try { fs.chmodSync(hookPath, 0o755); } catch { /* windows */ }
  console.log(`\n  ${O}Lore Scribe installed.${R}  canon: ${cfg.canon}  backend: ${cfg.backendUrl}`);
  console.log(`  Add a "Why:" line to a commit and it gets inscribed.\n`);
}

function parseHead() {
  const S = '<<<LORE>>>';
  const [hash, author, subject, body] = git(`log -1 --pretty=%H${S}%an${S}%s${S}%b`).split(S);
  const m = (body || '').match(/^\s*Why:\s*([\s\S]+?)\s*$/im);
  return { hash, author, subject, why: m ? m[1].trim() : '' };
}
async function cmdCapture() {
  try {
    const cfg = readConfig();
    const c = parseHead();
    if (!c.why) return;
    let branch = '';
    try { branch = git('rev-parse --abbrev-ref HEAD'); } catch { /* detached */ }
    await api(cfg.backendUrl + '/inscribe', 'POST', {
      hash: c.hash, message: c.subject, why: c.why, author: c.author, repo: cfg.canon, branch,
    });
  } catch { /* silent */ }
}
async function cmdRecall(args) {
  const cfg = readConfig();
  if (arg(args, '--url')) cfg.backendUrl = arg(args, '--url');
  const q = args.filter((a) => !a.startsWith('--')).join(' ').trim();
  if (!q) { console.error('Usage: lore recall "<question>"'); process.exit(1); }
  try { printAnswer(await api(cfg.backendUrl.replace(/\/+$/, '') + '/why', 'POST', { question: q })); }
  catch (e) { console.error('lore: ' + e.message + ' — is the backend running?'); process.exit(1); }
}
async function cmdCanon() {
  await canonInline(readConfig());
}
function usage() {
  console.log('\n  Lore\n');
  console.log('    lore                       launch the interactive prompt');
  console.log('    lore start [--url <u>]      same (alias: chat)');
  console.log('    lore recall "<question>"    ask once');
  console.log('    lore canon                  show the Canon');
  console.log('    lore init [--url] [--canon] install the commit hook');
  console.log('');
}

// ---------------------------------------------------------------- dispatch
const [cmd, ...rest] = process.argv.slice(2);
(async () => {
  switch (cmd) {
    case undefined:
    case 'start':
    case 'chat': await interactive(rest); break;
    case 'init': cmdInit(rest); break;
    case 'capture': await cmdCapture(); break;
    case 'recall': await cmdRecall(rest); break;
    case 'canon': case 'log': await cmdCanon(); break;
    case 'help': case '--help': case '-h': usage(); break;
    default: usage();
  }
})();
