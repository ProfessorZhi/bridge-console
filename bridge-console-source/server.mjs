import http from 'node:http';
import { promises as fs } from 'node:fs';
import { openSync, readFileSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawn, spawnSync } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { fileURLToPath } from 'node:url';

const PORT = Number(process.env.BRIDGE_CONSOLE_PORT || 3210);
const SOURCE_ROOT = path.dirname(fileURLToPath(import.meta.url));
const PRODUCT_ROOT = path.dirname(SOURCE_ROOT);
const WORKSPACE_ROOT = path.dirname(PRODUCT_ROOT);
const LOCAL_CONFIG_PATH = path.join(SOURCE_ROOT, 'bridge-console.local.json');
const HOME = os.homedir();
const CTI_HOME = path.join(HOME, '.claude-to-im');
const CTI_CONFIG = path.join(CTI_HOME, 'config.env');
const CTI_STATUS = path.join(CTI_HOME, 'runtime', 'status.json');
const CTI_LOG = path.join(CTI_HOME, 'logs', 'bridge.log');
const CTI_ERR = path.join(CTI_HOME, 'logs', 'bridge.err.log');
const CTI_AUDIT = path.join(CTI_HOME, 'data', 'audit.json');
const HAPPY_HOME = path.join(HOME, '.happy');
const HAPPY_SETTINGS = path.join(HAPPY_HOME, 'settings.json');
const HAPPY_DAEMON = path.join(HAPPY_HOME, 'daemon.state.json');
const MANAGER_HOME = path.join(HOME, '.bridge-console');
const MANAGER_SETTINGS = path.join(MANAGER_HOME, 'settings.json');
const DEFAULT_CTI_SOURCE = path.join(WORKSPACE_ROOT, 'Claude-to-IM-skill', 'Claude-to-IM-skill-source');
const HAPPY_CMD = path.join(HOME, 'AppData', 'Roaming', 'npm', 'happy.cmd');

function loadLocalConfig() {
  try {
    return JSON.parse(readFileSync(LOCAL_CONFIG_PATH, 'utf8'));
  } catch {
    return {};
  }
}

function resolveConfigPath(value, fallback) {
  if (!value) return fallback;
  return path.isAbsolute(value) ? value : path.resolve(SOURCE_ROOT, value);
}

const LOCAL_CONFIG = loadLocalConfig();
const CTI_SOURCE = resolveConfigPath(
  LOCAL_CONFIG.ctiSource || process.env.BRIDGE_CONSOLE_CTI_SOURCE,
  DEFAULT_CTI_SOURCE
);

const capabilityMatrix = [
  {
    channel: 'Feishu',
    kind: 'Claude-to-IM',
    processView: 'No live process stream',
    imageInput: 'Supported',
    imageOutput: 'Text reply only in this bridge flow',
    permissions: 'Text /perm flow',
    workdirMode: 'Uses shared CTI default workdir'
  },
  {
    channel: 'QQ',
    kind: 'Claude-to-IM',
    processView: 'No live process stream',
    imageInput: 'Supported',
    imageOutput: 'Not supported by current QQ adapter',
    permissions: 'Text /perm flow',
    workdirMode: 'Uses shared CTI default workdir'
  },
  {
    channel: 'Happy Codex',
    kind: 'Happy',
    processView: 'Shows process/session progress better',
    imageInput: 'Weak / not primary path',
    imageOutput: 'Limited',
    permissions: 'Happy mobile permission flow',
    workdirMode: 'Per launch/session directory'
  }
];

async function ensureManagerSettings() {
  await fs.mkdir(MANAGER_HOME, { recursive: true });
  try {
    await fs.access(MANAGER_SETTINGS);
  } catch {
    const initial = {
      projectPresets: []
    };
    await fs.writeFile(MANAGER_SETTINGS, JSON.stringify(initial, null, 2), 'utf8');
  }
  const settings = await readJson(MANAGER_SETTINGS, {});
  if (!Array.isArray(settings.projectPresets) && Array.isArray(settings.happyPresets)) {
    settings.projectPresets = settings.happyPresets;
  }
  settings.projectPresets = Array.isArray(settings.projectPresets) ? settings.projectPresets : [];
  await fs.writeFile(MANAGER_SETTINGS, JSON.stringify(settings, null, 2), 'utf8');
}

async function readJson(file, fallback) {
  try {
    return JSON.parse(await fs.readFile(file, 'utf8'));
  } catch {
    return fallback;
  }
}

async function readText(file, fallback = '') {
  try {
    return await fs.readFile(file, 'utf8');
  } catch {
    return fallback;
  }
}

function parseEnv(text) {
  const out = {};
  for (const rawLine of text.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line || line.startsWith('#')) continue;
    const idx = line.indexOf('=');
    if (idx === -1) continue;
    out[line.slice(0, idx)] = line.slice(idx + 1);
  }
  return out;
}

async function readEnvFile(file) {
  return parseEnv(await readText(file));
}

async function updateEnvKey(file, key, value) {
  const text = await readText(file);
  const lines = text ? text.split(/\r?\n/) : [];
  let replaced = false;
  const next = lines.map((line) => {
    if (line.startsWith(`${key}=`)) {
      replaced = true;
      return `${key}=${value}`;
    }
    return line;
  });
  if (!replaced) next.push(`${key}=${value}`);
  await fs.writeFile(file, next.filter(Boolean).join('\r\n') + '\r\n', 'utf8');
}

async function tailFile(file, maxLines = 80) {
  const text = await readText(file, '');
  const lines = text.split(/\r?\n/).filter(Boolean);
  return lines.slice(-maxLines).join('\n');
}

async function pathInfo(targetPath) {
  if (!targetPath) return { exists: false, isDirectory: false };
  try {
    const st = await fs.stat(targetPath);
    return { exists: true, isDirectory: st.isDirectory() };
  } catch {
    return { exists: false, isDirectory: false };
  }
}

function isBridgeRunning(status) {
  return Boolean(status?.running && status?.pid);
}

async function stopBridge() {
  const status = await readJson(CTI_STATUS, {});
  if (!status?.pid) return { ok: true, message: 'Bridge is not running.' };
  spawnSync('taskkill', ['/PID', String(status.pid), '/T', '/F'], { stdio: 'ignore' });
  return { ok: true, message: `Stopped bridge PID ${status.pid}.` };
}

async function startBridge() {
  const daemonEntry = path.join(CTI_SOURCE, 'dist', 'daemon.mjs');
  const sourceInfo = await pathInfo(CTI_SOURCE);
  const daemonInfo = await pathInfo(daemonEntry);
  if (!sourceInfo.exists || !sourceInfo.isDirectory) {
    return { ok: false, message: `Bridge 源码目录不存在：${CTI_SOURCE}` };
  }
  if (!daemonInfo.exists) {
    return { ok: false, message: `未找到 Bridge 入口文件：${daemonEntry}` };
  }
  await fs.mkdir(path.join(CTI_HOME, 'logs'), { recursive: true });
  await fs.mkdir(path.join(CTI_HOME, 'runtime'), { recursive: true });
  const out = openSync(CTI_LOG, 'a');
  const err = openSync(CTI_ERR, 'a');
  const child = spawn('node', [daemonEntry], {
    cwd: CTI_SOURCE,
    detached: true,
    windowsHide: true,
    stdio: ['ignore', out, err],
    env: process.env
  });
  child.unref();
  return { ok: true, message: `Bridge start requested (PID ${child.pid}).` };
}

async function launchHappyCodex(workdir) {
  const child = spawn('powershell.exe', ['-NoExit', '-Command', 'happy codex'], {
    cwd: workdir,
    detached: true,
    windowsHide: false,
    stdio: 'ignore',
    env: process.env
  });
  child.unref();
  return { ok: true, message: `Opened Happy Codex in ${workdir}.` };
}

async function buildState() {
  const env = await readEnvFile(CTI_CONFIG);
  const bridgeStatus = await readJson(CTI_STATUS, {});
  const happyDaemon = await readJson(HAPPY_DAEMON, {});
  const happySettings = await readJson(HAPPY_SETTINGS, {});
  const manager = await readJson(MANAGER_SETTINGS, { projectPresets: [] });
  if (!Array.isArray(manager.projectPresets) && Array.isArray(manager.happyPresets)) {
    manager.projectPresets = manager.happyPresets;
  }
  manager.projectPresets = Array.isArray(manager.projectPresets) ? manager.projectPresets : [];
  const audit = await readJson(CTI_AUDIT, []);
  const workdirInfo = await pathInfo(env.CTI_DEFAULT_WORKDIR);
  const bridgeLog = await tailFile(CTI_LOG, 40);
  const happyLog = happyDaemon?.daemonLogPath ? await tailFile(happyDaemon.daemonLogPath, 40) : '';

  return {
    bridge: {
      running: isBridgeRunning(bridgeStatus),
      status: bridgeStatus,
      runtime: env.CTI_RUNTIME || '',
      channels: (env.CTI_ENABLED_CHANNELS || '').split(',').filter(Boolean),
      defaultWorkdir: env.CTI_DEFAULT_WORKDIR || '',
      defaultMode: env.CTI_DEFAULT_MODE || '',
      workdirInfo,
      logTail: bridgeLog
    },
    happy: {
      daemon: happyDaemon,
      settings: happySettings,
      command: HAPPY_CMD,
      logTail: happyLog
    },
    manager,
    audit: Array.isArray(audit) ? audit.slice(-30).reverse() : [],
    capabilities: capabilityMatrix
  };
}

async function readBody(req) {
  const chunks = [];
  for await (const chunk of req) chunks.push(chunk);
  if (!chunks.length) return {};
  return JSON.parse(Buffer.concat(chunks).toString('utf8'));
}

function sendJson(res, status, payload) {
  res.writeHead(status, { 'Content-Type': 'application/json; charset=utf-8' });
  res.end(JSON.stringify(payload));
}

function sendHtml(res, html) {
  res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
  res.end(html);
}

function appHtml() {
  return `<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Bridge Console</title>
  <style>
    :root {
      --bg: #f3f0e8;
      --panel: #fffdfa;
      --ink: #1f2522;
      --muted: #5c665f;
      --accent: #166534;
      --accent-2: #0f766e;
      --line: #d9d2c2;
      --warn: #b45309;
      --good: #15803d;
      --bad: #b91c1c;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      font-family: "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
      background:
        radial-gradient(circle at top left, rgba(15, 118, 110, 0.12), transparent 30%),
        radial-gradient(circle at top right, rgba(22, 101, 52, 0.12), transparent 30%),
        var(--bg);
      color: var(--ink);
    }
    .wrap {
      max-width: 1280px;
      margin: 0 auto;
      padding: 24px;
    }
    .hero {
      display: flex;
      justify-content: space-between;
      align-items: end;
      gap: 16px;
      margin-bottom: 20px;
    }
    h1 {
      margin: 0;
      font-size: 34px;
      line-height: 1.1;
      letter-spacing: -0.02em;
    }
    .sub {
      color: var(--muted);
      margin-top: 6px;
    }
    .grid {
      display: grid;
      grid-template-columns: repeat(12, 1fr);
      gap: 16px;
    }
    .card {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 20px;
      padding: 18px;
      box-shadow: 0 12px 30px rgba(40, 38, 31, 0.06);
    }
    .span-4 { grid-column: span 4; }
    .span-5 { grid-column: span 5; }
    .span-6 { grid-column: span 6; }
    .span-7 { grid-column: span 7; }
    .span-8 { grid-column: span 8; }
    .span-12 { grid-column: span 12; }
    .kicker {
      color: var(--muted);
      text-transform: uppercase;
      font-size: 12px;
      letter-spacing: 0.08em;
      margin-bottom: 12px;
    }
    .row {
      display: flex;
      justify-content: space-between;
      gap: 12px;
      padding: 6px 0;
      border-bottom: 1px dashed rgba(92, 102, 95, 0.18);
    }
    .row:last-child { border-bottom: 0; }
    .label { color: var(--muted); }
    .value { text-align: right; word-break: break-all; }
    .badge {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 7px 12px;
      border-radius: 999px;
      font-size: 13px;
      font-weight: 600;
    }
    .ok { background: rgba(21, 128, 61, 0.12); color: var(--good); }
    .bad { background: rgba(185, 28, 28, 0.12); color: var(--bad); }
    button, input {
      font: inherit;
    }
    button {
      border: 0;
      background: linear-gradient(135deg, var(--accent), var(--accent-2));
      color: white;
      padding: 11px 14px;
      border-radius: 12px;
      cursor: pointer;
    }
    button.secondary {
      background: #e8ece8;
      color: var(--ink);
    }
    button.warn {
      background: linear-gradient(135deg, #dc2626, #b91c1c);
    }
    .actions {
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      margin-top: 14px;
    }
    .form {
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      margin-top: 10px;
    }
    input {
      flex: 1 1 280px;
      padding: 11px 12px;
      border-radius: 12px;
      border: 1px solid var(--line);
      background: white;
      min-width: 0;
    }
    pre {
      margin: 0;
      white-space: pre-wrap;
      word-break: break-word;
      background: #1f2937;
      color: #edf2f7;
      border-radius: 16px;
      padding: 14px;
      max-height: 320px;
      overflow: auto;
      font-size: 12px;
    }
    table {
      width: 100%;
      border-collapse: collapse;
      font-size: 14px;
    }
    th, td {
      border-bottom: 1px solid var(--line);
      text-align: left;
      padding: 10px 8px;
      vertical-align: top;
    }
    .preset {
      border: 1px solid var(--line);
      border-radius: 16px;
      padding: 14px;
      margin-top: 10px;
      background: #fff;
    }
    .preset-head {
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: start;
    }
    .preset-name {
      font-size: 18px;
      font-weight: 700;
    }
    .mini {
      color: var(--muted);
      margin-top: 4px;
      word-break: break-all;
    }
    .msg {
      margin-top: 12px;
      padding: 12px 14px;
      border-radius: 12px;
      display: none;
    }
    .msg.show { display: block; }
    .msg.ok { background: rgba(21, 128, 61, 0.12); color: var(--good); }
    .msg.bad { background: rgba(185, 28, 28, 0.12); color: var(--bad); }
    @media (max-width: 980px) {
      .span-4, .span-5, .span-6, .span-7, .span-8 { grid-column: span 12; }
      .hero { display: block; }
    }
  </style>
</head>
<body>
  <div class="wrap">
    <div class="hero">
      <div>
        <h1>Bridge Console</h1>
        <div class="sub">统一管理 Feishu / QQ / Happy Codex 的本地桥接、工作目录与会话入口。</div>
      </div>
      <div id="notice" class="msg"></div>
    </div>
    <div class="grid">
      <section class="card span-4">
        <div class="kicker">Bridge Status</div>
        <div id="bridge-status"></div>
        <div class="actions">
          <button onclick="bridgeAction('start')">启动 Bridge</button>
          <button class="warn" onclick="bridgeAction('stop')">停止 Bridge</button>
          <button class="secondary" onclick="loadState()">刷新</button>
        </div>
      </section>

      <section class="card span-4">
        <div class="kicker">Bridge Workdir</div>
        <div id="bridge-workdir"></div>
        <div class="form">
          <input id="workdir-input" placeholder="输入 Feishu / QQ 共用工作目录">
          <button onclick="saveWorkdir()">保存工作目录</button>
        </div>
      </section>

      <section class="card span-4">
        <div class="kicker">Happy Status</div>
        <div id="happy-status"></div>
      </section>

      <section class="card span-7">
        <div class="kicker">Happy Workdir Presets</div>
        <div class="sub">Happy Codex 是按启动目录生效的，所以这里管理的是目录预设和一键启动入口。</div>
        <div class="form">
          <input id="preset-name" placeholder="预设名称">
          <input id="preset-path" placeholder="Happy 目录，例如 F:\\funny_project\\tgporncopilot">
          <button onclick="addPreset()">新增预设</button>
        </div>
        <div id="preset-list"></div>
      </section>

      <section class="card span-5">
        <div class="kicker">Capabilities</div>
        <table>
          <thead>
            <tr>
              <th>通道</th>
              <th>过程</th>
              <th>图片</th>
              <th>工作目录</th>
            </tr>
          </thead>
          <tbody id="capability-table"></tbody>
        </table>
      </section>

      <section class="card span-6">
        <div class="kicker">Permission / Audit</div>
        <div id="audit-list"></div>
      </section>

      <section class="card span-6">
        <div class="kicker">Bridge Logs</div>
        <pre id="bridge-log"></pre>
      </section>

      <section class="card span-12">
        <div class="kicker">Happy Daemon Logs</div>
        <pre id="happy-log"></pre>
      </section>
    </div>
  </div>

  <script>
    function showMsg(text, ok = true) {
      const el = document.getElementById('notice');
      el.textContent = text;
      el.className = 'msg show ' + (ok ? 'ok' : 'bad');
      setTimeout(() => { el.className = 'msg'; }, 4500);
    }

    async function api(url, options = {}) {
      const res = await fetch(url, {
        headers: { 'Content-Type': 'application/json' },
        ...options
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || 'Request failed');
      return data;
    }

    function badge(ok, text) {
      return '<span class="badge ' + (ok ? 'ok' : 'bad') + '">' + text + '</span>';
    }

    function row(label, value) {
      return '<div class="row"><div class="label">' + label + '</div><div class="value">' + value + '</div></div>';
    }

    function renderPresets(presets) {
      const root = document.getElementById('preset-list');
      if (!presets.length) {
        root.innerHTML = '<div class="mini">还没有 Happy 工作目录预设。</div>';
        return;
      }
      root.innerHTML = presets.map((preset) => \`
        <div class="preset">
          <div class="preset-head">
            <div>
              <div class="preset-name">\${preset.name}</div>
              <div class="mini">\${preset.path}</div>
            </div>
            <div class="actions">
              <button onclick="setBridgeWorkdirFromPreset('\${preset.path.replace(/\\/g, '\\\\')}')">设为飞书/QQ工作目录</button>
              <button class="secondary" onclick="launchHappy('\${preset.path.replace(/\\/g, '\\\\')}')">启动 Happy Codex</button>
              <button class="warn" onclick="removePreset('\${preset.id}')">删除</button>
            </div>
          </div>
        </div>\`).join('');
    }

    function renderAudit(items) {
      const root = document.getElementById('audit-list');
      if (!items.length) {
        root.innerHTML = '<div class="mini">暂时还没有审计记录。</div>';
        return;
      }
      root.innerHTML = items.map((item) => \`
        <div class="preset">
          <div class="preset-head">
            <div>
              <div class="preset-name">\${item.channelType} / \${item.direction}</div>
              <div class="mini">\${item.summary || ''}</div>
            </div>
            <div class="mini">\${new Date(item.createdAt).toLocaleString()}</div>
          </div>
        </div>\`).join('');
    }

    async function loadState() {
      const state = await api('/api/state');
      document.getElementById('bridge-status').innerHTML =
        row('运行状态', badge(state.bridge.running, state.bridge.running ? 'Running' : 'Stopped')) +
        row('Runtime', state.bridge.runtime || '-') +
        row('Channels', (state.bridge.channels || []).join(', ') || '-') +
        row('Mode', state.bridge.defaultMode || '-') +
        row('PID', state.bridge.status.pid || '-');

      document.getElementById('bridge-workdir').innerHTML =
        row('当前目录', state.bridge.defaultWorkdir || '-') +
        row('路径有效', badge(state.bridge.workdirInfo.exists && state.bridge.workdirInfo.isDirectory, state.bridge.workdirInfo.exists ? 'Exists' : 'Missing'));
      document.getElementById('workdir-input').value = state.bridge.defaultWorkdir || '';

      const heartbeat = state.happy.daemon.lastHeartbeat || '-';
      document.getElementById('happy-status').innerHTML =
        row('Daemon PID', state.happy.daemon.pid || '-') +
        row('HTTP Port', state.happy.daemon.httpPort || '-') +
        row('Last Heartbeat', heartbeat) +
        row('Command', state.happy.command || '-');

      renderPresets(state.manager.projectPresets || []);
      renderAudit(state.audit || []);
      document.getElementById('bridge-log').textContent = state.bridge.logTail || '';
      document.getElementById('happy-log').textContent = state.happy.logTail || '';
      document.getElementById('capability-table').innerHTML = (state.capabilities || []).map((item) => \`
        <tr>
          <td>\${item.channel}</td>
          <td>\${item.processView}</td>
          <td>\${item.imageInput} / \${item.imageOutput}</td>
          <td>\${item.workdirMode}</td>
        </tr>\`).join('');
    }

    async function bridgeAction(action) {
      try {
        const data = await api('/api/bridge/' + action, { method: 'POST' });
        showMsg(data.message, true);
        setTimeout(loadState, 1200);
      } catch (err) {
        showMsg(err.message, false);
      }
    }

    async function saveWorkdir() {
      try {
        const target = document.getElementById('workdir-input').value.trim();
        const data = await api('/api/bridge/workdir', {
          method: 'POST',
          body: JSON.stringify({ path: target })
        });
        showMsg(data.message, true);
        loadState();
      } catch (err) {
        showMsg(err.message, false);
      }
    }

    async function setBridgeWorkdirFromPreset(target) {
      document.getElementById('workdir-input').value = target;
      await saveWorkdir();
    }

    async function addPreset() {
      try {
        const name = document.getElementById('preset-name').value.trim();
        const presetPath = document.getElementById('preset-path').value.trim();
        const data = await api('/api/presets', {
          method: 'POST',
          body: JSON.stringify({ name, path: presetPath })
        });
        document.getElementById('preset-name').value = '';
        document.getElementById('preset-path').value = '';
        showMsg(data.message, true);
        loadState();
      } catch (err) {
        showMsg(err.message, false);
      }
    }

    async function removePreset(id) {
      try {
        const data = await api('/api/presets/' + id, { method: 'DELETE' });
        showMsg(data.message, true);
        loadState();
      } catch (err) {
        showMsg(err.message, false);
      }
    }

    async function launchHappy(target) {
      try {
        const data = await api('/api/happy/launch', {
          method: 'POST',
          body: JSON.stringify({ path: target })
        });
        showMsg(data.message, true);
      } catch (err) {
        showMsg(err.message, false);
      }
    }

    loadState();
  </script>
</body>
</html>`;
}

async function handleApi(req, res) {
  const url = new URL(req.url, `http://${req.headers.host}`);
  if (req.method === 'GET' && url.pathname === '/api/state') {
    return sendJson(res, 200, await buildState());
  }

  if (req.method === 'POST' && url.pathname === '/api/bridge/start') {
    return sendJson(res, 200, await startBridge());
  }

  if (req.method === 'POST' && url.pathname === '/api/bridge/stop') {
    return sendJson(res, 200, await stopBridge());
  }

  if (req.method === 'POST' && url.pathname === '/api/bridge/workdir') {
    const body = await readBody(req);
    const targetPath = String(body.path || '').trim();
    const info = await pathInfo(targetPath);
    if (!info.exists || !info.isDirectory) {
      return sendJson(res, 400, { error: '工作目录不存在，或者不是文件夹。' });
    }
    await updateEnvKey(CTI_CONFIG, 'CTI_DEFAULT_WORKDIR', targetPath);
    return sendJson(res, 200, { ok: true, message: `已更新 Feishu / QQ 默认工作目录为 ${targetPath}` });
  }

  if (req.method === 'POST' && url.pathname === '/api/presets') {
    const body = await readBody(req);
    const name = String(body.name || '').trim();
    const presetPath = String(body.path || '').trim();
    if (!name || !presetPath) {
      return sendJson(res, 400, { error: '预设名称和路径都要填写。' });
    }
    const info = await pathInfo(presetPath);
    if (!info.exists || !info.isDirectory) {
      return sendJson(res, 400, { error: '预设路径不存在，或者不是文件夹。' });
    }
    const settings = await readJson(MANAGER_SETTINGS, { projectPresets: [] });
    if (!Array.isArray(settings.projectPresets) && Array.isArray(settings.happyPresets)) {
      settings.projectPresets = settings.happyPresets;
    }
    settings.projectPresets = settings.projectPresets || [];
    settings.projectPresets.push({ id: randomUUID(), name, path: presetPath });
    await fs.writeFile(MANAGER_SETTINGS, JSON.stringify(settings, null, 2), 'utf8');
    return sendJson(res, 200, { ok: true, message: `已新增 Happy 预设 ${name}` });
  }

  if (req.method === 'DELETE' && url.pathname.startsWith('/api/presets/')) {
    const id = decodeURIComponent(url.pathname.split('/').pop() || '');
    const settings = await readJson(MANAGER_SETTINGS, { projectPresets: [] });
    if (!Array.isArray(settings.projectPresets) && Array.isArray(settings.happyPresets)) {
      settings.projectPresets = settings.happyPresets;
    }
    settings.projectPresets = (settings.projectPresets || []).filter((item) => item.id !== id);
    await fs.writeFile(MANAGER_SETTINGS, JSON.stringify(settings, null, 2), 'utf8');
    return sendJson(res, 200, { ok: true, message: '已删除 Happy 预设。' });
  }

  if (req.method === 'POST' && url.pathname === '/api/happy/launch') {
    const body = await readBody(req);
    const targetPath = String(body.path || '').trim();
    const info = await pathInfo(targetPath);
    if (!info.exists || !info.isDirectory) {
      return sendJson(res, 400, { error: 'Happy 启动目录不存在。' });
    }
    return sendJson(res, 200, await launchHappyCodex(targetPath));
  }

  return sendJson(res, 404, { error: 'Not found' });
}

const server = http.createServer(async (req, res) => {
  try {
    if (req.url === '/' || req.url.startsWith('/?')) {
      return sendHtml(res, appHtml());
    }
    if (req.url.startsWith('/api/')) {
      return await handleApi(req, res);
    }
    res.writeHead(404);
    res.end('Not found');
  } catch (error) {
    sendJson(res, 500, { error: error instanceof Error ? error.message : String(error) });
  }
});

await ensureManagerSettings();
server.listen(PORT, () => {
  console.log(`Bridge Console running at http://127.0.0.1:${PORT}`);
});
