// ---------------------------------------------------------------------------
// 复习（今天该练什么）
//   规则：跟读低于 80 分、或你收藏过的句子 -> 自动排队；SM-2 决定什么时候再来。
//   一次只面对一句：听原声 -> 录 -> 立刻评分 -> 看下次什么时候见。
// ---------------------------------------------------------------------------
import { VN, api, ui, store, on, fmtTime, esc } from '../core.js';
import { Recorder, uploadBlob, wordDiffHtml, scoreColor } from '../recorder.js';
import { phonemePanelHtml } from '../phoneme-ui.js';
import { player } from './player.js';

const rec = new Recorder({
  onLevel: (p) => {
    const el = document.getElementById('r-meter-fill');
    if (el) el.style.width = `${Math.min(100, p * 140)}%`;
  },
});

const state = {
  queue: [], counts: {}, pos: 0, allProjects: false,
  blob: null, url: null, busy: false, result: null, includeFuture: false,
};

ui.registerTab({
  id: 'review', label: '复习', order: 38,
  async render(panel) {
    panel.innerHTML = '<div class="card"><span class="muted">加载复习队列…</span></div>';
    await load(panel);
  },
});

const STATUS_LABEL = { new: '新进队列', learning: '还在学', review: '复习中', suspended: '已暂停' };

function panelEl() { return document.querySelector('#content .panel'); }

async function load(panel) {
  if (!store.project && !state.allProjects) {
    panel.innerHTML = `<div class="card"><h3 style="margin-top:0">复习</h3>
      <div class="hint">左边先选一个项目；或者点下面按钮看所有项目的待复习句子。</div>
      <button class="btn small" id="r-all2">看所有项目</button></div>`;
    const b = panel.querySelector('#r-all2');
    if (b) b.onclick = () => { state.allProjects = true; load(panel); };
    return;
  }
  try {
    const d = state.allProjects
      ? await api.get('/api/reviews/due?limit=40')
      : await api.get(`/api/projects/${store.project.id}/reviews?limit=40&include_future=${state.includeFuture}`);
    state.queue = d.queue || [];
    state.counts = d.counts || {};
    state.pos = 0;
    state.result = null;
    state.blob = null;
    draw(panel);
  } catch (err) {
    panel.innerHTML = `<div class="card">加载失败：${esc(err.message)}</div>`;
  }
}

async function reload() {
  const p = panelEl();
  if (p) await load(p);
}

function draw(panel) {
  const c = state.counts;
  const doneToday = c.done_today || 0;
  panel.innerHTML = `
    <div class="card">
      <div class="row" style="justify-content:space-between;align-items:flex-start;gap:12px">
        <div>
          <h3 style="margin:0">复习队列</h3>
          <div class="muted">今天待复习 <b style="color:var(--accent)">${c.due ?? 0}</b> 张卡
            （句子 ${c.due_sentences ?? c.due ?? 0} · 单词 ${c.due_words ?? 0}）·
            队列共 ${c.total ?? 0} · 今日已练 ${doneToday} · 复习中 ${c.reviewing ?? 0} · 还在学 ${c.learning ?? 0}</div>
        </div>
        <div class="row">
          <button class="btn small" id="r-future">${state.includeFuture ? '只看今天到期的' : '预览全部（含未到期）'}</button>
          <button class="btn small" id="r-all">${state.allProjects ? '只看当前项目' : '看所有项目'}</button>
          <button class="btn small" id="r-sync" title="把历史低分句和收藏句补进队列">重新同步</button>
        </div>
      </div>
      <div class="hint" style="margin-top:8px">
        排队规则：跟读<b>低于 80 分</b>、或你<b>收藏过</b>的句子自动进入队列；「单词课」里练砸的词也进同一个队列，
        按约 <b>每 2 句夹 1 个词</b>混排。读得好就往后推（明天 → 3 天 → 8 天 → …），
        读砸了 10 分钟后再来一次。<b>空格</b>=听原声，<b>R</b>=开始/停止录音，停止后自动评分，<b>Enter</b>=下一张。
      </div>
    </div>
    <div id="r-train"></div>
    <div id="r-rest"></div>`;

  panel.querySelector('#r-all').onclick = () => { state.allProjects = !state.allProjects; reload(); };
  panel.querySelector('#r-future').onclick = () => { state.includeFuture = !state.includeFuture; reload(); };
  panel.querySelector('#r-sync').onclick = async () => {
    const pid = state.allProjects ? '' : (store.project?.id || '');
    const r = await api.post('/api/reviews/sync', { project_id: pid });
    const w = await api.post('/api/reviews/words/sync', {});
    ui.toast(`已同步：新增 ${r.created} 句 / ${w.created} 个词`, 'ok');
    await reload();
  };

  drawTrain(panel.querySelector('#r-train'));
  drawRest(panel.querySelector('#r-rest'));
}

function drawTrain(host) {
  const item = state.queue[state.pos];
  if (!item) {
    host.innerHTML = `<div class="card">
      <h3 style="margin-top:0">🎉 今天的复习做完了</h3>
      <div class="hint">${state.includeFuture
        ? '队列里暂时没有卡片。去「逐句转写」页收藏几句，或者跟读评分低于 80 分，句子就会自动排进来；「单词课」里练砸的词也会进来。'
        : '队列里的卡都还没到期（间隔重复就是为了让你别一次练太多）。想提前练就点上面的「预览全部」。'}</div>
    </div>`;
    return;
  }
  if (item.card_type === 'word') return drawWordTrain(host, item);
  const firstLine = state.queue[0] === item;
  host.innerHTML = `
    <div class="card train">
      <div class="row" style="justify-content:space-between;align-items:center">
        <div class="muted" style="font-size:11.5px">
          第 ${state.pos + 1} / ${state.queue.length} 句 ·
          ${esc(item.project_title || '')} · ${fmtTime(item.start)}-${fmtTime(item.end)}
          ${item.speaker ? ` · <span class="spk" style="background:${spkColor(item.speaker)}22;color:${spkColor(item.speaker)}">${esc(item.speaker)}</span>` : ''}
        </div>
        <div class="muted" style="font-size:11.5px">
          <span class="badge-mini">${STATUS_LABEL[item.status] || item.status}</span>
          ${item.best_score ? ` · 最高 <b style="color:${scoreColor(item.best_score)}">${item.best_score.toFixed(0)}</b>` : ''}
          ${item.reps ? ` · 复习过 ${item.reps} 次` : ''}
          ${item.lapses ? ` · 掉队 ${item.lapses} 次` : ''}
        </div>
      </div>

      <div class="seg-text" style="font-size:20px;line-height:1.6;margin:12px 0 4px">${esc(item.text) || '<span class="muted">（这句没有文本）</span>'}</div>
      ${item.translation ? `<div class="seg-zh" style="font-size:14px">${esc(item.translation)}</div>` : ''}
      <div class="muted" style="font-size:11.5px;margin-top:6px">进队列原因：${esc(item.reason || '—')}</div>

      <audio id="r-audio" src="${item.audio_url}" preload="none" style="display:none"></audio>

      <div class="player-bar" style="margin-top:12px">
        <button class="btn small primary" id="r-play">🔊 听原声 <span class="muted">(空格)</span></button>
        <button class="btn small" id="r-slow">🐢 慢速</button>
        <button class="btn small" id="r-rec">🎙 录音 <span class="muted">(R)</span></button>
        <span class="grow"></span>
        <button class="btn small ghost" id="r-open">↗ 在播放器里打开</button>
        <button class="btn small ghost" id="r-skip">明天再说</button>
        <button class="btn small ghost" id="r-done">✓ 已掌握</button>
      </div>

      <div class="meter-wrap" id="r-meter"><div class="meter-fill" id="r-meter-fill"></div></div>
      <div id="r-result" class="train-result"></div>
      <div class="row" id="r-next-row" style="margin-top:12px;${state.result ? '' : 'display:none'}">
        <button class="btn small primary" id="r-next">下一句 <span class="muted">(Enter)</span></button>
        <button class="btn small" id="r-again">再练一遍</button>
        ${firstLine ? '' : '<span class="grow"></span><button class="btn small ghost" id="r-restart">从头开始</button>'}
      </div>
    </div>`;

  const audio = host.querySelector('#r-audio');
  const recBtn = host.querySelector('#r-rec');
  const result = host.querySelector('#r-result');
  let rate = 1;

  const play = () => { audio.currentTime = 0; audio.playbackRate = rate; audio.play().catch(() => ui.toast('听不到原声：这个句子还没切片', 'err')); };
  host.querySelector('#r-play').onclick = play;
  host.querySelector('#r-slow').onclick = () => {
    rate = rate === 1 ? 0.7 : 1;
    host.querySelector('#r-slow').textContent = rate === 1 ? '🐢 慢速' : '🐇 常速';
    play();
  };

  recBtn.onclick = async () => {
    if (rec.recording) { await finishRecording(host, item, result); return; }
    try {
      await rec.start();
      audio.pause();
      recBtn.innerHTML = '<span class="rec-dot"></span>停止并评分';
      recBtn.classList.add('danger');
      result.innerHTML = '';
    } catch (e) {
      ui.toast('拿不到麦克风权限：请在浏览器地址栏左边允许麦克风', 'err', 8000);
    }
  };

  host.querySelector('#r-open').onclick = () => {
    ui.showTab('player');
    setTimeout(() => { try { player.seekTo(item.start, true); } catch (e) { /* ignore */ } }, 120);
  };
  host.querySelector('#r-skip').onclick = async () => {
    await api.post(`/api/reviews/${item.id}/snooze`, { days: 1 });
    ui.toast('好，明天再练这句', 'ok', 2000);
    state.pos += 1;
    state.result = null;
    draw(panelEl());
  };
  host.querySelector('#r-done').onclick = async () => {
    await api.post(`/api/reviews/${item.id}/suspend`, { suspended: true });
    ui.toast('已移出复习队列（句库里的收藏还在）', 'ok', 2500);
    state.pos += 1;
    state.result = null;
    await reload();
  };
  const next = () => {
    state.pos += 1;
    state.result = null;
    state.blob = null;
    draw(panelEl());
  };
  const nextBtn = host.querySelector('#r-next');
  if (nextBtn) nextBtn.onclick = next;
  const againBtn = host.querySelector('#r-again');
  if (againBtn) againBtn.onclick = () => { state.blob = null; state.result = null; draw(panelEl()); };
  const restartBtn = host.querySelector('#r-restart');
  if (restartBtn) restartBtn.onclick = () => { state.pos = 0; state.result = null; draw(panelEl()); };

  if (state.result) renderResult(result, state.result);
  host._next = next;
  host._play = play;
  host._toggleRec = () => recBtn.click();
  host._item = item;
}

// ---------------------------------------------------------------------------
// 单词卡（最小对立对）
//   跟句子卡同一套流程：听 -> 录 -> 判 -> 排下次。区别只在判定用音素模型，
//   看的是「那个关键音有没有发对」（think 读成 sink 时整词准确率还有 50%，
//   只看整词分数会把这个问题放过去）。
//   注意：/api/words/say 内部已经推进了这张卡的 SM-2，前端不再重复调 grade。
// ---------------------------------------------------------------------------
function drawWordTrain(host, item) {
  const firstCard = state.queue[0] === item;
  const pair = item.pair || {};
  host.innerHTML = `
    <div class="card train">
      <div class="row" style="justify-content:space-between;align-items:center">
        <div class="muted" style="font-size:11.5px">
          第 ${state.pos + 1} / ${state.queue.length} 张 ·
          <span class="badge-mini">单词卡</span> ${esc(item.group || '最小对立对')}
        </div>
        <div class="muted" style="font-size:11.5px">
          <span class="badge-mini">${STATUS_LABEL[item.status] || item.status}</span>
          ${item.best_score ? ` · 最高 <b style="color:${scoreColor(item.best_score)}">${item.best_score.toFixed(0)}</b>` : ''}
          ${item.reps ? ` · 复习过 ${item.reps} 次` : ''}
          ${item.lapses ? ` · 掉队 ${item.lapses} 次` : ''}
        </div>
      </div>

      <div class="row" style="align-items:baseline;gap:14px;margin:14px 0 2px;flex-wrap:wrap">
        <span style="font-size:34px;font-weight:700;letter-spacing:.5px">${esc(item.word)}</span>
        ${item.key_phone ? `<span class="ph-mini" style="font-size:15px">/${esc(item.key_phone)}/</span>` : ''}
        <span class="grow"></span>
        <span class="muted" style="font-size:12.5px">对照：<b>${esc(item.other || '')}</b>
          ${pair.pa && pair.pb ? `（/${esc(pair.pa)}/ vs /${esc(pair.pb)}/）` : ''}</span>
      </div>
      ${item.tip ? `<div class="hint" style="margin-top:4px">👄 ${esc(item.tip)}</div>` : ''}
      <div class="muted" style="font-size:11.5px;margin-top:6px">进队列原因：${esc(item.reason || '—')}</div>

      <audio id="r-audio" src="${item.audio_url}" preload="none" style="display:none"></audio>
      <audio id="r-audio2" src="${item.other_audio_url || ''}" preload="none" style="display:none"></audio>

      <div class="player-bar" style="margin-top:12px">
        <button class="btn small primary" id="r-play">🔊 听这个词 <span class="muted">(空格)</span></button>
        <button class="btn small" id="r-play2">🔉 听对照词</button>
        <button class="btn small" id="r-slow">🐢 慢速</button>
        <button class="btn small" id="r-rec">🎙 录音 <span class="muted">(R)</span></button>
        <span class="grow"></span>
        <button class="btn small ghost" id="r-skip">明天再说</button>
        <button class="btn small ghost" id="r-done">✓ 已掌握</button>
      </div>

      <div class="meter-wrap" id="r-meter"><div class="meter-fill" id="r-meter-fill"></div></div>
      <div id="r-result" class="train-result"></div>
      <div class="row" id="r-next-row" style="margin-top:12px;${state.result ? '' : 'display:none'}">
        <button class="btn small primary" id="r-next">下一张 <span class="muted">(Enter)</span></button>
        <button class="btn small" id="r-again">再读一遍</button>
        ${firstCard ? '' : '<span class="grow"></span><button class="btn small ghost" id="r-restart">从头开始</button>'}
      </div>
    </div>`;

  const audio = host.querySelector('#r-audio');
  const audio2 = host.querySelector('#r-audio2');
  const recBtn = host.querySelector('#r-rec');
  const result = host.querySelector('#r-result');
  const slowBtn = host.querySelector('#r-slow');

  const play = () => {
    audio.currentTime = 0;
    audio.play().catch(() => ui.toast('听不到示范音：espeak 合成不可用', 'err'));
  };
  const playOther = () => {
    if (!audio2.src) return;
    audio2.currentTime = 0;
    audio2.play().catch(() => {});
  };

  host.querySelector('#r-play').onclick = play;
  host.querySelector('#r-play2').onclick = playOther;
  slowBtn.onclick = () => {
    // 慢速是后端另一个 wpm 的合成缓存，换 src 即可
    const slow = !audio.src.includes('slow=true');
    audio.src = `${item.audio_url}&slow=${slow ? 'true' : 'false'}`;
    slowBtn.textContent = slow ? '🐇 常速' : '🐢 慢速';
    play();
  };

  recBtn.onclick = async () => {
    if (rec.recording) { await finishWordRecording(host, item, result); return; }
    try {
      await rec.start();
      audio.pause();
      recBtn.innerHTML = '<span class="rec-dot"></span>停止并评分';
      recBtn.classList.add('danger');
      result.innerHTML = '';
    } catch (e) {
      ui.toast('拿不到麦克风权限：请在浏览器地址栏左边允许麦克风', 'err', 8000);
    }
  };

  host.querySelector('#r-skip').onclick = async () => {
    await api.post(`/api/reviews/words/${item.id}/snooze`, { days: 1 });
    ui.toast('好，明天再练这个词', 'ok', 2000);
    state.pos += 1;
    state.result = null;
    draw(panelEl());
  };
  host.querySelector('#r-done').onclick = async () => {
    await api.post(`/api/reviews/words/${item.id}/suspend`, { suspended: true });
    ui.toast('已把这个词移出复习队列', 'ok', 2500);
    state.pos += 1;
    state.result = null;
    await reload();
  };

  const next = () => {
    state.pos += 1;
    state.result = null;
    state.blob = null;
    draw(panelEl());
  };
  host.querySelector('#r-next').onclick = next;
  host.querySelector('#r-again').onclick = () => { state.blob = null; state.result = null; draw(panelEl()); };
  const restartBtn = host.querySelector('#r-restart');
  if (restartBtn) restartBtn.onclick = () => { state.pos = 0; state.result = null; draw(panelEl()); };

  if (state.result) renderWordResult(result, state.result, item);
  host._next = next;
  host._play = play;
  host._toggleRec = () => recBtn.click();
  host._item = item;
}

async function finishWordRecording(host, item, result) {
  const { blob, url } = await rec.stop();
  state.blob = blob;
  state.url = url;
  const btn = host.querySelector('#r-rec');
  btn.innerHTML = '🎙 录音 <span class="muted">(R)</span>';
  btn.classList.remove('danger');
  btn.disabled = true;
  result.innerHTML = `<div class="hint">音素评测中…
    <audio controls src="${url}" style="display:block;width:100%;margin-top:6px"></audio></div>`;
  try {
    const r = await uploadBlob('/api/words/say', blob, {
      pair_id: item.pair_id, word: item.word,
    });
    state.result = r;
    // /words/say 已经推进了这张卡，这里只是把新状态回填到界面上
    const rev = r.review || {};
    if (rev.status) item.status = rev.status;
    if (rev.reps != null) item.reps = rev.reps;
    if (rev.lapses != null) item.lapses = rev.lapses;
    if (rev.interval_days != null) item.interval_days = rev.interval_days;
    item.best_score = Math.max(item.best_score || 0, r.word_score || 0);
    renderWordResult(result, r, item);
    const row = host.querySelector('#r-next-row');
    if (row) row.style.display = '';
    ui.toast(`关键音 /${r.key_phone}/ ${r.key_hit ? '读对了' : '没过关'}`
      + (rev.next_due ? ` · 下次 ${fmtDue(rev.next_due)}` : ''), r.key_hit ? 'ok' : '', 3200);
  } catch (err) {
    // 音素模型不可用时退化成自评：别让这张卡变成死路
    result.innerHTML = `<div class="hint" style="color:var(--warn)">音素评测不可用：${esc(err.message)}</div>
      <div class="row" style="gap:8px;margin-top:8px;align-items:center">
        <span class="muted" style="font-size:12px">自己判一下：</span>
        <button class="btn small" id="r-self-ok">读对了</button>
        <button class="btn small" id="r-self-bad">还不行</button>
      </div>`;
    const self = async (score) => {
      try {
        const g = await api.post(`/api/reviews/words/${item.id}/grade`, { score });
        state.result = { self: true, score, review: g };
        item.status = g.status || item.status;
        item.best_score = Math.max(item.best_score || 0, score);
        renderWordResult(result, state.result, item);
        const row = host.querySelector('#r-next-row');
        if (row) row.style.display = '';
      } catch (e) {
        result.innerHTML = `<div class="hint" style="color:var(--err)">记录失败：${esc(e.message)}</div>`;
      }
    };
    const okBtn = result.querySelector('#r-self-ok');
    const badBtn = result.querySelector('#r-self-bad');
    if (okBtn) okBtn.onclick = () => self(95);
    if (badBtn) badBtn.onclick = () => self(30);
  } finally {
    btn.disabled = false;
  }
}

function renderWordResult(host, r, item) {
  const rev = r.review || {};
  const nextLine = rev.next_due
    ? (rev.interval_days >= 1
      ? `<b style="color:var(--ok)">掌握得不错</b>，${Number(rev.interval_days).toFixed(0)} 天后再练这个词（${fmtDue(rev.next_due)}）`
      : `<b style="color:var(--warn)">还没过关</b>，10 分钟后再来一次`)
    : '';
  if (r.self) {
    host.innerHTML = `
      <div class="row" style="align-items:center;gap:16px;margin-top:12px">
        <div style="font-size:40px;font-weight:700;color:${scoreColor(r.score)};line-height:1">${r.score}</div>
        <div class="muted" style="font-size:12px">自评（音素模型这次用不了）</div>
      </div>
      ${nextLine ? `<div class="hint" style="margin-top:8px">${nextLine}</div>` : ''}`;
    return;
  }
  const phon = {
    ok: true,
    seconds: r.seconds,
    words: [{ word: r.word, ok: true, phones: r.phones, accuracy: r.accuracy,
              insertions: [], problem: 'word' }],
    errors: (r.phones || []).filter((x) => x.status === 'sub' || x.status === 'del')
      .map((x) => ({ expected: x.p, heard: x.heard, type: x.status === 'del' ? 'del' : 'sub' })),
  };
  host.innerHTML = `
    <div class="row" style="align-items:center;gap:14px;margin-top:12px">
      <div style="font-size:40px;font-weight:700;color:${scoreColor(r.word_score ?? r.score)};line-height:1">${Math.round(r.word_score ?? r.score ?? 0)}</div>
      <div>
        <div style="font-size:15px">关键音 <span class="ph-mini">/${esc(r.key_phone)}/</span>
          <b style="color:${r.key_hit ? 'var(--accent2)' : 'var(--warn)'}">${r.key_hit ? '读对了 ✅' : '需要再练 ❌'}</b></div>
        <div class="muted" style="font-size:12px">整词准确率 ${r.accuracy}% ·
          听到的是 ${esc((r.heard || []).join(' ')) || '（没识别到语音）'}</div>
      </div>
    </div>
    ${nextLine ? `<div class="hint" style="margin-top:8px">${nextLine}</div>` : ''}
    ${phonemePanelHtml(phon)}
    ${(r.tips || []).length ? `<ul class="hint" style="margin:8px 0 0 0;padding-left:18px">
      ${(r.tips || []).map((t) => `<li>${esc(t)}</li>`).join('')}</ul>` : ''}
    <div class="row" style="gap:10px;margin-top:8px;align-items:center">
      <span class="muted" style="font-size:11.5px">我的录音：</span>
      <audio controls src="${state.url || ''}" style="flex:1;min-width:180px"></audio>
    </div>`;
}

async function finishRecording(host, item, result) {
  const { blob, url } = await rec.stop();
  state.blob = blob;
  state.url = url;
  const btn = host.querySelector('#r-rec');
  btn.innerHTML = '🎙 录音 <span class="muted">(R)</span>';
  btn.classList.remove('danger');
  btn.disabled = true;
  result.innerHTML = `<div class="hint">本地识别中…（第一次要加载模型，十几秒正常）
    <audio controls src="${url}" style="display:block;width:100%;margin-top:6px"></audio></div>`;
  try {
    const r = await uploadBlob(`/api/projects/${item.project_id}/score`, blob, {
      seg_idx: String(item.seg_idx), mode: 'repeat',
    });
    state.result = r;
    // 本地更新这一条的进度信息，避免整页重画掉录音
    item.best_score = Math.max(item.best_score || 0, r.score);
    if (r.review && !r.review.error) {
      item.status = r.review.status || item.status;
      item.reps = r.review.reps ?? item.reps;
      item.lapses = r.review.lapses ?? item.lapses;
      item.interval_days = r.review.interval_days;
      item.next_due = r.review.next_due || r.review.due_at;
    }
    renderResult(result, r);
    const row = host.querySelector('#r-next-row');
    if (row) row.style.display = '';
    const head = host.querySelector('.card.train .row .muted');
    void head;
    ui.toast(`得分 ${r.score}${r.review?.next_due ? ` · 下次 ${fmtDue(r.review.next_due)}` : ''}`,
      r.score >= 80 ? 'ok' : '', 3000);
  } catch (err) {
    result.innerHTML = `<div class="hint" style="color:var(--err)">评分失败：${esc(err.message)}</div>`;
  } finally {
    btn.disabled = false;
  }
}

function renderResult(host, r) {
  const d = r.detail || {};
  const bar = (label, v) => `
    <div style="flex:1;min-width:110px">
      <div class="muted" style="font-size:11px">${label} ${v}%</div>
      <div class="progress" style="height:5px"><div class="progress-bar" style="width:${v}%;background:${scoreColor(v)}"></div></div>
    </div>`;
  const rev = r.review || {};
  const nextLine = rev.next_due
    ? (rev.interval_days >= 1
      ? `<b style="color:var(--ok)">掌握得不错</b>，${rev.interval_days.toFixed(0)} 天后再复习这句（${fmtDue(rev.next_due)}）`
      : `<b style="color:var(--warn)">还差一点</b>，10 分钟后再来一次`)
    : '';
  host.innerHTML = `
    <div class="row" style="align-items:center;gap:16px;margin-top:12px">
      <div style="font-size:44px;font-weight:700;color:${scoreColor(r.score)};line-height:1">${r.score}</div>
      <div style="flex:1"><div class="row" style="gap:10px">${bar('准确', r.accuracy)}${bar('完整', r.completeness)}${bar('流利', r.fluency)}</div></div>
    </div>
    ${nextLine ? `<div class="hint" style="margin-top:8px">${nextLine}</div>` : ''}
    <div style="margin-top:10px">
      <div class="muted" style="font-size:11.5px">听到的是：</div>
      <div class="seg-text">${esc(r.transcript) || '<span class="muted">（没识别到语音）</span>'}</div>
      <div class="muted" style="font-size:11.5px;margin-top:8px">逐词对比（绿=对 黄=读错 红=漏读 紫=多说）：</div>
      <div class="seg-text" style="font-size:16px;line-height:1.9">${wordDiffHtml(d.words)}</div>
    </div>
    ${phonemePanelHtml(r.phonemes)}
    <ul class="hint" style="margin:8px 0 0 0;padding-left:18px">
      ${(r.advice || []).map((a) => `<li>${esc(a)}</li>`).join('')}
    </ul>
    <div class="row" style="gap:10px;margin-top:8px;align-items:center">
      <span class="muted" style="font-size:11.5px">我的录音：</span>
      <audio controls src="${state.url || r.recording_url}" style="flex:1;min-width:180px"></audio>
    </div>`;
}

function drawRest(host) {
  const rest = state.queue.slice(state.pos + 1);
  if (!rest.length) { host.innerHTML = ''; return; }
  const words = rest.filter((x) => x.card_type === 'word').length;
  host.innerHTML = `<div class="card">
    <div class="row" style="justify-content:space-between"><b>接下来还有 ${rest.length} 张</b>
      <span class="muted" style="font-size:11.5px">${words ? `其中 ${words} 张单词卡 · ` : ''}点句子可以跳到播放器的对应位置</span></div>
    <div class="rq-list">
      ${rest.map((it, i) => `
        <div class="rq-item" data-i="${i}">
          <span class="rq-n">${state.pos + 2 + i}</span>
          <span class="rq-text">${it.card_type === 'word'
            ? `<span class="badge-mini" style="margin-right:6px">词</span>${esc(it.word)} <span class="muted" style="font-size:11.5px">/${esc(it.key_phone || '')}/</span>`
            : esc(it.text)}</span>
          <span class="rq-meta">${it.best_score ? `<b style="color:${scoreColor(it.best_score)}">${it.best_score.toFixed(0)}</b>` : '—'}
            ${it.status === 'learning' ? ' · 学习中' : ''}</span>
          ${it.card_type === 'word' ? '' : '<button class="btn small ghost" data-act="goto">↗</button>'}
          <button class="btn small ghost" data-act="first">轮到这张</button>
        </div>`).join('')}
    </div>
    <div class="hint">「轮到这张」= 把当前训练卡换成这张（不用按顺序做）。</div>
  </div>`;
  host.querySelectorAll('.rq-item').forEach((el) => {
    const idx = Number(el.dataset.i) + state.pos + 1;
    el.querySelector('[data-act="first"]').onclick = (e) => {
      e.stopPropagation();
      const [item] = state.queue.splice(idx, 1);
      state.queue.splice(state.pos, 0, item);
      state.result = null;
      draw(panelEl());
    };
    const goto = el.querySelector('[data-act="goto"]');
    if (goto) goto.onclick = (e) => {
      e.stopPropagation();
      const item = state.queue[idx];
      if (!item) return;
      if (state.allProjects && store.project?.id !== item.project_id) {
        ui.toast('这句在别的项目里，先在左边切到那个项目', '', 3000);
        return;
      }
      ui.showTab('player');
      setTimeout(() => { try { player.seekTo(item.start, true); } catch (err) { /* ignore */ } }, 120);
    };
  });
}

function fmtDue(ts) {
  if (!ts) return '';
  const t = String(ts).replace('T', ' ');
  const d = new Date(String(ts).length <= 19 ? `${ts}` : ts);
  if (Number.isNaN(d.getTime())) return t;
  const diff = (d - new Date()) / 1000;
  if (diff < 3600) return `${Math.max(1, Math.round(diff / 60))} 分钟后`;
  if (diff < 86400) return `${Math.round(diff / 3600)} 小时后`;
  return `${Math.round(diff / 86400)} 天后`;
}

function spkColor(name) {
  let h = 0;
  for (const ch of String(name)) h = (h * 31 + ch.charCodeAt(0)) % 360;
  return `hsl(${h} 70% 60%)`;
}

// 键盘：空格=听，R=录音，Enter=下一句
on('keydown', ({ event: ev }) => {
  if (!document.querySelector('#tabs .tab.active[data-id="review"]')) return;
  const host = document.querySelector('.card.train');
  if (!host) return;
  if (ev.code === 'Space') { ev.preventDefault(); host._play?.(); }
  else if (ev.key === 'r' || ev.key === 'R') { ev.preventDefault(); host._toggleRec?.(); }
  else if (ev.key === 'Enter') { ev.preventDefault(); host._next?.(); }
});

on('projectChanged', () => { state.queue = []; state.pos = 0; state.result = null; state.allProjects = false; });
