"""报告导出的纯逻辑单元测试（T21/M4-05，#22；契约 §2/§3/§5/§7 CT-12）。

seam：不访问数据库、不启动 HTTP 的纯函数——

- ``canonical_json_bytes`` / ``content_hash``：同一个报告文档必须产生逐字节
  相同的 JSON 与稳定 SHA-256（CT-12 的“旧报告不变”以哈希为证据）；
- ``render_html``：静态 HTML（无脚本、无外部资源），对会话原文、理由、证据
  引用做 HTML 转义（XSS 安全）；
- ``ReportExportRequest``：``format`` 只接受 json/html，其他值在 422 层被拒；
- ``resolve_artifact_path``：artifact_ref 不能逃出 ``ARTIFACT_ROOT``。
"""

from __future__ import annotations

import hashlib
from uuid import uuid4

import pytest
from pydantic import ValidationError

from bank_assistant.api.models.reports import ReportExportRequest
from bank_assistant.reports.schema import (
    REPORT_FORMAT_VALUES,
    ReportArtifactError,
    canonical_json_bytes,
    content_hash,
    render_html,
    report_artifact_ref,
    resolve_artifact_path,
)

# 固定文档：不依赖数据库与当前时间，只验证序列化与转义。
FIXED_DOCUMENT: dict[str, object] = {
    "report_version": "report.v1",
    "inspection": {
        "inspection_id": "0b9c7f8e-1a2b-4c3d-8e9f-0123456789ab",
        "status": "succeeded",
    },
    "review": {"review_id": None, "review_revision": 0},
    "conversation": {
        "turn_count": 1,
        "turns": [
            {
                "turn_id": "11111111-2222-4333-8444-555555555555",
                "ordinal": 1,
                "speaker": "客户",
                "content": "请问年费怎么算",
            }
        ],
    },
    "findings": [],
    "summary": {"finding_count": 0},
}


def test_canonical_json_bytes_are_order_independent_and_utf8() -> None:
    """字典插入顺序不影响字节；中文按 UTF-8 原样输出（不转 \\u 转义）。"""
    reordered = {
        "summary": {"finding_count": 0},
        "findings": [],
        "conversation": {
            "turns": [
                {
                    "content": "请问年费怎么算",
                    "speaker": "客户",
                    "ordinal": 1,
                    "turn_id": "11111111-2222-4333-8444-555555555555",
                }
            ],
            "turn_count": 1,
        },
        "review": {"review_revision": 0, "review_id": None},
        "inspection": {
            "status": "succeeded",
            "inspection_id": "0b9c7f8e-1a2b-4c3d-8e9f-0123456789ab",
        },
        "report_version": "report.v1",
    }

    assert canonical_json_bytes(FIXED_DOCUMENT) == canonical_json_bytes(reordered)
    first = canonical_json_bytes(FIXED_DOCUMENT)
    assert isinstance(first, bytes)
    assert "请问年费怎么算".encode("utf-8") in first
    assert b"\\u" not in first  # 不把中文转成 ASCII 转义，便于人工核对
    # 稳定的分隔符：无多余空格，键按字典序排列。
    assert first.startswith(b'{"conversation":')


def test_content_hash_is_a_stable_sha256_of_the_exact_bytes() -> None:
    """哈希是内容字节的 SHA-256；同文档同哈希、改一个字即不同。"""
    payload = canonical_json_bytes(FIXED_DOCUMENT)
    # 独立来源：直接用 hashlib 对同一份字节计算，并固定已知值。
    assert content_hash(payload) == hashlib.sha256(payload).hexdigest()
    assert (
        content_hash(b'{"a":1}')
        == "015abd7f5cc57a2dd94b7590f04ad8084273905ee33ec5cebeae62276a97f862"
    )
    assert content_hash(payload) == content_hash(payload)
    changed = {**FIXED_DOCUMENT, "report_version": "report.v2"}
    assert content_hash(canonical_json_bytes(changed)) != content_hash(payload)


def test_render_html_escapes_user_content_and_has_no_script_or_external_assets() -> None:
    """会话原文里的 <script> 与引号必须被转义，HTML 保持静态。"""
    payload = '<script>alert("x")</script> & <img src="http://evil.invalid/x.png">'
    document = {
        **FIXED_DOCUMENT,
        "conversation": {
            "turn_count": 1,
            "turns": [
                {
                    "turn_id": "11111111-2222-4333-8444-555555555555",
                    "ordinal": 1,
                    "speaker": "<b>客服</b>",
                    "content": payload,
                }
            ],
        },
        "findings": [
            {
                "finding_id": "22222222-3333-4444-8555-666666666666",
                "rule_code": "DEMO-XSS-01",
                "severity": "high",
                "machine_verdict": "hit",
                "human_final_verdict": None,
                "pending_business_confirmation": False,
                "rationale": "<em>理由</em> 含 <script>bad()</script>",
                "evidence": [
                    {
                        "turn_ordinal": 1,
                        "quote": payload,
                        "validated": True,
                    }
                ],
                "knowledge_refs": [{"version_id": "v", "chunk_id": "c"}],
            }
        ],
    }

    html = render_html(document)
    lowered = html.lower()

    assert "<script" not in lowered
    assert "<img" not in lowered
    assert "javascript:" not in lowered
    assert "<link" not in lowered
    assert 'src="http' not in lowered
    assert "&lt;script&gt;" in html
    assert "&lt;b&gt;客服&lt;/b&gt;" in html
    assert "&amp;" in html
    assert "&quot;" in html or "&#x27;" in html
    # 静态页面仍要含真实报告内容与中文标题。
    assert "DEMO-XSS-01" in html
    assert "质检报告" in html
    assert "<!DOCTYPE html>" in html


def test_html_render_escapes_verdict_values_used_as_css_hooks() -> None:
    """被判为未知 verdict 时不能把任意字符串拼进 class 属性。"""
    document = {
        **FIXED_DOCUMENT,
        "findings": [
            {
                "finding_id": "33333333-4444-4555-8666-777777777777",
                "rule_code": "X",
                "severity": '"><script>alert(1)</script>',
                "machine_verdict": '"><script>alert(2)</script>',
                "human_final_verdict": None,
                "pending_business_confirmation": False,
                "rationale": "",
                "evidence": [],
                "knowledge_refs": [],
            }
        ],
    }
    html = render_html(document).lower()
    assert "<script" not in html
    assert "&quot;&gt;&lt;script&gt;" in html or "&lt;script&gt;" in html


def test_report_formats_are_exactly_json_and_html() -> None:
    assert REPORT_FORMAT_VALUES == ("json", "html")


def test_format_validation_rejects_unknown_values() -> None:
    """路由层据此返回 422；请求模型不接受 json/html 之外的格式。"""
    inspection_id = uuid4()
    assert (
        ReportExportRequest.model_validate(
            {"inspection_id": str(inspection_id), "format": "json"}
        ).format
        == "json"
    )
    with pytest.raises(ValidationError):
        ReportExportRequest.model_validate(
            {"inspection_id": str(inspection_id), "format": "pdf"}
        )
    with pytest.raises(ValidationError):
        ReportExportRequest.model_validate(
            {"inspection_id": str(inspection_id), "format": "JSON "}
        )


def test_artifact_ref_is_relative_and_cannot_escape_the_artifact_root() -> None:
    export_id = uuid4()
    assert report_artifact_ref(export_id, "json") == (
        f"exports/reports/{export_id}.json"
    )
    assert report_artifact_ref(export_id, "html") == (
        f"exports/reports/{export_id}.html"
    )

    with pytest.raises(ReportArtifactError):
        resolve_artifact_path("artifacts", "../secrets.txt")
    with pytest.raises(ReportArtifactError):
        resolve_artifact_path("artifacts", "/etc/passwd")
    with pytest.raises(ReportArtifactError):
        resolve_artifact_path("artifacts", "exports/../../secrets.txt")

    resolved = resolve_artifact_path("artifacts", f"exports/reports/{export_id}.json")
    assert resolved.is_absolute()
