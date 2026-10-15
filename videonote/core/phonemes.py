# -*- coding: utf-8 -*-
"""音素级发音评测：指出「哪个音发错了」。

比如把 think 的 /θ/ 读成 /s/、把 very 的 /v/ 读成 /w/、把长短元音读混。

为什么值得单独做一层：
    词级评分只能告诉你「对方听懂没有」；
    音素级才能告诉你「嘴该怎么改」。对中式英语来说，
    θ/ð/v/ɹ/ŋ/æ 和长短元音就是那 20% 决定口音的音。

算法（两段式，纯本地、零 API）：
    1. 期望侧：文本 --espeak-ng--> IPA 音素串（去重音）
    2. 识别侧：wav2vec2 音素模型 --CTC--> 音素后验
    3. 对齐：2N+1 状态的标准 CTC 强制对齐（Viterbi），拿到每个音素的帧区间
    4. 打分：GOP = log p(该音素) - log p(最大音素)，逐音素判 正确/替换/遗漏
    5. 兜底：自由解码 + 加权编辑距离，抓「多读了音」

性能设计（关键）：不做全句音素识别。
    先用 Whisper 的词级结果找出「可疑词」，只对这些词切片（前后各留 0.25s）跑模型，
    所以 5 秒的一句话通常只推理 0~2 个 1 秒片段，额外延迟 < 1 秒。
"""
from __future__ import annotations

import math
import os
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from ..config import settings

MODEL_DIR_NAME = "wav2vec2-lv-60-espeak-cv-ft"

# --------------------------------------------------------------------------
# 符号归一化：把各种写法收敛到模型词表里那 388 个 IPA 音素
# --------------------------------------------------------------------------
NORM = {
    "ɐ": "ə", "ᵻ": "ɪ", "ɾ": "t", "r": "ɹ", "e": "ɛ", "ɡ": "ɡ", "g": "ɡ",
    "T": "θ", "D": "ð", "S": "ʃ", "Z": "ʒ", "N": "ŋ", "tS": "tʃ", "dZ": "dʒ",
    "th": "θ", "sh": "ʃ", "ch": "tʃ",
    "ɒ": "ɑ", "əʊ": "oʊ", "ɜ": "ɜː", "ɚ": "ɚ",
}

# 英文音素白名单：模型词表里混了阿拉伯语/法语等杂音素，不过滤的话
# 一段噪声能被解成 dˤ ʈ ɖ，把结果淹掉
EN_PHONES = frozenset("""
p b t d k ɡ f v θ ð s z ʃ ʒ h tʃ dʒ m n ŋ l ɹ j w
ɪ i iː ɛ æ ɑ ɑː ɔ ɔː ʊ u uː ʌ ɜː ə ɚ
eɪ aɪ ɔɪ oʊ aʊ
ɪɹ ɛɹ ʊɹ ɑːɹ ɔːɹ
""".split())

# 中文母语者高频混淆对（用于把「替换」说得更具体，也用于对齐时降低替换代价）
CONFUSIONS = {
    ("θ", "s"), ("θ", "f"), ("ð", "z"), ("ð", "d"), ("v", "w"), ("ɹ", "l"), ("ɹ", "w"),
    ("ŋ", "n"), ("æ", "ɛ"), ("ɪ", "iː"), ("ʊ", "uː"), ("iː", "ɪ"), ("uː", "ʊ"),
    ("l", "n"), ("ʃ", "s"), ("ʒ", "z"), ("tʃ", "ʃ"), ("dʒ", "ʒ"), ("s", "θ"), ("f", "θ"),
}

PHONE_TIPS = {
    ("θ", "s"): "咬舌音 /θ/ 读成了 /s/：舌尖要轻轻放在上下齿之间，送气但不振动声带（think, three, mouth）",
    ("θ", "f"): "咬舌音 /θ/ 读成了 /f/：上齿轻触舌尖，别用下唇碰牙",
    ("ð", "z"): "浊咬舌音 /ð/ 读成了 /z/：this, that, mother 里的 th 要舌尖出齿、声带振动",
    ("ð", "d"): "浊咬舌音 /ð/ 读成了 /d/：舌尖要露在齿间，不要顶住上齿龈",
    ("v", "w"): "/v/ 读成了 /w/：上齿要碰到下唇（very, love），/w/ 是圆唇不碰齿",
    ("ɹ", "l"): "/ɹ/ 读成了 /l/：舌尖不要顶上齿龈，卷起但不接触（right, very, car）",
    ("ɹ", "w"): "/ɹ/ 读成了 /w/：嘴唇别收圆，舌尖微卷",
    ("ŋ", "n"): "/ŋ/ 读成了 /n/：舌根要抵住软腭，-ing 结尾最常见（going, thinking）",
    ("æ", "ɛ"): "/æ/ 读成了 /ɛ/：嘴要张大、下巴放低（cat, bad, actually）",
    ("ɪ", "iː"): "短音 /ɪ/ 读成了长音 /iː/：/ɪ/ 要短而松弛（sit, live, this）",
    ("iː", "ɪ"): "长音 /iː/ 读成了短音 /ɪ/：/iː/ 要拉长（see, need, people）",
    ("ʊ", "uː"): "短音 /ʊ/ 读成了长音 /uː/：/ʊ/ 要短（book, good, put）",
    ("uː", "ʊ"): "长音 /uː/ 读成了短音 /ʊ/：/uː/ 要拉长（food, two, soon）",
    ("l", "n"): "词尾 /l/ 读成了 /n/：舌尖要抵上齿龈并保持（people, little, all）",
}

_lock = threading.RLock()
_state: Dict[str, Any] = {"fe": None, "tok": None, "model": None, "vocab": None, "blank": 0,
                          "torch": None, "espeak_lib": None, "espeak_voice": "", "error": "",
                          "espeak_chdir": "", "cmudict": None}
_g2p_cache: Dict[str, List[str]] = {}


# --------------------------------------------------------------------------
# 可用性 / 模型位置
# --------------------------------------------------------------------------
def model_dir() -> Path:
    if settings.phoneme_model_dir:
        return Path(settings.phoneme_model_dir)
    return settings.models_dir / MODEL_DIR_NAME


def tokenizer_ready() -> bool:
    """词表齐了就能做 G2P（不需要 1.2GB 权重）。"""
    return all((model_dir() / f).exists() for f in ("config.json", "vocab.json"))


def _model_files_ok(d: Path) -> bool:
    if not d.exists():
        return False
    for name in ("config.json", "vocab.json"):
        if not (d / name).exists():
            return False
    return any((d / f).exists() for f in ("model.safetensors", "pytorch_model.bin"))


def model_ready() -> bool:
    return _model_files_ok(model_dir())


def deps_ok() -> Tuple[bool, str]:
    try:
        import torch  # noqa: F401
        import transformers  # noqa: F401
        import espeakng_loader  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        return False, (f"缺少依赖：{exc}。需要 transformers / torch / espeakng-loader，"
                       f"见 requirements.txt")
    return True, ""


def enabled() -> bool:
    if settings.phoneme_provider.lower() in ("off", "none", "0", "false"):
        return False
    return True


def available() -> Tuple[bool, str]:
    """能不能用音素评测。返回 (ok, 原因)。"""
    if not enabled():
        return False, "已在配置里关闭（VN_PHONEME_PROVIDER=off）"
    ok, why = deps_ok()
    if not ok:
        return False, why
    if not model_ready():
        return False, (f"音素模型还没下载（{model_dir()}）。"
                       f"运行 python tools\\get_phoneme_model.py 下载（约 1.2GB，一次性）")
    return True, ""


def status() -> Dict[str, Any]:
    ok, why = available()
    return {
        "enabled": enabled(),
        "available": ok,
        "reason": why,
        "model": settings.phoneme_model,
        "model_dir": str(model_dir()),
        "model_ready": model_ready(),
        "gop_threshold": settings.phoneme_gop_threshold,
        "max_words": settings.phoneme_max_words,
        "loaded": _state["model"] is not None,
        "error": _state.get("error", ""),
    }


# --------------------------------------------------------------------------
# 模型加载（单例）
# --------------------------------------------------------------------------
def _espeak_arg():
    """算出该传给 espeak 的数据目录参数。

    坑：espeak-ng 在 Windows 上是用 ANSI 编码打开文件的，路径里只要有中文
    （比如 C:\\Users\\谢安豪\\...）它就找不到数据，静默退回编译时的默认路径。
    解决办法是**只传相对路径**：espeak 拿到的字符串全是 ASCII，
    真正的目录解析交给操作系统（操作系统内部是 Unicode，不受影响）。
    返回 (路径参数, 需要临时切换到的目录)。
    """
    import espeakng_loader
    real = Path(espeakng_loader.get_data_path())
    try:
        rel = os.path.relpath(real, os.getcwd())
        if rel.isascii():
            return rel, ""
    except ValueError:
        pass
    # 退路：临时切到数据目录的父目录，用纯 ASCII 的相对名
    if real.name.isascii() and real.parent.is_dir():
        return real.name, str(real.parent)
    return str(real), ""


def _init_espeak():
    """加载 espeak-ng 并把语音设成美式英语（失败就抛异常，上层会退到 CMU 词典）。"""
    if _state.get("espeak_lib") is not None:
        return _state["espeak_lib"]
    import ctypes

    import espeakng_loader

    lib = ctypes.CDLL(str(espeakng_loader.get_library_path()))
    lib.espeak_Initialize.restype = ctypes.c_int
    lib.espeak_Initialize.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_char_p, ctypes.c_int]
    lib.espeak_SetVoiceByName.restype = ctypes.c_int
    lib.espeak_SetVoiceByName.argtypes = [ctypes.c_char_p]
    lib.espeak_TextToPhonemes.restype = ctypes.c_char_p
    lib.espeak_TextToPhonemes.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]

    arg, chdir_to = _espeak_arg()
    old = None
    try:
        if chdir_to:
            old = os.getcwd()
            os.chdir(chdir_to)
        # AUDIO_OUTPUT_RETRIEVAL(1)：不占声卡、不播放，样本通过回调取。
        # 这样同一个 espeak 实例既能做「文本->音标」，也能做「文本->语音」（core/tts.py）。
        rc = lib.espeak_Initialize(1, 0, arg.encode("ascii", "strict"), 0)
        if rc <= 0:
            raise RuntimeError(f"espeak_Initialize 返回 {rc}（数据目录 {arg}）")
        voice = ""
        for name in ("en-us", "en"):
            if lib.espeak_SetVoiceByName(name.encode("ascii")) == 0:
                voice = name
                break
        if not voice:
            raise RuntimeError("espeak-ng 里没有英语语音包")
        _state["espeak_lib"] = lib
        _state["espeak_voice"] = voice
        _state["espeak_chdir"] = chdir_to
        _state["espeak_arg"] = arg
        # 自检：读不出 /θ/ 说明数据没加载对，直接认输走词典兜底
        probe = _espeak_ipa_raw("think")
        if "θ" not in probe:
            _state["espeak_lib"] = None
            raise RuntimeError(f"espeak 自检失败（think -> {probe!r}）")
    finally:
        if old:
            os.chdir(old)
    return lib


def _espeak_ipa_raw(text: str) -> str:
    import ctypes

    lib = _state.get("espeak_lib")
    if lib is None:
        return ""
    old = None
    chdir_to = _state.get("espeak_chdir") or ""
    if chdir_to:
        old = os.getcwd()
        os.chdir(chdir_to)
    try:
        with _lock:
            buf = ctypes.c_char_p(text.encode("utf-8"))
            out: List[str] = []
            for _ in range(8):
                res = lib.espeak_TextToPhonemes(ctypes.byref(buf), 1, 2)   # UTF8, IPA
                if not res:
                    break
                chunk = res.decode("utf-8", "ignore")
                if chunk:
                    out.append(chunk)
            return " ".join(out)
    finally:
        if old:
            os.chdir(old)


def _espeak_ipa(text: str) -> str:
    """让 espeak-ng 输出 IPA 音标（含重音符号，后面会清掉）。"""
    if _state.get("espeak_lib") is None:
        _init_espeak()
    return _espeak_ipa_raw(text)


# --------------------------------------------------------------------------
# CMU 发音词典兜底（纯 Python，没有任何原生依赖）
# --------------------------------------------------------------------------
ARPABET_IPA = {
    "AA": "ɑː", "AE": "æ", "AH": "ʌ", "AO": "ɔː", "AW": "aʊ", "AY": "aɪ",
    "EH": "ɛ", "ER": "ɜː", "EY": "eɪ", "IH": "ɪ", "IY": "iː", "OW": "oʊ",
    "OY": "ɔɪ", "UH": "ʊ", "UW": "uː",
    "B": "b", "CH": "tʃ", "D": "d", "DH": "ð", "F": "f", "G": "ɡ", "HH": "h",
    "JH": "dʒ", "K": "k", "L": "l", "M": "m", "N": "n", "NG": "ŋ", "P": "p",
    "R": "ɹ", "S": "s", "SH": "ʃ", "T": "t", "TH": "θ", "V": "v", "W": "w",
    "Y": "j", "Z": "z", "ZH": "ʒ",
}
# 同一个 ARPAbet 符号在不同重音下音质不同（这是中式口音最容易糊的地方）
STRESS_OVERRIDE = {"AH0": "ə", "AH1": "ʌ", "AH2": "ʌ", "ER0": "ɚ", "ER1": "ɜː", "ER2": "ɜː"}
# 词形回退：CMU 词典没有这个词时，去后缀再补上后缀的读音
SUFFIX_FALLBACK = [
    ("'s", "", ["z"]), ("s'", "", ["z"]),
    ("ing", "", ["ɪ", "ŋ"]), ("ing", "e", ["ɪ", "ŋ"]),
    ("ed", "", ["d"]), ("ed", "e", ["d"]),
    ("es", "", ["ɪ", "z"]), ("es", "e", ["z"]),
    ("s", "", ["z"]), ("s", "e", ["s"]),
    ("ly", "", ["l", "i"]), ("er", "", ["ɚ"]), ("est", "", ["ɪ", "s", "t"]),
]


def _cmudict():
    if _state.get("cmudict") is not None:
        return _state["cmudict"]
    try:
        import cmudict
        _state["cmudict"] = cmudict.dict()
    except Exception as exc:  # noqa: BLE001
        _state["cmudict"] = {}
        _state["cmudict_error"] = str(exc)
    return _state["cmudict"]


def _arpabet_to_ipa(sym: str) -> str:
    s = (sym or "").upper()
    if s in STRESS_OVERRIDE:
        return STRESS_OVERRIDE[s]
    base = s.rstrip("012")
    return ARPABET_IPA.get(base, "")


def _cmudict_phones(word: str) -> List[str]:
    """用 CMU 词典查音素（纯 Python 兜底路径）。"""
    d = _cmudict()
    if not d:
        return []
    key = "".join(ch for ch in word.lower() if ch.isalpha() or ch == "'")
    entry = d.get(key)
    suffix_extra: List[str] = []
    if not entry:
        for suf, add, extra in SUFFIX_FALLBACK:
            if key.endswith(suf) and len(key) > len(suf) + 1:
                base = key[: -len(suf)] + add
                if base in d:
                    entry = d[base]
                    suffix_extra = extra
                    break
    if not entry:
        return []
    phones = [p for p in (_arpabet_to_ipa(s) for s in entry[0]) if p]
    return phones + suffix_extra


# --------------------------------------------------------------------------
# 词表（只加载 tokenizer，不加载 1.2GB 权重）
# --------------------------------------------------------------------------
def _ensure_vocab() -> Dict[str, int]:
    if _state.get("vocab"):
        return _state["vocab"]
    if not tokenizer_ready():
        return {}
    try:
        # 注意：这个仓库的 tokenizer_class 是 Wav2Vec2PhonemeCTCTokenizer，
        # 它内部依赖 phonemizer（会拖进 csvw 等一串包）。我只需要「音素->id」这张表，
        # 文本转音素我自己用 espeak/CMU 词典做，所以直接上 Wav2Vec2CTCTokenizer。
        from transformers import Wav2Vec2CTCTokenizer
        tok = Wav2Vec2CTCTokenizer.from_pretrained(str(model_dir()))
        _state["vocab"] = dict(tok.get_vocab())
        _state["blank"] = int(getattr(tok, "pad_token_id", 0) or 0)
        _state["tok"] = tok
    except Exception as exc:  # noqa: BLE001
        _state["error"] = f"读取音素词表失败：{exc}"
        # 最后的退路：直接读 vocab.json
        try:
            import json as _json
            _state["vocab"] = _json.loads((model_dir() / "vocab.json").read_text(encoding="utf-8"))
            _state["blank"] = int(_state["vocab"].get("<pad>", 0))
        except Exception:  # noqa: BLE001
            pass
    return _state.get("vocab") or {}


def _coerce_to_vocab(phones: List[str], vocab: Dict[str, int]) -> List[str]:
    """把音素收敛到模型词表里存在的写法（词表里没有就退化：去掉长音符号）。"""
    if not vocab:
        return [p for p in phones if p]
    keys = set(vocab)
    out: List[str] = []
    for p in phones:
        if p in keys:
            out.append(p)
            continue
        trimmed = p.replace("ː", "")
        if trimmed in keys:
            out.append(trimmed)
            continue
        if p == "ɚ" and "ə" in keys:
            out.append("ə")
            continue
        # 词表里没有这个音素：宁可不评，也不要给出错的音标
    return out


def word_to_phones(word: str) -> List[str]:
    """一个英文单词 -> 音素列表（带缓存）。

    优先 espeak-ng（和模型的训练标签同一套 IPA），
    失败或查不到就退到 CMU 发音词典（纯 Python，够稳）。
    """
    key = (word or "").strip().lower()
    if not key:
        return []
    if key in _g2p_cache:
        return _g2p_cache[key]
    vocab = _ensure_vocab()
    phones: List[str] = []
    source = ""
    try:
        raw = _espeak_ipa(key)
        phones = _clean_phones(raw)
        if phones:
            source = "espeak"
    except Exception as exc:  # noqa: BLE001 - espeak 用不了不致命，下面有词典兜底
        _state["espeak_error"] = str(exc)
    if not phones:
        phones = _coerce_to_vocab(_cmudict_phones(key), vocab)
        if phones:
            source = "cmudict"
    if source == "espeak":
        phones = _coerce_to_vocab(phones, vocab)
    _state.setdefault("g2p_sources", {})[key] = source
    _g2p_cache[key] = phones
    return phones


def load():
    """加载音素模型（第一次调用比较慢，之后常驻）。"""
    with _lock:
        if _state["model"] is not None:
            return _state["fe"], _state["model"]
        import torch
        from transformers import Wav2Vec2CTCTokenizer, Wav2Vec2FeatureExtractor, Wav2Vec2ForCTC

        try:
            torch.set_num_threads(max(1, int(settings.phoneme_threads)))
        except Exception:  # noqa: BLE001
            pass
        src = str(model_dir()) if model_ready() else settings.phoneme_model
        # 不用 Wav2Vec2Processor：它会去实例化 Wav2Vec2PhonemeCTCTokenizer（依赖 phonemizer）
        fe = Wav2Vec2FeatureExtractor.from_pretrained(src)
        tok = Wav2Vec2CTCTokenizer.from_pretrained(src)
        model = Wav2Vec2ForCTC.from_pretrained(src)
        model.eval()
        _state.update({
            "fe": fe, "tok": tok, "model": model,
            "vocab": dict(tok.get_vocab()),
            "blank": int(getattr(tok, "pad_token_id", 0) or 0),
            "torch": torch, "error": "",
        })
        return fe, model


def download_model(progress=None) -> Path:
    """把模型下到 data/models 下（用 hf-mirror，国内可下）。"""
    from huggingface_hub import snapshot_download

    dst = model_dir()
    dst.mkdir(parents=True, exist_ok=True)
    if progress:
        progress("正在下载音素模型（约 1.2GB，只需一次）…")
    snapshot_download(repo_id=settings.phoneme_model, local_dir=str(dst),
                      allow_patterns=["config.json", "vocab.json", "preprocessor_config.json",
                                      "special_tokens_map.json", "tokenizer_config.json",
                                      "pytorch_model.bin", "model.safetensors"])
    if progress:
        progress("音素模型下载完成")
    return dst


# --------------------------------------------------------------------------
# G2P：文本 -> IPA 音素
# --------------------------------------------------------------------------
def _clean_phones(raw: str) -> List[str]:
    """把 espeak 的输出切成音素 token 列表：去掉重音记号、空白和 (en) 之类的注释。"""
    text = (raw or "").split("(")[0]           # espeak 会附加 (en) 这类语言标记
    out: List[str] = []
    for ch in text:
        if ch in "ˈˌ._-|'\"[]":
            continue
        if ch.isspace():
            continue
        if ch == "ː" and out:          # 长音符号跟在元音后面，合并成单 token
            out[-1] = out[-1] + "ː"
            continue
        out.append(ch)
    # 归一化 + 白名单过滤；多字符 token（tʃ dʒ aɪ …）需要再合并一次
    return _merge_and_filter(out)


def _merge_and_filter(chars: List[str]) -> List[str]:
    """把字符级序列合并成 vocab 里存在的音素 token。"""
    vocab = _state.get("vocab") or {}
    keys = sorted((k for k in vocab if k and not k.startswith("<") and k not in ("[PAD]", "[UNK]")),
                  key=len, reverse=True)
    by_len: Dict[int, set] = {}
    for k in keys:
        by_len.setdefault(len(k), set()).add(k)
    max_len = max(by_len) if by_len else 1

    out: List[str] = []
    i = 0
    n = len(chars)
    while i < n:
        matched = None
        for ln in range(min(max_len, n - i), 0, -1):
            cand = "".join(chars[i:i + ln])
            cand = NORM.get(cand, cand)
            if cand in by_len.get(len(cand), set()):
                matched = (cand, ln)
                break
        if matched:
            out.append(matched[0])
            i += matched[1]
        else:
            ch = NORM.get(chars[i], chars[i])
            if ch in EN_PHONES:
                out.append(ch)
            i += 1
    return out


def text_to_phones(text: str, words: Optional[Sequence[str]] = None) -> List[str]:
    """整句音素（拼接每个词的音素，用于全句模式）。"""
    seq: List[str] = []
    for w in (words if words is not None else (text or "").split()):
        seq.extend(word_to_phones(w))
    return seq


# 记法等价：同一个音，espeak 和模型写的不一样时不要判用户「读错」。
# 典型场景：作者写 ɛɹ / ɔːɹ，模型听成 ɛ / ɔː + ɹ（卷舌音单独成一个 token），
# 或者模型丢掉了长音符号。这些是标记差异，不是发音错误。
def _strip_len(p: str) -> str:
    return (p or "").replace("ː", "")


def equivalent_phones(expected: str, heard: str) -> bool:
    if not expected or not heard:
        return False
    if expected == heard:
        return True
    a, b = _strip_len(expected), _strip_len(heard)
    if a == b:
        return True
    # 卷舌色彩元音：Xɹ <-> X
    if a.endswith("ɹ") and a[:-1] == b:
        return True
    if b.endswith("ɹ") and b[:-1] == a:
        return True
    # 央元音/卷舌央元音
    if {a, b} <= {"ə", "ɚ"}:
        return True
    return False


# --------------------------------------------------------------------------
# CTC 强制对齐（2N+1 状态标准形式，支持相邻重复音素）
# --------------------------------------------------------------------------
NEG = -1e30


def _viterbi(logp: np.ndarray, targets: Sequence[int], blank: int):
    """CTC 强制对齐的核心。

    状态序列是标准的 2N+1 形式（blank 与音素交替），这样相邻的重复音素也能对齐。
    **返回的路径一定从终止状态（最后一个 blank）回溯**——不是从 argmax 回溯，
    因为「强制」对齐的语义就是「无论音频多不像，都必须把这条音素串走完」。
    早期版本从 argmax 回溯，导致音频不匹配时后面的音素拿不到帧、误判成漏读。
    返回 (每帧的 state 下标, 路径总分, 状态序列 label 表)。
    """
    T = int(logp.shape[0])
    N = len(targets)
    S = 2 * N + 1
    labels = np.empty(S, dtype=np.int64)
    labels[0::2] = blank
    labels[1::2] = np.asarray(targets, dtype=np.int64)
    if T == 0 or N == 0:
        return np.zeros(T, dtype=np.int64), 0.0, labels
    emit_all = logp[:, labels]
    a = np.full(S, NEG, dtype=np.float64)
    a[0] = 0.0
    back = np.zeros((T, S), dtype=np.int8)
    diff2 = np.zeros(S, dtype=bool)
    if S > 2:
        diff2[2:] = labels[2:] != labels[:-2]
    for t in range(T):
        emit = emit_all[t]
        stay = a + emit
        adv1 = np.full(S, NEG); adv1[1:] = a[:-1]; adv1 += emit
        adv2 = np.full(S, NEG); adv2[2:] = a[:-2]; adv2 += emit
        best = stay.copy(); bp = np.zeros(S, dtype=np.int8)
        m = adv1 > best
        best[m] = adv1[m]; bp[m] = 1
        ok2 = diff2 & (adv2 > best)
        best[ok2] = adv2[ok2]; bp[ok2] = 2
        a = best
        back[t] = bp
    # 从终止状态回溯
    s = S - 1
    score = float(a[s])
    path = np.zeros(T, dtype=np.int64)
    for t in range(T - 1, -1, -1):
        path[t] = s
        s -= int(back[t, s])
    return path, score, labels


def forced_align(logp: np.ndarray, targets: Sequence[int], blank: int) -> Tuple[np.ndarray, float]:
    """返回 (每帧对应的音素 id 或 -1 表示 blank, 路径总对数概率)。"""
    path, score, labels = _viterbi(logp, targets, blank)
    frames = np.where(path % 2 == 1, labels[np.clip(path, 0, len(labels) - 1)], -1)
    return frames.astype(np.int64), score


def greedy_decode(logp: np.ndarray, blank: int, id2phone: Dict[int, str]) -> List[str]:
    """贪心 CTC 解码（合并重复、去 blank），返回音素序列。"""
    ids = logp.argmax(-1)
    out: List[str] = []
    prev = -1
    for i in ids:
        i = int(i)
        if i != prev and i != blank:
            ph = id2phone.get(i)
            if ph:
                out.append(ph)
        prev = i
    return out


# --------------------------------------------------------------------------
# 编辑距离（加权，输出 替换/遗漏/多余）
# --------------------------------------------------------------------------
def nw_align(exp: Sequence[str], hyp: Sequence[str]) -> List[Dict[str, Any]]:
    n, m = len(exp), len(hyp)
    dp = np.zeros((n + 1, m + 1), dtype=np.float64)
    for i in range(1, n + 1):
        dp[i][0] = i
    for j in range(1, m + 1):
        dp[0][j] = j
    def sub_cost(a: str, b: str) -> float:
        if a == b:
            return 0.0
        return 0.6 if (a, b) in CONFUSIONS else 1.0
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            dp[i][j] = min(
                dp[i - 1][j - 1] + sub_cost(exp[i - 1], hyp[j - 1]),
                dp[i - 1][j] + 1.0,
                dp[i][j - 1] + 1.0,
            )
    ops: List[Dict[str, Any]] = []
    i, j = n, m
    while i > 0 or j > 0:
        if i > 0 and j > 0 and dp[i][j] == dp[i - 1][j - 1] + sub_cost(exp[i - 1], hyp[j - 1]):
            if exp[i - 1] == hyp[j - 1]:
                ops.append({"type": "ok", "expected": exp[i - 1], "heard": hyp[j - 1]})
            else:
                ops.append({"type": "sub", "expected": exp[i - 1], "heard": hyp[j - 1]})
            i -= 1; j -= 1
        elif i > 0 and dp[i][j] == dp[i - 1][j] + 1.0:
            ops.append({"type": "del", "expected": exp[i - 1], "heard": ""})
            i -= 1
        else:
            ops.append({"type": "ins", "expected": "", "heard": hyp[j - 1]})
            j -= 1
    ops.reverse()
    return ops


# --------------------------------------------------------------------------
# 核心：一段音频 + 期望音素 -> 逐音素判定
# --------------------------------------------------------------------------
def _log_softmax(x: np.ndarray) -> np.ndarray:
    mx = x.max(axis=-1, keepdims=True)
    e = np.exp(x - mx)
    return (x - mx) - np.log(e.sum(axis=-1, keepdims=True) + 1e-12)


def phone_id_maps() -> Tuple[Dict[str, int], Dict[int, str]]:
    vocab = _state.get("vocab") or {}
    p2i: Dict[str, int] = {}
    i2p: Dict[int, str] = {}
    for tok, idx in vocab.items():
        if not tok or tok.startswith("<") or tok in ("[PAD]", "[UNK]", "|"):
            continue
        t = NORM.get(tok, tok)
        if t in EN_PHONES:
            p2i.setdefault(t, int(idx))
            i2p[int(idx)] = t
    return p2i, i2p


def assess_audio(wav: np.ndarray, expected: Sequence[str], sr: int = 16000) -> Dict[str, Any]:
    """对一小段音频做音素级评估。expected 是期望音素序列（IPA）。"""
    ok, why = available()
    if not ok:
        return {"ok": False, "reason": why}
    if len(wav) < int(0.12 * sr) or not len(expected):
        return {"ok": False, "reason": "音频太短或没有期望音素"}
    # 静音护栏：宁可说「没听清」，也不要冤枉用户「漏读」
    rms = float(np.sqrt(np.mean(np.square(wav)))) if len(wav) else 0.0
    if rms < settings.phoneme_min_rms:
        return {"ok": False, "rms": round(rms, 5),
                "reason": "这段录音几乎是静音（麦克风没录上或离太远），没法判断发音"}

    fe, model = load()
    torch = _state["torch"]
    p2i, i2p = phone_id_maps()
    exp_ids = [p2i[p] for p in expected if p in p2i]
    if not exp_ids:
        return {"ok": False, "reason": "期望音素不在模型词表里"}

    with torch.inference_mode():
        inputs = fe(wav, sampling_rate=sr, return_tensors="pt")
        logits = model(inputs.input_values).logits[0].float().numpy()
    logp = _log_softmax(logits)
    prob = np.exp(logp)
    blank = int(_state["blank"])

    frames, path_score = forced_align(logp, exp_ids, blank)
    hyp = greedy_decode(logp, blank, i2p)
    spans = _target_spans(logp, exp_ids, blank)
    # 自由解码一个音素都没有 = 这段音频里没有语音内容。
    # 此时强制对齐会把所有音素都塞进少数几帧，得出「全漏读」的错误结论。
    unclear = len(hyp) == 0

    # 逐音素：帧区间 -> GOP -> 判定
    phones: List[Dict[str, Any]] = []
    for k, ph in enumerate(expected):
        if ph not in p2i:
            continue
        pid = p2i[ph]
        s_frames = spans[k] if k < len(spans) else np.array([], dtype=np.int64)
        if unclear or len(s_frames) == 0:
            phones.append({"p": ph, "status": "unclear" if unclear else "del", "heard": "",
                           "score": 0.0, "gop": -9.9, "t": [0.0, 0.0]})
            continue
        seg = prob[s_frames]
        tgt = seg[:, pid]
        best = seg.max(-1)
        gop_nats = float(np.mean(np.log(np.clip(tgt, 1e-9, 1)) - np.log(np.clip(best, 1e-9, 1))))
        gop = gop_nats / math.log(10.0)            # 换成 log10，和配置里的阈值口径一致
        heard_ids = seg.argmax(-1)
        heard_ids = heard_ids[heard_ids != blank]
        heard = ""
        if len(heard_ids):
            vals, counts = np.unique(heard_ids, return_counts=True)
            heard = i2p.get(int(vals[int(np.argmax(counts))]), "")
        thr = settings.phoneme_gop_threshold
        same = equivalent_phones(ph, heard)
        # 判定规则（宁可放过、不可错杀 —— 误报「读错」比漏报更打击人）：
        #   听到了同一个音      -> ok，只有后验特别低才标 weak（含糊）
        #   没听到明确的音素    -> 后验也差才算 del（漏读），否则 weak
        #   听到了别的音        -> GOP 低才是真替换，GOP 高更像模型听岔了，标 weak
        if same:
            status = "weak" if gop < (thr - 0.6) else "ok"
        elif not heard:
            status = "del" if gop < thr else "weak"
        else:
            status = "sub" if gop < thr else "weak"
        f0 = int(s_frames[0]); f1 = int(s_frames[-1])
        phones.append({
            "p": ph, "status": status, "heard": heard,
            "gop": round(gop, 2), "score": round(max(0.0, min(100.0, 100.0 + gop * 42)), 1),
            "t": [round(f0 / 50.0, 3), round((f1 + 1) / 50.0, 3)],   # 模型 50 帧/秒
        })

    ops = nw_align(expected, hyp)
    insertions = [o["heard"] for o in ops if o["type"] == "ins"]
    subs = [o for o in ops if o["type"] == "sub"]
    dels = [o for o in ops if o["type"] == "del"]

    n_ok = sum(1 for p in phones if p["status"] == "ok")
    n_weak = sum(1 for p in phones if p["status"] == "weak")
    problems = [p for p in phones if p["status"] in ("sub", "del")]
    total = max(1, len(phones))
    return {
        "ok": True,
        "unclear": unclear,
        "rms": round(rms, 5),
        "expected": list(expected),
        "heard": hyp,
        "phones": phones,
        "insertions": insertions[:8],
        "substitutions": subs[:8],
        "deletions": dels[:8],
        # accuracy = 「识别出来了」的比例（ok + 含糊），problems 才是真正该改的音
        "accuracy": round((n_ok + n_weak) / total * 100, 1),
        "strict_accuracy": round(n_ok / total * 100, 1),
        "problems": len(problems),
        "path_score": round(path_score, 2),
    }


def _target_spans(logp: np.ndarray, exp_ids: Sequence[int], blank: int) -> List[np.ndarray]:
    """返回每个目标音素占用的帧下标（用同一条强制对齐路径）。"""
    N = len(exp_ids)
    if N == 0:
        return []
    path, _score, _labels = _viterbi(logp, exp_ids, blank)
    spans: List[np.ndarray] = []
    for k in range(N):
        idx = np.where(path == (2 * k + 1))[0]
        if len(idx) == 0 and k + 1 < N:
            # 这个音素被挤没了：把它后面那个 blank 的帧借过来（至少给个位置）
            idx = np.where(path == (2 * k + 2))[0]
        spans.append(idx)
    return spans


# --------------------------------------------------------------------------
# 上层入口：对「可疑词」做音素精评
# --------------------------------------------------------------------------
def assess_words(wav: np.ndarray, sr: int, items: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """items: [{"word": "think", "start": s, "end": e, "problem": "wrong"}, ...]
    返回每个词的音素级结论。会做「切片 + 上下文」和缓存。
    """
    out: List[Dict[str, Any]] = []
    for it in items:
        word = str(it.get("word") or "")
        expected = word_to_phones(word)
        if not expected:
            out.append({"word": word, "ok": False, "reason": "取不到音标"})
            continue
        pad = 0.25
        s = max(0.0, float(it.get("start") or 0) - pad)
        e = min(len(wav) / sr, float(it.get("end") or 0) + pad)
        clip = wav[int(s * sr):int(e * sr)]
        res = assess_audio(clip, expected, sr=sr)
        res["word"] = word
        res["clip"] = [round(s, 2), round(e, 2)]
        res["problem"] = it.get("problem") or ""
        out.append(res)
    return out


def tips_for(result: Dict[str, Any]) -> List[str]:
    """把音素错误翻译成人话 + 发音要点。"""
    tips: List[str] = []
    if result.get("unclear"):
        return ["这段录音里几乎听不到语音，先确认麦克风录上了再练"]
    for p in result.get("phones") or []:
        if p.get("status") in ("sub", "del", "weak"):
            key = (p.get("p"), p.get("heard"))
            tip = PHONE_TIPS.get(key)
            if tip:
                tips.append(tip)
            elif p.get("status") == "del":
                tips.append(f"/{p.get('p')}/ 几乎没发出来（漏音）")
            elif p.get("status") == "sub":
                tips.append(f"/{p.get('p')}/ 听起来像 /{p.get('heard')}/")
    for extra in result.get("insertions") or []:
        tips.append(f"多读了一个 /{extra}/，注意别加音")
    return tips[:4]
