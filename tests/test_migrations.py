"""迁移链单元测试（不需要数据库）：基线之后必须只有一个线性 head。

每一项新实体各自新增 revision，禁止改写 ``0001_schema_meta``；并行开发时
如果出现多个 head，空库迁移会失败，所以这里把它变成快速失败的单元测试。
"""

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

REPO_ROOT = Path(__file__).resolve().parents[1]
BASELINE = "0001_schema_meta"
AUTH_REVISION = "0002_users"
KNOWLEDGE_REVISION = "0003_knowledge"
TASKS_REVISION = "0005_tasks"
CHUNKS_REVISION = "0006_knowledge_chunks"
SUGGESTIONS_REVISION = "0007_suggestions"
RULES_REVISION = "0008_rules"
FEEDBACK_REVISION = "0008_suggestion_feedback"
INSPECTIONS_REVISION = "0009_inspections"
REVIEWS_REVISION = "0010_reviews"
M2_MERGE_HEAD = "cf1edc530a0c"
M4_RULES_MERGE_HEAD = "8889f869f465"


def _script() -> ScriptDirectory:
    return ScriptDirectory.from_config(Config(str(REPO_ROOT / "alembic.ini")))


def test_migration_graph_has_exactly_one_head():
    assert len(_script().get_heads()) == 1


def test_users_revision_extends_the_baseline_and_is_reachable():
    script = _script()
    revision = script.get_revision(AUTH_REVISION)

    assert revision is not None
    assert revision.down_revision == BASELINE
    assert AUTH_REVISION in {item.revision for item in script.walk_revisions()}


def test_knowledge_revision_branches_from_users_and_is_reachable():
    """T11 只新增自己的 revision，不合并其他票的 head（由 Lead 的合并 revision 负责）。"""
    script = _script()
    revision = script.get_revision(KNOWLEDGE_REVISION)

    assert revision is not None
    assert revision.down_revision == AUTH_REVISION
    assert KNOWLEDGE_REVISION in {item.revision for item in script.walk_revisions()}


def test_tasks_revision_branches_from_users_and_is_reachable():
    """T13 只新增自己的 revision，不合并其他票的 head（由 Lead 的合并 revision 负责）。"""
    script = _script()
    revision = script.get_revision(TASKS_REVISION)

    assert revision is not None
    assert revision.down_revision == AUTH_REVISION
    assert TASKS_REVISION in {item.revision for item in script.walk_revisions()}


def test_knowledge_chunks_revision_extends_the_current_single_head():
    """T14 的 knowledge_chunks 只能接在 M2 合并 head 之后，不另起分支。"""
    script = _script()
    revision = script.get_revision(CHUNKS_REVISION)

    assert revision is not None
    assert revision.down_revision == M2_MERGE_HEAD
    assert CHUNKS_REVISION in {item.revision for item in script.walk_revisions()}


def test_suggestions_revision_extends_0006_and_stays_reachable():
    """T15 的 suggestions/model_runs 只能接在 0006 之后，保持单一 head。"""
    script = _script()
    revision = script.get_revision(SUGGESTIONS_REVISION)

    assert revision is not None
    assert revision.down_revision == CHUNKS_REVISION
    assert SUGGESTIONS_REVISION in {item.revision for item in script.walk_revisions()}


def test_suggestion_feedback_revision_extends_0007():
    """T16 的 suggestion_feedback 接在 0007 之后；并行票的 head 由 Lead 合并 revision 收口。"""
    script = _script()
    revision = script.get_revision(FEEDBACK_REVISION)

    assert revision is not None
    assert revision.down_revision == SUGGESTIONS_REVISION
    assert FEEDBACK_REVISION in {item.revision for item in script.walk_revisions()}


def test_rules_revision_extends_0007():
    """T17 的 rule_sets/rule_versions 接在 0007 之后；并行票的 head 由 Lead 合并 revision 收口。"""
    script = _script()
    revision = script.get_revision(RULES_REVISION)

    assert revision is not None
    assert revision.down_revision == SUGGESTIONS_REVISION
    assert RULES_REVISION in {item.revision for item in script.walk_revisions()}


def test_inspections_revision_extends_the_m4_merge_head():
    """T18 的 inspection_runs/findings 只能接在 M4 合并 head 之后，保持单一 head。"""
    script = _script()
    revision = script.get_revision(INSPECTIONS_REVISION)

    assert revision is not None
    assert revision.down_revision == M4_RULES_MERGE_HEAD
    assert INSPECTIONS_REVISION in {item.revision for item in script.walk_revisions()}


def test_reviews_revision_extends_0009_and_is_the_single_head():
    """T20 的 review_cases/review_decisions 接在 0009 之后，保持单一 head。"""
    script = _script()
    revision = script.get_revision(REVIEWS_REVISION)

    assert revision is not None
    assert revision.down_revision == INSPECTIONS_REVISION
    assert REVIEWS_REVISION in {item.revision for item in script.walk_revisions()}
    assert script.get_current_head() == REVIEWS_REVISION
