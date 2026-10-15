// ---------------------------------------------------------------------------
// 关键帧画廊（画面分析结果）：点图跳转到对应时间
// ---------------------------------------------------------------------------
import { VN, api, ui, store, fmtTime, esc } from '../core.js';
import { player } from './player.js';

ui.registerTab({
  id: 'frames', label: '关键帧', order: 40,
  async render(panel) {
    if (!store.project) { panel.innerHTML = '<div class="card">先选择一个项目</div>'; return; }
    const pid = store.project.id;
    panel.innerHTML = '<div class="card"><span class="muted">加载关键帧…</span></div>';
    const data = await api.frames(pid);
    if (!data.total) {
      panel.innerHTML = `<div class="card"><h3>还没有画面分析结果</h3>
        <div class="hint">跑一次「完整分析」或「画面关键帧」任务。
        画面分析会按场景切换抽帧，如果装了 OCR（<code>pip install rapidocr-onnxruntime</code> 并把 <code>VN_ENABLE_OCR=true</code>），
        还能把画面上的字幕、板书、PPT 文字读出来。</div></div>`;
      return;
    }
    panel.innerHTML = `
      <div class="card"><div class="row" style="justify-content:space-between;align-items:center">
        <div>共 <b>${data.total}</b> 张关键帧，点击跳转到对应时间</div>
        <label class="toggle"><input type="checkbox" id="f-ocr"> 只看有文字的帧</label>
      </div></div>
      <div class="grid-frames" id="f-grid"></div>`;

    const grid = panel.querySelector('#f-grid');
    function draw(onlyOcr) {
      const items = data.frames.filter((f) => !onlyOcr || (f.ocr_text || '').trim() || (f.caption || '').trim());
      grid.innerHTML = items.map((f) => `
        <div class="frame" data-t="${f.time}">
          <img loading="lazy" src="${api.frameUrl(pid, f.idx)}" alt="frame ${f.idx}" />
          <div class="frame-meta">${fmtTime(f.time)} · 变化度 ${(f.score ?? 0).toFixed(2)}
            ${f.ocr_text ? `<div style="color:#9fd0ff;margin-top:3px">${esc(f.ocr_text.slice(0, 80))}</div>` : ''}
            ${f.caption ? `<div style="margin-top:3px">${esc(f.caption.slice(0, 80))}</div>` : ''}
          </div>
        </div>`).join('') || '<div class="muted">没有符合条件的帧</div>';
      grid.querySelectorAll('.frame').forEach((el) => {
        el.onclick = () => player.seekTo(Number(el.dataset.t));
      });
    }
    panel.querySelector('#f-ocr').onchange = (e) => draw(e.target.checked);
    draw(false);
  },
});
