// ---------------------------------------------------------------------------
// 课程工坊：一段文本 / 一个主题 -> 分集教学视频（画面 + 配音 + 合成）
//
// 生成过程跑在后端 worker 里（复用 jobs 队列），这里只负责提交 + 轮询进度。
// 课程不属于任何项目，所以这个标签页在没有选中项目时也能用。
// ---------------------------------------------------------------------------
import { VN, api, ui, store, esc } from '../core.js';

const state = { jobId: '', timer: 0, busy: false };

ui.registerTab({
  id: 'course', label: '课程工坊', order: 36,

  async render(panel) {
    panel.innerHTML = `
      <div class="card course-ui">
        <h3>把一段文本变成一套教学视频</h3>
        <div class="hint">
          给一个主题，或直接粘贴一段文稿 / 一本书的简介。会先出分集大纲，
          再把每集拆成「一句口播 + 一屏画面」，配好语音合成成 MP4。
        </div>
        <textarea id="c-src" rows="6" style="width:100%;margin-top:8px"
          placeholder="例：间隔重复为什么有效，以及怎么制定复习计划&#10;或直接粘贴一段书稿"></textarea>
        <div class="row" style="gap:14px;margin-top:10px;flex-wrap:wrap;align-items:center">
          <label>集数 <input id="c-eps" type="number" value="3" min="1" max="20" style="width:64px"></label>
          <label>每集目标时长（分钟） <input id="c-min" type="number" value="3" min="1" max="30" style="width:64px"></label>
          <button id="c-go" class="btn">开始生成</button>
          <button id="c-refresh" class="btn">刷新产出</button>
        </div>
        <div id="c-status" class="hint" style="margin-top:10px"></div>
        <div id="c-prog-wrap" hidden
             style="height:8px;background:#e9ecef;border-radius:4px;overflow:hidden;margin-top:8px">
          <div id="c-prog" style="height:100%;width:0;background:#5b8def;transition:width .3s"></div>
        </div>
      </div>
      <div class="card">
        <h3>已生成的课程视频</h3>
        <div id="c-list"><span class="muted">加载中…</span></div>
      </div>`;

    // 允许传 '#id' 或 'id'，避免出现 '##id' 这种非法选择器
    const $ = (sel) => panel.querySelector(sel.startsWith('#') ? sel : '#' + sel);
    const srcEl = $('#c-src');
    const epsEl = $('#c-eps');
    const minEl = $('#c-min');
    const statusEl = $('#c-status');
    const prog = $('#c-prog');
    const wrap = $('#c-prog-wrap');

    function stopPoll() {
      if (state.timer) { clearInterval(state.timer); state.timer = 0; }
    }

    async function refreshList() {
      const host = $('#c-list');
      if (!host) return;
      try {
        const data = await api.get('/api/course/list');
        const courses = data.courses || [];
        if (!courses.length) {
          host.innerHTML = '<span class="muted">还没有生成任何课程视频</span>';
          return;
        }
        host.innerHTML = courses.map((c) => `
          <div style="border-bottom:1px solid #2b3140;padding:10px 0">
            <div style="margin-bottom:6px">
              <b>${esc(c.title)}</b>
              <span class="muted"> · ${esc(c.created_at)} · ${c.count} 集</span>
            </div>
            <div>${(c.episodes || []).map((e) => `
              <a class="btn" style="margin:0 6px 6px 0" target="_blank" rel="noopener"
                 href="/api/course/video?course=${encodeURIComponent(c.id)}&ep=${encodeURIComponent(e.episode)}"
                 title="${esc(e.title || '')}">${esc(e.episode)} · ${e.size_mb}MB</a>`).join('')}
            </div>
          </div>`).join('');
      } catch (e) {
        host.innerHTML = `<span class="muted">读取失败：${esc(String(e.message || e))}</span>`;
      }
    }

    async function poll() {
      if (!state.jobId) return;
      try {
        const d = await api.get(`/api/jobs/${state.jobId}?logs=false`);
        const j = d.job || {};
        const pct = Math.max(0, Math.min(100, Number(j.progress) || 0));
        prog.style.width = pct + '%';
        if (j.status === 'done') {
          stopPoll();
          state.busy = false;
          const r = j.result || {};
          statusEl.textContent = r.count
            ? `完成 ✅ ${r.ok}/${r.count} 集成功，共 ${r.seconds} 秒`
            : '完成 ✅';
          refreshList();
        } else if (j.status === 'failed' || j.status === 'canceled') {
          stopPoll();
          state.busy = false;
          statusEl.textContent = `失败：${j.error || j.message || j.status}`;
        } else {
          statusEl.textContent = `${pct}%  ${j.message || j.status || ''}`;
        }
      } catch (e) {
        // 单次轮询失败不算错（服务重启 / 网络抖动），下一次接着来
      }
    }

    $('#c-go').onclick = async () => {
      if (state.busy) return;
      const source = (srcEl.value || '').trim();
      if (!source) { statusEl.textContent = '先写一个主题，或粘贴一段文本'; return; }
      state.busy = true;
      wrap.hidden = false;
      prog.style.width = '0%';
      statusEl.textContent = '提交中…';
      try {
        const r = await api.post('/api/course/build', {
          source,
          episodes: Number(epsEl.value) || 3,
          minutes: Number(minEl.value) || 3,
          all: true,
        });
        state.jobId = r.job_id;
        statusEl.textContent = '已排队，等待 worker 接手…';
        stopPoll();
        state.timer = setInterval(poll, 1500);
        poll();
      } catch (e) {
        state.busy = false;
        statusEl.textContent = '提交失败：' + (e.message || e);
      }
    };

    $('#c-refresh').onclick = refreshList;
    refreshList();
  },
});
