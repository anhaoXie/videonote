// ---------------------------------------------------------------------------
// 设置 / 引擎状态 / 插件清单 —— 也是「怎么加新功能」的说明书
// ---------------------------------------------------------------------------
import { VN, api, ui, store, esc } from '../core.js';

/** 音素评测状态那一行：可用要说清用的哪个模型，不可用要说清怎么开。 */
function phonemeLine(p) {
  if (!p) return '状态未知（服务没返回）';
  if (p.available) {
    return `<span style="color:var(--accent2)">已就绪</span> · ${esc(p.model || '')}
      · 判错阈值 ${esc(String(p.gop_threshold))} · 每次最多精听 ${esc(String(p.max_words))} 个词
      ${p.loaded ? '<span class="muted">（模型已载入内存）</span>' : '<span class="muted">（首次评分会加载模型，约十几秒）</span>'}`;
  }
  return `<span style="color:var(--warn)">未启用</span>：${esc(p.reason || '')}
    ${p.model_ready ? '' : '<br><span class="muted">下载命令：<code>.venv\\Scripts\\python.exe tools\\get_phoneme_model.py</code></span>'}`;
}

ui.registerTab({
  id: 'settings', label: '设置 / 插件', order: 90,
  render(panel) {
    const cfg = store.capabilities?.config || {};
    const cap = store.capabilities || {};
    panel.innerHTML = `
      <div class="card">
        <h3>引擎状态</h3>
        <div class="kv">
          <div>语音识别</div><div>${esc(cfg.asr?.effective_provider || '-')} ${cfg.asr?.effective_provider === 'cloud'
            ? `(${esc(cfg.asr?.cloud_model || '')})` : `(faster-whisper ${esc(cfg.asr?.local_model || '')} / ${esc(cfg.asr?.device || '')} / ${esc(cfg.asr?.compute_type || '')})`}</div>
          <div>云端 ASR key</div><div>${cfg.asr?.cloud_key_set ? '<span style="color:var(--accent2)">已配置</span>' : '未配置（改 .env 里的 VN_ASR_API_KEY）'}</div>
          <div>大模型</div><div>${esc(cfg.llm?.base_url || '')} · ${esc(cfg.llm?.model || '')}</div>
          <div>大模型 key</div><div>${cfg.llm?.ready ? '<span style="color:var(--accent2)">已配置</span>' : '<span style="color:var(--warn)">未配置</span>：笔记会退回本地抽取式摘要'}</div>
          <div>说话人分离</div><div>${esc(cfg.diarize?.provider || 'local_ecapa')} · ${cfg.diarize?.num_speakers ? cfg.diarize.num_speakers + ' 人（固定）' : '自动判断人数'}</div>
          <div>画面描述</div><div>${cfg.vision?.provider ? `${esc(cfg.vision.provider)} · ${esc(cfg.vision.model || '')} ${cfg.vision.key_set ? '' : '<span style="color:var(--warn)">(未配置 key)</span>'}` : '未开启（VN_VISION_PROVIDER=vlm 可开）'}</div>
          <div>音素级发音评测</div><div>${phonemeLine(cap.phoneme)}</div>
          <div>OCR</div><div>${cfg.features?.ocr ? '已开启' : '未开启（VN_ENABLE_OCR=true）'}</div>
          <div>数据目录</div><div><code>${esc(cfg.data_dir || '')}</code></div>
        </div>
        <div class="hint">改完 <code>.env</code> 后重启服务生效。</div>
      </div>

      <div class="card">
        <h3>已注册的处理阶段（插件）</h3>
        <div class="md"><table>
          <tr><th>阶段</th><th>说明</th><th>产出</th><th>可选</th></tr>
          ${(cap.stages || []).map((s) => `<tr>
            <td><code>${esc(s.name)}</code><br><span class="muted">${esc(s.label || '')}</span></td>
            <td>${esc(s.description || '')}</td>
            <td><code>${esc(s.produces || '')}</code></td>
            <td>${s.optional ? '是' : ''}</td></tr>`).join('')}
        </table></div>
      </div>

      <div class="card">
        <h3>任务类型</h3>
        <div class="md"><table><tr><th>名称</th><th>流程</th></tr>
          ${(cap.job_kinds || []).map((k) => `<tr><td><code>${esc(k.name)}</code><br><span class="muted">${esc(k.label)}</span></td>
            <td>${(k.stages || []).map((x) => `<code>${esc(x)}</code>`).join(' → ')}<br><span class="muted">${esc(k.description || '')}</span></td></tr>`).join('')}
        </table></div>
      </div>

      <div class="card">
        <h3>Provider（可替换的实现）</h3>
        <div class="kv">
          <div>语音识别</div><div>${(cap.providers?.asr || []).map((x) => `<code>${esc(x)}</code>`).join(' ')}</div>
          <div>大模型</div><div>${(cap.providers?.llm || []).map((x) => `<code>${esc(x)}</code>`).join(' ')}</div>
          <div>视觉</div><div>${(cap.providers?.vision || []).map((x) => `<code>${esc(x)}</code>`).join(' ')}</div>
          <div>说话人分离</div><div>${(cap.providers?.diarize || []).map((x) => `<code>${esc(x)}</code>`).join(' ')}</div>
        </div>
      </div>

      <div class="card">
        <h3>怎么加新功能（三步）</h3>
        <div class="md">
          <ol>
            <li>在 <code>videonote/pipeline/</code> 新建一个 <code>.py</code>，写一个 <code>Stage</code> 子类，加 <code>@register_stage</code>；</li>
            <li>在 <code>videonote/pipeline/jobkinds.py</code> 里把它串成一个任务类型；</li>
            <li>需要界面就在 <code>web/js/features/</code> 加一个文件，调 <code>VN.ui.registerTab({...})</code>，然后在 <code>web/app.js</code> import 一下。</li>
          </ol>
          <p>后端会自动发现插件；前端下拉框自动出现新任务类型，不用改一行既有代码。</p>
        </div>
      </div>

      ${Object.keys(cap.plugins?.import_errors || {}).length ? `
      <div class="card">
        <h3 style="color:var(--warn)">加载失败的插件</h3>
        <div class="logbox">${esc(JSON.stringify(cap.plugins.import_errors, null, 2))}</div>
      </div>` : ''}

      <div class="card">
        <h3>快捷键</h3>
        <div class="kv">
          <div>空格</div><div>播放 / 暂停</div>
          <div>R</div><div>重听本句</div>
          <div>N / →</div><div>下一句</div>
          <div>P / ←</div><div>上一句</div>
          <div>M</div><div>录音</div>
          <div>L</div><div>单句循环</div>
        </div>
      </div>`;
  },
});
