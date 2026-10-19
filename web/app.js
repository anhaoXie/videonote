// ---------------------------------------------------------------------------
// 应用启动：加载能力清单 -> 渲染功能标签页 -> 项目列表 -> SSE 实时进度
// ---------------------------------------------------------------------------
import { VN, api, ui, store, emit, on, fmtTime, fmtDur, esc } from './js/core.js';

// 功能模块：每个文件自己往 ui 里注册标签页。加功能就在这里加一行 import。
import './js/features/library.js';
import './js/features/player.js';
import './js/features/transcript.js';
import './js/features/notes.js';
import './js/features/frames.js';
import './js/features/clips.js';
import './js/features/review.js';
import './js/features/words.js';
import './js/features/stats.js';
import './js/features/settings.js';

// ---------------------------------------------------------------------------
// 兜底的任务类型：万一能力清单没加载上（比如页面是在服务没运行时打开的），
// 至少保证「导入视频」还能用，不会出现下拉框空白、点开始没反应。
// ---------------------------------------------------------------------------
const FALLBACK_KINDS = [
  { name: 'analyze', label: '完整分析（推荐）' },
  { name: 'transcribe', label: '转写 + 字幕' },
  { name: 'subtitle_full', label: '字幕（含翻译，双语）' },
  { name: 'summarize', label: '重做摘要笔记' },
  { name: 'frames', label: '画面关键帧' },
  { name: 'diarize', label: '说话人分离' },
  { name: 'split_speakers', label: '按说话人拆句' },
  { name: 'captions', label: '画面描述（视觉大模型）' },
  { name: 'ingest', label: '仅导入媒体' },
];

function setBanner(html) {
  const el = document.getElementById('conn-banner');
  if (!el) return;
  if (!html) { el.hidden = true; el.innerHTML = ''; return; }
  el.hidden = false;
  el.innerHTML = html;
}

/**
 * 看门狗：每 5 秒确认服务还活着。
 * 服务断了 → 顶部红条提醒；服务回来了 → 自动刷新页面，把死掉的界面救活。
 * （之前「点不动」的核心原因就是服务没了但页面不知道，现在页面会自己发现并自愈）
 */
function startWatchdog() {
  let wasDown = false;
  let fails = 0;
  const tick = async () => {
    try {
      await api.req('/api/health', { timeout: 4000 });
      if (wasDown) {
        wasDown = false;
        setBanner('<span>服务已恢复，正在重新加载界面…</span>');
        setTimeout(() => location.reload(), 800);
      }
      fails = 0;
    } catch (err) {
      fails += 1;
      if (fails >= 2 && !wasDown) {
        wasDown = true;
        setBanner(`<span>⚠ <b>后台服务已断开</b>，界面暂时点不动是正常的。
          请双击桌面上的 <b>VideoNote</b> 图标重新启动它；服务恢复后本页会自动刷新。</span>
          <button class="btn small" onclick="location.reload()">重新加载页面</button>`);
        ui.toast('后台服务已断开，请双击桌面 VideoNote 图标重启', 'err', 12000);
      }
    }
    setTimeout(tick, 5000);
  };
  setTimeout(tick, 3000);
}

/**
 * 内置文件浏览器：直接点着选本机视频，不用手敲路径。
 * （「在文件夹里不好找」这个痛点的解法）
 */
function wireFileBrowser() {
  const box = document.getElementById('fs-browser');
  const btn = document.getElementById('fs-open');
  const input = document.getElementById('path-input');
  if (!box || !btn) return;
  let cwd = '';

  const fmtSize = (n) => {
    if (n > 1024 * 1024 * 1024) return `${(n / 1073741824).toFixed(2)} GB`;
    if (n > 1024 * 1024) return `${(n / 1048576).toFixed(0)} MB`;
    return `${Math.max(1, Math.round(n / 1024))} KB`;
  };

  async function open(path) {
    box.hidden = false;
    box.innerHTML = '<div class="fs-empty">读取中…</div>';
    try {
      const d = await api.get(`/api/fs/list?path=${encodeURIComponent(path || '')}`);
      cwd = d.path || '';
      render(d);
    } catch (err) {
      box.innerHTML = `<div class="fs-empty" style="color:var(--warn)">${esc(err.message)}</div>`;
    }
  }

  function render(d) {
    const quick = (d.quick || []).map((q) =>
      `<button class="btn small" data-go="${esc(q.path)}">${esc(q.name)}</button>`).join('');
    const dirs = (d.dirs || []).map((x) =>
      `<div class="fs-item" data-dir="${esc(x.path)}"><span class="ic">📁</span>
        <span class="nm">${esc(x.name)}</span></div>`).join('');
    const files = (d.files || []).map((x) =>
      `<div class="fs-item" data-file="${esc(x.path)}">
        <span class="ic">${x.kind === 'video' ? '🎬' : '🎵'}</span>
        <span class="nm">${esc(x.name)}</span><span class="sz">${fmtSize(x.size)}</span></div>`).join('');
    const empty = (!d.dirs?.length && !d.files?.length)
      ? '<div class="fs-empty">这里没有子文件夹，也没有视频/音频文件</div>' : '';
    box.innerHTML = `
      <div class="fs-quick">${quick}</div>
      <div class="fs-crumb">
        ${d.path ? `<button class="btn small ghost" data-go="${esc(d.parent || '')}">↑ 上级</button>
          <span title="${esc(d.path)}">${esc(d.path)}</span>` : '<span>选择磁盘，或点上面的快捷位置</span>'}
      </div>
      <div class="fs-list">${dirs}${files}${empty}</div>`;
    box.querySelectorAll('[data-go]').forEach((el) => { el.onclick = () => open(el.dataset.go); });
    box.querySelectorAll('[data-dir]').forEach((el) => { el.onclick = () => open(el.dataset.dir); });
    box.querySelectorAll('[data-file]').forEach((el) => {
      el.onclick = () => {
        input.value = el.dataset.file;
        box.hidden = true;
        ui.toast(`已选中：${esc(el.querySelector('.nm').textContent)} —— 填好标题后点「开始」`, 'ok', 5000);
      };
    });
  }

  btn.onclick = () => {
    if (box.hidden) open(cwd || input.value.trim());
    else box.hidden = true;
  };
}

async function boot() {
  // 接线失败绝不能中断后面的事。真实事故：空状态里的 #btn-new2 会被 renderTabs()
  // 重写 #content 时删掉 → wireChrome() 抛异常 → 能力清单从未加载
  // → 两个下拉框永远是空的、项目列表也不出来。
  try {
    wireChrome();
  } catch (err) {
    console.error('[boot] 界面接线失败（其余功能继续）：', err);
  }
  connectSSE();
  startWatchdog();
  await loadCapabilities();
  try {
    await refreshProjects();
  } catch (err) {
    setBanner(`<span>⚠ 读取项目列表失败（${esc(err.message)}）。服务恢复后本页会自动重试。</span>
      <button class="btn small" onclick="location.reload()">立即重试</button>`);
    ui.renderTabs();
    return;
  }
  const saved = localStorage.getItem('vn.lastProject');
  const first = store.projects.find((p) => p.id === saved) || store.projects[0];
  if (first) await selectProject(first.id);
  else ui.renderTabs();
}

/** 加载能力清单；失败就每 3 秒重试，并挂横幅提示 —— 服务恢复后页面会自己好。 */
async function loadCapabilities(attempt = 0) {
  try {
    store.capabilities = await api.capabilities();
    renderCapabilities();
    setBanner('');
    return true;
  } catch (err) {
    setBanner(`<span>⚠ 连不上后端服务（${esc(err.message)}）。如果服务没在跑，双击项目里的
      <code>run.bat</code> 启动它。本页每 3 秒自动重试。</span>
      <button class="btn small" onclick="location.reload()">立即重试</button>`);
    ui.toast(`加载能力清单失败：${esc(err.message)}`, 'err', 6000);
    if (attempt < 40) setTimeout(() => loadCapabilities(attempt + 1), 3000);
    return false;
  }
}

function renderCapabilities() {
  const cap = store.capabilities;
  if (!cap) return;
  document.getElementById('version').textContent = `v${cap.version}`;
  const cfg = cap.config;
  const badge = document.getElementById('engine-badge');
  const asr = cfg.asr.effective_provider === 'cloud'
    ? `ASR 云端 ${esc(cfg.asr.cloud_model)}`
    : `ASR 本地 whisper-${esc(cfg.asr.local_model)}`;
  badge.innerHTML = `${asr} · ${cfg.llm.ready ? `摘要 ${esc(cfg.llm.model)}` : '摘要 未配置key'}`;
  badge.title = cfg.llm.ready ? '' : '未配置大模型 key：笔记将使用本地抽取式摘要';

  const kinds = (cap.job_kinds && cap.job_kinds.length) ? cap.job_kinds : FALLBACK_KINDS;
  const opts = kinds.map((k) => `<option value="${k.name}">${esc(k.label)}</option>`).join('');
  const top = document.getElementById('job-kind');
  const imp = document.getElementById('import-job');
  const keepTop = top.value;
  const keepImp = imp.value;
  top.innerHTML = opts;
  imp.innerHTML = opts;
  if (keepTop) top.value = keepTop;      // 别把用户已选的项重置掉
  if (keepImp) imp.value = keepImp;
  if (!top.value) top.value = 'analyze'; // 空下拉框是绝对不允许出现的
  if (!imp.value) imp.value = 'analyze';

  const hint = document.getElementById('startup-hint');
  if (hint && cap.plugins.import_errors && Object.keys(cap.plugins.import_errors).length) {
    hint.innerHTML = `<span style="color:var(--warn)">有插件加载失败：</span><br>` +
      Object.entries(cap.plugins.import_errors).map(([k, v]) => `<code>${esc(k)}</code>: ${esc(v)}`).join('<br>');
  }
  syncImportJob();
}

// 「导入后跑什么」：折叠条上显示当前选中的是什么，展开后显示这条流水线是干什么的。
// 选项本身由 renderCapabilities() 从 /api/capabilities 填好。
function syncImportJob() {
  const sel = document.getElementById('import-job');
  if (!sel) return;
  const opt = sel.options[sel.selectedIndex];
  const cur = document.getElementById('adv-current');
  const hint = document.getElementById('adv-hint');
  const kinds = (store.capabilities?.job_kinds?.length) ? store.capabilities.job_kinds : FALLBACK_KINDS;
  const meta = kinds.find((k) => k.name === sel.value);
  if (cur) cur.textContent = opt ? opt.textContent : '完整分析（推荐）';
  if (hint) hint.textContent = (meta && meta.description)
    ? meta.description
    : '决定导入后自动跑哪条流水线；不确定就用默认的「完整分析」。';
}

async function refreshProjects() {
  const data = await api.projects();
  store.projects = data.projects;
  document.getElementById('proj-count').textContent = store.projects.length;
  const host = document.getElementById('project-list');
  host.innerHTML = '';
  store.projects.forEach((p) => {
    const el = document.createElement('div');
    el.className = `proj${store.project?.id === p.id ? ' active' : ''}`;
    const st = p.last_job ? p.last_job.status : p.status;
    el.innerHTML = `
      <div class="proj-title">${esc(p.title)}</div>
      <div class="proj-sub"><span>${fmtDur(p.duration)}</span><span>·</span><span>${statusText(st)}</span></div>`;
    el.onclick = () => selectProject(p.id);
    // 双击改名（比右键菜单更好发现）
    el.ondblclick = async (e) => {
      e.stopPropagation();
      const name = prompt('改个名字：', p.title);
      if (!name || name === p.title) return;
      try {
        await api.req(`/api/projects/${p.id}`, { method: 'PATCH', body: JSON.stringify({ title: name }) });
        ui.toast('已改名', 'ok');
        await refreshProjects();
      } catch (err) {
        ui.toast(`改名失败：${err.message}`, 'err');
      }
    };
    el.title = `${p.title}\n双击改名 · 右键删除`;
    el.oncontextmenu = async (e) => {
      e.preventDefault();
      if (!confirm(`删除项目「${p.title}」及其全部文件？`)) return;
      await api.deleteProject(p.id);
      if (store.project?.id === p.id) { store.project = null; store.detail = null; store.segments = []; }
      ui.toast('已删除', 'ok');
      await refreshProjects();
      ui.showTab(localStorage.getItem('vn.tab') || 'library');
    };
    host.appendChild(el);
  });
  renderJobs(store.detail?.jobs || []);
}

function statusText(s) {
  return { queued: '排队中', running: '处理中', done: '已完成', failed: '失败', canceled: '已取消', ready: '就绪', created: '新建', ingested: '已导入', failed_: '' }[s] || s || '新建';
}

function renderJobs(jobs) {
  const host = document.getElementById('job-list');
  host.innerHTML = '';
  (jobs || []).slice(0, 12).forEach((j) => {
    const el = document.createElement('div');
    el.className = `job ${j.status}`;
    el.innerHTML = `
      <div class="job-row"><b>${esc(j.kind)}</b><span>${Math.round(j.progress || 0)}%</span></div>
      <div class="job-msg">${esc(j.error || j.message || statusText(j.status))}</div>`;
    el.onclick = () => showJobLog(j.id);
    host.appendChild(el);
  });
  if (!jobs || !jobs.length) host.innerHTML = '<div class="muted" style="font-size:12px">暂无任务</div>';
}

async function showJobLog(jid) {
  const { job, logs } = await api.job(jid);
  const body = logs.map((l) => `[${l.ts.slice(11)}] ${l.message}`).join('\n');
  const w = window.open('', '_blank', 'width=760,height=560');
  if (!w) return;
  w.document.write(`<title>任务日志 ${esc(jid)}</title>
    <style>body{background:#0b0d12;color:#e6e9ef;font:12px Consolas,monospace;padding:14px}
    pre{white-space:pre-wrap;line-height:1.6}h3{font-family:system-ui}</style>
    <h3>${esc(job.kind)} · ${esc(job.status)} · ${Math.round(job.progress)}%</h3>
    ${job.error ? `<p style="color:#ef4444">${esc(job.error)}</p>` : ''}
    <pre>${esc(body || '（暂无日志）')}</pre>`);
}

export async function selectProject(pid) {
  if (!pid) return;
  localStorage.setItem('vn.lastProject', pid);
  const detail = await api.project(pid);
  store.detail = detail;
  store.project = detail.project;
  store.translation = {};
  document.getElementById('project-title').textContent = store.project.title;
  const arts = detail.artifacts.map((a) => a.key);
  document.getElementById('project-meta').textContent =
    `${fmtDur(store.project.duration)} · ${detail.segment_count} 句 · 产出: ${arts.length ? arts.join(', ') : '无'}`;

  // 句子 + 译文
  try {
    const seg = await api.segments(pid);
    store.segments = seg.segments;
  } catch (e) { store.segments = []; }
  const trArt = detail.artifacts.find((a) => a.key === 'translation');
  if (trArt?.path) {
    try { store.translation = await (await fetch(api.artifactUrl(pid, 'translation'))).json(); } catch (e) { /* ignore */ }
  }

  emit('projectChanged', { project: store.project, detail, segments: store.segments });
  await refreshProjects();
  ui.renderTabs();
}

// ---------------------------------------------------------------------------
// 接线助手：某个元素缺失时只跳过它自己，绝不让后面所有接线一起中断。
// 真实事故：空状态里的 #btn-new2 会被 renderTabs() 重写 #content 时删掉，
// 于是「开始」按钮 / 渠道按钮 / 关闭按钮全都没接上，能力清单也不再加载。
function wire(id, handler, event = 'onclick') {
  const el = document.getElementById(id);
  if (!el) { console.warn(`[wire] 找不到 #${id}，已跳过`); return null; }
  el[event] = handler;
  return el;
}

function wireChrome() {
  wireFileBrowser();
  wire('btn-refresh', async () => {
    await refreshProjects();
    if (store.project) await selectProject(store.project.id);
    ui.toast('已刷新', 'ok', 1500);
  });
  wire('btn-run', async () => {
    if (!store.project) return ui.toast('先选择一个项目', 'err');
    // 能力清单还没加载上时下拉框是空的，兜底走完整分析，别发空任务类型
    const kind = document.getElementById('job-kind').value || 'analyze';
    const job = await api.runJob(store.project.id, kind);
    ui.toast(`已排队：${kind}`, 'ok');
    setTimeout(() => selectProject(store.project.id), 800);
    void job;
  });
  const modal = document.getElementById('modal');
  let uploadAbort = null;
  const openModal = () => { if (modal) modal.hidden = false; };
  // 关弹窗时顺手取消还在进行的上传，避免"弹窗关不掉的假死"感觉
  const closeModal = () => {
    if (modal) modal.hidden = true;
    if (uploadAbort) { try { uploadAbort.abort(); } catch (e) { /* ignore */ } uploadAbort = null; }
  };
  // 「导入视频」用事件委托而不是直接绑元素：空状态那个按钮会被 renderTabs() 重写掉，
  // 而重写之后重新绑是没人负责的 —— 委托就永远有效。
  document.addEventListener('click', (e) => {
    const t = e.target.closest && e.target.closest('[data-open-import]');
    if (t) { e.preventDefault(); openModal(); }
  });
  wire('btn-new', openModal);
  wire('btn-new2', openModal);
  wire('modal-close', closeModal);
  wire('modal-cancel', closeModal);
  if (!modal) console.warn('[wire] 找不到导入弹窗 #modal');
  // 点遮罩空白处 / 按 ESC 也能关 —— 弹窗挡住整页时用户必须能脱身
  if (modal) modal.addEventListener('click', (e) => { if (e.target === modal) closeModal(); });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && modal && !modal.hidden) closeModal();
  });

  // ---- 第 1 步：渠道选择，一次只让一个渠道的输入面板出现 ----
  let mode = 'upload';
  const setMode = (m) => {
    mode = m;
    document.querySelectorAll('#import-mode .seg-btn').forEach((x) =>
      x.classList.toggle('active', x.dataset.mode === m));
    document.querySelectorAll('[data-pane]').forEach((p) => { p.hidden = p.dataset.pane !== m; });
    if (m !== 'path') {
      const box = document.getElementById('fs-browser');
      if (box) box.hidden = true;      // 换渠道时把文件浏览器收起来
    }
  };
  document.querySelectorAll('#import-mode .seg-btn').forEach((b) => {
    b.onclick = () => setMode(b.dataset.mode);
  });
  setMode('upload');

  // ---- 第 2 步：选完文件必须看得见。原生 file input 的反馈太弱，
  // 只显示个文件名，很容易让人以为"没选上"，然后觉得「开始」点不动。----
  const fileInput = document.getElementById('file-input');
  const picked = document.getElementById('file-picked');
  if (fileInput && picked) fileInput.addEventListener('change', () => {
    const f = fileInput.files[0];
    if (!f) { picked.hidden = true; return; }
    const mb = f.size / 1048576;
    const big = mb > 300;
    picked.hidden = false;
    picked.className = `picked${big ? ' warn' : ''}`;
    picked.innerHTML =
      `<b>${esc(f.name)}</b><span class="sz">${mb >= 1024
        ? `${(mb / 1024).toFixed(2)} GB`
        : `${mb.toFixed(mb < 10 ? 1 : 0)} MB`}</span>` +
      (big ? `<span style="color:var(--warn)">文件较大：浏览器上传会慢，而且会复制一份进项目目录。</span>
        <button class="btn small" id="to-path" type="button">改用「本机路径」（不复制）</button>` : '');
    const toPath = document.getElementById('to-path');
    if (toPath) toPath.onclick = () => setMode('path');
  });

  // ---- 第 3 步：流水线类型默认收起（第一次用不需要在导入时做这个选择）----
  const advBox = document.getElementById('adv-box');
  wire('adv-toggle', () => { if (advBox) advBox.hidden = !advBox.hidden; });
  const jobSel = document.getElementById('import-job');
  if (jobSel) jobSel.addEventListener('change', syncImportJob);
  syncImportJob();

  const okBtn = document.getElementById('modal-ok');
  if (okBtn) okBtn.onclick = async () => {
    const title = document.getElementById('title-input').value.trim();
    // 兜底：下拉框万一没选项（能力清单没加载上），也默认走「完整分析」，绝不发空任务类型
    const kind = document.getElementById('import-job').value || 'analyze';
    const btn = document.getElementById('modal-ok');
    btn.disabled = true;
    try {
      let res;
      if (mode === 'upload') {
        const f = document.getElementById('file-input').files[0];
        if (!f) throw new Error('请先点「选择文件」挑一个视频');
        btn.textContent = '上传中…';
        const fd = new FormData();
        fd.append('file', f);
        fd.append('title', title);
        fd.append('auto_start', 'true');
        fd.append('job_kind', kind);
        const bar = document.getElementById('upload-bar');
        bar.hidden = false;
        uploadAbort = new AbortController();
        const t0 = Date.now();
        res = await api.upload(fd, (pct, loaded, total) => {
          document.getElementById('upload-fill').style.width = `${pct}%`;
          const secs = Math.max(0.5, (Date.now() - t0) / 1000);
          const speed = loaded / 1048576 / secs;
          btn.textContent = pct >= 100
            ? '服务器处理中…'
            : `上传 ${pct.toFixed(0)}% · ${(loaded / 1048576).toFixed(0)}/${(total / 1048576).toFixed(0)}MB · ${speed.toFixed(1)}MB/s`;
        }, uploadAbort.signal);
        uploadAbort = null;
        bar.hidden = true;
      } else if (mode === 'path') {
        const path = document.getElementById('path-input').value.trim();
        if (!path) throw new Error('请输入本机文件路径，或点「📁 浏览」选一个');
        res = await api.createProject({ title, source_type: 'file', path, auto_start: true, job_kind: kind });
      } else {
        const url = document.getElementById('url-input').value.trim();
        if (!url) throw new Error('请输入视频链接');
        res = await api.createProject({ title, source_type: 'url', url, auto_start: true, job_kind: kind });
      }
      closeModal();
      fileInput.value = '';        // 下次打开是干净的
      picked.hidden = true;
      ui.toast('已创建，任务开始跑', 'ok');
      await refreshProjects();
      if (res?.project?.id) await selectProject(res.project.id);
    } catch (err) {
      ui.toast(`导入失败：${esc(err.message)}`, 'err', 9000);
      document.getElementById('upload-bar').hidden = true;
    } finally {
      btn.disabled = false;
      btn.textContent = '开始';
      document.getElementById('upload-fill').style.width = '0%';
    }
  };

  // 全局快捷键说明放设置页；这里只处理空格在非输入框时不误触
  window.addEventListener('keydown', (e) => {
    const tag = (e.target.tagName || '').toLowerCase();
    if (['input', 'textarea', 'select'].includes(tag)) return;
    emit('keydown', { event: e });
  });
}

// ---------------------------------------------------------------------------
function connectSSE() {
  const es = new EventSource('/api/events');
  let pending = false;
  es.onmessage = (ev) => {
    let d;
    try { d = JSON.parse(ev.data); } catch (e) { return; }
    if (d.type === 'progress') {
      ui.setProgress(d.pct, d.stage, d.message, d.job_id);
      if (!pending) {
        pending = true;
        setTimeout(async () => {
          pending = false;
          if (store.project) {
            const detail = await api.project(store.project.id);
            store.detail = detail;
            renderJobs(detail.jobs);
          }
        }, 1200);
      }
    } else if (d.type === 'job') {
      const done = ['done', 'failed', 'canceled'].includes(d.status);
      if (!done) {
        // 任务刚排队/开始就先把进度条亮出来：上传完到第一个阶段真正干活之间
        // 有一段空窗（模型加载、worker 轮询），不给反馈就会觉得"卡住了"。
        ui.setProgress(0, d.stage || '准备中', d.message || '任务已开始', d.job_id);
      }
      if (done) {
        ui.setProgress(d.status === 'done' ? 100 : null, d.stage || '', d.message, d.job_id);
        if (d.status === 'done') ui.toast('任务完成', 'ok');
        if (d.status === 'failed') ui.toast(`任务失败：${esc(d.message)}`, 'err', 9000);
        setTimeout(() => { ui.setProgress(null); }, 2500);
        if (store.project && d.project_id === store.project.id) {
          selectProject(store.project.id).then(() => emit('jobFinished', d));
        } else {
          refreshProjects();
        }
      }
    }
  };
  es.onerror = () => { /* 浏览器会自动重连 */ };
}

boot();
