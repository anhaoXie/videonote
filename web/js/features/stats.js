// ---------------------------------------------------------------------------
// 训练统计：我练了多少 / 有没有变好 / 最该补什么 / 接下来练什么
//   图表全部用内联 SVG 手画 —— 不引入任何前端库，离线可用。
// ---------------------------------------------------------------------------
import { VN, api, ui, store, fmtTime, esc } from '../core.js';
import { scoreColor } from '../recorder.js';
import { player } from './player.js';

const state = { data: null, days: 30, scope: 'project' };

ui.registerTab({
  id: 'stats', label: '统计', order: 60,
  async render(panel) {
    panel.innerHTML = '<div class="card"><span class="muted">统计中…</span></div>';
    await load(panel);
  },
});

function panelEl() { return document.querySelector('#content .panel'); }

async function load(panel) {
  const q = [state.scope === 'all' ? '' : (store.project ? `project_id=${store.project.id}` : ''), `days=120`]
    .filter(Boolean).join('&');
  try {
    state.data = await api.get(`/api/analytics?${q}`);
  } catch (err) {
    panel.innerHTML = `<div class="card">统计失败：${esc(err.message)}</div>`;
    return;
  }
  draw(panel);
}

function draw(panel) {
  const d = state.data;
  const ov = d.overview;
  const empty = !ov.attempts;
  panel.innerHTML = `
    <div class="card">
      <div class="row" style="justify-content:space-between;align-items:center">
        <div><h3 style="margin:0">训练统计</h3>
          <div class="muted">${state.scope === 'all' ? '所有项目' : esc(store.project?.title || '当前项目')}</div></div>
        <div class="row">
          <button class="btn small" id="s-scope">${state.scope === 'all' ? '只看当前项目' : '看所有项目'}</button>
          <button class="btn small" id="s-refresh">刷新</button>
        </div>
      </div>
    </div>

    ${empty ? `<div class="card"><div class="hint">还没有练习记录。去「复习」页练几句，或者打开「播放/跟读」录音评分，这里就有数据了。</div></div>` : ''}

    <div class="tiles">
      ${tile('练习次数', ov.attempts, '次')}
      ${tile('覆盖句子', ov.sentences, '句')}
      ${tile('平均分', ov.avg_score, '分', scoreColor(ov.avg_score))}
      ${tile('最近 20 次均分', ov.recent_avg, '分', scoreColor(ov.recent_avg),
        ov.delta == null ? '' : `${ov.delta >= 0 ? '↑' : '↓'} ${Math.abs(ov.delta)} 分`)}
      ${tile('累计练习', ov.minutes, '分钟')}
      ${tile('连续打卡', ov.streak, '天', '', `最长 ${ov.longest_streak} 天 · 累计 ${ov.active_days} 天`)}
    </div>

    <div class="card">
      <div class="row" style="justify-content:space-between;align-items:baseline">
        <b>最近 120 天：每天练了多少 / 平均几分</b>
        <span class="muted" style="font-size:11.5px">柱子=练习次数，折线=当天平均分</span>
      </div>
      <div class="chart">${chartDaily(d.daily)}</div>
      <div class="row" style="justify-content:space-between;align-items:baseline;margin-top:10px">
        <b>打卡热力图（最近 12 周）</b><span class="muted" style="font-size:11.5px">越绿练得越多</span>
      </div>
      <div class="heat">${heatmap(d.daily)}</div>
    </div>

    <div class="grid2">
      <div class="card">
        <b>分数分布</b>
        <div style="margin-top:8px">${distBars(d.distribution)}</div>
      </div>
      <div class="card">
        <b>接下来 14 天的复习量</b>
        <div class="muted" style="font-size:11.5px">SM-2 排出来的（今天 ${d.review.counts.due} 句到期，队列共 ${d.review.counts.total}）</div>
        <div style="margin-top:8px">${forecastBars(d.review.forecast)}</div>
      </div>
    </div>

    <div class="grid2">
      <div class="card">
        <b>最常漏掉的词</b>
        <div class="hint">这些词你压根没说出口 —— 多半是不熟或跟不上节奏</div>
        ${wordTable(d.weak_words.missing, '漏')}
      </div>
      <div class="card">
        <b>最常读错的词</b>
        <div class="hint">左边是你该念的，右边是识别听成的 —— 往往就差一个音</div>
        ${wrongTable(d.weak_words.wrong)}
      </div>
    </div>

    ${d.weak_phonemes.length ? `<div class="card"><b>音素级错误（音素评测开启后统计）</b>
      <div class="hint">这是最有价值的表：能直接告诉你是哪个音没发对</div>
      ${phonemeTable(d.weak_phonemes)}</div>` : ''}

    <div class="card">
      <b>最该重练的句子</b>
      <div class="hint">按历史最高分从低到高排 —— 最高分都没上去，说明一直没真正读顺</div>
      ${worstTable(d.worst_segments)}
    </div>

    ${d.by_project.length ? `<div class="card">
      <b>分项目明细</b>
      ${projectTable(d.by_project)}
    </div>` : ''}

    ${d.modes.length ? `<div class="card"><b>练习方式分布</b>
      <div class="row" style="gap:14px;flex-wrap:wrap;margin-top:6px">
        ${d.modes.map((m) => `<div><span class="muted" style="font-size:11.5px">${esc(m.label)}</span>
          <div style="font-size:20px;font-weight:600">${m.count}</div></div>`).join('')}
      </div></div>` : ''}`;

  panel.querySelector('#s-scope').onclick = () => {
    state.scope = state.scope === 'all' ? 'project' : 'all';
    load(panel);
  };
  panel.querySelector('#s-refresh').onclick = () => load(panel);
  panel.querySelectorAll('[data-goto]').forEach((el) => {
    el.onclick = async () => {
      const pid = el.dataset.pid;
      const start = Number(el.dataset.start || 0);
      if (store.project?.id !== pid) {
        try {
          const { selectProject } = await import('../app.js');
          await selectProject(pid);
        } catch (e) {
          ui.toast('先在左边切到那个项目', '', 3000);
          return;
        }
      }
      ui.showTab('player');
      setTimeout(() => { try { player.seekTo(start, true); } catch (e) { /* ignore */ } }, 200);
    };
  });
  panel.querySelectorAll('[data-enqueue]').forEach((el) => {
    el.onclick = async () => {
      await api.post(`/api/projects/${el.dataset.pid}/reviews`,
        { seg_idx: Number(el.dataset.enqueue), reason: '从统计页手动加入' });
      ui.toast('已加入复习队列', 'ok', 2500);
    };
  });
}

function tile(label, value, unit, color, sub) {
  return `<div class="tile">
    <div class="tile-label">${label}</div>
    <div class="tile-value" ${color ? `style="color:${color}"` : ''}>${value}<span class="tile-unit">${unit}</span></div>
    ${sub ? `<div class="tile-sub">${esc(sub)}</div>` : ''}
  </div>`;
}

// --------------------------------------------------------------------------
// 图表
// --------------------------------------------------------------------------
function chartDaily(daily) {
  const W = 760, H = 170, padL = 30, padR = 10, padT = 8, padB = 20;
  const iw = W - padL - padR, ih = H - padT - padB;
  const n = Math.max(1, daily.length);
  const maxA = Math.max(1, ...daily.map((d) => d.attempts));
  const bw = iw / n;
  const bars = daily.map((d, i) => {
    if (!d.attempts) return '';
    const h = Math.max(1.5, (d.attempts / maxA) * ih);
    const x = padL + i * bw + 0.4;
    return `<rect x="${x.toFixed(2)}" y="${(padT + ih - h).toFixed(2)}" width="${Math.max(1, bw - 0.8).toFixed(2)}"
      height="${h.toFixed(2)}" fill="rgba(79,140,255,.45)" rx="1">
      <title>${d.day}：${d.attempts} 次，平均 ${d.avg_score} 分</title></rect>`;
  }).join('');

  // 平均分折线（只有练过的日子才有值，断开处跳过）
  const segs = [];
  let cur = [];
  daily.forEach((d, i) => {
    if (d.attempts) {
      const x = padL + i * bw + bw / 2;
      const y = padT + ih - (Math.max(0, Math.min(100, d.avg_score)) / 100) * ih;
      cur.push([x, y]);
    } else if (cur.length) { segs.push(cur); cur = []; }
  });
  if (cur.length) segs.push(cur);
  const line = segs.map((s) => `<polyline fill="none" stroke="#22c55e" stroke-width="1.8"
      points="${s.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(' ')}" />`).join('');
  const dots = segs.flat().map(([x, y]) => `<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="1.8" fill="#22c55e" />`).join('');

  const ticks = [0, 25, 50, 75, 100].map((v) => {
    const y = padT + ih - (v / 100) * ih;
    return `<line x1="${padL}" y1="${y.toFixed(1)}" x2="${W - padR}" y2="${y.toFixed(1)}" stroke="rgba(255,255,255,.07)" />
      <text x="${padL - 5}" y="${(y + 3).toFixed(1)}" text-anchor="end" class="ax">${v}</text>`;
  }).join('');

  const labels = daily.map((d, i) => {
    if (i % 20 !== 0 && i !== daily.length - 1) return '';
    return `<text x="${(padL + i * bw + bw / 2).toFixed(1)}" y="${H - 5}" text-anchor="middle" class="ax">${d.day.slice(5)}</text>`;
  }).join('');

  return `<svg viewBox="0 0 ${W} ${H}" width="100%" height="${H}" preserveAspectRatio="none">
    ${ticks}${bars}${line}${dots}${labels}</svg>`;
}

function heatmap(daily) {
  const weeks = 12;
  const days = daily.slice(-weeks * 7);
  const maxA = Math.max(1, ...days.map((d) => d.attempts));
  // 按周分列：第 0 列是最早那周的周日
  const cells = days.map((d, i) => {
    const col = Math.floor(i / 7), row = i % 7;
    const t = d.attempts / maxA;
    const fill = d.attempts ? `rgba(34,197,94,${(0.25 + t * 0.75).toFixed(2)})` : 'rgba(255,255,255,.05)';
    return `<rect x="${col * 13}" y="${row * 13}" width="11" height="11" rx="2" fill="${fill}">
      <title>${d.day}：${d.attempts} 次</title></rect>`;
  }).join('');
  return `<svg viewBox="0 0 ${weeks * 13} ${7 * 13}" width="100%" height="${7 * 13}" style="max-width:640px">${cells}</svg>`;
}

function distBars(dist) {
  const total = Math.max(1, dist.reduce((n, x) => n + x.count, 0));
  return dist.map((x) => `
    <div class="bar-row">
      <span class="bar-label">${x.label}<span class="muted"> ${x.lo}-${x.hi}</span></span>
      <span class="bar-track"><span class="bar-fill" style="width:${(x.count / total * 100).toFixed(1)}%;background:${scoreColor((x.lo + x.hi) / 2)}"></span></span>
      <span class="bar-num">${x.count}</span>
    </div>`).join('');
}

function forecastBars(fc) {
  const max = Math.max(1, ...fc.map((f) => f.count));
  return `<div class="fc">${fc.map((f, i) => `
    <div class="fc-col" title="${f.day}：${f.count} 句">
      <div class="fc-bar" style="height:${(f.count / max * 52).toFixed(1)}px;${f.count ? '' : 'opacity:.25'}"></div>
      <div class="fc-n">${f.count || ''}</div>
      <div class="fc-d">${i === 0 ? '今' : i === 1 ? '明' : f.day.slice(8)}</div>
    </div>`).join('')}</div>`;
}

function wordTable(rows, kind) {
  if (!rows.length) return '<div class="muted" style="font-size:12px;margin-top:6px">暂无数据</div>';
  const max = Math.max(...rows.map((r) => r.count));
  return `<table class="mini" style="margin-top:6px">${rows.map((r) => `
    <tr><td class="w">${esc(r.word)}</td>
      <td><span class="bar-track sm"><span class="bar-fill" style="width:${r.count / max * 100}%;background:var(--warn)"></span></span></td>
      <td class="n">${kind} ${r.count}</td></tr>`).join('')}</table>`;
}

function wrongTable(rows) {
  if (!rows.length) return '<div class="muted" style="font-size:12px;margin-top:6px">暂无数据</div>';
  const max = Math.max(...rows.map((r) => r.count));
  return `<table class="mini" style="margin-top:6px">${rows.map((r) => `
    <tr><td class="w">${esc(r.word)}</td>
      <td class="muted">→ 听成</td><td class="w" style="color:var(--warn)">${esc(r.heard) || '？'}</td>
      <td><span class="bar-track sm"><span class="bar-fill" style="width:${r.count / max * 100}%;background:var(--warn)"></span></span></td>
      <td class="n">${r.count}</td></tr>`).join('')}</table>`;
}

function phonemeTable(rows) {
  return `<table class="mini" style="margin-top:6px">
    <tr><th>该发的音</th><th>实际发的音</th><th>类型</th><th>次数</th></tr>
    ${rows.map((r) => `<tr><td class="w">/${esc(r.expected)}/</td>
      <td class="w" style="color:var(--warn)">/${esc(r.heard) || '？'}/</td>
      <td class="muted">${{ sub: '读错', del: '漏掉', ins: '多读' }[r.type] || r.type}</td>
      <td class="n">${r.count}</td></tr>`).join('')}</table>`;
}

function worstTable(rows) {
  if (!rows.length) return '<div class="muted" style="font-size:12px;margin-top:6px">没有低于 95 分的句子 —— 数据太少或你太强了</div>';
  return `<table class="mini" style="margin-top:6px">
    <tr><th>句子</th><th>最高分</th><th>练过</th><th></th></tr>
    ${rows.map((r) => `<tr>
      <td class="txt" title="${esc(r.text)}">${esc(r.text.slice(0, 60))}${r.text.length > 60 ? '…' : ''}
        <span class="muted" style="font-size:11px;display:block">${esc(r.project_title)} · ${fmtTime(r.start)}</span></td>
      <td class="n" style="color:${scoreColor(r.best)}">${r.best.toFixed(0)}</td>
      <td class="n">${r.tries}</td>
      <td><button class="btn small ghost" data-goto data-pid="${r.project_id}" data-start="${r.start}">↗ 听</button>
        <button class="btn small ghost" data-enqueue="${r.seg_idx}" data-pid="${r.project_id}">＋复习</button></td>
    </tr>`).join('')}</table>`;
}

function projectTable(rows) {
  return `<table class="mini" style="margin-top:6px">
    <tr><th>项目</th><th>练习</th><th>平均分</th><th>最好</th><th>时长</th></tr>
    ${rows.map((p) => `<tr><td class="txt">${esc(p.title)}</td>
      <td class="n">${p.attempts}</td>
      <td class="n" style="color:${scoreColor(p.avg_score)}">${p.avg_score}</td>
      <td class="n">${p.best}</td>
      <td class="n">${p.minutes} 分</td></tr>`).join('')}</table>`;
}
