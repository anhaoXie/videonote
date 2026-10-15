// ---------------------------------------------------------------------------
// 共用录音组件：MediaRecorder + 实时音量条
// 用法：
//   const rec = new Recorder();
//   await rec.start();            // 需要用户手势触发
//   const {blob, url, duration} = await rec.stop();
// ---------------------------------------------------------------------------

export class Recorder {
  constructor(opts = {}) {
    this.opts = opts;
    this.chunks = [];
    this.recorder = null;
    this.stream = null;
    this.startedAt = 0;
    this.blob = null;
    this.url = null;
    this._audioCtx = null;
    this._raf = null;
    this._onLevel = opts.onLevel || null;
  }

  get recording() { return this.recorder && this.recorder.state === 'recording'; }

  async start() {
    if (this.recording) return;
    if (!this.stream) {
      this.stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, channelCount: 1 },
      });
    }
    this.chunks = [];
    const mime = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4'].find(
      (m) => window.MediaRecorder && MediaRecorder.isTypeSupported && MediaRecorder.isTypeSupported(m),
    );
    this.recorder = new MediaRecorder(this.stream, mime ? { mimeType: mime } : undefined);
    this.recorder.ondataavailable = (e) => { if (e.data && e.data.size) this.chunks.push(e.data); };
    this.recorder.start(250);
    this.startedAt = performance.now();
    this._meter();
    return this;
  }

  stop() {
    return new Promise((resolve, reject) => {
      if (!this.recorder) return reject(new Error('还没开始录音'));
      const finish = () => {
        const type = this.recorder.mimeType || 'audio/webm';
        this.blob = new Blob(this.chunks, { type });
        if (this.url) URL.revokeObjectURL(this.url);
        this.url = URL.createObjectURL(this.blob);
        this._stopMeter();
        resolve({ blob: this.blob, url: this.url, duration: (performance.now() - this.startedAt) / 1000 });
      };
      this.recorder.onstop = finish;
      try { this.recorder.stop(); } catch (e) { reject(e); }
    });
  }

  _meter() {
    if (!this._onLevel) return;
    try {
      this._audioCtx = this._audioCtx || new (window.AudioContext || window.webkitAudioContext)();
      const src = this._audioCtx.createMediaStreamSource(this.stream);
      const analyser = this._audioCtx.createAnalyser();
      analyser.fftSize = 512;
      src.connect(analyser);
      const buf = new Uint8Array(analyser.frequencyBinCount);
      const tick = () => {
        if (!this.recording) return;
        analyser.getByteTimeDomainData(buf);
        let peak = 0;
        for (let i = 0; i < buf.length; i++) peak = Math.max(peak, Math.abs(buf[i] - 128) / 128);
        this._onLevel(peak);
        this._raf = requestAnimationFrame(tick);
      };
      tick();
    } catch (e) { /* 音量条失败不影响录音 */ }
  }

  _stopMeter() {
    if (this._raf) cancelAnimationFrame(this._raf);
    this._raf = null;
    if (this._onLevel) this._onLevel(0);
  }
}

/** 把 blob POST 到指定接口（multipart），返回 JSON。 */
export async function uploadBlob(url, blob, fields = {}) {
  const fd = new FormData();
  const ext = (blob.type || '').includes('mp4') ? 'mp4' : 'webm';
  fd.append('file', blob, `recording.${ext}`);
  Object.entries(fields).forEach(([k, v]) => fd.append(k, String(v)));
  const res = await fetch(url, { method: 'POST', body: fd });
  if (!res.ok) {
    let msg = res.statusText;
    try { msg = (await res.json()).detail || msg; } catch (e) { /* ignore */ }
    throw new Error(msg);
  }
  return res.json();
}

/** 逐词结果渲染成彩色 HTML。 */
export function wordDiffHtml(words) {
  const color = { ok: '#22c55e', wrong: '#f59e0b', missing: '#ef4444', extra: '#8b5cf6' };
  const label = { ok: '', wrong: '（听成了别的）', missing: '（没念出来）', extra: '（多说的）' };
  return (words || []).map((w) => {
    const c = color[w.status] || '#e6e9ef';
    const heard = w.heard && w.heard !== w.w ? ` 听到:${w.heard}` : '';
    return `<span style="color:${c}" title="${w.status}${label[w.status] || ''}${heard}">${w.w}</span>`;
  }).join(' ');
}

/** 分数颜色。 */
export function scoreColor(score) {
  if (score >= 90) return '#22c55e';
  if (score >= 75) return '#84cc16';
  if (score >= 60) return '#f59e0b';
  return '#ef4444';
}
