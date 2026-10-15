# -*- coding: utf-8 -*-
"""假的视觉大模型服务 —— 用来在没有 API key 的情况下验证「画面描述」整条管线。

    .venv\\Scripts\\python.exe tools\\mock_vlm.py          # 监听 127.0.0.1:8799

然后在 .env 里填：
    VN_VISION_PROVIDER=vlm
    VN_VLM_BASE_URL=http://127.0.0.1:8799/v1
    VN_VLM_API_KEY=mock
    VN_VLM_MODEL=mock-vl

它会按调用顺序返回不同的假描述，这样你能确认「每帧都拿到了自己的描述」。
"""
from __future__ import annotations

import itertools
import time

from fastapi import FastAPI, Request
import uvicorn

app = FastAPI(title="Mock VLM")
_counter = itertools.count(1)
SAMPLES = [
    "画面是一张幻灯片，标题写着「2024 年增长目标」，下方有三个要点。",
    "画面里是代码编辑器，正在演示一个 Python 函数的定义。",
    "画面是数据图表，柱状图对比了两组数字。",
    "画面是白板，上面写着一个公式和一个箭头图示。",
]


@app.post("/v1/chat/completions")
async def chat(request: Request):
    body = await request.json()
    i = next(_counter)
    text = SAMPLES[i % len(SAMPLES)]
    print(f"[mock-vlm] 第 {i} 次调用 model={body.get('model')} 返回: {text}")
    return {
        "id": f"mock-{i}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": body.get("model", "mock-vl"),
        "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 30, "total_tokens": 130},
    }


@app.get("/v1/models")
def models():
    return {"data": [{"id": "mock-vl", "object": "model"}]}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8799, log_level="warning")
