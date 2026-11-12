"""合成演示知识的校验与导入（M3，契约 §3，GLOSSARY「知识来源/演示制度」）。

演示知识只允许两类来源（GLOSSARY「知识来源」）：

- ``public_regulation``：公开监管条文，只能引用公开链接，正文自行组织，
  不得伪造条文原文；
- ``demo_policy``：虚构演示制度，必须显式标注 ``demo_only`` 且正文包含
  “虚构演示制度”，不得与任何真实机构制度或话术混同。

导入走真实生命周期（``create_draft_version → submit_version →
publish_version``），作者与审核人必须是两个不同账号：本模块绝不创建用户，
账号由显式脚本 ``scripts/seed_demo_accounts.py`` 单独 seed。

幂等：按 ``(scope_id, source_uri)`` 找文档、按 ``version_no`` 找版本；已存在
且内容哈希一致则跳过，内容不同则报错（禁止静默覆盖，必须递增 version_no）。
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from bank_assistant.infra.models import KnowledgeDocument, KnowledgeVersion
from bank_assistant.knowledge.lifecycle import (
    SOURCE_TYPES,
    KnowledgeStatus,
    hash_content,
    validate_window,
)
# 注意：`bank_assistant.api.__init__` 会导入 routers，而 routers 又导入本包的
# service；先显式加载 api.errors，可避免直接 import service 时的循环导入。
import bank_assistant.api.errors  # noqa: E402, F401  isort:skip
from bank_assistant.knowledge.service import (  # noqa: E402
    create_draft_version,
    publish_version,
    submit_version,
)
from bank_assistant.security.identity import Identity

DEMO_KNOWLEDGE_DIR = (
    Path(__file__).resolve().parents[3] / "data" / "demo" / "knowledge"
)

DEMO_BUSINESS_TYPES: tuple[str, ...] = ("lost_card", "credit_card_fee", "complaint")
# 公开监管条文只允许指向公开监管机构站点；demo.invalid 是保留 TLD，绝不解析。
PUBLIC_SOURCE_HOSTS: frozenset[str] = frozenset(
    {"www.gov.cn", "www.pbc.gov.cn", "www.npc.gov.cn", "www.csr.gov.cn"}
)
DEMO_SOURCE_HOST = "demo.invalid"
DEMO_MARKER = "虚构演示制度"
DEMO_AUTHOR_USERNAME = "demo-author"
DEMO_REVIEWER_USERNAME = "demo-reviewer"
REVIEW_REASON = "演示知识种子：作者提交，由另一审核人发布"


class DemoSeedError(ValueError):
    """演示知识数据或导入前置条件不合法；脚本应非零退出并说明原因。"""


@dataclass(frozen=True, slots=True)
class DemoKnowledgeEntry:
    """一条已通过校验的演示知识种子条目。"""

    entry_id: str
    title: str
    source_type: str
    source_uri: str
    business_type: str
    content: str
    valid_from: datetime
    valid_to: datetime | None
    version_no: int
    demo_only: bool


@dataclass(frozen=True, slots=True)
class DemoSeedReport:
    """一次导入的幂等统计；脚本据此打印“新建/跳过”。"""

    total_entries: int
    created_documents: int
    published_versions: int
    skipped_versions: int


def _fail(message: str, *, source: str) -> DemoSeedError:
    return DemoSeedError(f"{source}: {message}")


def _parse_moment(value: object, *, field: str, source: str) -> datetime:
    if not isinstance(value, str):
        raise _fail(f"{field} 必须是 ISO 8601 字符串", source=source)
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise _fail(f"{field} 不是合法 ISO 8601 时间：{value!r}", source=source) from exc
    if moment.tzinfo is None:
        raise _fail(f"{field} 必须带时区（契约 §1 统一 UTC）", source=source)
    return moment


def _parse_entry(raw: object, *, source: str, index: int) -> DemoKnowledgeEntry:
    if not isinstance(raw, dict):
        raise _fail(f"第 {index} 条不是 JSON 对象", source=source)
    label = f"第 {index} 条（id={raw.get('id', '<missing>')}）"

    required = (
        "title",
        "source_type",
        "source_uri",
        "business_type",
        "content",
        "valid_from",
        "version_no",
    )
    missing = [field for field in required if field not in raw]
    if missing:
        raise _fail(f"{label} 缺少字段：{', '.join(missing)}", source=source)

    entry_id = raw.get("id")
    if not isinstance(entry_id, str) or not entry_id.strip():
        raise _fail(f"{label} 缺少非空 id", source=source)

    title = raw["title"]
    if not isinstance(title, str) or not title.strip() or len(title) > 200:
        raise _fail(f"{label} 的 title 必须非空且不超过 200 字符", source=source)

    source_type = raw["source_type"]
    if source_type not in SOURCE_TYPES:
        raise _fail(
            f"{label} 的 source_type 必须是 {sorted(SOURCE_TYPES)}，实际 {source_type!r}",
            source=source,
        )

    business_type = raw["business_type"]
    if business_type not in DEMO_BUSINESS_TYPES:
        raise _fail(
            f"{label} 的 business_type 必须是 {DEMO_BUSINESS_TYPES}，"
            f"实际 {business_type!r}",
            source=source,
        )

    content = raw["content"]
    if not isinstance(content, str) or len(content.strip()) < 20:
        raise _fail(f"{label} 的 content 必须非空且至少 20 字符", source=source)

    source_uri = raw["source_uri"]
    if not isinstance(source_uri, str) or not source_uri.strip():
        raise _fail(f"{label} 缺少非空 source_uri", source=source)
    parsed = urlparse(source_uri)
    host = (parsed.hostname or "").lower()

    demo_only = raw.get("demo_only")
    if not isinstance(demo_only, bool):
        raise _fail(f"{label} 必须显式声明 demo_only（true/false）", source=source)

    if source_type == "demo_policy":
        if not demo_only:
            raise _fail(f"{label} 是演示制度，demo_only 必须为 true", source=source)
        if DEMO_MARKER not in content:
            raise _fail(
                f"{label} 是演示制度，正文必须注明“{DEMO_MARKER}”", source=source
            )
        if host and not (host == DEMO_SOURCE_HOST or host.endswith(".invalid")):
            raise _fail(
                f"{label} 的 source_uri 必须使用 {DEMO_SOURCE_HOST} 占位域名",
                source=source,
            )
    else:
        if demo_only:
            raise _fail(f"{label} 是公开监管条文，demo_only 必须为 false", source=source)
        if DEMO_MARKER in content:
            raise _fail(
                f"{label} 是公开监管条文，正文不得出现“{DEMO_MARKER}”", source=source
            )
        if parsed.scheme not in {"http", "https"} or host not in PUBLIC_SOURCE_HOSTS:
            raise _fail(
                f"{label} 的公开监管条文必须引用公开链接（允许域名 "
                f"{sorted(PUBLIC_SOURCE_HOSTS)}），实际 {source_uri!r}",
                source=source,
            )

    valid_from = _parse_moment(raw["valid_from"], field="valid_from", source=source)
    valid_to = (
        _parse_moment(raw["valid_to"], field="valid_to", source=source)
        if raw.get("valid_to") is not None
        else None
    )
    try:
        validate_window(valid_from, valid_to)
    except ValueError as exc:
        raise _fail(f"{label} 的有效期非法：{exc}", source=source) from exc

    version_no = raw["version_no"]
    if not isinstance(version_no, int) or version_no < 1:
        raise _fail(f"{label} 的 version_no 必须是正整数", source=source)

    return DemoKnowledgeEntry(
        entry_id=entry_id,
        title=title,
        source_type=source_type,
        source_uri=source_uri,
        business_type=business_type,
        content=content,
        valid_from=valid_from,
        valid_to=valid_to,
        version_no=version_no,
        demo_only=demo_only,
    )


def load_demo_entries(
    directory: Path | str = DEMO_KNOWLEDGE_DIR,
) -> tuple[DemoKnowledgeEntry, ...]:
    """读取目录下全部 ``*.json`` 种子并校验；任何一条不合法都拒绝整个种子集。"""
    root = Path(directory)
    files = sorted(root.glob("*.json"))
    if not files:
        raise DemoSeedError(f"没有找到演示知识文件：{root}")

    entries: list[DemoKnowledgeEntry] = []
    seen: dict[tuple[str, int], str] = {}
    ids: set[str] = set()
    for file_path in files:
        try:
            payload = json.loads(file_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise DemoSeedError(f"{file_path.name}: 无法解析 JSON（{exc}）") from exc
        raw_entries = payload.get("entries") if isinstance(payload, dict) else payload
        if not isinstance(raw_entries, list):
            raise DemoSeedError(f"{file_path.name}: 缺少 entries 列表")

        for index, raw in enumerate(raw_entries, start=1):
            entry = _parse_entry(raw, source=file_path.name, index=index)
            if entry.entry_id in ids:
                raise _fail(f"id {entry.entry_id!r} 重复", source=file_path.name)
            ids.add(entry.entry_id)
            # 文档身份用 title（同一公开条文可以支撑多条不同知识条目），
            # source_uri 在导入时作为一致性校验，也必须与已存在文档一致。
            key = (entry.title, entry.version_no)
            if key in seen:
                raise _fail(
                    f"(document, version_no) 重复：{entry.title} v{entry.version_no}",
                    source=file_path.name,
                )
            seen[key] = entry.entry_id
            entries.append(entry)
    return tuple(entries)


def _load_accounts(
    session: Session,
    *,
    author: Identity,
    reviewer: Identity,
    scope_id: str,
) -> None:
    if author.scope_id != scope_id or reviewer.scope_id != scope_id:
        raise DemoSeedError(
            f"演示账号必须属于 scope {scope_id!r}："
            f"作者 {author.scope_id!r}，审核人 {reviewer.scope_id!r}"
        )
    if author.user_id == reviewer.user_id:
        raise DemoSeedError("作者与审核人必须是两个不同账号（发布者与作者分离）")
    if author.role not in {"agent", "admin"}:
        raise DemoSeedError(f"演示作者角色必须为 agent/admin，实际 {author.role!r}")
    if reviewer.role not in {"reviewer", "admin"}:
        raise DemoSeedError(
            f"演示审核人角色必须为 reviewer/admin，实际 {reviewer.role!r}"
        )


def _find_document(
    session: Session, *, scope_id: str, title: str
) -> KnowledgeDocument | None:
    """文档身份 = (scope_id, title)；source_uri 只是来源标注，可被多条条目复用。"""
    return session.scalar(
        select(KnowledgeDocument).where(
            KnowledgeDocument.scope_id == scope_id,
            KnowledgeDocument.title == title,
        )
    )


def _find_version(
    session: Session, *, document_id, version_no: int
) -> KnowledgeVersion | None:
    return session.scalar(
        select(KnowledgeVersion).where(
            KnowledgeVersion.document_id == document_id,
            KnowledgeVersion.version_no == version_no,
        )
    )


def import_demo_knowledge(
    session: Session,
    entries: Sequence[DemoKnowledgeEntry],
    *,
    author: Identity,
    reviewer: Identity,
    scope_id: str,
) -> DemoSeedReport:
    """幂等导入演示知识：作者提交、另一审核人发布；已存在且一致则跳过。"""
    _load_accounts(session, author=author, reviewer=reviewer, scope_id=scope_id)

    created_documents = 0
    published_versions = 0
    skipped_versions = 0

    for entry in entries:
        document = _find_document(session, scope_id=scope_id, title=entry.title)
        version: KnowledgeVersion | None = None
        if document is not None:
            if (
                document.source_type != entry.source_type
                or document.business_type != entry.business_type
                or document.source_uri != entry.source_uri
            ):
                raise DemoSeedError(
                    f"文档「{entry.title}」已存在但来源/业务/链接不一致，"
                    "演示种子不允许静默改写，请人工核对"
                )
            version = _find_version(
                session, document_id=document.document_id, version_no=entry.version_no
            )
            if version is not None:
                if version.content_hash != hash_content(entry.content):
                    raise DemoSeedError(
                        f"{entry.source_uri} v{entry.version_no} 已存在且内容不同；"
                        "请递增 version_no，不能覆盖已导入版本"
                    )
                if version.status != KnowledgeStatus.PUBLISHED.value:
                    raise DemoSeedError(
                        f"{entry.source_uri} v{entry.version_no} 已存在但状态为 "
                        f"{version.status}，演示种子必须最终处于已发布状态"
                    )
                skipped_versions += 1
                continue

        if version is None:
            is_new_document = document is None
            document, version = create_draft_version(
                session,
                author,
                title=entry.title,
                source_type=entry.source_type,
                source_uri=entry.source_uri,
                business_type=entry.business_type,
                content=entry.content,
                valid_from=entry.valid_from,
                valid_to=entry.valid_to,
                document_id=document.document_id if document is not None else None,
            )
            if is_new_document:
                created_documents += 1
        if version.version_no != entry.version_no:
            raise DemoSeedError(
                f"{entry.source_uri} 期望 version_no={entry.version_no}，"
                f"实际写入 {version.version_no}；种子版本号必须连续"
            )

        submit_version(
            session, author, version.version_id, expected_revision=version.revision
        )
        publish_version(
            session,
            reviewer,
            version.version_id,
            expected_revision=version.revision,
            review_reason=REVIEW_REASON,
        )
        published_versions += 1

    return DemoSeedReport(
        total_entries=len(entries),
        created_documents=created_documents,
        published_versions=published_versions,
        skipped_versions=skipped_versions,
    )


__all__ = [
    "DEMO_AUTHOR_USERNAME",
    "DEMO_BUSINESS_TYPES",
    "DEMO_KNOWLEDGE_DIR",
    "DEMO_MARKER",
    "DEMO_REVIEWER_USERNAME",
    "REVIEW_REASON",
    "DemoKnowledgeEntry",
    "DemoSeedError",
    "DemoSeedReport",
    "import_demo_knowledge",
    "load_demo_entries",
]
