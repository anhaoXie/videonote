// ---------------------------------------------------------------------------
// 播放器 + 逐句跟读（Repeat 模式）
//   * 每说完一句自动暂停，等你跟读
//   * 一��循环 / 重听 / 上一句 / 下一句
//   * 录音对比：录下你读的，跟原声 A/B 对照
// ---------------------------------------------------------------------------
import { VN, api, ui, store, emit, on, fmtTime, esc } from '../core.js';
import { Recorder, uploadBlob, wordDiffHtml, scoreColor } from '../recorder.js';

let video = null;
let micStream = null;
let recorder = null;
let chunks = [];
let myRecUrl = null;
let myRecBlob = null;
let armed = -1;          // 正在朝哪一句的结尾播放
let loopCount = 0;
let continueTimer = null;
// 影子跟读
const shadow = new Recorder();
let shadowVideoStart = 0;
let shadowStartSeg = -1;
let shadowBlobUrl = null;

function ensureVideo() {
  if (video) return video;
  video = document.createElement('video');
  video.controls = true;
  video.preload = 'metadata';
  video.playsInline = true;
  video.style.width = '100%';

  video.addEventListener('play', () => { armFromCurrent(); emit('playingChanged', { playing: true }); });
  video.addEventListener('pause', () => { clearTimeout(continueTimer); emit('playingChanged', { playing: false }); });
  video.addEventListener('timeupdate', onTime);
  video.addEventListener('seeking', () => { armed = -1; });
  video.addEventListener('loadedmetadata', () => emit('videoReady', {}));
  return video;
}

function armFromCurrent() {
  const t = video.currentTime;
  let idx = store.currentIdx;
  if (idx < 0 || (store.segments[idx] && t > store.segments[idx].end - 0.05)) {
    idx = nextIndexFrom(t);
  }
  armed = idx;
  loopCount = 0;
}

function nextIndexFrom(t) {
  const segs = store.segments;
  for (let i = 0; i < segs.length; i++) if (segs[i].end > t + 0.05) return i;
  return -1;
}

function onTime() {
  const t = video.currentTime;
  store.currentTime = t;
  const idx = VN.segAt(t);
  if (idx !== store.currentIdx) {
    store.currentIdx = idx;
    emit('segChanged', { idx });
    document.querySelectorAll('#content .seg').forEach((el) => {
      el.classList.toggle('active', Number(el.dataset.idx) === idx);
      if (Number(el.dataset.idx) === idx && el.scrollIntoView) el.scrollIntoView({ block: 'nearest' });
    });
    emit('time', { time: t });
  }
  if (!store.opts.autoPause || armed < 0) return;
  const seg = store.segments[armed];
  if (!seg) return;
  if (t >= seg.end - 0.03) {
    if (store.opts.loop && loopCount < (store.opts.loopTimes || 3) - 1) {
      loopCount += 1;
      video.currentTime = Math.max(0, seg.start - 0.05);
      video.play().catch(() => {});
      return;
    }
    video.pause();
    const finished = armed;
    armed = finished + 1;
    loopCount = 0;
    emit('sentenceDone', { idx: finished });
    if (store.opts.autoContinue > 0) {
      clearTimeout(continueTimer);
      continueTimer = setTimeout(() => {
        if (!video.paused) return;
        video.currentTime = Math.max(0, (store.segments[finished + 1]?.start ?? seg.end) - 0.02);
        video.play().catch(() => {});
      }, store.opts.autoContinue * 1000);
    }
  }
}

function seekTo(sec, play = true) {
  video.currentTime = Math.max(0, sec);
  armed = VN.segAt(sec);
  if (armed < 0) armed = nextIndexFrom(sec);
  loopCount = 0;
  if (play) video.play().catch(() => {});
}

function replaySentence(idx) {
  const seg = store.segments[idx];
  if (!seg) return;
  video.currentTime = Math.max(0, seg.start - 0.08);
  armed = idx;
  loopCount = 0;
  video.play().catch(() => {});
}

function step(delta) {
  if (!store.segments.length) return;
  let idx = store.currentIdx;
  if (idx < 0) idx = nextIndexFrom(video.currentTime) - 1;
  const target = Math.max(0, Math.min(store.segments.length - 1, idx + delta));
  const seg = store.segments[target];
  if (!seg) return;
  video.pause();
  video.currentTime = Math.max(0, seg.start - 0.08);
  armed = target;
  loopCount = 0;
  store.currentIdx = target;
  emit('segChanged', { idx: target });
}

// ---------------------------------------------------------------------------
ui.registerTab({
  id: 'player', label: '播放 / 跟读', order: 10,
  render(panel) {
    if (!store.project) { panel.innerHTML = '<div class="card">先选择一个项目</div>'; return; }
    panel.innerHTML = `
      <div class="player-wrap">
        <div class="player-stage" id="stage">
          <div class="sub-overlay"><span class="sub-line" id="subline"></span></div>
        </div>
        <div class="player-bar">
          <button class="btn" id="p-replay">↻ 重听本句 (R)</button>
          <button class="btn" id="p-prev">⏮ 上一句</button>
          <button class="btn" id="p-next">下一句 ⏭</button>
          <button class="btn" id="p-fav">☆ 收藏本句</button>
          <span class="grow"></span>
          <label class="toggle"><input type="checkbox" id="p-autopause" ${store.opts.autoPause ? 'checked' : ''}> 说完一句自动暂停</label>
          <label class="toggle"><input type="checkbox" id="p-loop" ${store.opts.loop ? 'checked' : ''}> 单句循环</label>
          <label class="toggle">自动继续
            <select class="select" id="p-continue">
              <option value="0">关闭</option><option value="2">2 秒</option>
              <option value="4">4 秒</option><option value="8">8 秒</option>
            </select>
          </label>
          <label class="toggle"><input type="checkbox" id="p-bilingual" ${store.opts.bilingual ? 'checked' : ''}> 显示译文</label>
        </div>
        <div class="card">
          <h3>当前这一句</h3>
          <div class="now-sentence" id="now"><span class="muted">按播放开始，每说完一句会自动停下来等你跟读</span></div>
        </div>
        <div class="repeat-panel">
          <div class="card">
            <h3>🅰 原声</h3>
            <div class="player-bar">
              <button class="btn" id="a-replay">再听一遍</button>
              <button class="btn" id="a-slow">0.75x 慢速</button>
              <button class="btn ghost" id="a-normal">1.0x</button>
            </div>
            <div class="hint">0.75 倍速听清连读，再回到原速跟读，效果最好。</div>
          </div>
          <div class="card">
            <h3>🅱 你的跟读</h3>
            <div class="player-bar">
              <button class="btn primary" id="m-rec">🎙 开始录音 (M)</button>
              <button class="btn" id="m-play" disabled>▶ 听我的</button>
              <button class="btn" id="m-ab" disabled>AB 对比</button>
              <button class="btn" id="m-score" disabled>🎯 评分</button>
            </div>
            <div class="hint" id="m-hint">录下你的跟读，和原声对比着听 —— 这是练口语最有效的循环。</div>
            <audio id="m-audio" style="width:100%;margin-top:8px" controls hidden></audio>
            <div id="m-result"></div>
          </div>
        </div>
        <div class="card">
          <h3>🎧 影子跟读（不暂停，滞后约 1 秒跟着说）</h3>
          <div class="hint">原声继续放，你像影子一样跟着说。停下来后系统会算出你覆盖了多少句、
          平均滞后几秒、哪几句跟丢了。这是同传训练里最有效的方法，练的是「边听边说」。</div>
          <div class="player-bar" style="margin-top:8px">
            <button class="btn primary" id="sh-rec">🎙 开始影子跟读</button>
            <div class="level-meter" id="sh-level"><div></div></div>
            <span class="muted" id="sh-timer">00:00</span>
          </div>
          <div id="sh-result"></div>
        </div>
        <div class="card">
          <h3>快捷键</h3>
          <div class="kv">
            <div>空格</div><div>播放 / 暂停</div>
            <div>R</div><div>重听本句</div>
            <div>N / →</div><div>下一句</div>
            <div>P / ←</div><div>上一句</div>
            <div>M</div><div>开始 / 停止录音</div>
            <div>L</div><div>单句循环开关</div>
          </div>
        </div>
      </div>`;

    const stage = panel.querySelector('#stage');
    const v = ensureVideo();
    const wantSrc = location.origin + api.rawUrl(store.project.id);
    if (v.src !== wantSrc) {
      v.src = api.rawUrl(store.project.id);
    }
    // 注意：同一个项目反复渲染时不要把 video 元素换掉（换掉=播放进度丢失）
    stage.insertBefore(v, stage.firstChild);

    panel.querySelector('#p-replay').onclick = () => replaySentence(store.currentIdx < 0 ? nextIndexFrom(video.currentTime) : store.currentIdx);
    panel.querySelector('#p-prev').onclick = () => step(-1);
    panel.querySelector('#p-next').onclick = () => step(1);
    panel.querySelector('#a-replay').onclick = () => replaySentence(store.currentIdx < 0 ? nextIndexFrom(video.currentTime) : store.currentIdx);
    panel.querySelector('#a-slow').onclick = () => { video.playbackRate = 0.75; };
    panel.querySelector('#a-normal').onclick = () => { video.playbackRate = 1.0; };

    const ap = panel.querySelector('#p-autopause');
    ap.onchange = () => { store.opts.autoPause = ap.checked; localStorage.setItem('vn.autopause', ap.checked ? '1' : '0'); };
    const lp = panel.querySelector('#p-loop');
    lp.onchange = () => { store.opts.loop = lp.checked; localStorage.setItem('vn.loop', lp.checked ? '1' : '0'); };
    const bi = panel.querySelector('#p-bilingual');
    bi.onchange = () => { store.opts.bilingual = bi.checked; localStorage.setItem('vn.bilingual', bi.checked ? '1' : '0'); drawSubtitle(); };
    const cont = panel.querySelector('#p-continue');
    cont.value = String(store.opts.autoContinue || 0);
    cont.onchange = () => { store.opts.autoContinue = Number(cont.value); };

    // ---- 录音 ----
    const recBtn = panel.querySelector('#m-rec');
    const playBtn = panel.querySelector('#m-play');
    const abBtn = panel.querySelector('#m-ab');
    const audio = panel.querySelector('#m-audio');
    const hint = panel.querySelector('#m-hint');

    async function toggleRec() {
      if (recorder && recorder.state === 'recording') {
        recorder.stop();
        return;
      }
      try {
        micStream = micStream || await navigator.mediaDevices.getUserMedia({ audio: true });
      } catch (err) {
        ui.toast('拿不到麦克风权限，请在浏览器里允许', 'err');
        return;
      }
      chunks = [];
      recorder = new MediaRecorder(micStream);
      recorder.ondataavailable = (e) => chunks.push(e.data);
      recorder.onstop = () => {
        const blob = new Blob(chunks, { type: recorder.mimeType || 'audio/webm' });
        if (myRecUrl) URL.revokeObjectURL(myRecUrl);
        myRecUrl = URL.createObjectURL(blob);
        myRecBlob = blob;
        audio.src = myRecUrl;
        audio.hidden = false;
        playBtn.disabled = false;
        abBtn.disabled = false;
        scoreBtn.disabled = false;
        recBtn.innerHTML = '🎙 开始录音 (M)';
        hint.textContent = `已录 ${(blob.size / 1024).toFixed(0)} KB，点「听我的」或「🎯 评分」看看读得怎么样。`;
      };
      recorder.start();
      recBtn.innerHTML = '<span class="rec-dot"></span>停止录音 (M)';
      hint.textContent = '录音中… 说完点停止';
      // 录音时暂停原声，避免互相干扰
      video.pause();
    }
    recBtn.onclick = toggleRec;
    playBtn.onclick = () => audio.play();
    abBtn.onclick = async () => {
      replaySentence(store.currentIdx < 0 ? nextIndexFrom(video.currentTime) : store.currentIdx);
      const seg = store.segments[store.currentIdx];
      if (!seg) return;
      const wait = Math.max(1200, (seg.end - seg.start) * 1000 + 900);
      setTimeout(() => { video.pause(); audio.play(); }, wait);
    };

    // ---- 🎯 发音评分（本地 Whisper 回环）----
    const scoreBtn = panel.querySelector('#m-score');
    const mResult = panel.querySelector('#m-result');
    scoreBtn.onclick = async () => {
      const idx = store.currentIdx < 0 ? nextIndexFrom(video.currentTime) : store.currentIdx;
      if (!myRecBlob) return ui.toast('先录一段你的跟读', 'err');
      if (idx < 0) return ui.toast('先播放到某一句', 'err');
      scoreBtn.disabled = true;
      scoreBtn.textContent = '评分中…';
      mResult.innerHTML = '<div class="hint">本地识别中（第一次加载模型要十几秒）…</div>';
      try {
        const r = await uploadBlob(`/api/projects/${store.project.id}/score`, myRecBlob,
          { seg_idx: String(idx), mode: 'repeat' });
        renderRepeatScore(mResult, r, myRecUrl);
        const color = scoreColor(r.score);
        ui.toast(`第 ${idx + 1} 句得分 <b style="color:${color}">${r.score}</b>`, r.score >= 75 ? 'ok' : '', 4000);
      } catch (err) {
        mResult.innerHTML = `<div class="hint" style="color:var(--err)">评分失败：${esc(err.message)}</div>`;
      } finally {
        scoreBtn.disabled = false;
        scoreBtn.textContent = '🎯 评分';
      }
    };

    // ---- ⭐ 收藏本句 ----
    const favBtn = panel.querySelector('#p-fav');
    favBtn.onclick = async () => {
      const idx = store.currentIdx < 0 ? nextIndexFrom(video.currentTime) : store.currentIdx;
      if (idx < 0) return ui.toast('先播放到某一句', 'err');
      favBtn.disabled = true;
      try {
        await api.post(`/api/projects/${store.project.id}/clips`, { seg_idx: idx });
        favBtn.textContent = '★ 已收藏';
        ui.toast('已收藏，去「跟读句库」页跟读', 'ok', 2500);
      } catch (err) {
        ui.toast(`收藏失败：${esc(err.message)}`, 'err');
      } finally {
        favBtn.disabled = false;
      }
    };

    // ---- 🎧 影子跟读 ----
    const shBtn = panel.querySelector('#sh-rec');
    const shResult = panel.querySelector('#sh-result');
    const shLevel = panel.querySelector('#sh-level > div');
    const shTimer = panel.querySelector('#sh-timer');
    let shTick = null;

    async function startShadow() {
      shadowStartSeg = store.currentIdx < 0 ? nextIndexFrom(video.currentTime) : store.currentIdx;
      if (shadowStartSeg < 0) return ui.toast('先播放到某一句', 'err');
      try {
        await shadow.start();
      } catch (err) {
        return ui.toast('拿不到麦克风权限', 'err');
      }
      shadowVideoStart = video.currentTime;
      video.play().catch(() => {});
      shBtn.innerHTML = '<span class="rec-dot"></span>停止并分析';
      shResult.innerHTML = '';
      const t0 = Date.now();
      shTick = setInterval(() => {
        const s = Math.floor((Date.now() - t0) / 1000);
        shTimer.textContent = `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`;
      }, 500);
    }

    async function stopShadow() {
      clearInterval(shTick);
      shBtn.innerHTML = '🎙 开始影子跟读';
      shTimer.textContent = '';
      const stopAtVideo = video.currentTime;
      video.pause();
      const { blob, url, duration } = await shadow.stop();
      if (shadowBlobUrl) URL.revokeObjectURL(shadowBlobUrl);
      shadowBlobUrl = url;
      // 找出这次覆盖到的句子范围
      const segs = store.segments;
      const endIdx = segs.reduce((acc, s, i) => (s.start <= stopAtVideo + 2 ? i : acc), shadowStartSeg);
      shResult.innerHTML = `<div class="hint">分析中（本地识别 ${duration.toFixed(0)} 秒录音，可能要一会儿）…</div>
        <audio controls src="${url}" style="width:100%;margin-top:6px"></audio>`;
      try {
        const r = await uploadBlob(`/api/projects/${store.project.id}/shadow/analyze`, blob, {
          start_seg: String(shadowStartSeg), end_seg: String(endIdx), video_start: String(shadowVideoStart),
        });
        renderShadow(shResult, r, url);
      } catch (err) {
        shResult.innerHTML = `<div class="hint" style="color:var(--err)">分析失败：${esc(err.message)}</div>
          <audio controls src="${url}" style="width:100%;margin-top:6px"></audio>`;
      }
    }

    shBtn.onclick = async () => {
      if (shadow.recording) await stopShadow();
      else await startShadow();
    };
    shadow._onLevel = (v) => { if (shLevel) shLevel.style.width = `${Math.min(100, v * 160)}%`; };

    window.__vnToggleRec = toggleRec;
    drawSubtitle();
  },
});

function drawSubtitle() {
  const el = document.getElementById('subline');
  const now = document.getElementById('now');
  if (!el) return;
  const seg = store.segments[store.currentIdx];
  if (!seg) { el.textContent = ''; if (now) now.innerHTML = '<span class="muted">（这一句还没识别到）</span>'; return; }
  const zh = store.translation[String(seg.idx)];
  const showZh = store.opts.bilingual && zh;
  el.innerHTML = `${esc(seg.text)}${showZh ? `<small>${esc(zh)}</small>` : ''}`;
  if (now) {
    now.innerHTML = `<div>${esc(seg.text)}</div>${showZh ? `<div class="zh">${esc(zh)}</div>` : ''}
      <div class="muted" style="margin-top:6px;font-size:11.5px">
        ${seg.speaker ? `<span class="spk">${esc(seg.speaker)}</span> · ` : ''}
        第 ${seg.idx + 1} / ${store.segments.length} 句 ·
      ${fmtTime(seg.start)} - ${fmtTime(seg.end)}</div>`;
  }
}

on('segChanged', drawSubtitle);
on('sentenceDone', ({ idx }) => {
  const seg = store.segments[idx + 1];
  if (seg) ui.toast(`该你了 👉 ${esc(store.segments[idx + 1].text.slice(0, 60))}`, '', 3500);
});
on('time', drawSubtitle);
on('jobFinished', () => { armed = -1; });

on('keydown', ({ event: e }) => {
  const tab = document.querySelector('#tabs .tab.active')?.dataset.id;
  if (tab !== 'player' || !video) return;
  const key = e.key.toLowerCase();
  if (e.code === 'Space') { e.preventDefault(); video.paused ? video.play() : video.pause(); }
  else if (key === 'r') { e.preventDefault(); replaySentence(store.currentIdx < 0 ? nextIndexFrom(video.currentTime) : store.currentIdx); }
  else if (key === 'n' || e.key === 'ArrowRight') { e.preventDefault(); step(1); }
  else if (key === 'p' || e.key === 'ArrowLeft') { e.preventDefault(); step(-1); }
  else if (key === 'm') { e.preventDefault(); window.__vnToggleRec?.(); }
  else if (key === 'l') {
    e.preventDefault();
    store.opts.loop = !store.opts.loop;
    localStorage.setItem('vn.loop', store.opts.loop ? '1' : '0');
    const box = document.getElementById('p-loop');
    if (box) box.checked = store.opts.loop;
  }
});

function bar(label, v) {
  const c = scoreColor(v);
  return `<div style="flex:1;min-width:110px">
    <div class="muted" style="font-size:11px">${label} ${v}%</div>
    <div class="progress" style="height:5px"><div class="progress-bar" style="width:${v}%;background:${c}"></div></div>
  </div>`;
}

function renderRepeatScore(host, r, myUrl) {
  const d = r.detail || {};
  host.innerHTML = `
    <div class="row" style="align-items:center;gap:16px;margin-top:10px">
      <div style="font-size:38px;font-weight:700;color:${scoreColor(r.score)};line-height:1">${r.score}</div>
      <div style="flex:1"><div class="row" style="gap:10px">
        ${bar('准确', r.accuracy)}${bar('完整', r.completeness)}${bar('流利', r.fluency)}
      </div></div>
    </div>
    <div class="muted" style="font-size:11.5px;margin-top:10px">识别听到的是：</div>
    <div class="seg-text">${esc(r.transcript) || '<span class="muted">（没识别到语音，可能没录上）</span>'}</div>
    <div class="muted" style="font-size:11.5px;margin-top:8px">逐词对比（绿=对，黄=读错，红=漏读，紫=多说）：</div>
    <div class="seg-text" style="font-size:16px;line-height:1.9">${wordDiffHtml(d.words)}</div>
    <ul class="hint" style="padding-left:18px;margin:8px 0 0">
      ${(r.advice || []).map((a) => `<li>${esc(a)}</li>`).join('')}
    </ul>
    <audio controls src="${myUrl || r.recording_url}" style="width:100%;margin-top:8px"></audio>`;
}

function renderShadow(host, r, url) {
  const segs = (r.segments || []).map((s) => {
    const c = s.coverage >= 0.85 ? '#22c55e' : s.coverage >= 0.6 ? '#f59e0b' : '#ef4444';
    return `<div class="seg" data-t="${s.start}">
      <div class="seg-time">${fmtTime(s.start)}</div>
      <div><div class="seg-text">${esc(s.text)}</div>
        <div class="seg-zh">覆盖 ${(s.coverage * 100).toFixed(0)}%
          ${s.lag != null ? ` · 滞后 ${s.lag}s` : ''}</div>
        <div class="progress" style="height:4px;margin-top:4px"><div class="progress-bar" style="width:${s.coverage * 100}%;background:${c}"></div></div>
      </div></div>`;
  }).join('');
  host.innerHTML = `
    <div class="row" style="gap:18px;align-items:center;margin-top:12px">
      <div style="font-size:34px;font-weight:700;color:${scoreColor(r.coverage * 100)};line-height:1">${(r.coverage * 100).toFixed(0)}%</div>
      <div>
        <div class="muted">整段覆盖率（跟上了多少词）</div>
        <div style="font-size:15px;margin-top:4px">
          平均滞后 <b>${r.lag == null ? '—' : r.lag + ' 秒'}</b> ·
          录音 ${r.duration} 秒 · 识别耗时 ${r.asr_seconds} 秒
        </div>
      </div>
    </div>
    <ul class="hint" style="padding-left:18px;margin:10px 0 0">
      ${(r.advice || []).map((a) => `<li>${esc(a)}</li>`).join('')}
    </ul>
    <div class="hint" style="margin-top:8px">逐句覆盖（点一句跳过去重听）：</div>
    <div class="seg-list">${segs}</div>
    <audio controls src="${url || r.recording_url}" style="width:100%;margin-top:8px"></audio>`;
  host.querySelectorAll('.seg').forEach((el) => {
    el.onclick = () => player.seekTo(Number(el.dataset.t));
  });
}

/** 给别的模块用的播放器 API（例如句子列表点一下就跳过去）。 */
export const player = {  seekTo: (sec, play = true) => { ensureVideo(); seekTo(sec, play); },
  replaySentence: (idx) => { ensureVideo(); replaySentence(idx); },
  step: (d) => { ensureVideo(); step(d); },
  getVideo: () => video,
};
window.__vnPlayer = player;

