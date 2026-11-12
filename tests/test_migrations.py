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
