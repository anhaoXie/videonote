# -*- coding: utf-8 -*-
"""最小对立对（minimal pair）单词库：训练「差一个音就变另一个词」的耳朵和嘴。

为什么中文母语者特别需要这个：
    汉语的音节结构和英语不同，很多人**根本听不出** /iː/ 和 /ɪ/ 的区别，
    所以先说错、再听不出自己说错。最小对立对是纠正这个的标准方法：
    一次只改一个音，让"差一点"变成"差很多"。

数据里每一对包含：
    a / b   —— 两个词
    phones  —— 用来对照的**区分音素**（a 的是哪个、b 的是哪个）
    tip     —— 嘴巴怎么放的要点（中文）
    note    —— 这对词在语义上的提示，帮助记忆

音频由 espeak-ng 现场合成（离线、零成本）。
**但不是每一对都收录**：合成音要能被音素模型听出区别才算数，
过滤结果由 tools/verify_pairs.py 产出，存在 data/minimal_pairs_verified.json。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Optional

from ..config import settings

# 音素组：key 是「对照标识」，用于按用户的错音自动组题
PAIRS: List[Dict] = [
    # ---- 长短元音（中文母语者第一大坑）----
    {"id": "iy-ih", "a": "sheep", "b": "ship", "pa": "iː", "pb": "ɪ", "group": "长短元音 iː / ɪ",
     "tip": "/iː/ 嘴角向两边拉、拉长；/ɪ/ 短而松弛，不要拉长"},
    {"id": "iy-ih-2", "a": "feet", "b": "fit", "pa": "iː", "pb": "ɪ", "group": "长短元音 iː / ɪ",
     "tip": "feet 的元音要长、紧；fit 要短、松"},
    {"id": "iy-ih-3", "a": "leave", "b": "live", "pa": "iː", "pb": "ɪ", "group": "长短元音 iː / ɪ",
     "tip": "leave（离开）比 live（住）元音长一倍"},
    {"id": "uw-uh", "a": "fool", "b": "full", "pa": "uː", "pb": "ʊ", "group": "长短元音 uː / ʊ",
     "tip": "/uː/ 嘴唇收圆并拉长；/ʊ/ 短、嘴稍放松"},
    {"id": "uw-uh-2", "a": "pool", "b": "pull", "pa": "uː", "pb": "ʊ", "group": "长短元音 uː / ʊ",
     "tip": "pool 长，pull 短"},
    {"id": "ae-e", "a": "bad", "b": "bed", "pa": "æ", "pb": "ɛ", "group": "æ / ɛ",
     "tip": "/æ/ 下巴要放下来、嘴张大；/ɛ/ 只要半开"},
    {"id": "ae-e-2", "a": "man", "b": "men", "pa": "æ", "pb": "ɛ", "group": "æ / ɛ",
     "tip": "man 的嘴比 men 张得更大"},
    {"id": "ae-e-3", "a": "sat", "b": "set", "pa": "æ", "pb": "ɛ", "group": "æ / ɛ",
     "tip": "sat 张嘴，set 半张"},
    # ---- 咬舌音 ----
    {"id": "th-s", "a": "think", "b": "sink", "pa": "θ", "pb": "s", "group": "θ / s",
     "tip": "/θ/ 舌尖轻放在上下齿之间送气；/s/ 舌尖在齿后"},
    {"id": "th-s-2", "a": "thick", "b": "sick", "pa": "θ", "pb": "s", "group": "θ / s",
     "tip": "thick 要咬舌，sick 不要"},
    {"id": "th-f", "a": "three", "b": "free", "pa": "θ", "pb": "f", "group": "θ / f",
     "tip": "/θ/ 用舌尖，/f/ 用下唇碰上门齿 —— 别混"},
    {"id": "dh-z", "a": "breathe", "b": "breeze", "pa": "ð", "pb": "z", "group": "ð / z",
     "tip": "/ð/ 舌尖出齿并振动声带"},
    {"id": "th-t", "a": "thin", "b": "tin", "pa": "θ", "pb": "t", "group": "θ / t",
     "tip": "thin 咬舌，tin 不咬"},
    # ---- v / w ----
    {"id": "v-w", "a": "vest", "b": "west", "pa": "v", "pb": "w", "group": "v / w",
     "tip": "/v/ 上齿必须碰到下唇；/w/ 圆唇、不碰齿"},
    {"id": "v-w-2", "a": "vine", "b": "wine", "pa": "v", "pb": "w", "group": "v / w",
     "tip": "vine 咬唇，wine 收圆唇"},
    # ---- ɹ / l ----
    {"id": "r-l", "a": "right", "b": "light", "pa": "ɹ", "pb": "l", "group": "ɹ / l",
     "tip": "/ɹ/ 舌尖卷起但不碰上腭；/l/ 舌尖要抵住上齿龈"},
    {"id": "r-l-2", "a": "rice", "b": "lice", "pa": "ɹ", "pb": "l", "group": "ɹ / l",
     "tip": "rice 卷舌，lice 顶齿龈"},
    {"id": "r-l-3", "a": "pray", "b": "play", "pa": "ɹ", "pb": "l", "group": "ɹ / l",
     "tip": "连在辅音后面时最容易混"},
    # ---- ŋ / n ----
    {"id": "ng-n", "a": "sing", "b": "sin", "pa": "ŋ", "pb": "n", "group": "ŋ / n",
     "tip": "/ŋ/ 舌根抵软腭，-ing 结尾要收住"},
    {"id": "ng-n-2", "a": "thing", "b": "thin", "pa": "ŋ", "pb": "n", "group": "ŋ / n",
     "tip": "thing 结尾是鼻音 /ŋ/，thin 是 /n/"},
    {"id": "ng-nk", "a": "ban", "b": "bang", "pa": "n", "pb": "ŋ", "group": "ŋ / n",
     "tip": "bang 的舌根要抬起来"},
    # ---- 词尾清浊 ----
    {"id": "p-b", "a": "cap", "b": "cab", "pa": "p", "pb": "b", "group": "词尾清浊 p / b",
     "tip": "词尾 /p/ 不振动声带，/b/ 要振动（很多人词尾都读成清音）"},
    {"id": "t-d", "a": "bat", "b": "bad", "pa": "t", "pb": "d", "group": "词尾清浊 t / d",
     "tip": "bad 的词尾要出声音"},
    {"id": "k-g", "a": "back", "b": "bag", "pa": "k", "pb": "ɡ", "group": "词尾清浊 k / g",
     "tip": "bag 结尾振动声带"},
    {"id": "s-z", "a": "price", "b": "prize", "pa": "s", "pb": "z", "group": "词尾清浊 s / z",
     "tip": "prize 的 /z/ 要振动"},
    {"id": "f-v", "a": "leaf", "b": "leave", "pa": "f", "pb": "v", "group": "词尾清浊 f / v",
     "tip": "leave 结尾咬唇并振动"},
    # ---- 其他高频混淆 ----
    {"id": "sh-s", "a": "ship", "b": "sip", "pa": "ʃ", "pb": "s", "group": "ʃ / s",
     "tip": "/ʃ/ 嘴唇稍收圆、舌面靠近硬腭"},
    {"id": "ch-sh", "a": "cheap", "b": "sheep", "pa": "tʃ", "pb": "ʃ", "group": "tʃ / ʃ",
     "tip": "cheap 前面有个 /t/ 的成阻"},
    {"id": "l-n", "a": "light", "b": "night", "pa": "l", "pb": "n", "group": "l / n",
     "tip": "/l/ 舌尖抵齿龈但气流从舌两侧走"},
    {"id": "z-dh", "a": "zoo", "b": "the", "pa": "z", "pb": "ð", "group": "z / ð",
     "tip": "zoo 舌尖在齿后，the 舌尖出齿"},
    {"id": "aa-ah", "a": "cot", "b": "cut", "pa": "ɑː", "pb": "ʌ", "group": "ɑː / ʌ",
     "tip": "cot 嘴张开发后元音，cut 是短促的央元音"},
    {"id": "ao-ah", "a": "caught", "b": "cut", "pa": "ɔː", "pb": "ʌ", "group": "ɔː / ʌ",
     "tip": "caught 圆唇后元音"},
    {"id": "er-ao", "a": "bird", "b": "bored", "pa": "ɜː", "pb": "ɔːɹ", "group": "ɜː / ɔːɹ",
     "tip": "bird 是卷舌央元音，bored 更圆"},
    {"id": "e-a", "a": "pen", "b": "pan", "pa": "ɛ", "pb": "æ", "group": "ɛ / æ",
     "tip": "pan 的嘴要张大"},
    {"id": "oy-ow", "a": "boy", "b": "bow", "pa": "ɔɪ", "pb": "oʊ", "group": "ɔɪ / oʊ",
     "tip": "boy 是从 /ɔ/ 滑向 /ɪ/"},
]


def verified_path() -> Path:
    return settings.data_dir / "minimal_pairs_verified.json"


def load_verified() -> Dict[str, dict]:
    """读「合成音验证过」的结果；没验证过返回空 dict（前端会提示先跑验证）。"""
    p = verified_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8")).get("pairs", {})
    except Exception:  # noqa: BLE001
        return {}


def all_pairs(only_verified: bool = True) -> List[dict]:
    """返回词对列表。only_verified=True 时只给音频能听出区别的。"""
    v = load_verified()
    out: List[dict] = []
    for p in PAIRS:
        item = dict(p)
        info = v.get(p["id"])
        item["verified"] = bool(info and info.get("ok"))
        item["audio"] = info or {}
        if only_verified and not item["verified"]:
            continue
        out.append(item)
    return out


def groups() -> List[Dict]:
    seen: Dict[str, int] = {}
    for p in PAIRS:
        seen[p["group"]] = seen.get(p["group"], 0) + 1
    return [{"name": k, "count": v} for k, v in seen.items()]


def find(pair_id: str) -> Optional[dict]:
    for p in PAIRS:
        if p["id"] == pair_id:
            return p
    return None


def matching_contrast(phoneme: str) -> List[str]:
    """按用户读错的音素，找出该练哪几对。"""
    out = []
    for p in PAIRS:
        if phoneme in (p["pa"], p["pb"]):
            out.append(p["id"])
    return out
