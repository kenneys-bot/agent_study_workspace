#!/usr/bin/env python3
"""一次性脚本：把计划书 v1.1 的执行切片发布为 GitHub issues（含原生依赖边）。

约定见 docs/agents/issue-tracker.md：gh 2.92 无 --blocked-by，用
POST /repos/{owner}/{repo}/issues/{n}/dependencies/blocked_by -F issue_id=<blocker db id>
标签：ready-for-agent + m0..m7；父 issue 用 spec 标签，子 issue 首行 Part of #parent。
"""

import json
import subprocess
import sys

PARENT_TITLE = "执行总览：银行智能客服助手（计划书 v1.1）"
PARENT_BODY = """计划书 v1.1 的执行切片总览。规格：`docs/plan/bank-customer-service-plan.md`；契约：`docs/plan/bank-customer-service-contracts.md`；决策：`docs/adr/0001..0004`；术语：`GLOSSARY.md`。

- 里程碑 M0→M7，共 31 张 ticket，按依赖边形成 task graph（frontier = 无未关闭阻塞）。
- 集成分支：`feat/bank-assistant`。
- 这是公开仓库：issue 内不出现凭据、不写服务器地址与密码；服务器统一称 `my-ubuntu-server`。
- 验收纪律：PASS 必须来自真实执行证据；未实现必须非零退出或明确 `NOT_IMPLEMENTED`。
"""

TICKETS = [
    ("T01", "依赖与命令入口基线", "m0", [], "唯一依赖入口（pyproject.toml + uv.lock）、Makefile 覆盖计划全部目标命令、doctor 检查环境且不打印秘密、未实现命令非零退出。",
     ["`uv sync --locked` 成功", "`make doctor` 退出 0", "未实现命令（smoke/eval/up…）退出码非 0 且说明所属里程碑", "README 命令与 Makefile 一致"]),
    ("T02", "包重命名 app → bank_assistant（宽重构）", "m0", ["T01"], "全量导入改写、tests/ 移到仓库根、pytest 能收集并运行；不再出现 ModuleNotFoundError。",
     ["`uv run pytest` 无 ImportError", "`from bank_assistant.*` 全部可用", "`archive/` 不被测试收集"]),
    ("T03", "会话导入闭环", "m1", ["T02"], "上传文本/JSON → 稳定轮次（speaker/content 正确、时间戳为 datetime）、稳定 session_id；非法输入返回 4xx 且保留原文。",
     ["解析器三处缺陷（正则分组、JSON 数组 session_id、时间戳类型）各有回归测试", "`ParseConversationResponse` 响应模型存在且被路由使用", "非法输入返回 4xx", "speaker/content 不再错位"]),
    ("T04", "检索闭环", "m1", ["T02"], "查询 → 过滤 → 候选数 ≤ count 且带分数与原文；重排失败显式而非静默退回。",
     ["`query_with_score` 收到扁平查询列表", "count=3 时只返回 3 条", "重排失败记录错误码并暴露", "filters 真正传到业务层"]),
    ("T05", "模型适配层与候选答复", "m1", ["T02"], "统一模型适配层（DeepSeek 官方主用、第三方中继回退）；system 消息保留；结构化 schema 校验；失败分类（超时/限流/结构错误/空内容+length）。",
     ["实际请求包含 system 消息", "非 JSON/不合 schema 的输出判为失败", "`finish_reason=length` 且 content 为空判为失败", "SDK/凭据缺失时启动失败或报能力不可用", "不存在随机 embedding 与隐式 mock"]),
    ("T06", "质检与报告停止伪造", "m1", ["T02"], "报告导出与复核提交只返回真实数据；未实现时明确 `NOT_IMPLEMENTED`，不再返回固定 85 分与 2024-01-01。",
     ["导出接口不再返回固定分数", "复核提交无伪造时间戳", "复核记录不存实例字典（实现真实持久化或明确未实现）"]),
    ("T07", "容器可构建与冒烟", "m1", ["T01"], "Dockerfile 真正 COPY 代码与依赖、compose 启动 API；`make smoke` 覆盖存活、就绪与失败路径。",
     ["`docker build` 成功", "容器内 `uvicorn bank_assistant.main:app` 可启动", "`make smoke` 断言 /health/live 与错误路径"]),
    ("T08", "错误码与 HTTP 契约", "m1", ["T02"], "统一错误结构 `{code,message,trace_id,retryable,details}`；4xx/409/422 映射；trace_id 贯穿请求。",
     ["错误响应含 trace_id", "非法输入返回 400/422 且不泄露堆栈", "状态冲突用 409", "StringUtils.truncate 契约与测试一致（改实现或改测试并注明理由）"]),
    ("T09", "PostgreSQL 与迁移骨架", "m2", ["T01"], "docker compose 起 PostgreSQL 18；Alembic 空库迁移；readiness 真实检查数据库。",
     ["空库 `alembic upgrade head` 成功", "`make test-integration` 起容器并跑通", "`/health/ready` 在 PG 不可用时返回 503"]),
    ("T10", "登录、身份与权限", "m2", ["T09"], "登录（密码哈希）、服务端身份、角色与 `scope_id`；越权 403、不可见资源 404。",
     ["未登录 401", "跨 scope 访问 403", "不泄露资源存在性", "密码不以明文存储"]),
    ("T11", "知识生命周期", "m2", ["T10"], "草稿→in_review→published→retired；发布者与作者分离；发布后内容不可覆盖。",
     ["同一文档可多版本", "发布需 reviewer 角色且不能是作者", "草稿与过期版本不进入检索", "退役后不再命中"]),
    ("T12", "会话持久化与审计", "m2", ["T10"], "会话/轮次入库且原文不可变；追加式审计；时间以 UTC 存储。",
     ["重启后数据仍在", "会话内 ordinal 唯一", "审计 before/after 摘要不含敏感正文"]),
    ("T13", "幂等与任务表", "m2", ["T09"], "`Idempotency-Key` 与任务租约基础。",
     ["同键同输入返回原结果", "同键不同输入返回 409", "租约过期任务可重新领取"]),
    ("T14", "演示知识与版本化索引", "m3", ["T11"], "三场景各 ≥10 条已审核演示知识；版本化索引 + 校准向量哈希 + 可重建。",
     ["`make seed-demo` 幂等", "索引清单含 calibration hash", "重建后检索结果一致", "草稿/过期版本不命中"]),
    ("T15", "候选答复端到端", "m3", ["T14", "T05", "T13"], "过滤→检索→生成→引用校验→状态机 `answered/needs_clarification/needs_human/failed`。",
     ["引用只能来自本次授权召回且有效版本", "无依据时返回 needs_human 或 needs_clarification", "failed 仅用于系统失败", "每场景至少 5 条端到端用例"]),
    ("T16", "采纳反馈记录", "m3", ["T15"], "adopt/edit/reject 与理由，反馈关联具体建议。",
     ["反馈可查询", "不把反馈当作正确性标签", "编辑文本留存"]),
    ("T17", "规则版本与演示规则集", "m4", ["T11"], "≥6 条规则，覆盖三场景；`demo_only` 标记、适用范围、证据要求、风险级别、正反例与豁免。",
     ["规则可发布与退役", "演示规则标记 demo_only", "至少一条高风险规则"]),
    ("T18", "质检任务执行", "m4", ["T17", "T13", "T12"], "异步 worker、租约、重试上限 3、幂等最终结果、失败显式。",
     ["queued→running→succeeded 全链", "worker 中断后可恢复且不产生重复报告", "失败任务有 error_code"]),
    ("T19", "证据定位与校验", "m4", ["T18"], "码点坐标；emoji/换行前后端一致；无效区间拒绝；`hit/clear/insufficient_evidence/not_applicable` 四态。",
     ["证据区间与原文完全匹配", "无效区间返回校验失败", "不适用与证据不足分开记录"]),
    ("T20", "人工复核闭环", "m4", ["T19", "T10"], "逐项 confirm/reject/modify + 理由；`expected_revision` 冲突 409；完成前每条适用规则有结论。",
     ["两复核员并发提交一成功一 409", "机器判断不可被覆盖", "完成态可查询历史"]),
    ("T21", "报告导出", "m4", ["T20"], "JSON/HTML 导出，绑定 `inspection_id + review_revision` 快照，权限校验。",
     ["导出后再改判，旧报告内容不变", "无权限 403", "记录内容哈希"]),
    ("T22", "前端骨架 + 登录 + 系统版本页", "m5", ["T10"], "React + TS + Vite 工程可启动；登录与系统版本页展示 SHA/模型/索引/模式。",
     ["`npm run dev` 可启动", "登录后显示真实版本信息", "不显示静态假数据"]),
    ("T23", "坐席工作台页", "m5", ["T15", "T16", "T22"], "会话 + 候选答复 + 引用可打开原文 + 采纳/编辑。",
     ["三场景可在界面完成", "引用可打开原文", "加载/空/失败/无权限均有 UI 状态"]),
    ("T24", "质检复核页", "m5", ["T20", "T22"], "原文与证据并排、逐项复核、409 冲突提示。",
     ["证据高亮与原文一致（emoji/换行）", "并发冲突可见", "完成态展示"]),
    ("T25", "演示脚本与端到端", "m5", ["T23", "T24", "T07"], "种子数据、8–10 分钟演示脚本、浏览器 e2e、`make e2e`。",
     ["干净 checkout 按 README 可启动", "三个业务故事可完成", "至少一条真实云模型链路成功"]),
    ("T26", "数据集构建与冻结", "m6", ["T15", "T18"], "300 条检索查询 / 180 段质检会话；去重分组、60/20/20 拆分、manifest。",
     ["冻结测试集有哈希", "无同源改写泄漏", "标注过程有记录"]),
    ("T27", "评测脚本与基线对比", "m6", ["T26"], "`make eval` 跑固定测试集，输出 Recall@5、MRR、无答案处理、引用支持度、逐规则 F1、延迟与成本。",
     ["Ubuntu 上可复跑", "报告含分母与失败案例", "产物落 ARTIFACT_ROOT"]),
    ("T28", "条件性微调（QLoRA 意图分类）", "m6", ["T26", "T27"], "dry-run→训练→冻结测试集对比；资源不满足则如实 `NOT_RUN`。",
     ["记录模型/数据哈希/超参/随机种子/显存/耗时", "测试集不参与训练或挑 checkpoint", "结论说明有无改善"]),
    ("T29", "部署与恢复演练", "m7", ["T07", "T09", "T25"], "容器部署、配置与数据分离、备份恢复演练、回滚、索引重建。",
     ["备份恢复后记录数与关键哈希一致", "回滚演练成功", "索引可重建"]),
    ("T30", "监控、日志与安全测试", "m7", ["T29"], "trace_id、脱敏日志、限流、注入/越权/并发/worker 崩溃恢复测试。",
     ["日志不含敏感原文与密钥", "提示注入不改变系统约束", "超限返回 429"]),
    ("T31", "发布包与运维手册", "m7", ["T29", "T30", "T27"], "不可变发布版本、部署/运维/应急手册、风险清单、阶段验收报告。",
     ["版本可追溯到 SHA 与镜像", "手册可照做执行", "风险清单记录未解决项"]),
]


def run(cmd, **kw):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True, **kw)


def main():
    repo = run("gh repo view --json nameWithOwner -q .nameWithOwner").stdout.strip()
    if not repo:
        sys.exit("无法确定仓库")
    print(f"repo={repo}")
    for name, desc, color in [
        ("spec", "执行总览/规格", "5319e7"),
        ("ready-for-agent", "可交给 agent 执行", "0e8a16"),
        ("m0", "M0 基线", "ededed"), ("m1", "M1 修通后端", "ededed"),
        ("m2", "M2 存储与权限", "ededed"), ("m3", "M3 坐席辅助", "ededed"),
        ("m4", "M4 质检复核", "ededed"), ("m5", "M5 工作台", "ededed"),
        ("m6", "M6 评测", "ededed"), ("m7", "M7 工程就绪", "ededed"),
    ]:
        run(f'gh label create "{name}" --color {color} --description "{desc}"')

    existing = run(f'gh issue list --state all --limit 200 --json number,title').stdout
    numbers = {}
    parent = None
    try:
        for it in json.loads(existing or "[]"):
            if it["title"] == PARENT_TITLE:
                parent = it["number"]
    except json.JSONDecodeError:
        pass

    if parent is None:
        out = run(f'gh issue create --title "{PARENT_TITLE}" --label spec --body-file -', input=PARENT_BODY)
        parent = int(out.stdout.strip().rstrip("/").split("/")[-1])
        print(f"parent=#{parent}")
    else:
        print(f"parent=#{parent} (已存在)")

    for tid, title, ms, blocked, build, criteria in TICKETS:
        full_title = f"[{tid}] {title}"
        if full_title in (existing or ""):
            print(f"skip {full_title}")
            continue
        body = [f"Part of #{parent}", "", "## What to build", "", build, "", "## Acceptance criteria", ""]
        body += [f"- [ ] {c}" for c in criteria]
        body += ["", "## Blocked by", ""]
        body += [f"- {b}" for b in blocked] if blocked else ["None (can start immediately)"]
        body += ["", f"规格：`docs/plan/bank-customer-service-plan.md`；决策：`docs/adr/`；术语：`GLOSSARY.md`。"]
        path = f"/tmp/issue-{tid}.md"
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(body))
        out = run(f'gh issue create --title "{full_title}" --label ready-for-agent --label {ms} --body-file {path}')
        num = int(out.stdout.strip().rstrip("/").split("/")[-1])
        numbers[tid] = num
        print(f"{tid} -> #{num}")

    # 依赖边：等全部建完再补，用 blocker 的 database id
    dbid = {}
    for tid, num in numbers.items():
        r = run(f"gh api repos/{repo}/issues/{num} --jq .id")
        dbid[tid] = r.stdout.strip()
    added = 0
    for tid, title, ms, blocked, _, _ in TICKETS:
        child = numbers.get(tid)
        if not child:
            r = run(f'gh issue list --state all --json number,title -q \'.[] | select(.title=="[{tid}] {title}") | .number\'')
            child = r.stdout.strip()
            if not child:
                continue
        for b in blocked:
            bid = dbid.get(b)
            if not bid:
                continue
            r = run(f"gh api -X POST repos/{repo}/issues/{child}/dependencies/blocked_by -F issue_id={bid}")
            if r.returncode == 0:
                added += 1
    print(f"依赖边已建立 {added} 条；tickets={len(numbers)}")


if __name__ == "__main__":
    main()
