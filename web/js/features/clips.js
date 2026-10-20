// ---------------------------------------------------------------------------
// 跟读句库 + 跟读训练 + Anki 导出
//   * 收藏的句子都在这里，可以播放原声、录音跟读、立刻评分
//   * 一键导出成 Anki 牌组（.apkg，带音频），手机上接着练
// ---------------------------------------------------------------------------
import { VN, api, ui, store, on, fmtTime, esc } from '../core.js';
import { Recorder, uploadBlob, wordDiffHtml, scoreColor } from '../recorder.js';
import { phonemePanelHtml } from '../phoneme-ui.js';

const rec = new Recorder();
let recordingClip = null;
let filter = { tag: '', onlyUnpracticed: false, allProjects: false };
let cache = { clips: [], scores: {} };

ui.registerTab({
  id: 'clips', label: '跟读句库', order: 35,
  async render(panel) {
    panel.innerHTML = '<div class="card"><span class="muted">加载句库…</span></div>';
    await load(panel);
  },
});

async function load(panel) {
  let clips = [];
  let scores = {};
  try {
    if (filter.allProjects) {
      const d = await api.get('/api/clips');
      clips = d.clips;
    } else if (store.project) {
      const d = await api.get(`/api/projects/${store.project.id}/clips`);
      clips = d.clips;
      scores = d.scores || {};
    }
  } catch (err) {
    panel.innerHTML = `<div class="card">加载失败：${esc(err.message)}</div>`;
    return;
  }
  cache = { clips, scores };
  draw(panel);
}

async function reload() {
  const panel = document.querySelector('#content .panel');
  if (panel) await load(panel);
}

function draw(panel) {
  const clips = cache.clips;
  const pid = store.project?.id;
  const practiced = clips.reduce((n, c) => n + (c.review_count || 0), 0);
  const withScore = Object.values(cache.scores).map((s) => s.best).filter((x) => x != null);
  const avg = withScore.length ? (withScore.reduce((a, b) => a + b, 0) / withScore.length).toFixed(1) : '—';

  panel.innerHTML = `
    <div class="card">
      <div class="row" style="justify-content:space-between;align-items:center">
        <div>
          <h3 style="margin:0">跟读句库</h3>
          <div class="muted">${clips.length} 句 · 累计练习 ${practiced} 次 · 平均最高分 <b style="color:${scoreColor(Number(avg) || 0)}">${avg}</b></div>
        </div>
        <div class="row">
          <button class="btn small" id="c-all">${filter.allProjects ? '只看当前项目' : '看所有项目'}</button>
          <button class="btn small primary" id="c-export" ${clips.length && !filter.allProjects ? '' : 'disabled'}>导出 Anki 牌组</button>
        </div>
      </div>
      <div class="searchbar" style="margin-top:10px">
        <input type="text" id="c-tag" placeholder="按标签筛选…" value="${esc(filter.tag)}" />
        <label class="toggle"><input type="checkbox" id="c-unprac" ${filter.onlyUnpracticed ? 'checked' : ''}> 只看没练过的</label>
        <button class="btn small" id="c-refresh">刷新</button>
      </div>
      <div class="hint">怎么用：点「🔊 原声」听一遍 → 点「🎙 跟读」录你自己 → 点「🎯 评分」看差在哪个词。
      练熟了导出成 Anki 牌组，手机上每天过一遍。句子右上角的 ⭐ 在「逐句转写」页里收藏。</div>
    </div>
    <div id="c-list"></div>`;

  const list = panel.querySelector('#c-list');
  const shown = clips.filter((c) => {
    if (filter.tag && !(c.tags || '').includes(filter.tag)) return false;
    if (filter.onlyUnpracticed && (c.review_count || 0) > 0) return false;
    return true;
  });
  if (!shown.length) {
    list.innerHTML = `<div class="card"><span class="muted">还没有收藏的句子。去「逐句转写」页，点句子右边的 ☆ 就能收进来。</span></div>`;
  }

  shown.forEach((c) => {
    const s = cache.scores[String(c.seg_idx)] || {};
    const el = document.createElement('div');
    el.className = 'card clip';
    el.dataset.id = c.id;
    el.innerHTML = `
      <div class="row" style="justify-content:space-between;align-items:flex-start">
        <div style="flex:1;min-width:0">
          <div class="muted" style="font-size:11.5px">
            ${filter.allProjects ? esc(c.project_title || '') + ' · ' : ''}${fmtTime(c.start)} - ${fmtTime(c.end)}
            ${s.best != null ? `· 最高 <b style="color:${scoreColor(s.best)}">${s.best.toFixed(0)}</b> 分（${s.tries} 次）` : ''}
          </div>
          <div class="seg-text" style="font-size:15px;margin-top:4px">${esc(c.text)}</div>
          ${c.translation ? `<div class="seg-zh">${esc(c.translation)}</div>` : ''}
          ${c.note ? `<div class="muted" style="margin-top:4px">📝 ${esc(c.note)}</div>` : ''}
        </div>
        <div class="muted" style="font-size:11.5px;white-space:nowrap">已练 ${c.review_count || 0} 次</div>
      </div>
      <div class="player-bar" style="margin-top:10px">
        <audio src="/api/clips/${c.id}/media?kind=audio" preload="none" id="aud-${c.id}" style="display:none"></audio>
        <button class="btn small" data-act="play">🔊 原声</button>
        <button class="btn small primary" data-act="rec">🎙 跟读</button>
        <button class="btn small" data-act="score" disabled>🎯 评分</button>
        <span class="grow"></span>
        <button class="btn small ghost" data-act="review">✓ 记一次复习</button>
        <button class="btn small ghost" data-act="edit">✎ 备注</button>
        <button class="btn small ghost" data-act="del">🗑</button>
      </div>
      <div class="clip-result" hidden></div>`;

    const audio = el.querySelector(`#aud-${c.id}`);
    const scoreBtn = el.querySelector('[data-act="score"]');
    const result = el.querySelector('.clip-result');
    let myUrl = null;

    el.querySelector('[data-act="play"]').onclick = () => {
      audio.currentTime = 0;
      audio.play().catch(() => ui.toast('这个句子还没切片，重新收藏一次即可生成', 'err'));
    };

    const recBtn = el.querySelector('[data-act="rec"]');
    recBtn.onclick = async () => {
      if (rec.recording) {
        const { blob, url } = await rec.stop();
        myUrl = url;
        recBtn.innerHTML = '🎙 跟读';
        recordingClip = null;
        el._blob = blob;
        scoreBtn.disabled = false;
        result.hidden = false;
        result.innerHTML = `<div class="hint">录好了，点「🎯 评分」看看读得怎么样。</div>
          <audio controls src="${url}" style="width:100%;margin-top:6px"></audio>`;
        return;
      }
      try {
        await rec.start();
        recordingClip = c.id;
        recBtn.innerHTML = '<span class="rec-dot"></span>停止';
        audio.pause();
      } catch (e) {
        ui.toast('拿不到麦克风权限', 'err');
      }
    };

    scoreBtn.onclick = async () => {
      if (!el._blob) return ui.toast('先录一段', 'err');
      scoreBtn.disabled = true;
      scoreBtn.textContent = '评分中…';
      result.hidden = false;
      result.innerHTML = '<div class="hint">本地识别中（第一次会加载模型，十几秒很正常）…</div>';
      try {
        const r = await uploadBlob(`/api/projects/${c.project_id}/score`, el._blob, {
          seg_idx: String(c.seg_idx), mode: 'repeat',
        });
        renderScore(result, r, myUrl);
        ui.toast(`得分 ${r.score}`, r.score >= 75 ? 'ok' : '', 2500);
        await reload();
      } catch (err) {
        result.innerHTML = `<div class="hint" style="color:var(--err)">评分失败：${esc(err.message)}</div>`;
      } finally {
        scoreBtn.disabled = false;
        scoreBtn.textContent = '🎯 评分';
      }
    };

    el.querySelector('[data-act="review"]').onclick = async () => {
      await api.post(`/api/clips/${c.id}/review`, {});
      ui.toast('复习 +1', 'ok', 1200);
      await reload();
    };

    el.querySelector('[data-act="edit"]').onclick = async () => {
      const note = prompt('备注（比如：这句的重音在第二个词）', c.note || '');
      if (note === null) return;
      const tags = prompt('标签，逗号分隔（比如：连读,俚语）', c.tags || '');
      if (tags === null) return;
      await api.req(`/api/clips/${c.id}`, { method: 'PATCH', body: JSON.stringify({ note, tags }) });
      await reload();
    };

    el.querySelector('[data-act="del"]').onclick = async () => {
      if (!confirm('从句库移除这一句？')) return;
      await api.del(`/api/clips/${c.id}`);
      await reload();
    };

    list.appendChild(el);
  });

  panel.querySelector('#c-tag').oninput = (e) => { filter.tag = e.target.value; draw(panel); };
  panel.querySelector('#c-unprac').onchange = (e) => { filter.onlyUnpracticed = e.target.checked; draw(panel); };
  panel.querySelector('#c-all').onclick = () => { filter.allProjects = !filter.allProjects; reload(); };
  panel.querySelector('#c-refresh').onclick = () => reload();
  const exp = panel.querySelector('#c-export');
  if (exp) {
    exp.onclick = async () => {
      if (!pid) return;
      exp.disabled = true;
      exp.textContent = '打包中…';
      try {
        const r = await api.post(`/api/projects/${pid}/clips/export`, {});
        ui.toast(`已生成 Anki 牌组（${r.clips} 张卡，${r.kind}）`, 'ok', 6000);
        api.downloadArtifact(pid, 'anki_deck');
      } catch (err) {
        ui.toast(`导出失败：${esc(err.message)}`, 'err', 6000);
      } finally {
        exp.disabled = false;
        exp.textContent = '导出 Anki 牌组';
      }
    };
  }
}

function renderScore(host, r, myUrl) {
  const d = r.detail || {};
  const bar = (label, v) => `
    <div style="flex:1;min-width:120px">
      <div class="muted" style="font-size:11px">${label} ${v}%</div>
      <div class="progress" style="height:5px"><div class="progress-bar" style="width:${v}%;background:${scoreColor(v)}"></div></div>
    </div>`;
  host.innerHTML = `
    <div class="row" style="align-items:center;gap:16px;margin-top:10px">
      <div style="font-size:40px;font-weight:700;color:${scoreColor(r.score)};line-height:1">${r.score}</div>
      <div style="flex:1">
        <div class="row" style="gap:10px">${bar('准确', r.accuracy)}${bar('完整', r.completeness)}${bar('流利', r.fluency)}</div>
      </div>
    </div>
    <div style="margin-top:10px">
      <div class="muted" style="font-size:11.5px">识别听到的是：</div>
      <div class="seg-text">${esc(r.transcript) || '<span class="muted">（没识别到语音）</span>'}</div>
      <div class="muted" style="font-size:11.5px;margin-top:8px">逐词对比（绿=对，黄=读错，红=漏读，紫=多说）：</div>
      <div class="seg-text" style="font-size:16px;line-height:1.9">${wordDiffHtml(d.words)}</div>
    </div>
    ${phonemePanelHtml(r.phonemes)}
    <ul class="hint" style="margin:8px 0 0 0;padding-left:18px">
      ${(r.advice || []).map((a) => `<li>${esc(a)}</li>`).join('')}
    </ul>
    <div class="row" style="gap:10px;margin-top:8px;align-items:center">
      <span class="muted" style="font-size:11.5px">我的录音：</span>
      <audio controls src="${myUrl || r.recording_url}" style="flex:1;min-width:200px"></audio>
    </div>`;
}

on('projectChanged', () => { cache = { clips: [], scores: {} }; });
