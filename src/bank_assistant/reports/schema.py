"""报告导出的纯逻辑：固定格式序列化、内容哈希、静态 HTML 与产物路径（T21/#22）。

契约 §2 的 ``ReportExport`` 要求「内容对应固定快照」，§3 要求「最终报告由具体
``inspection_id`` 与 ``review_revision`` 生成，后续复核不篡改已有导出」，
CT-12 用「导出 → 改判 → 旧报告不变」验证。本模块把这些约束落成可单测的纯函数：

- :func:`canonical_json_bytes`：字典序键 + 紧凑分隔符 + UTF-8，保证同一文档
  在任何进程、任何字典插入顺序下都得到逐字节相同的 JSON；
- :func:`content_hash`：对**产物字节**做 SHA-256，导出时写入数据库；
- :func:`render_html`：静态 HTML（无 ``<script>``、无外部 JS/CSS/图片），所有
  动态文本都经 :func:`html.escape` 转义；CSS class 只来自固定白名单，绝不把
  原始 verdict 拼进属性；
- :func:`resolve_artifact_path`：``artifact_ref`` 只能是 ``ARTIFACT_ROOT`` 下的
  相对路径，拒绝绝对路径与 ``..`` 逃逸。

这里不访问数据库、不调用模型，也不读取配置。
"""

from __future__ import annotations

import hashlib
import html
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

__all__ = [
    "ARTIFACT_SUBDIR",
    "REPORT_FORMAT_HTML",
    "REPORT_FORMAT_JSON",
    "REPORT_FORMAT_VALUES",
    "REPORT_SCHEMA_VERSION",
    "ReportArtifactError",
    "canonical_json_bytes",
    "content_hash",
    "render_html",
    "report_artifact_ref",
    "resolve_artifact_path",
]

REPORT_SCHEMA_VERSION = "report.v1"
REPORT_FORMAT_JSON = "json"
REPORT_FORMAT_HTML = "html"
REPORT_FORMAT_VALUES: tuple[str, ...] = (REPORT_FORMAT_JSON, REPORT_FORMAT_HTML)

# 与计划书 ARTIFACT_ROOT 下的 exports/ 布局一致：<ARTIFACT_ROOT>/exports/reports/。
ARTIFACT_SUBDIR = "exports/reports"

# verdict → CSS class 的固定白名单；未知值一律落到 unknown，原值只作为转义后的
# 文本展示，绝不进入属性。
_VERDICT_CLASSES: dict[str, str] = {
    "hit": "verdict-hit",
    "clear": "verdict-clear",
    "insufficient_evidence": "verdict-insufficient",
    "not_applicable": "verdict-na",
}

_VERDICT_LABELS: dict[str, str] = {
    "hit": "命中",
    "clear": "合格",
    "insufficient_evidence": "证据不足",
    "not_applicable": "不适用",
}


class ReportArtifactError(Exception):
    """产物引用非法、文件缺失或内容哈希不匹配；调用方必须显式失败。"""


def _json_default(value: Any) -> Any:
    """报告文档只允许 JSON 原生类型；UUID/时间在这里显式转换。"""
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC).isoformat()
    raise TypeError(f"报告文档包含不可序列化类型: {type(value).__name__}")


def canonical_json_bytes(document: Mapping[str, Any]) -> bytes:
    """返回规范 JSON 字节：键排序、紧凑分隔符、UTF-8、不转义中文。"""
    return json.dumps(
        dict(document),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
        allow_nan=False,
    ).encode("utf-8")


def content_hash(payload: bytes) -> str:
    """报告产物的 SHA-256（十六进制小写，64 字符）。"""
    return hashlib.sha256(payload).hexdigest()


def report_artifact_ref(export_id: UUID, report_format: str) -> str:
    """导出产物的相对引用；只接受 json/html，路径不含主机绝对路径。"""
    if report_format not in REPORT_FORMAT_VALUES:
        raise ValueError(f"未知报告格式: {report_format}")
    return f"{ARTIFACT_SUBDIR}/{export_id}.{report_format}"


def resolve_artifact_path(root: str | Path, artifact_ref: str) -> Path:
    """把相对 artifact_ref 解析为 ``ARTIFACT_ROOT`` 内的绝对路径。

    绝对路径、``..`` 逃逸、以及解析后不在 root 下的符号链接目标都拒绝，
    避免 ``GET /reports/{id}`` 变成任意文件读取。
    """
    if not artifact_ref or Path(artifact_ref).is_absolute():
        raise ReportArtifactError("artifact_ref 必须是相对路径")
    root_path = Path(root).expanduser().resolve()
    candidate = (root_path / artifact_ref).resolve()
    if candidate == root_path or root_path not in candidate.parents:
        raise ReportArtifactError("artifact_ref 越出 ARTIFACT_ROOT")
    return candidate


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _esc(value: Any) -> str:
    """文本节点统一转义（含引号），任何动态内容都不做“安全 HTML”放行。"""
    return html.escape(_text(value), quote=True)


def _verdict_class(value: Any) -> str:
    return _VERDICT_CLASSES.get(_text(value), "verdict-unknown")


def _verdict_label(value: Any) -> str:
    return _VERDICT_LABELS.get(_text(value), _text(value))


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, (list, tuple)) else []


_CSS = """
body { font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif;
       margin: 2rem auto; max-width: 960px; padding: 0 1rem; color: #1f2933; }
h1 { font-size: 1.6rem; border-bottom: 2px solid #1f2933; padding-bottom: .4rem; }
h2 { font-size: 1.15rem; margin-top: 2rem; color: #243b53; }
.meta { color: #52606d; font-size: .9rem; }
dl { display: grid; grid-template-columns: 10rem 1fr; gap: .35rem 1rem; }
dt { color: #52606d; }
dd { margin: 0; word-break: break-all; }
.turns { padding-left: 1.2rem; }
.turn { margin: .6rem 0; }
.speaker { font-weight: 600; color: #243b53; }
.turn-content { white-space: pre-wrap; background: #f5f7fa; padding: .5rem .75rem;
                border-left: 3px solid #9fb3c8; margin-top: .2rem; }
.finding { border: 1px solid #d9e2ec; border-radius: 6px; padding: .75rem 1rem;
           margin: 1rem 0; }
.finding h3 { margin: 0 0 .5rem; font-size: 1rem; }
.verdict-hit { color: #b91c1c; font-weight: 600; }
.verdict-clear { color: #047857; font-weight: 600; }
.verdict-insufficient { color: #b45309; font-weight: 600; }
.verdict-na { color: #52606d; }
.verdict-unknown { color: #1f2933; }
.pending { color: #b45309; }
.evidence { margin: .3rem 0 0; padding-left: 1.2rem; }
.note { color: #52606d; font-size: .85rem; }
table { border-collapse: collapse; width: 100%; margin-top: .5rem; }
th, td { border: 1px solid #d9e2ec; padding: .35rem .5rem; text-align: left;
         vertical-align: top; }
th { background: #f0f4f8; }
""".strip()


def render_html(document: Mapping[str, Any]) -> str:
    """把报告文档渲染成静态、自包含且 XSS 安全的 HTML。"""
    inspection = _mapping(document.get("inspection"))
    review = _mapping(document.get("review"))
    conversation = _mapping(document.get("conversation"))
    findings = _list(document.get("findings"))
    summary = _mapping(document.get("summary"))

    title = f"质检报告 {_text(inspection.get('inspection_id'))}"

    parts: list[str] = [
        "<!DOCTYPE html>",
        '<html lang="zh-CN">',
        "<head>",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>{_esc(title)}</title>",
        f"<style>{_CSS}</style>",
        "</head>",
        "<body>",
        "<h1>质检报告</h1>",
        f'<p class="meta">报告版本：{_esc(document.get("report_version"))}</p>',
    ]

    parts.append("<section><h2>质检信息</h2><dl>")
    for label, key in (
        ("质检 ID", "inspection_id"),
        ("会话 ID", "conversation_id"),
        ("质检状态", "status"),
        ("会话原文哈希", "conversation_hash"),
        ("规则数量", "rule_count"),
        ("创建时间", "created_at"),
        ("开始时间", "started_at"),
        ("结束时间", "finished_at"),
    ):
        parts.append(f"<dt>{_esc(label)}</dt><dd>{_esc(inspection.get(key))}</dd>")
    parts.append("</dl></section>")

    parts.append("<section><h2>复核快照</h2><dl>")
    for label, key in (
        ("复核 ID", "review_id"),
        ("复核 revision", "review_revision"),
        ("该 revision 的决定数", "decision_count"),
    ):
        parts.append(f"<dt>{_esc(label)}</dt><dd>{_esc(review.get(key))}</dd>")
    parts.append("</dl></section>")

    parts.append("<section><h2>会话轮次（展示文本）</h2>")
    turns = _list(conversation.get("turns"))
    if turns:
        parts.append('<ol class="turns">')
        for turn in turns:
            item = _mapping(turn)
            parts.append('<li class="turn">')
            parts.append(
                f'<span class="speaker">第 {_esc(item.get("ordinal"))} 轮 · '
                f'{_esc(item.get("speaker"))}</span>'
            )
            parts.append(
                f'<div class="turn-content">{_esc(item.get("content"))}</div>'
            )
            parts.append("</li>")
        parts.append("</ol>")
    else:
        parts.append('<p class="note">（无可用会话轮次）</p>')
    if conversation.get("redaction_applied"):
        parts.append(
            '<p class="note">展示文本中的号码类信息已按读取层规则遮蔽；'
            "证据坐标仍以不可变原文的 Unicode 码点为基准。</p>"
        )
    parts.append("</section>")

    parts.append("<section><h2>逐规则判断</h2>")
    if not findings:
        parts.append('<p class="note">（本次质检没有逐规则判断）</p>')
    for finding in findings:
        item = _mapping(finding)
        machine_verdict = item.get("machine_verdict")
        human_final = item.get("human_final_verdict")
        pending = bool(item.get("pending_business_confirmation"))
        parts.append('<article class="finding">')
        parts.append(
            f'<h3>{_esc(item.get("rule_code"))} '
            f'（v{_esc(item.get("version_no"))}，严重级别 {_esc(item.get("severity"))}）</h3>'
        )
        parts.append("<table><tbody>")
        parts.append(
            "<tr><th>机器判断（读取路径回验后）</th>"
            f'<td class="{_verdict_class(machine_verdict)}">'
            f"{_esc(_verdict_label(machine_verdict))}"
            f"（{_esc(machine_verdict)}）</td></tr>"
        )
        if item.get("raw_machine_verdict") is not None:
            parts.append(
                "<tr><th>原始机器判断（证据无效，仅审计）</th>"
                f"<td>{_esc(item.get('raw_machine_verdict'))}</td></tr>"
            )
        parts.append(
            "<tr><th>人工最终结论</th>"
            f'<td class="{_verdict_class(human_final)}">'
            f"{_esc(_verdict_label(human_final)) if human_final else '（未复核）'}"
            "</td></tr>"
        )
        if pending:
            parts.append(
                '<tr><th>待业务确认</th><td class="pending">是（证据不足，未转成合格）</td></tr>'
            )
        parts.append(
            f"<tr><th>证据是否通过回验</th>"
            f"<td>{'是' if item.get('evidence_valid') else '否'}</td></tr>"
        )
        parts.append("</tbody></table>")

        rationale = item.get("rationale")
        if rationale:
            parts.append(f'<p><strong>判断理由：</strong>{_esc(rationale)}</p>')

        evidence = _list(item.get("evidence"))
        if evidence:
            parts.append("<p><strong>证据：</strong></p>")
            parts.append('<ul class="evidence">')
            for span in evidence:
                span_item = _mapping(span)
                validated = span_item.get("validated")
                suffix = "（回验通过）" if validated else "（回验未通过）"
                parts.append(
                    "<li>"
                    f"第 {_esc(span_item.get('turn_ordinal'))} 轮 "
                    f"[{_esc(span_item.get('start'))}, {_esc(span_item.get('end'))})："
                    f"“{_esc(span_item.get('quote'))}”{_esc(suffix)}"
                    "</li>"
                )
            parts.append("</ul>")

        refs = _list(item.get("knowledge_refs"))
        if refs:
            parts.append("<p><strong>知识引用：</strong></p>")
            parts.append('<ul class="evidence">')
            for ref in refs:
                ref_item = _mapping(ref)
                parts.append(
                    "<li>"
                    f"version {_esc(ref_item.get('version_id'))} / "
                    f"chunk {_esc(ref_item.get('chunk_id'))}"
                    "</li>"
                )
            parts.append("</ul>")

        decision = _mapping(item.get("human_decision"))
        if decision:
            parts.append(
                "<p><strong>人工结论记录：</strong>"
                f"动作 {_esc(decision.get('action'))}，"
                f"结论 {_esc(decision.get('final_verdict'))}，"
                f"revision {_esc(decision.get('accepted_revision'))}，"
                f"理由：{_esc(decision.get('reason'))}</p>"
            )
        parts.append("</article>")
    parts.append("</section>")

    parts.append("<section><h2>汇总</h2><table><tbody>")
    parts.append(
        "<tr><th>规则判断总数</th>"
        f"<td>{_esc(summary.get('finding_count'))}</td></tr>"
    )
    machine_counts = _mapping(summary.get("machine_verdict_counts"))
    for key in ("hit", "clear", "insufficient_evidence", "not_applicable"):
        parts.append(
            f"<tr><th>{_esc(_verdict_label(key))}（{_esc(key)}）</th>"
            f"<td>{_esc(machine_counts.get(key, 0))}</td></tr>"
        )
    parts.append(
        "<tr><th>已人工复核</th>"
        f"<td>{_esc(summary.get('human_decided_count'))}</td></tr>"
    )
    parts.append(
        "<tr><th>待业务确认</th>"
        f"<td>{_esc(summary.get('pending_business_confirmation_count'))}</td></tr>"
    )
    parts.append("</tbody></table></section>")

    parts.append(
        '<p class="note">本报告由系统按固定快照生成；内容不含凭据、模型原始输出或'
        "未授权范围的资源。后续复核不会改写已导出的报告。</p>"
    )
    parts.append("</body></html>")

    return "\n".join(parts)
