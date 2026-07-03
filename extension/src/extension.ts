import * as vscode from 'vscode';

// VS Code 1.85 runs on Node 18+, which has a global fetch. Access it loosely
// to avoid pulling DOM/undici type definitions into the extension build.
const fetchFn: (input: any, init?: any) => Promise<any> = (globalThis as any).fetch;

function backendUrl(): string {
  const raw = String(
    vscode.workspace.getConfiguration('lore').get('backendUrl', 'http://localhost:8000')
  );
  return raw.replace(/\/+$/, '');
}

async function postWhy(question: string): Promise<any> {
  const res = await fetchFn(`${backendUrl()}/why`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question }),
  });
  if (!res.ok) throw new Error(`backend responded ${res.status}`);
  return res.json();
}

/** Sidebar webview: the /why chat. */
class LoreViewProvider implements vscode.WebviewViewProvider {
  public static readonly viewType = 'lore.chat';
  private view?: vscode.WebviewView;
  private pending?: string;

  constructor(private readonly extUri: vscode.Uri) {}

  resolveWebviewView(view: vscode.WebviewView) {
    this.view = view;
    view.webview.options = {
      enableScripts: true,
      localResourceRoots: [vscode.Uri.joinPath(this.extUri, 'media')],
    };
    view.webview.html = this.html(view.webview);
    view.webview.onDidReceiveMessage((msg) => {
      if (msg?.type === 'ask') this.run(String(msg.question || ''));
    });
    if (this.pending) {
      const q = this.pending;
      this.pending = undefined;
      this.run(q);
    }
  }

  /** Ask a question programmatically (from a command). Reveals the view first. */
  public async ask(question: string) {
    await vscode.commands.executeCommand('lore.chat.focus');
    if (!this.view) {
      this.pending = question; // flushed when resolveWebviewView runs
      return;
    }
    setTimeout(() => this.run(question), 60); // let the webview mount
  }

  private async run(question: string) {
    question = question.trim();
    if (!question || !this.view) return;
    this.view.webview.postMessage({ type: 'asking', question });
    try {
      const data = await postWhy(question);
      this.view.webview.postMessage({ type: 'answer', data });
    } catch (e: any) {
      this.view.webview.postMessage({ type: 'error', message: e?.message || String(e) });
    }
  }

  private html(webview: vscode.Webview): string {
    const uri = (p: string) =>
      webview.asWebviewUri(vscode.Uri.joinPath(this.extUri, 'media', p));
    const nonce = String(Math.random()).slice(2);
    const csp =
      `default-src 'none'; img-src ${webview.cspSource}; ` +
      `style-src ${webview.cspSource}; font-src ${webview.cspSource} https:; ` +
      `script-src 'nonce-${nonce}';`;
    return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta http-equiv="Content-Security-Policy" content="${csp}" />
  <link rel="stylesheet" href="${uri('panel.css')}" />
</head>
<body>
  <div id="log"></div>
  <form id="form">
    <span class="slash">/lore</span>
    <input id="input" placeholder="is auth built on short-lived tokens?" autocomplete="off" />
    <button type="submit">Ask</button>
  </form>
  <script nonce="${nonce}" src="${uri('panel.js')}"></script>
</body>
</html>`;
  }
}

export function activate(context: vscode.ExtensionContext) {
  const provider = new LoreViewProvider(context.extensionUri);
  context.subscriptions.push(
    vscode.window.registerWebviewViewProvider(LoreViewProvider.viewType, provider)
  );

  context.subscriptions.push(
    vscode.commands.registerCommand('lore.askWhy', async () => {
      const q = await vscode.window.showInputBox({
        prompt: 'Recall a decision from the Canon',
        placeHolder: 'why is auth built on short-lived tokens?',
      });
      if (q) provider.ask(q);
    }),

    vscode.commands.registerCommand('lore.whyThis', async () => {
      const ed = vscode.window.activeTextEditor;
      if (!ed) {
        vscode.window.showInformationMessage('Open a file and select some code first.');
        return;
      }
      const sel = ed.document.getText(ed.selection).trim();
      const rel = vscode.workspace.asRelativePath(ed.document.uri);
      const q = sel
        ? `Why is this here? (${rel})\n\n${sel.slice(0, 800)}`
        : `Why is ${rel} built the way it is?`;
      provider.ask(q);
    }),

    vscode.commands.registerCommand('lore.loadDemo', async () => {
      try {
        const res = await fetchFn(`${backendUrl()}/ingest/seed`, { method: 'POST' });
        const data = await res.json();
        vscode.window.showInformationMessage(
          `Lore: seeded the Canon (${data.ingested} Whys, ${data.mode} mode).`
        );
      } catch (e: any) {
        vscode.window.showErrorMessage(
          `Lore: ${e?.message || e}. Is the backend running on ${backendUrl()}?`
        );
      }
    })
  );

  // Status bar: reflect backend mode (mock / live / offline).
  const status = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 100);
  status.command = 'lore.askWhy';
  status.text = '$(book) Lore';
  status.show();
  context.subscriptions.push(status);
  refreshStatus(status);
}

async function refreshStatus(status: vscode.StatusBarItem) {
  try {
    const res = await fetchFn(`${backendUrl()}/health`);
    const h = await res.json();
    status.text = `$(book) Lore: ${h.mode}`;
    status.tooltip = `Lore backend online — ${h.mode} mode. Click to ask /why.`;
  } catch {
    status.text = '$(book) Lore: offline';
    status.tooltip = `Lore backend not reachable at ${backendUrl()}. Start it with: uvicorn main:app --port 8000`;
  }
}

export function deactivate() {}
