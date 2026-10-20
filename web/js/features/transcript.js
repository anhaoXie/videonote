// ---------------------------------------------------------------------------
// 逐句转写：搜索 / 点击跳转 / 中英对照 / 收藏进句库 / 显示跟读得分与说话人
// ---------------------------------------------------------------------------
import { VN, api, ui, store, on, fmtTime, esc } from '../core.js';
import { player } from './player.js';
import { scoreColor } from '../recorder.js';

let keyword = '';
let collected = new Set();      // 已收藏的句子 idx
let best = {};                  // seg_idx -> {best, tries}
let speakerFilter = '';

/** 同一个说话人固定一个颜色（按名字取哈希）。 */
function spkColor(name) {
  let h = 0;
  for (const ch of String(name)) h = (h * 31 + ch.charCodeAt(0)) % 360;
  return `hsl(${h} 70% 62%)`;
}
export { spkColor };

ui.registerTab({
  id: 'transcript', label: '逐句转写', order: 20,
  async render(panel) {
    if (!store.project) { panel.innerHTML = '<div class="card">先选择一个项目</div>'; return; }
    if (!store.segments.length) {
      panel.innerHTML = `<div class="card"><h3>还没有转写结果</h3>
        <p class="muted">去顶部选「完整分析」或「转写 + 字幕」然后点「开始分析」。</p></div>`;
      return;
    }
    const pid = store.project.id;
    try {
      const [clipRes, scoreRes] = await Promise.all([
        api.get(`/api/projects/${pid}/clips`),
        api.get(`/api/projects/${pid}/scores`),
      ]);
      collected = new Set((clipRes.clips || []).map((c) => Number(c.seg_idx)));
      best = clipRes.scores || {};
      scoreRes.best && Object.entries(scoreRes.best).forEach(([k, v]) => { best[k] = v; });
    } catch (e) { /* 句库还没建好也不影响 */ }

    const speakers = [...new Set(store.segments.map((s) => s.speaker).filter(Boolean))];
    panel.innerHTML = `
      <div class="searchbar">
        <input type="text" id="t-q" placeholder="搜索台词关键词…" value="${esc(keyword)}" />
        ${speakers.length ? `<select class="select" id="t-spk">
          <option value="">全部说话人</option>
          ${speakers.map((s) => `<option value="${esc(s)}" ${speakerFilter === s ? 'selected' : ''}>${esc(s)}</option>`).join('')}
        </select>` : ''}
        <button class="btn" id="t-export">导出 SRT</button>
        <button class="btn ghost" id="t-export-vtt">VTT</button>
      </div>
      <div class="muted" id="t-count" style="margin-bottom:8px"></div>
      <div class="seg-list" id="t-list"></div>`;

    const list = panel.querySelector('#t-list');
    const count = panel.querySelector('#t-count');

    function draw() {
      const kw = keyword.trim().toLowerCase();
      const shown = store.segments.filter((s) => {
        if (kw && !(s.text || '').toLowerCase().includes(kw)) return false;
        if (speakerFilter && (s.speaker || '') !== speakerFilter) return false;
        return true;
      });
      count.textContent = `共 ${store.segments.length} 句${kw || speakerFilter ? `，匹配 ${shown.length} 句` : ''} · 点句子跳转，☆ 收藏进句库`;
      list.innerHTML = shown.map((s) => {
        const zh = store.opts.bilingual ? store.translation[String(s.idx)] : '';
        const b = best[String(s.idx)];
        const isFav = collected.has(Number(s.idx));
        return `<div class="seg" data-idx="${s.idx}" data-start="${s.start}">
          <div class="seg-time">${fmtTime(s.start)}
            ${s.speaker ? `<div class="spk" style="color:${spkColor(s.speaker)};background:${spkColor(s.speaker)}22;margin-top:3px">${esc(s.speaker)}</div>` : ''}</div>
          <div><div class="seg-text">${esc(s.text)}</div>
            ${zh ? `<div class="seg-zh">${esc(zh)}</div>` : ''}
            <div class="seg-actions">
              <button class="btn small ghost" data-fav="${s.idx}">${isFav ? '★ 已收藏' : '☆ 收藏'}</button>
              ${b ? `<span class="badge" style="color:${scoreColor(b.best)}">最佳 ${Number(b.best).toFixed(0)} 分 · ${b.tries} 次</span>` : ''}
            </div>
          </div>
        </div>`;
      }).join('');
      list.querySelectorAll('.seg').forEach((el) => {
        el.onclick = () => player.seekTo(Number(el.dataset.start));
      });
      list.querySelectorAll('[data-fav]').forEach((btn) => {
        btn.onclick = async (e) => {
          e.stopPropagation();
          const idx = Number(btn.dataset.fav);
          if (collected.has(idx)) { ui.toast('已经在句库里了（去「跟读句库」页管理）', '', 2500); return; }
          btn.disabled = true;
          btn.textContent = '切片中…';
          try {
            await api.post(`/api/projects/${pid}/clips`, { seg_idx: idx });
            collected.add(idx);
            btn.textContent = '★ 已收藏';
            ui.toast('已收藏，去「跟读句库」页跟读', 'ok', 2500);
          } catch (err) {
            btn.textContent = '☆ 收藏';
            ui.toast(`收藏失败：${esc(err.message)}`, 'err');
          } finally {
            btn.disabled = false;
          }
        };
      });
      markActive(store.currentIdx);
    }
    function markActive(idx) {
      list.querySelectorAll('.seg').forEach((el) => el.classList.toggle('active', Number(el.dataset.idx) === idx));
    }

    panel.querySelector('#t-q').oninput = (e) => { keyword = e.target.value; draw(); };
    const spk = panel.querySelector('#t-spk');
    if (spk) spk.onchange = (e) => { speakerFilter = e.target.value; draw(); };
    panel.querySelector('#t-export').onclick = () => api.downloadArtifact(pid, 'subtitles_srt');
    panel.querySelector('#t-export-vtt').onclick = () => api.downloadArtifact(pid, 'subtitles_vtt');
    draw();
    on('segChanged', ({ idx }) => markActive(idx));
  },
});
