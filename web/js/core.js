// ---------------------------------------------------------------------------
// 核心：API 客户端 / 全局状态 / 事件总线 / 标签页注册表 / UI 小工具
// 加新功能 = 新建 web/js/features/xxx.js，调 VN.ui.registerTab(...)
// ---------------------------------------------------------------------------

export const api = {
  /** 关键：任何请求都有超时。服务没在跑时绝不挂死，一律快速报错。 */
  async req(path, opts = {}) {
    const timeout = opts.timeout || 30000;
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), timeout);
    let res;
    try {
      res = await fetch(path, {
        headers: opts.body && !(opts.body instanceof FormData) ? { 'Content-Type': 'application/json' } : undefined,
        ...opts,
        signal: opts.signal || ctrl.signal,
      });
    } catch (err) {
      const aborted = err && (err.name === 'AbortError' || err.name === 'TimeoutError');
      throw new Error(aborted
        ? `请求超时（${Math.round(timeout / 1000)} 秒）——服务可能卡住或没在运行`
        : '连不上 VideoNote 服务 —— 请双击桌面上的 VideoNote 图标启动它');
    } finally {
      clearTimeout(timer);
    }
    if (!res.ok) {
      // 401 = 没登录（服务端设了 VN_PASSWORD）。直接去登录页，别让界面半死不活。
      if (res.status === 401 && !location.pathname.endsWith('/login.html')) {
        location.replace('/login.html');
        throw new Error('需要登录');
      }
      let detail = res.statusText;
      try { detail = (await res.json()).detail || detail; } catch (e) { /* ignore */ }
      throw new Error(detail);
    }
    const ct = res.headers.get('content-type') || '';
    return ct.includes('application/json') ? res.json() : res.text();
  },
  get(p) { return this.req(p); },
  post(p, body) { return this.req(p, { method: 'POST', body: body instanceof FormData ? body : JSON.stringify(body || {}) }); },
  del(p) { return this.req(p, { method: 'DELETE' }); },

  capabilities() { return this.get('/api/capabilities'); },
  projects() { return this.get('/api/projects'); },
  project(pid) { return this.get(`/api/projects/${pid}`); },
  createProject(body) { return this.post('/api/projects', body); },
  upload(formData, onProgress, signal) { return uploadWithProgress('/api/projects/upload', formData, onProgress, signal); },
  deleteProject(pid) { return this.del(`/api/projects/${pid}?delete_files=true`); },
  runJob(pid, kind, params) { return this.post(`/api/projects/${pid}/jobs`, { kind, params: params || {} }); },
  job(jid) { return this.get(`/api/jobs/${jid}`); },
  cancelJob(jid) { return this.post(`/api/jobs/${jid}/cancel`, {}); },
  segments(pid, q = '') { return this.get(`/api/projects/${pid}/segments?limit=5000${q ? `&q=${encodeURIComponent(q)}` : ''}`); },
  notes(pid) { return this.get(`/api/projects/${pid}/notes`); },
  frames(pid) { return this.get(`/api/projects/${pid}/frames`); },
  transcript(pid) { return this.get(`/api/projects/${pid}/transcript`); },
  rawUrl(pid) { return `/api/projects/${pid}/raw`; },
  artifactUrl(pid, key, download = false) { return `/api/projects/${pid}/artifact/${key}${download ? '?download=true' : ''}`; },
  // 下载产出文件。
  // 为什么不用 window.open()：本应用跑在 webview 里，window.open 会试图把
  // 「附件下载地址」当成网页去导航；webview 渲染不了 attachment，就显示白屏
  // 「页面加载失败」。改成 fetch + Blob + <a download>，全程不导航，
  // 文件名从 Content-Disposition 里取，失败时给明确提示而不是白屏。
  async downloadArtifact(pid, key, filename) {
    try {
      const res = await fetch(this.artifactUrl(pid, key, true));
      if (!res.ok) {
        throw new Error(res.status === 404 ? '这个文件还没生成（先跑一次分析）' : `HTTP ${res.status}`);
      }
      const cd = res.headers.get('content-disposition') || '';
      const m = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(cd);
      const name = filename || (m ? decodeURIComponent(m[1]) : `${key}.txt`);
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = name;
      document.body.appendChild(a);
      a.click();
      a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 3000);
      ui.toast(`已开始下载 ${name}`, 'ok', 3000);
      return true;
    } catch (e) {
      ui.toast(`下载失败：${e.message}`, 'err', 8000);
      return false;
    }
  },
  frameUrl(pid, idx) { return `/api/projects/${pid}/frames/${idx}/image`; },
};

function uploadWithProgress(url, formData, onProgress, signal) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', url);
    // 关键：上传绝不允许无限等待（大文件 + 服务中途挂掉 = 之前弹窗卡死的元凶）
    xhr.timeout = 30 * 60 * 1000;
    let lastLoaded = 0;
    let lastAt = Date.now();
    let stalled = false;
    const stallTimer = setInterval(() => {
      // 5 分钟没有任何新字节就判定卡死
      if (Date.now() - lastAt > 5 * 60 * 1000) {
        stalled = true;
        clearInterval(stallTimer);
        try { xhr.abort(); } catch (e) { /* ignore */ }
        reject(new Error('上传卡住了（5 分钟没有进展），已自动取消。请改用「本机路径」方式导入'));
      }
    }, 15000);
    xhr.upload.onprogress = (e) => {
      if (e.loaded > lastLoaded) { lastLoaded = e.loaded; lastAt = Date.now(); }
      if (e.lengthComputable && onProgress) onProgress((e.loaded / e.total) * 100, e.loaded, e.total);
    };
    xhr.onload = () => {
      clearInterval(stallTimer);
      if (xhr.status >= 200 && xhr.status < 300) {
        try { resolve(JSON.parse(xhr.responseText)); } catch (e) { resolve({}); }
      } else {
        let msg = xhr.statusText;
        try { msg = JSON.parse(xhr.responseText).detail || msg; } catch (e) { /* ignore */ }
        reject(new Error(msg));
      }
    };
    xhr.onerror = () => { clearInterval(stallTimer); reject(new Error('上传失败：连不上服务，请确认 VideoNote 正在运行')); };
    xhr.ontimeout = () => { clearInterval(stallTimer); reject(new Error('上传超时（30 分钟）')); };
    xhr.onabort = () => { clearInterval(stallTimer); reject(new Error(stalled ? '上传卡住已取消' : '已取消上传')); };
    if (signal) {
      if (signal.aborted) { clearInterval(stallTimer); return reject(new Error('已取消上传')); }
      signal.addEventListener('abort', () => { try { xhr.abort(); } catch (e) { /* ignore */ } });
    }
    xhr.send(formData);
  });
}

// ---------------------------------------------------------------------------
export const bus = new EventTarget();
export function emit(type, detail) { bus.dispatchEvent(new CustomEvent(type, { detail })); }
export function on(type, fn) { bus.addEventListener(type, fn); }

export const store = {
  capabilities: null,
  projects: [],
  project: null,
  detail: null,
  segments: [],
  translation: {},
  currentTime: 0,
  currentIdx: -1,
  opts: {
    autoPause: localStorage.getItem('vn.autopause') !== '0',
    bilingual: localStorage.getItem('vn.bilingual') !== '0',
    loop: false,
    repeatGap: Number(localStorage.getItem('vn.gap') || 0),
  },
};

export function segAt(time) {
  const segs = store.segments;
  if (!segs.length) return -1;
  let lo = 0, hi = segs.length - 1, ans = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (segs[mid].start <= time) { ans = mid; lo = mid + 1; } else { hi = mid - 1; }
  }
  if (ans >= 0 && time <= segs[ans].end + 0.35) return ans;
  return ans >= 0 && time < (segs[ans + 1]?.start ?? Infinity) ? ans : -1;
}

export function fmtTime(s) {
  s = Math.max(0, Number(s) || 0);
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = Math.floor(s % 60);
  return h ? `${h}:${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}`
           : `${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}`;
}
export function fmtDur(s) {
  s = Number(s) || 0;
  if (s < 60) return `${s.toFixed(0)} 秒`;
  if (s < 3600) return `${(s / 60).toFixed(1)} 分钟`;
  return `${(s / 3600).toFixed(1)} 小时`;
}
export function esc(html) {
  return String(html ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

// ---------------------------------------------------------------------------
// UI：标签页注册表 + toast
// ---------------------------------------------------------------------------
const tabs = [];
const panels = new Map();

export const ui = {
  registerTab(def) {
    tabs.push({ order: 50, ...def });
    tabs.sort((a, b) => a.order - b.order);
    this.renderTabs();
  },
  renderTabs() {
    const bar = document.getElementById('tabs');
    if (!bar) return;
    const active = bar.querySelector('.tab.active')?.dataset.id;
    bar.innerHTML = '';
    tabs.forEach((t) => {
      const b = document.createElement('button');
      b.className = 'tab';
      b.dataset.id = t.id;
      b.textContent = t.label;
      b.onclick = () => this.showTab(t.id);
      bar.appendChild(b);
    });
    const ids = tabs.map((t) => t.id);
    const want = ids.includes(active) ? active : defaultTabFor();
    if (want) this.showTab(want);
  },
  showTab(id) {
    const def = tabs.find((t) => t.id === id);
    if (!def) return;
    document.querySelectorAll('#tabs .tab').forEach((b) => b.classList.toggle('active', b.dataset.id === id));
    const host = document.getElementById('content');
    host.innerHTML = '';
    const panel = document.createElement('div');
    panel.className = 'panel';
    host.appendChild(panel);
    panels.set(id, panel);
    try {
      def.render(panel, { project: store.project, detail: store.detail, segments: store.segments });
    } catch (err) {
      panel.innerHTML = `<div class="card">模块渲染失败：${esc(err.message)}</div>`;
      console.error(err);
    }
    localStorage.setItem('vn.tab', id);
    emit('tabShown', { id });
  },
  refreshCurrent() {
    const id = document.querySelector('#tabs .tab.active')?.dataset.id;
    if (id) this.showTab(id);
  },
  toast(msg, kind = '', ms = 4000) {
    const box = document.getElementById('toasts');
    const el = document.createElement('div');
    el.className = `toast ${kind}`;
    el.innerHTML = msg;
    box.appendChild(el);
    setTimeout(() => { el.style.opacity = '0'; setTimeout(() => el.remove(), 300); }, ms);
  },
  // 进度条「只增不减」：真实事故是多个任务/多个阶段的事件交错时数值来回横跳。
  // jobId 变化 = 换了个任务，允许归零；同一个任务内只接受更大的百分比。
  setProgress(pct, stage, msg, jobId) {
    const wrap = document.getElementById('progress-wrap');
    if (pct === null) {
      wrap.hidden = true;
      this._progJob = null;
      this._progPct = 0;
      return;
    }
    wrap.hidden = false;
    if (jobId && jobId !== this._progJob) { this._progJob = jobId; this._progPct = 0; }
    const p = Math.max(0, Math.min(100, Number(pct) || 0));
    this._progPct = Math.max(this._progPct || 0, p);
    document.getElementById('progress-bar').style.width = `${this._progPct}%`;
    document.getElementById('progress-stage').textContent = stage || '';
    document.getElementById('progress-msg').textContent = msg || '';
  },
};

function defaultTabFor() {
  if (!store.project) return tabs[0]?.id;
  return localStorage.getItem('vn.tab') || tabs[0]?.id;
}

// 迷你 Markdown 渲染（无外部依赖，离线可用）
export function mdToHtml(md) {
  if (!md) return '';
  const lines = md.replace(/\r/g, '').split('\n');
  let html = '', inCode = false, listType = null, tableBuf = [];
  const inline = (t) => esc(t)
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/(^|\W)\*([^*]+)\*/g, '$1<em>$2</em>')
    .replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank">$1</a>');

  const flushList = () => { if (listType) { html += `</${listType}>`; listType = null; } };
  const flushTable = () => {
    if (!tableBuf.length) return;
    const rows = tableBuf.filter((r) => !/^\s*\|?[\s:-]+\|[\s:|-]*$/.test(r));
    html += '<table>';
    rows.forEach((r, i) => {
      const cells = r.replace(/^\||\|$/g, '').split('|').map((c) => inline(c.trim()));
      html += '<tr>' + cells.map((c) => (i === 0 ? `<th>${c}</th>` : `<td>${c}</td>`)).join('') + '</tr>';
    });
    html += '</table>';
    tableBuf = [];
  };

  for (const raw of lines) {
    const line = raw;
    if (/^```/.test(line)) { flushList(); flushTable(); html += inCode ? '</code></pre>' : '<pre><code>'; inCode = !inCode; continue; }
    if (inCode) { html += esc(line) + '\n'; continue; }
    if (/^\s*\|.*\|\s*$/.test(line)) { flushList(); tableBuf.push(line); continue; }
    flushTable();
    let m;
    if ((m = line.match(/^(#{1,6})\s+(.*)$/))) { flushList(); html += `<h${m[1].length}>${inline(m[2])}</h${m[1].length}>`; continue; }
    if (/^\s*>\s?/.test(line)) { flushList(); html += `<blockquote>${inline(line.replace(/^\s*>\s?/, ''))}</blockquote>`; continue; }
    if (/^\s*[-*+]\s+/.test(line)) { if (listType !== 'ul') { flushList(); html += '<ul>'; listType = 'ul'; } html += `<li>${inline(line.replace(/^\s*[-*+]\s+/, ''))}</li>`; continue; }
    if (/^\s*\d+\.\s+/.test(line)) { if (listType !== 'ol') { flushList(); html += '<ol>'; listType = 'ol'; } html += `<li>${inline(line.replace(/^\s*\d+\.\s+/, ''))}</li>`; continue; }
    if (!line.trim()) { flushList(); continue; }
    flushList();
    html += `<p>${inline(line)}</p>`;
  }
  flushList(); flushTable();
  return html;
}

export const VN = { api, ui, store, bus, emit, on, mdToHtml, fmtTime, fmtDur, esc, segAt };
window.VN = VN;
