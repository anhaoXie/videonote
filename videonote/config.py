"""全局配置：读 .env + 环境变量，全部带默认值，开箱即用（没 key 也能跑）。"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent

load_dotenv(ROOT / ".env", override=False)


def _get(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def _get_bool(name: str, default: bool = False) -> bool:
    raw = _get(name)
    if not raw:
        return default
    return raw.lower() in ("1", "true", "yes", "on", "y")


def _get_int(name: str, default: int) -> int:
    try:
        return int(_get(name) or default)
    except ValueError:
        return default


class Settings:
    """进程级单例配置。改完 .env 重启服务即可生效。"""

    # --- 大模型 ---
    llm_provider: str = _get("VN_LLM_PROVIDER", "openai_compat")
    llm_base_url: str = _get("VN_LLM_BASE_URL", "https://api.deepseek.com/v1")
    llm_api_key: str = _get("VN_LLM_API_KEY")
    llm_model: str = _get("VN_LLM_MODEL", "deepseek-chat")
    llm_temperature: float = float(_get("VN_LLM_TEMPERATURE", "0.3") or 0.3)
    llm_max_tokens: int = _get_int("VN_LLM_MAX_TOKENS", 4096)

    # --- 语音识别 ---
    asr_provider: str = _get("VN_ASR_PROVIDER", "local")
    whisper_model: str = _get("VN_WHISPER_MODEL", "small")
    whisper_device: str = _get("VN_WHISPER_DEVICE", "cpu")
    whisper_compute_type: str = _get("VN_WHISPER_COMPUTE_TYPE", "int8")
    whisper_beam_size: int = _get_int("VN_WHISPER_BEAM_SIZE", 5)
    whisper_vad: bool = _get_bool("VN_WHISPER_VAD", True)
    whisper_model_dir: Optional[str] = _get("VN_WHISPER_MODEL_DIR") or None
    hf_endpoint: str = _get("VN_HF_ENDPOINT", "https://hf-mirror.com")

    asr_base_url: str = _get("VN_ASR_BASE_URL", "https://api.siliconflow.cn/v1")
    asr_api_key: str = _get("VN_ASR_API_KEY")
    asr_model: str = _get("VN_ASR_MODEL", "FunAudioLLM/SenseVoiceSmall")

    # --- 说话人分离 ---
    diarize_provider: str = _get("VN_DIARIZE_PROVIDER", "local_ecapa")   # local_ecapa | pyannote
    diarize_num_speakers: int = _get_int("VN_DIARIZE_NUM_SPEAKERS", 0)   # 0 = 自动判断

    # --- 音素级发音评测（指出「哪个音发错了」）---
    # auto = 依赖装好了就自动开启；off = 关掉只做词级评分
    phoneme_provider: str = _get("VN_PHONEME_PROVIDER", "auto")
    phoneme_model: str = _get("VN_PHONEME_MODEL", "facebook/wav2vec2-lv-60-espeak-cv-ft")
    phoneme_model_dir: Optional[str] = _get("VN_PHONEME_MODEL_DIR") or None
    # GOP（音素后验比）阈值，log10 口径：越接近 0 越好，低于阈值判为可疑
    phoneme_gop_threshold: float = float(_get("VN_PHONEME_GOP_THRESHOLD", "-1.2") or -1.2)
    phoneme_max_words: int = _get_int("VN_PHONEME_MAX_WORDS", 8)   # 每次最多精评几个可疑词
    phoneme_threads: int = _get_int("VN_PHONEME_THREADS", 8)
    # 小于这个音量（RMS）就认为「这段没录上」，直接说没听清，而不是判用户漏读
    phoneme_min_rms: float = float(_get("VN_PHONEME_MIN_RMS", "0.008") or 0.008)

    # --- 视觉大模型（画面理解，可选）---
    vision_provider: str = _get("VN_VISION_PROVIDER", "")   # 空 = 只抽关键帧不做描述；vlm = 调视觉模型
    vlm_base_url: str = _get("VN_VLM_BASE_URL", "")          # 留空则用 LLM 的 base_url
    vlm_api_key: str = _get("VN_VLM_API_KEY", "")            # 留空则用 LLM 的 key
    vlm_model: str = _get("VN_VLM_MODEL", "Qwen/Qwen2.5-VL-7B-Instruct")
    vlm_max_frames: int = _get_int("VN_VLM_MAX_FRAMES", 40)

    # --- 服务 ---
    host: str = _get("VN_HOST", "127.0.0.1")
    port: int = _get_int("VN_PORT", 8756)
    data_dir: Path = Path(_get("VN_DATA_DIR", "data"))
    ffmpeg_path: str = _get("VN_FFMPEG")

    # --- 功能开关 ---
    enable_ocr: bool = _get_bool("VN_ENABLE_OCR", False)
    ytdlp_cookies: Optional[str] = _get("VN_YTDLP_COOKIES") or None

    # ---------------------------------------------------------------
    def __init__(self) -> None:
        if not self.data_dir.is_absolute():
            self.data_dir = (ROOT / self.data_dir).resolve()
        # 国内镜像，faster-whisper 首次下载模型时用
        if self.hf_endpoint:
            os.environ.setdefault("HF_ENDPOINT", self.hf_endpoint)
        # huggingface_hub 2.x 默认启用 Xet 传输后端，在部分 Windows 机器上会 OSError 5（拒绝访问）。
        # 关掉它退回普通 HTTP 下载，实测可正常下载模型。
        os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
        # Windows 未开开发者模式时不支持符号链接，hf 会刷警告，关掉
        os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
        # 模型缓存统一放进项目 data/hf：一来目录权限可控（用户主目录下的
        # ~/.cache/huggingface/hub 在部分机器上会被拒绝访问），二来整个项目可以搬走。
        os.environ.setdefault("HF_HOME", str(self.data_dir / "hf"))

    # --- 路径 ---
    @property
    def root(self) -> Path:
        return ROOT

    @property
    def projects_dir(self) -> Path:
        return self.data_dir / "projects"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "videonote.db"

    @property
    def models_dir(self) -> Path:
        return self.data_dir / "models"

    @property
    def web_dir(self) -> Path:
        return ROOT / "web"

    def project_dir(self, project_id: str) -> Path:
        return self.projects_dir / project_id

    def ensure_dirs(self) -> None:
        for p in (self.data_dir, self.projects_dir, self.models_dir):
            p.mkdir(parents=True, exist_ok=True)

    # --- 状态 ---
    @property
    def llm_ready(self) -> bool:
        return bool(self.llm_api_key)

    def asr_effective_provider(self) -> str:
        """解析 auto：有云端 key 就云端，否则本地。"""
        p = self.asr_provider.lower()
        if p == "auto":
            return "cloud" if self.asr_api_key else "local"
        return p

    def redacted(self) -> dict:
        """给前端看的配置快照（绝不返回 key 明文）。"""
        return {
            "llm": {
                "provider": self.llm_provider,
                "base_url": self.llm_base_url,
                "model": self.llm_model,
                "api_key_set": bool(self.llm_api_key),
                "ready": self.llm_ready,
            },
            "asr": {
                "configured_provider": self.asr_provider,
                "effective_provider": self.asr_effective_provider(),
                "local_model": self.whisper_model,
                "device": self.whisper_device,
                "compute_type": self.whisper_compute_type,
                "cloud_base_url": self.asr_base_url,
                "cloud_model": self.asr_model,
                "cloud_key_set": bool(self.asr_api_key),
            },
            "features": {"ocr": self.enable_ocr},
            "diarize": {"provider": self.diarize_provider, "num_speakers": self.diarize_num_speakers},
            "phoneme": {"provider": self.phoneme_provider, "model": self.phoneme_model,
                        "gop_threshold": self.phoneme_gop_threshold,
                        "max_words": self.phoneme_max_words},
            "vision": {"provider": self.vision_provider,
                       "model": self.vlm_model,
                       "base_url": self.vlm_base_url or self.llm_base_url,
                       # 画面描述要单独的视觉模型 key；不能用摘要那个 key 冒充，
                       # 否则界面会显示成"已就绪"，用户会以为开了
                       "key_set": bool(self.vlm_api_key),
                       "ready": bool(self.vlm_api_key) and self.vision_provider == "vlm",
                       "hint": "" if self.vlm_api_key else "开启画面描述：VN_VISION_PROVIDER=vlm + VN_VLM_API_KEY"},
            "data_dir": str(self.data_dir),
            "server": {"host": self.host, "port": self.port},
        }


settings = Settings()
