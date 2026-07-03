// @ts-nocheck
const vscode = acquireVsCodeApi();
const log = document.getElementById('log');
const form = document.getElementById('form');
const input = document.getElementById('input');

const EXAMPLES = [
  'is auth built on short-lived tokens?',
  'why not microservices?',
  'why Postgres and not Mongo?',
  'did we build feature flags ourselves?',
];

function el(cls, html) {
  const d = document.createElement('div');
  d.className = cls;
  if (html !== undefined) d.innerHTML = html;
  return d;
}
function esc(s) {
  return String(s).replace(/[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c]));
}
function scroll() { log.scrollTop = log.scrollHeight; }

function intro() {
  const d = el('intro', 'Ask <code>/lore</code> to recall a decision from your team’s Canon — answered with provenance.');
  const row = el('chip-row');
  EXAMPLES.forEach((q) => {
    const c = el('chip');
    c.textContent = q;
    c.onclick = () => submit(q);
    row.appendChild(c);
  });
  d.appendChild(row);
  log.appendChild(d);
}
intro();

function addUser(q) {
  const m = el('msg user');
  m.appendChild(el('who', 'You'));
  m.appendChild(el('bubble', '<span class="slash">/lore</span> ' + esc(q)));
  log.appendChild(m);
  scroll();
}

let thinkingEl = null;
function addThinking() {
  const m = el('msg bot');
  m.appendChild(el('who', 'Lore'));
  m.appendChild(el('bubble spinner', 'searching decision memory'));
  log.appendChild(m);
  thinkingEl = m;
  scroll();
}

function addAnswer(data) {
  if (thinkingEl) { thinkingEl.remove(); thinkingEl = null; }
  const m = el('msg bot');
  m.appendChild(el('who', 'Lore'));
  m.appendChild(el('bubble', esc(data.answer)));
  if (data.sources && data.sources.length) {
    const s = el('sources');
    s.appendChild(el('prov-label', 'Provenance'));
    data.sources.forEach((src) => {
      const label = Array.isArray(src) ? src[1] : src;
      s.appendChild(el('src', esc(label)));
    });
    m.appendChild(s);
  }
  m.appendChild(el('meta', `${data.mode} · ${data.latency_s}s`));
  log.appendChild(m);
  scroll();
}

function addError(msg) {
  if (thinkingEl) { thinkingEl.remove(); thinkingEl = null; }
  const m = el('msg bot');
  m.appendChild(el('who', 'Lore'));
  m.appendChild(el('bubble error', 'Couldn’t reach the backend: ' + esc(msg) + '. Is it running on the configured URL?'));
  log.appendChild(m);
  scroll();
}

function submit(q) {
  q = (q || '').trim();
  if (!q) return;
  vscode.postMessage({ type: 'ask', question: q });
  input.value = '';
}

form.addEventListener('submit', (e) => {
  e.preventDefault();
  submit(input.value);
});

window.addEventListener('message', (e) => {
  const msg = e.data;
  if (msg.type === 'asking') { addUser(msg.question); addThinking(); }
  else if (msg.type === 'answer') { addAnswer(msg.data); }
  else if (msg.type === 'error') { addError(msg.message); }
  else if (msg.type === 'prefill') { input.value = msg.question; input.focus(); }
});
