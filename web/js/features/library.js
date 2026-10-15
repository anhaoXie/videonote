// ---------------------------------------------------------------------------
// 项目概览：产出文件、任务历史、常见操作
// ---------------------------------------------------------------------------
import { VN, api, ui, store, emit, fmtTime, fmtDur, esc } from '../core.js';

const ART_LABEL = {
  media: '媒体信息', media_file: '视频文件', audio: '音频(16k)', transcript: '转写 JSON',
  transcript_text: '转写文本', frames: '关键帧索引', frames_sheet: '缩略图墙', notes: '笔记 Markdown',
  summary: '结构化摘要', subtitles_srt: '字幕 SRT', subtitles_vtt: '字幕 VTT',
  translation: '逐句译文', video_hardsub: '硬字幕视频', upload_tmp: '上传文件',
};

ui.registerTab({
  id: 'library', label: '概览', order: 5,
  render(panel) {
    if (!store.project) {
      panel.innerHTML = `<div class="card"><h3>还没有项目</h3>
        <div class="hint">点左上角「＋ 导入视频」，支持：本地上传 / 本机路径 / 视频链接（B站、YouTube 等）。<br>
        导入后会自动跑「完整分析」：语音识别 → 画面关键帧 → 摘要笔记 → 字幕。</div></div>`;
      return;
    }
    const p = store.project;
    const d = store.detail || { artifacts: [], jobs: [] };
    const has = (k) => d.artifacts.some((a) => a.key === k);
    panel.innerHTML = `
      <div class="card">
        <h3>${esc(p.title)}</h3>
        <div class="kv">
          <div>项目 ID</div><div><code>${esc(p.id)}</code></div>
          <div>来源</div><div>${p.source_type === 'url' ? esc(p.source_uri) : esc(p.source_uri || p.media_path)}</div>
          <div>时长</div><div>${fmtDur(p.duration)}</div>
          <div>状态</div><div>${esc(p.status)}</div>
          <div>创建时间</div><div>${esc(p.created_at || '')}</div>
          <div>句子数</div><div>${store.segments.length}</div>
        </div>
      </div>

      <div class="card">
        <h3>产出与下载</h3>
        <div class="row">
          ${d.artifacts.length ? d.artifacts.map((a) => `
            <button class="btn small" data-key="${esc(a.key)}">
              ${esc(ART_LABEL[a.key] || a.key)} ↓
            </button>`).join('') : '<span class="muted">还没有产出，先跑一次分析</span>'}
        </div>
        <div class="hint" id="ov-hint"></div>
      </div>

      <div class="card">
        <h3>一键操作</h3>
        <div class="row">
          <button class="btn" data-run="transcribe">转写 + 字幕</button>
          <button class="btn" data-run="subtitle_full">双语字幕（翻译）</button>
          <button class="btn" data-run="summarize">重做摘要</button>
          <button class="btn" data-run="frames">画面关键帧</button>
          <button class="btn" data-run="diarize">说话人分离</button>
          <button class="btn" data-run="captions">画面描述（VLM）</button>
          <button class="btn" data-run="burn">压制硬字幕视频</button>
        </div>
        <div class="hint">「压制硬字幕」很慢（要重新编码整部视频）；「双语字幕」「画面描述」需要大模型 key；
        「说话人分离」用本地声纹模型，CPU 也能跑（第一次会下载约 80MB 模型）。</div>
      </div>

      <div class="card">
        <h3>任务历史</h3>
        <div class="seg-list">
          ${d.jobs.length ? d.jobs.map((j) => `
            <div class="seg" data-job="${esc(j.id)}">
              <div class="seg-time">${esc((j.created_at || '').slice(11, 19))}</div>
              <div><div class="seg-text">${esc(j.kind)} · ${esc(j.status)} · ${Math.round(j.progress || 0)}%</div>
              <div class="seg-zh">${esc(j.error || j.message || '')}</div></div>
            </div>`).join('') : '<span class="muted">暂无任务</span>'}
        </div>
      </div>`;

    panel.querySelectorAll('[data-key]').forEach((el) => {
      el.onclick = () => window.open(api.artifactUrl(p.id, el.dataset.key, true), '_blank');
    });
    panel.querySelectorAll('[data-run]').forEach((el) => {
      el.onclick = async () => {
        await api.runJob(p.id, el.dataset.run);
        ui.toast(`已排队：${el.dataset.run}`, 'ok');
        emit('projectChanged', {});
      };
    });
    panel.querySelectorAll('[data-job]').forEach((el) => {
      el.onclick = async () => {
        const { job, logs } = await api.job(el.dataset.job);
        const w = window.open('', '_blank');
        w.document.write(`<pre style="font:12px Consolas;background:#0b0d12;color:#ddd;padding:14px">${
          esc(logs.map((l) => `[${l.ts.slice(11)}] ${l.message}`).join('\n') || '无日志')}</pre>`);
      };
    });
  },
});
