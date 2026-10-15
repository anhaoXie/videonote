// ---------------------------------------------------------------------------
// 音素级发音结果的展示组件
//   把「/θ/ 发成了 /s/」这种结论画成人能一眼看懂的样子：
//   每个词下面挂一串音标，颜色代表 正确/含糊/读错/漏读。
// 评分接口返回的 phonemes 字段直接喂进来即可（clips / review / player 共用）。
// ---------------------------------------------------------------------------

const PH_STATUS = {
  ok: { color: '#22c55e', label: '正确' },
  weak: { color: '#a3e635', label: '有点含糊' },
  sub: { color: '#f59e0b', label: '读成了别的音' },
  del: { color: '#ef4444', label: '几乎没发出来' },
  unclear: { color: '#6b7280', label: '没听清（疑似没录上）' },
};

function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

/** 单个音素的胶囊。 */
function phoneChip(p) {
  const st = PH_STATUS[p.status] || PH_STATUS.ok;
  const heard = p.heard && p.heard !== p.p ? `  听成 /${p.heard}/` : '';
  const title = `${st.label}${heard}${p.gop != null ? `  GOP ${p.gop}` : ''}`;
  return `<span class="ph" style="color:${st.color};border-color:${st.color}55" title="${esc(title)}">/${esc(p.p)}/</span>`;
}

/** 一个词的音素明细。 */
function wordRow(w) {
  if (!w.ok) {
    return `<div class="ph-row"><span class="ph-word">${esc(w.word)}</span>
      <span class="muted" style="font-size:11.5px">${esc(w.reason || '无法判断')}</span></div>`;
  }
  const phones = (w.phones || []).map(phoneChip).join('');
  const extra = (w.insertions || []).length
    ? `<span class="ph" style="color:#8b5cf6;border-color:#8b5cf655" title="多读出来的音">+${(w.insertions || []).map((x) => `/${esc(x)}/`).join('')}</span>`
    : '';
  const acc = w.accuracy != null
    ? `<span class="muted" style="font-size:11px;margin-left:6px">${w.accuracy}%</span>` : '';
  return `<div class="ph-row">
    <span class="ph-word" title="${esc(w.problem || '')}">${esc(w.word)}</span>
    <span class="ph-phones">${phones}${extra}${acc}</span></div>`;
}

/** 完整的音素面板（没人练过/没有音素数据时返回空串）。 */
export function phonemePanelHtml(phon, opts = {}) {
  if (!phon) return '';
  if (!phon.ok) {
    const reason = phon.reason || '未启用';
    // 「未启用」不值得占地方，只有真出错时才提示
    if (!opts.showDisabled && /未启用|关闭/.test(reason)) return '';
    return `<div class="hint" style="font-size:11.5px">音素级分析不可用：${esc(reason)}</div>`;
  }
  const words = (phon.words || []).filter((w) => w.ok && (w.phones || []).length);
  const skipped = (phon.words || []).filter((w) => !w.ok);
  const errors = phon.errors || [];
  const legend = `<div class="ph-legend muted">
      音素级诊断（${words.length} 个词 · ${errors.length} 个问题音
      ${phon.seconds ? ` · ${phon.seconds}s` : ''}）：
      <span style="color:${PH_STATUS.ok.color}">正确</span>
      <span style="color:${PH_STATUS.weak.color}">含糊</span>
      <span style="color:${PH_STATUS.sub.color}">读错</span>
      <span style="color:${PH_STATUS.del.color}">漏读</span>
      <span style="color:#8b5cf6">多读</span>
    </div>`;
  if (!words.length && !skipped.length) {
    return `${legend}<div class="muted" style="font-size:11.5px">这次没有发现需要精听的词 —— 说明整句都读得挺清楚 👍</div>`;
  }
  const rows = words.map(wordRow).join('');
  const skipNote = skipped.length
    ? `<div class="muted" style="font-size:11px">有 ${skipped.length} 个词没法判断（多半是那段录音里没声音）</div>` : '';
  return `${legend}<div class="ph-box">${rows}</div>${skipNote}`;
}

/** 一行简短的音素摘要，用于列表/句库这种位置（例如「θ→s」）。 */
export function phonemeSummary(phon) {
  if (!phon || !phon.ok) return '';
  const errs = phon.errors || [];
  if (!errs.length) return '';
  return errs.slice(0, 4).map((e) => {
    if (e.type === 'ins') return `<span class="ph-mini" style="color:#8b5cf6">+${esc(e.heard)}</span>`;
    const heard = e.heard ? `→${esc(e.heard)}` : '→漏';
    return `<span class="ph-mini" style="color:${e.type === 'del' ? PH_STATUS.del.color : PH_STATUS.sub.color}">${esc(e.expected)}${heard}</span>`;
  }).join('');
}
