// ---------------------------------------------------------------------------
// 单词级训练：最小对立对（ship/sheep、think/sink）
//   听音辨词 —— 练耳朵：放一个词，你选是哪个。只出「能被分辨」的词对。
//   跟读对比 —— 练嘴：你读一个词，音素模型告诉你那个关键音对不对。
// 音频是 espeak 合成的机器音（离线、零成本），只作示意；
// 真正的判定发生在你自己的录音上，判分目标是 IPA 音标。
// ---------------------------------------------------------------------------
import { VN, api, ui, store, esc } from '../core.js';
import { Recorder, uploadBlob } from '../recorder.js';
import { phonemePanelHtml } from '../phoneme-ui.js';

let DATA = null;          // /api/words/pairs 的结果
let PLAN = [];            // /api/words/plan
let MODE = 'listen';      // listen | say
let CURRENT = null;       // 当前词对
let ANSWER = null;        // 听音辨词：这一轮放的是哪个词
let DONE = { right: 0, wrong: 0 };

const rec = new Recorder();

async function loadData(force) {
  if (DATA && !force) return DATA;
  DATA = await api.get('/api/words/pairs');
  try {
    PLAN = (await api.get('/api/words/plan?limit=12')).plan || [];
  } catch (e) { PLAN = []; }
  return DATA;
}

function pickPair(verifiedOnly) {
  const pool = (DATA?.pairs || []).filter((p) => (verifiedOnly ? p.audio?.ok : true));
  if (!pool.length) return null;
  // 优先练「还没练过」或「正确率低」的
  const scored = pool.map((p) => {
    const s = (MODE === 'listen' ? p.stats?.listen : p.stats?.say) || {};
    const n = s.n || 0;
    const acc = n ? (s.ok || 0) / n : -1;
    return { p, weight: (n === 0 ? 0.55 : acc) + Math.random() * 0.35 };
  }).sort((a, b) => a.weight - b.weight);
  return scored[0].p;
}

function wordAudioUrl(word, slow) {
  return `/api/words/audio?word=${encodeURIComponent(word)}${slow ? '&slow=true' : ''}`;
}

function playWord(word, slow) {
  const a = new Audio(wordAudioUrl(word, slow));
  a.play().catch(() => ui.toast('浏览器拦住了自动播放，点一下▶再试', 'err'));
  return a;
}

ui.registerTab({
  id: 'words', label: '单词', order: 36,
  render(panel) {
    panel.innerHTML = `
      <div class="card">
        <h3>最小对立对训练 <span class="muted" style="font-weight:400;font-size:12px">
          差一个音，就是另一个词</span></h3>
        <div class="hint" style="margin-bottom:8px">
          汉语的音节结构和英语不同，很多人<b>听不出</b> /iː/ 和 /ɪ/ 的差别，于是先说错、再听不出自己说错。
          这里一次只改一个音，把"差一点"变成"差很多"。
        </div>
        <div class="row" style="gap:8px;flex-wrap:wrap">
          <button class="btn" data-mode="listen">🎧 听音辨词</button>
          <button class="btn ghost" data-mode="say">🎙 跟读对比</button>
          <span class="muted" id="w-stats" style="margin-left:auto;font-size:12px"></span>
        </div>
      </div>
      <div id="w-stage"></div>
      <div class="card" id="w-plan-card" hidden>
        <h3>该练什么 <span class="muted" style="font-size:12px;font-weight:400">按你实际读错的音排序</span></h3>
        <div id="w-plan"></div>
      </div>`;

    const statsEl = panel.querySelector('#w-stats');
    const stage = panel.querySelector('#w-stage');
    const planCard = panel.querySelector('#w-plan-card');
    const planEl = panel.querySelector('#w-plan');

    const setMode = (m) => {
      MODE = m;
      panel.querySelectorAll('[data-mode]').forEach((b) => {
        b.classList.toggle('ghost', b.dataset.mode !== m);
      });
      next();
    };
    panel.querySelectorAll('[data-mode]').forEach((b) => {
      b.onclick = () => setMode(b.dataset.mode);
    });

    function renderStats() {
      const total = DATA?.total || 0;
      const verified = DATA?.verified_total || 0;
      statsEl.innerHTML = `词库 ${total} 对（听音可用 ${verified}） · `
        + `本轮 <span style="color:var(--accent2)">${DONE.right}</span> 对 / `
        + `<span style="color:var(--warn)">${DONE.wrong}</span> 错`;
    }

    function renderPlan() {
      if (!PLAN.length) { planCard.hidden = true; return; }
      planCard.hidden = false;
      planEl.innerHTML = `<div class="ph-box">` + PLAN.map((p) => `
        <div class="ph-row" style="justify-content:space-between">
          <div>
            <b>${esc(p.a)}</b> <span class="muted">vs</span> <b>${esc(p.b)}</b>
            <span class="ph-mini">/${esc(p.pa)}/ · /${esc(p.pb)}/</span>
            <span class="muted" style="font-size:11.5px">${esc(p.why)}</span>
          </div>
          <div>
            <button class="btn ghost" data-play="${esc(p.a)}" title="听 ${esc(p.a)}">▶</button>
            <button class="btn ghost" data-play="${esc(p.b)}" title="听 ${esc(p.b)}">▶</button>
            <button class="btn ghost" data-drill="${esc(p.id)}">练这个</button>
          </div>
        </div>`).join('') + `</div>`;
      planEl.querySelectorAll('[data-play]').forEach((b) => {
        b.onclick = () => playWord(b.dataset.play);
      });
      planEl.querySelectorAll('[data-drill]').forEach((b) => {
        b.onclick = () => {
          const p = (DATA?.pairs || []).find((x) => x.id === b.dataset.drill);
          if (!p) return;
          if (!p.audio?.ok && MODE === 'listen') setMode('say');
          CURRENT = p;
          renderStage();
        };
      });
    }

    function next() {
      CURRENT = pickPair(MODE === 'listen');
      renderStats();
      renderStage();
    }

    function renderStage() {
      if (!CURRENT) {
        stage.innerHTML = `<div class="card"><div class="hint">词库为空。先跑一次
          <code>tools/verify_pairs.py</code> 生成可用词对。</div></div>`;
        return;
      }
      return MODE === 'listen' ? renderListen() : renderSay();
    }

    // ---------------- 听音辨词 ----------------
    function renderListen() {
      const p = CURRENT;
      ANSWER = Math.random() < 0.5 ? p.a : p.b;
      stage.innerHTML = `
        <div class="card train">
          <div class="muted" style="font-size:12px">听一个词，它是哪一个？</div>
          <div style="font-size:12px;margin:6px 0 10px">
            <span class="ph-mini">/${esc(p.pa)}/</span> vs <span class="ph-mini">/${esc(p.pb)}/</span>
            <span class="muted">${esc(p.tip || '')}</span>
          </div>
          <div class="row" style="gap:10px;flex-wrap:wrap">
            <button class="btn ghost" id="w-play" style="font-size:18px">🔊 再放一次</button>
            <button class="btn ghost" id="w-slow" style="font-size:18px">🐢 慢速</button>
          </div>
          <div class="row" style="gap:10px;margin-top:14px;flex-wrap:wrap">
            <button class="btn" data-pick="${esc(p.a)}" style="min-width:140px;font-size:17px">${esc(p.a)}</button>
            <button class="btn" data-pick="${esc(p.b)}" style="min-width:140px;font-size:17px">${esc(p.b)}</button>
          </div>
          <div id="w-feedback" style="margin-top:10px"></div>
        </div>`;
      const play = (slow) => playWord(ANSWER, slow);
      stage.querySelector('#w-play').onclick = () => play(false);
      stage.querySelector('#w-slow').onclick = () => play(true);
      play(false);
      stage.querySelectorAll('[data-pick]').forEach((b) => {
        b.onclick = async () => {
          const chosen = b.dataset.pick;
          const correct = chosen.toLowerCase() === ANSWER.toLowerCase();
          correct ? DONE.right++ : DONE.wrong++;
          stage.querySelectorAll('[data-pick]').forEach((x) => { x.disabled = true; });
          stage.querySelector('#w-feedback').innerHTML = correct
            ? `<div style="color:var(--accent2)">✅ 对了，刚才放的是 <b>${esc(ANSWER)}</b></div>`
            : `<div style="color:var(--warn)">❌ 刚才放的是 <b>${esc(ANSWER)}</b>，你选了 ${esc(chosen)}<br>
                 <span class="muted">${esc(p.tip || '')}</span></div>`;
          renderStats();
          try {
            await api.post('/api/words/listen', {
              pair_id: p.id, chosen, answer: ANSWER, seconds: 0,
            });
          } catch (e) { /* 记账失败不影响练习 */ }
          setTimeout(next, correct ? 900 : 2200);
        };
      });
    }

    // ---------------- 跟读对比 ----------------
    function renderSay() {
      const p = CURRENT;
      const target = p.a;      // 默认练第一个词，可切换
      stage.innerHTML = `
        <div class="card train">
          <div class="muted" style="font-size:12px">读一遍这个词，我告诉你那个关键音对不对</div>
          <div class="row" style="gap:8px;margin:10px 0;flex-wrap:wrap">
            <button class="btn ghost" data-word="${esc(p.a)}">▶ ${esc(p.a)}</button>
            <button class="btn ghost" data-word="${esc(p.b)}">▶ ${esc(p.b)}</button>
            <button class="btn ghost" data-slow="${esc(p.a)}">🐢 慢速</button>
          </div>
          <div class="hint" style="font-size:12px">
            这一对练的是 <span class="ph-mini">/${esc(p.pa)}/</span> 和
            <span class="ph-mini">/${esc(p.pb)}/</span>：${esc(p.tip || '')}
          </div>
          <div class="row" style="gap:10px;margin-top:12px;flex-wrap:wrap">
            <button class="btn ghost" id="w-rec">🎙 开始录音</button>
            <span class="muted" id="w-target" style="font-size:12px"></span>
          </div>
          <audio id="w-mine" controls hidden style="width:100%;margin-top:10px"></audio>
          <div id="w-result" style="margin-top:10px"></div>
        </div>`;
      let targetWord = target;
      const targetEl = stage.querySelector('#w-target');
      const setTarget = (w) => {
        targetWord = w;
        targetEl.innerHTML = `这次要读：<b>${esc(w)}</b>（关键音 /${esc(w === p.a ? p.pa : p.pb)}/）`;
      };
      setTarget(targetWord);
      stage.querySelectorAll('[data-word]').forEach((b) => {
        b.onclick = () => { playWord(b.dataset.word); setTarget(b.dataset.word); };
      });
      stage.querySelectorAll('[data-slow]').forEach((b) => {
        b.onclick = () => playWord(b.dataset.slow, true);
      });

      const recBtn = stage.querySelector('#w-rec');
      const mine = stage.querySelector('#w-mine');
      const result = stage.querySelector('#w-result');
      let blob = null;
      recBtn.onclick = async () => {
        if (rec.recording) {
          const { blob: b, url } = await rec.stop();
          blob = b;
          mine.src = url; mine.hidden = false;
          recBtn.innerHTML = '🎙 重录';
          result.innerHTML = '<div class="hint">录好了，正在本地评测…</div>';
          try {
            const r = await uploadBlob('/api/words/say', blob,
              { pair_id: p.id, word: targetWord });
            renderSayResult(result, r, p);
            r.key_hit ? DONE.right++ : DONE.wrong++;
            renderStats();
          } catch (e) {
            result.innerHTML = `<div class="hint" style="color:var(--warn)">评测失败：${esc(e.message || e)}</div>`;
          }
          return;
        }
        try {
          await rec.start();
          recBtn.innerHTML = '<span class="rec-dot"></span>说这个词，说完点停止';
          result.innerHTML = '';
        } catch (e) {
          ui.toast('拿不到麦克风权限', 'err');
        }
      };
    }

    function renderSayResult(box, r, p) {
      const keyColor = r.key_hit ? 'var(--accent2)' : 'var(--warn)';
      const phon = {
        ok: true,
        seconds: r.seconds,
        words: [{ word: r.word, ok: true, phones: r.phones, accuracy: r.accuracy,
                  insertions: [], problem: 'word' }],
        errors: (r.phones || []).filter((x) => x.status === 'sub' || x.status === 'del')
          .map((x) => ({ expected: x.p, heard: x.heard, type: x.status === 'del' ? 'del' : 'sub' })),
      };
      box.innerHTML = `
        <div style="font-size:15px">
          关键音 <span class="ph-mini">/${esc(r.key_phone)}/</span>
          <b style="color:${keyColor}">${r.key_hit ? '读对了 ✅' : '需要再练 ❌'}</b>
          <span class="muted" style="font-size:12px">整词准确率 ${r.accuracy}%</span>
        </div>
        ${phonemePanelHtml(phon)}
        ${(r.tips || []).length ? `<ul class="hint" style="margin:8px 0 0 0;padding-left:18px">
          ${(r.tips || []).map((t) => `<li>${esc(t)}</li>`).join('')}</ul>` : ''}
        <div class="hint" style="font-size:11.5px;margin-top:6px">
          参考音是机器音（只作示意）。你要模仿的是 /${esc(r.key_phone)}/ 的发音位置，不是这个嗓音。
        </div>`;
    }

    // 初始化
    (async () => {
      stage.innerHTML = '<div class="card"><div class="hint">正在读词库…</div></div>';
      try {
        await loadData(true);
      } catch (e) {
        stage.innerHTML = `<div class="card"><div class="hint" style="color:var(--warn)">
          词库加载失败：${esc(e.message || e)}</div></div>`;
        return;
      }
      setMode('listen');
      renderPlan();
    })();
  },
  onHide() { if (rec.recording) rec.stop().catch(() => {}); },
});

