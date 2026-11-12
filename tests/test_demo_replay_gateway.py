"""MODEL_MODE=replay 的显式回放接线测试（T25）。"""

from __future__ import annotations

import json

from bank_assistant.assistants.candidate_answer import CandidateAnswerPayload
from bank_assistant.config.settings import settings
from bank_assistant.core.model_gateway import ModelGateway


def test_from_settings_replay_does_not_need_cloud_credentials(monkeypatch):
    monkeypatch.setattr(settings, "MODEL_MODE", "replay")
    gateway = ModelGateway.from_settings()
    assert gateway.mode == "replay"

    payload = {
        "query": "银行卡丢了怎么挂失？",
        "business_type": "lost_card",
        "knowledge": [
            {
                "chunk_id": "00000000-0000-0000-0000-000000000201",
                "version_id": "00000000-0000-0000-0000-000000000301",
                "title": "挂失渠道",
                "source_type": "demo_policy",
                "business_type": "lost_card",
                "text": "请通过官方手机银行或客服热线办理挂失。",
            }
        ],
    }
    result = gateway.structured(
        [
            {"role": "system", "content": "只依据知识"},
            {"role": "user", "content": "请生成候选答复\n" + json.dumps(payload, ensure_ascii=False)},
        ],
        schema=CandidateAnswerPayload,
    )
    assert result.ok
    assert result.mode == "replay"
    assert result.provider is None
    assert result.data.status == "answered"
