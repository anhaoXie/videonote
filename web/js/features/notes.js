// ---------------------------------------------------------------------------
// 摘要 / 笔记：直接渲染后端生成的 notes.md + 结构化摘要卡片
// ---------------------------------------------------------------------------
import { VN, api, ui, store, on, mdToHtml, fmtTime, esc } from '../core.js';
import { player } from './player.js';

let cache = { pid: null, data: null };

ui.registerTab({
  id: 'notes', label: '摘要笔记', order: 30,
  async render(panel) {
    if (!store.project) { panel.innerHTML = '<div class="card">先选择一个项目</div>'; return; }
    const pid = store.project.id;
    panel.innerHTML = '<div class="card"><span class="muted">加载笔记中…</span></div>';
    let data = cache.pid === pid ? cache.data : null;
    if (!data) {
      data = await api.notes(pid);
      cache = { pid, data };
    }
    if (!data.markdown) {
      panel.innerHTML = `<div class="card"><h3>还没有笔记</h3>
        <p class="muted">跑一次「完整分析」或「重做摘要笔记」。没配大模型 key 时会用本地抽取式摘要，质量会差不少。</p>
        <button class="btn primary" id="n-run">现在生成笔记</button></div>`;
      panel.querySelector('#n-run').onclick = async () => {
        await api.runJob(pid, 'summarize');
        ui.toast('已排队：生成笔记', 'ok');
      };
      return;
    }

    const s = data.summary || {};
    const tsHtml = (text) => esc(text).replace(/\[(\d{1,2}:\d{2}(?::\d{2})?)\]/g,
      (m, t) => `<a href="#" class="ts" data-t="${t}">[${t}]</a>`);

    panel.innerHTML = `
      <div class="card">
        <div class="row" style="justify-content:space-between;align-items:center">
          <div><h3 style="margin:0">${esc(s.title || store.project.title)}</h3>
            <div class="muted">${esc(s.one_liner || '')}</div></div>
          <div class="row">
            <button class="btn small" id="n-regen">重新生成</button>
            <button class="btn small" id="n-dl">下载 .md</button>
            <button class="btn small ghost" id="n-dljson">下载 .json</button>
          </div>
        </div>
      </div>
      ${s.chapters?.length ? `<div class="card"><h3>章节时间轴</h3><div class="seg-list">
        ${s.chapters.map((c) => `<div class="seg" data-t="${esc(c.start)}">
            <div class="seg-time">${esc(c.start || '')}</div>
            <div><div class="seg-text">${esc(c.title || '')}</div>
            ${(c.points || []).map((p) => `<div class="seg-zh">· ${esc(p)}</div>`).join('')}</div>
          </div>`).join('')}
      </div></div>` : ''}
      ${s.speaking_material?.length ? `<div class="card"><h3>🗣 口语跟读素材（点时间跳转）</h3><div class="seg-list">
        ${s.speaking_material.map((x) => `<div class="seg" data-t="${esc(x.time)}">
            <div class="seg-time">${esc(x.time || '')}</div>
            <div><div class="seg-text">${esc(x.line || '')}</div><div class="seg-zh">${esc(x.why || '')}</div></div>
          </div>`).join('')}
      </div></div>` : ''}
      <div class="card"><h3>笔记正文</h3><div class="md" id="n-md">${mdToHtml(data.markdown)}</div></div>`;

    panel.querySelectorAll('[data-t]').forEach((el) => {
      el.onclick = (e) => { e.preventDefault(); player.seekTo(toSec(el.dataset.t)); };
    });
    panel.querySelectorAll('a.ts').forEach((el) => {
      el.onclick = (e) => { e.preventDefault(); player.seekTo(toSec(el.dataset.t)); };
    });
    panel.querySelector('#n-regen').onclick = async () => {
      cache = { pid: null, data: null };
      await api.runJob(pid, 'summarize');
      ui.toast('已排队：重新生成笔记', 'ok');
    };
    panel.querySelector('#n-dl').onclick = () => window.open(api.artifactUrl(pid, 'notes', true), '_blank');
    panel.querySelector('#n-dljson').onclick = () => window.open(api.artifactUrl(pid, 'summary', true), '_blank');
  },
});

function toSec(t) {
  const parts = String(t || '0:0').split(':').map(Number);
  return parts.length === 3 ? parts[0] * 3600 + parts[1] * 60 + parts[2] : (parts[0] || 0) * 60 + (parts[1] || 0);
}
