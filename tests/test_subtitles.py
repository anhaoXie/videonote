# -*- coding: utf-8 -*-
"""subtitles 纯函数的回归测试。

这些函数只做字符串/时间戳处理，不依赖 ffmpeg、模型或网络，所以可以在
任意装了 pytest 的环境里直接跑：

    python -m pytest tests/test_subtitles.py -q
"""
from __future__ import annotations

import pytest

from videonote.pipeline.subtitles import _fmt, _wrap, to_srt, to_vtt


class TestFmt:
    def test_plain_values(self):
        assert _fmt(0) == "00:00:00,000"
        assert _fmt(1.5) == "00:00:01,500"
        assert _fmt(61.25) == "00:01:01,250"
        assert _fmt(3661.001) == "01:01:01,001"

    def test_negative_is_clamped_to_zero(self):
        assert _fmt(-3) == "00:00:00,000"

    @pytest.mark.parametrize(
        "seconds, expected",
        [
            (59.9996, "00:01:00,000"),
            (3599.9996, "01:00:00,000"),
            (86399.9996, "24:00:00,000"),
        ],
    )
    def test_rounding_carries_into_next_unit(self, seconds, expected):
        # 回归：旧实现会在 59.9996s 输出 00:00:60,000（非法时间戳）
        assert _fmt(seconds) == expected

    def test_vtt_uses_dot_separator(self):
        assert _fmt(1.5, ".") == "00:00:01.500"


class TestToSrt:
    def test_numbering_and_min_duration(self):
        cues = [
            {"start": 0.0, "end": 0.1, "text": "hello"},
            {"start": 1.0, "end": 2.0, "text": "world"},
        ]
        lines = to_srt(cues).split("\n")

        assert lines[0] == "1"
        assert lines[1] == "00:00:00,000 --> 00:00:00,400"  # 短于 0.4s 的 cue 会被抬长
        assert lines[2] == "hello"
        assert lines[4] == "2"
        assert lines[5] == "00:00:01,000 --> 00:00:02,000"

    def test_ends_with_blank_line(self):
        assert to_srt([{"start": 0, "end": 1, "text": "x"}]).endswith("\n")


class TestToVtt:
    def test_header_and_dot_timestamps(self):
        vtt = to_vtt([{"start": 0.0, "end": 1.0, "text": "hi"}])
        assert vtt.startswith("WEBVTT\n")
        assert "00:00:00.000 --> 00:00:01.000" in vtt
        assert "00:00:00,000" not in vtt


class TestWrap:
    def test_chinese_text_loses_no_character(self):
        text = "这是一个很长的中文字幕句子，用来验证自动换行不会丢掉任何一个字。"
        wrapped = _wrap(text, limit=12)
        assert wrapped.replace("\n", "") == text
        assert len(wrapped.split("\n")) > 1

    def test_english_words_are_not_split(self):
        text = "hello world this is a subtitle wrap test"
        wrapped = _wrap(text, limit=10)
        assert " ".join(wrapped.split()) == text
        for line in wrapped.split("\n"):
            assert " " not in line.strip() or all(len(w) <= 10 for w in line.split(" "))

    def test_existing_newlines_are_preserved_as_separate_lines(self):
        wrapped = _wrap("第一行\n第二行", limit=50)
        assert "第一行" in wrapped
        assert "第二行" in wrapped

    def test_empty_text(self):
        assert _wrap("") == ""
