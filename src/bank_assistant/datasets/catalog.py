"""M6 合成数据集目录（T26，#27）：三场景意图与质检会话模板。

所有文本都是**合成**内容：客户与坐席发言由本文件的模板生成，不对应任何真实
银行、真实客户或真实通话。语料只来自仓库内的虚构演示知识与公开监管要点摘要
（``data/demo/``）。

分组口径（M6-02）：

- 检索：``ret:<场景>:<知识来源 doc_id>:<意图簇>``——同一知识来源、同一意图的
  不同问法是同源改写，必须同组，不能跨 train/dev/test；
- 质检：``ins:<场景>:<会话模板家族>:<意图簇>``——同一模板家族的变体同组。

困难样本六类固定为 :data:`HARD_CATEGORIES`；每个场景的每一类都提供独立模板，
避免用“正常样本换个词”冒充困难样本。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "FRAMES",
    "HARD_CATEGORIES",
    "InspectionTemplate",
    "LabelSpec",
    "SCENARIO_SPECS",
    "ScenarioSpec",
    "IntentSpec",
    "HardQuery",
]

HARD_CATEGORIES: tuple[str, ...] = (
    "no_answer",
    "insufficient_info",
    "expired_knowledge",
    "negation",
    "quoted_speech",
    "ambiguous_intent",
)

# 检索问法的通用外壳；同一意图轮换取 4 个，避免每个意图手写四遍又保持句式多样。
FRAMES: tuple[str, ...] = (
    "请问{phrase}？",
    "客户问{phrase}，应该怎么答复？",
    "{phrase}，具体有什么规定？",
    "帮我查一下{phrase}的要求。",
    "如果客户问到{phrase}，按哪条制度说明？",
    "{phrase}需要满足什么条件？",
)


@dataclass(frozen=True, slots=True)
class IntentSpec:
    """一个检索意图：绑定唯一知识来源，短语进入问法模板。"""

    slug: str
    phrase: str
    doc_id: str


@dataclass(frozen=True, slots=True)
class HardQuery:
    """一条困难检索样本；``reason`` 只对无答案型使用。"""

    query: str
    doc_ids: tuple[str, ...] = ()
    excluded_doc_ids: tuple[str, ...] = ()
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class LabelSpec:
    """单条规则标签；``evidence`` 为 ``(turn_index, quote)``，quote 必须逐字出现。"""

    verdict: str
    rationale: str
    evidence: tuple[int, str] | None = None


def _clear(rationale: str) -> LabelSpec:
    return LabelSpec("clear", rationale)


def _insufficient(rationale: str) -> LabelSpec:
    return LabelSpec("insufficient_evidence", rationale)


def _hit(rationale: str, turn_index: int, quote: str) -> LabelSpec:
    return LabelSpec("hit", rationale, (turn_index, quote))


@dataclass(frozen=True, slots=True)
class InspectionTemplate:
    """一个质检会话模板家族；变体只改结尾措辞，标签与证据保持不变。"""

    slug: str
    intent_cluster: str
    turns: tuple[tuple[str, str], ...]
    labels: Mapping[str, LabelSpec]
    hard_categories: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ScenarioSpec:
    key: str
    label: str
    business_type: str
    rule_codes: tuple[str, str]
    intents: tuple[IntentSpec, ...]
    normal_templates: tuple[InspectionTemplate, ...]
    hard_templates: tuple[InspectionTemplate, ...]
    hard_queries: Mapping[str, tuple[HardQuery, ...]]


# ---------------------------------------------------------------------------
# 挂失咨询
# ---------------------------------------------------------------------------

_LOST_INTENTS: tuple[IntentSpec, ...] = (
    IntentSpec("credit_record_retention", "征信不良信息保存多久", "lost-card-pub-01"),
    IntentSpec("credit_record_dispute", "征信记录有错误怎么提异议", "lost-card-pub-01"),
    IntentSpec("lost_card_dispute_notice", "挂失和异常交易的告知义务", "lost-card-pub-02"),
    IntentSpec("lost_card_contract_terms", "挂失补卡在合同里怎么约定", "lost-card-pub-02"),
    IntentSpec("lost_report_channel", "挂失受理渠道", "lost-card-pub-03"),
    IntentSpec("consumer_info_security", "金融消费者信息安全怎么保障", "lost-card-pub-03"),
    IntentSpec("credit_dispute_timeline", "征信异议核查要多久", "lost-card-pub-04"),
    IntentSpec("credit_dispute_reply", "征信异议结果怎么答复", "lost-card-pub-04"),
    IntentSpec("report_channel_verify", "电话挂失需要核验哪些信息", "lost-card-demo-01"),
    IntentSpec("report_channel_list", "挂失可以通过哪些渠道办理", "lost-card-demo-01"),
    IntentSpec("temporary_freeze", "临时冻结和正式挂失有什么区别", "lost-card-demo-02"),
    IntentSpec("freeze_to_report", "临时冻结怎么转正式挂失", "lost-card-demo-02"),
    IntentSpec("replacement_timeline", "补卡需要多久", "lost-card-demo-03"),
    IntentSpec("replacement_fee", "补卡工本费多少", "lost-card-demo-03"),
    IntentSpec("report_effective_time", "挂失什么时候生效", "lost-card-demo-04"),
    IntentSpec("loss_liability_split", "挂失前后的损失责任怎么划分", "lost-card-demo-04"),
    IntentSpec("overseas_report", "境外丢失卡片怎么挂失", "lost-card-demo-05"),
    IntentSpec("remote_report", "异地可以办挂失吗", "lost-card-demo-05"),
    IntentSpec("dispute_material", "挂失后争议交易要哪些材料", "lost-card-demo-06"),
    IntentSpec("dispute_flow", "争议交易受理流程是什么", "lost-card-demo-06"),
)

_LOST_NORMAL: tuple[InspectionTemplate, ...] = (
    InspectionTemplate(
        "safe_code_verify",
        "identity_verification",
        (
            ("客户", "您好，我的信用卡丢了，现在要挂失。"),
            (
                "坐席",
                "您好，请先在官方App的挂失页面完成身份核验；不要把验证码告诉任何人，只在App内输入。",
            ),
            ("客户", "好的，我只在App里输入。"),
        ),
        {
            "DEMO-PWD-01": LabelSpec(
                "clear",
                "坐席要求客户在官方App内自行输入验证码并明确不要告知，属于规则反例。",
                (1, "不要把验证码告诉任何人"),
            ),
            "DEMO-CLAIM-01": _clear("坐席只说明核验流程，未声称挂失已经办理成功。"),
        },
    ),
    InspectionTemplate(
        "pwd_request",
        "password_handling",
        (
            ("客户", "我现在人在外地，怎么尽快挂失？"),
            ("坐席", "为了核验身份，请把收到的短信验证码报给我，我这边直接帮您办理挂失。"),
            ("客户", "为什么要报验证码？"),
            ("坐席", "您报给我就行，我这边替您操作。"),
        ),
        {
            "DEMO-PWD-01": _hit(
                "坐席主动索取短信验证码，属于演示规则命中的高风险行为。",
                1,
                "短信验证码报给我",
            ),
            "DEMO-CLAIM-01": _clear("坐席承诺代办挂失，但没有声称已经办理成功。"),
        },
    ),
    InspectionTemplate(
        "claim_success",
        "completion_claim",
        (
            ("客户", "我的挂失到底办好没有？"),
            ("坐席", "您的挂失已经办理成功，新卡三到五天寄到，放心用。"),
            ("客户", "那就好。"),
        ),
        {
            "DEMO-PWD-01": _clear("会话没有出现索取密码或验证码的内容。"),
            "DEMO-CLAIM-01": _hit(
                "坐席声称已办理成功，但会话中没有受理编号或系统回执。",
                1,
                "已经办理成功",
            ),
        },
    ),
    InspectionTemplate(
        "receipt_given",
        "completion_receipt",
        (
            ("客户", "挂失办好了吗？"),
            (
                "坐席",
                "已经受理，受理编号 LB20261112001，请在App里核对，结果以短信为准。",
            ),
        ),
        {
            "DEMO-PWD-01": _clear("会话没有出现索取密码或验证码的内容。"),
            "DEMO-CLAIM-01": _clear("坐席给出可核验受理编号，并说明以短信为准，不是无依据声称成功。"),
        },
    ),
    InspectionTemplate(
        "temporary_freeze",
        "freeze_vs_report",
        (
            ("客户", "我想先临时冻结，不正式挂失，可以吗？"),
            (
                "坐席",
                "可以先临时冻结，冻结期间不能消费；确认卡片找不到后，再转正式挂失，生效时点以系统记录为准。",
            ),
        ),
        {
            "DEMO-PWD-01": _clear("会话只讨论冻结与挂失流程，没有索取密码或验证码。"),
            "DEMO-CLAIM-01": _clear("坐席说明需转正式挂失，未声称已经办理成功。"),
        },
    ),
    InspectionTemplate(
        "overseas_loss",
        "overseas_report",
        (
            ("客户", "我在国外，卡丢了，怎么办？"),
            (
                "坐席",
                "请拨打卡背面的境外服务电话，或通过手机银行提交挂失；境外挂失同样需要核对身份信息。",
            ),
        ),
        {
            "DEMO-PWD-01": _clear("坐席只说明渠道与身份核对，没有索取密码或验证码。"),
            "DEMO-CLAIM-01": _clear("坐席说明提交方式，未声称已经办理成功。"),
        },
    ),
    InspectionTemplate(
        "dispute_materials",
        "dispute_material",
        (
            ("客户", "挂失前有一笔不是我刷的交易，要提供什么？"),
            (
                "坐席",
                "请准备交易时间、金额、商户名称和本人声明，我们会按争议交易流程受理，其他材料以受理回执为准。",
            ),
        ),
        {
            "DEMO-PWD-01": _clear("会话没有出现索取密码或验证码的内容。"),
            "DEMO-CLAIM-01": _clear("坐席只说明材料与流程，没有声称办理成功。"),
        },
    ),
    InspectionTemplate(
        "replacement_fee",
        "replacement_timeline",
        (
            ("客户", "补卡要多少钱，多久能到？"),
            (
                "坐席",
                "补卡工本费和寄送时间以最新公告为准，我帮您查询后再答复，不会先承诺具体金额。",
            ),
        ),
        {
            "DEMO-PWD-01": _clear("会话没有出现索取密码或验证码的内容。"),
            "DEMO-CLAIM-01": _clear("坐席说明查询后再答复，未声称已经办理成功。"),
        },
    ),
    InspectionTemplate(
        "contact_update",
        "contact_change",
        (
            ("客户", "手机号换了，还能电话挂失吗？"),
            (
                "坐席",
                "手机号不一致时需要补充核验，请按页面提示上传证件信息，核验通过后再办理挂失。",
            ),
        ),
        {
            "DEMO-PWD-01": _clear("坐席要求上传证件核验，没有索取密码或验证码。"),
            "DEMO-CLAIM-01": _clear("坐席说明核验通过后再办理，不是已办理成功。"),
        },
    ),
    InspectionTemplate(
        "customer_upset",
        "service_recovery",
        (
            ("客户", "你们效率太低了，我卡丢了半天没人管！"),
            ("坐席", "非常抱歉让您久等，我马上为您登记挂失，并说明需要补充的材料。"),
        ),
        {
            "DEMO-PWD-01": _clear("会话没有出现索取密码或验证码的内容。"),
            "DEMO-CLAIM-01": _clear("坐席只承诺登记，未声称已经办理成功。"),
        },
    ),
    InspectionTemplate(
        "counter_handoff",
        "counter_handoff",
        (
            ("客户", "我还是想去网点办。"),
            (
                "坐席",
                "可以，请带本人有效证件到就近网点办理挂失，网点会核实身份并给您受理回执。",
            ),
        ),
        {
            "DEMO-PWD-01": _clear("会话没有出现索取密码或验证码的内容。"),
            "DEMO-CLAIM-01": _clear("坐席说明网点会核实并给回执，未声称已经办理成功。"),
        },
    ),
)

_LOST_HARD: tuple[InspectionTemplate, ...] = (
    InspectionTemplate(
        "negation_pwd",
        "password_handling",
        (
            ("客户", "你们是不是要我把验证码给你们？"),
            ("坐席", "不会的，不要把验证码告诉任何人，请只在官方App输入。"),
        ),
        {
            "DEMO-PWD-01": LabelSpec(
                "clear",
                "关键词“验证码”出现在否定/防护语境，不是坐席索取行为。",
                (1, "不要把验证码告诉任何人"),
            ),
            "DEMO-CLAIM-01": _clear("会话没有声称挂失已经办理成功。"),
        },
        hard_categories=("negation",),
    ),
    InspectionTemplate(
        "quoted_claim",
        "completion_claim",
        (
            ("客户", "听说挂失已经办理成功就能直接补卡？"),
            ("坐席", "以系统受理结果为准，我帮您查一下再答复。"),
        ),
        {
            "DEMO-PWD-01": _clear("会话没有出现索取密码或验证码的内容。"),
            "DEMO-CLAIM-01": LabelSpec(
                "clear",
                "关键词出现在客户转述中，不是坐席声称成功，不能按关键词判命中。",
                (0, "已经办理成功"),
            ),
        },
        hard_categories=("quoted_speech",),
    ),
    InspectionTemplate(
        "partial_case",
        "identity_verification",
        (
            ("客户", "我那个卡的事，你们看怎么办吧。"),
            ("坐席", "您好，请具体说明是挂失、补卡还是争议交易，我才能判断。"),
        ),
        {
            "DEMO-PWD-01": _insufficient("会话没有提到验证码或密码，也没有坐席索取行为的原文，不能可靠判断。"),
            "DEMO-CLAIM-01": _insufficient("客户未说明办理事项，坐席也尚未答复，无法判断是否声称成功。"),
        },
        hard_categories=("insufficient_info",),
    ),
    InspectionTemplate(
        "empty_call",
        "no_content",
        (("客户", "喂？有人吗？"),),
        {
            "DEMO-PWD-01": _insufficient("会话只有客户呼叫，没有可供判断的坐席发言。"),
            "DEMO-CLAIM-01": _insufficient("会话只有客户呼叫，没有可供判断的坐席发言。"),
        },
        hard_categories=("no_answer",),
    ),
    InspectionTemplate(
        "expired_policy",
        "replacement_timeline",
        (
            ("客户", "2024年的规定是不是挂失后5个工作日补卡？"),
            (
                "坐席",
                "您说的5个工作日补卡是2024年度旧规定，该版本已经失效，现行口径要以最新公告为准，我再帮您核实。",
            ),
        ),
        {
            "DEMO-PWD-01": _clear("会话没有出现索取密码或验证码的内容。"),
            "DEMO-CLAIM-01": _insufficient("引用的2024年度版本已于2025-01-01失效，不能据此判断办理状态。"),
        },
        hard_categories=("expired_knowledge",),
    ),
    InspectionTemplate(
        "mixed_request",
        "completion_claim",
        (
            ("客户", "卡丢了，顺便问下我上周那笔退款算不算办好了？"),
            ("坐席", "挂失我先帮您登记；退款是否到账要看账单，我查了再答复。"),
        ),
        {
            "DEMO-PWD-01": _clear("会话没有出现索取密码或验证码的内容。"),
            "DEMO-CLAIM-01": _insufficient("客户把挂失与退款混在一句里，无法确认所指的“办好”是哪项业务。"),
        },
        hard_categories=("ambiguous_intent",),
    ),
)

_LOST_HARD_QUERIES: dict[str, tuple[HardQuery, ...]] = {
    "no_answer": (
        HardQuery("企业网银的对公转账限额是多少？"),
        HardQuery("房贷利率现在是多少？"),
        HardQuery("你们的理财产品怎么赎回？"),
        HardQuery("可以开立美元现钞账户吗？"),
        HardQuery("信用卡积分怎么兑换航空里程？"),
    ),
    "insufficient_info": (
        HardQuery("我卡丢了，这个怎么办？"),
        HardQuery("刚才说的那个可以吗？"),
        HardQuery("帮我查一下。"),
        HardQuery("你说是就是吧？"),
        HardQuery("那个费用怎么算？"),
    ),
    "expired_knowledge": tuple(
        HardQuery(text, excluded_doc_ids=("edge-expired-01",), reason="expired_knowledge")
        for text in (
            "2024年挂失后5个工作日补卡的规定现在还有效吗？",
            "2024年补卡工本费10元的旧口径还能用吗？",
            "去年说的挂失补卡时限是5个工作日吧？",
            "2024年度挂失受理规则是不是还在执行？",
            "按2024年的挂失规定，补卡要等几天？",
        )
    ),
    "negation": (
        HardQuery("我不是要挂失，我是想问补卡要多久。", ("lost-card-demo-03",)),
        HardQuery("不用挂失，卡片只是暂时找不到。", ("lost-card-demo-02",)),
        HardQuery("我不是问年费，我问的是补卡工本费。", ("lost-card-demo-03",)),
        HardQuery("不需要临时冻结，直接告诉我正式挂失怎么弄。", ("lost-card-demo-02",)),
        HardQuery("先别挂失，我只是想确认补卡流程。", ("lost-card-demo-03",)),
    ),
    "quoted_speech": (
        HardQuery("听说挂失后当天就能补卡，是真的吗？", ("lost-card-demo-03",)),
        HardQuery("客服说临时冻结和正式挂失一样，对吧？", ("lost-card-demo-02",)),
        HardQuery("朋友说他挂失不用核验身份，是这样吗？", ("lost-card-demo-01",)),
        HardQuery("有人说补卡不要工本费，是真的吗？", ("lost-card-demo-03",)),
        HardQuery("同事说挂失马上生效，是吗？", ("lost-card-demo-04",)),
    ),
    "ambiguous_intent": (
        HardQuery("挂失之后年费还收吗？", ("lost-card-demo-02", "fee-demo-01")),
        HardQuery("卡丢了，我上周的账单还能分期吗？", ("lost-card-demo-01", "fee-demo-02")),
        HardQuery("我卡丢了，之前那笔退款算办好了吗？", ("lost-card-demo-01", "fee-demo-06")),
        HardQuery("挂失前刷的那笔钱要不要先还？", ("lost-card-demo-04", "fee-demo-03")),
        HardQuery("卡片丢失期间的利息怎么算？", ("lost-card-demo-04", "fee-pub-03")),
    ),
}

# ---------------------------------------------------------------------------
# 信用卡费用与还款咨询
# ---------------------------------------------------------------------------

_FEE_INTENTS: tuple[IntentSpec, ...] = (
    IntentSpec("fee_disclosure", "信用卡息费怎么披露", "fee-pub-01"),
    IntentSpec("contract_fee_clause", "合同里的费用条款怎么查看", "fee-pub-01"),
    IntentSpec("fee_transparency", "收费透明和知情权要求", "fee-pub-02"),
    IntentSpec("fee_notice_duty", "银行费用告知义务有哪些", "fee-pub-02"),
    IntentSpec("repayment_interest_rule", "信用卡还款和计息规则", "fee-pub-03"),
    IntentSpec("interest_free_period", "免息期和计息方式怎么算", "fee-pub-03"),
    IntentSpec("annual_fee_notice", "年费怎么约定和告知", "fee-pub-04"),
    IntentSpec("late_fee_notice", "违约金怎么约定", "fee-pub-04"),
    IntentSpec("annual_fee_waiver", "年费减免条件", "fee-demo-01"),
    IntentSpec("waiver_application", "怎么申请年费减免", "fee-demo-01"),
    IntentSpec("installment_fee_display", "分期手续费怎么展示", "fee-demo-02"),
    IntentSpec("early_settlement_fee", "分期提前结清手续费怎么算", "fee-demo-02"),
    IntentSpec("minimum_payment_interest", "最低还款利息怎么算", "fee-demo-03"),
    IntentSpec("revolving_interest", "循环利息是什么意思", "fee-demo-03"),
    IntentSpec("repayment_order", "还款入账顺序", "fee-demo-04"),
    IntentSpec("grace_period", "容时容差是多久", "fee-demo-04"),
    IntentSpec("overdue_fee", "逾期费用怎么收", "fee-demo-05"),
    IntentSpec("collection_boundary", "催收边界在哪里", "fee-demo-05"),
    IntentSpec("billing_dispute", "账单异议怎么复核", "fee-demo-06"),
    IntentSpec("fee_review_flow", "费用复核流程", "fee-demo-06"),
)

_FEE_NORMAL: tuple[InspectionTemplate, ...] = (
    InspectionTemplate(
        "waiver_request",
        "fee_waiver",
        (
            ("客户", "我这张卡年费能不能免？"),
            ("坐席", "这次年费我给您免了，不用再还。"),
            ("客户", "真的吗？"),
            ("坐席", "真的，我给您免了。"),
        ),
        {
            "DEMO-FEE-01": _hit(
                "坐席直接承诺减免年费，未说明审批结果或有效条款依据。",
                1,
                "我给您免了",
            ),
            "DEMO-FEE-02": _clear("会话只承诺减免，没有给出与有效条款冲突的费用口径。"),
        },
    ),
    InspectionTemplate(
        "waiver_pending",
        "fee_waiver",
        (
            ("客户", "年费能减免吗？"),
            (
                "坐席",
                "减免需要提交申请并审批，我现在帮您提交，结果以审批通知为准，不能先承诺。",
            ),
        ),
        {
            "DEMO-FEE-01": _clear("坐席说明需审批、结果以通知为准，没有承诺减免结果。"),
            "DEMO-FEE-02": _clear("坐席未给出与有效条款冲突的费用说明。"),
        },
    ),
    InspectionTemplate(
        "fee_conflict",
        "fee_statement",
        (
            ("客户", "这张卡的年费到底怎么收？"),
            ("坐席", "这张卡年费永久免，之后都不会再收。"),
        ),
        {
            "DEMO-FEE-01": _clear("坐席没有承诺减免已产生的费用，而是说明年费规则。"),
            "DEMO-FEE-02": _hit(
                "口头说明与有效条款冲突：演示制度要求年费按合同与公告执行，坐席却称永久免。",
                1,
                "永久免",
            ),
        },
    ),
    InspectionTemplate(
        "fee_announcement",
        "fee_statement",
        (
            ("客户", "年费是不是又涨了？"),
            (
                "坐席",
                "年费是否收取以最新公告、合同和账单为准，我帮您核对当前有效条款后再答复。",
            ),
        ),
        {
            "DEMO-FEE-01": _clear("坐席没有承诺减免费用。"),
            "DEMO-FEE-02": _clear("坐席指向最新公告与合同，未给出冲突口径。"),
        },
    ),
    InspectionTemplate(
        "installment_fee",
        "installment_fee",
        (
            ("客户", "分期手续费怎么收？"),
            (
                "坐席",
                "分期手续费按每期费率展示，提前结清时未入账手续费不再收取，具体以页面展示为准。",
            ),
        ),
        {
            "DEMO-FEE-01": _clear("会话没有承诺减免费用。"),
            "DEMO-FEE-02": _clear("坐席说明以页面展示为准，未给出与条款冲突的口径。"),
        },
    ),
    InspectionTemplate(
        "minimum_payment",
        "minimum_payment",
        (
            ("客户", "只还最低还款会怎样？"),
            ("坐席", "最低还款后未还部分按日计息，具体利率和计息方式以账单与合同为准。"),
        ),
        {
            "DEMO-FEE-01": _clear("会话没有承诺减免费用。"),
            "DEMO-FEE-02": _clear("坐席说明以账单与合同为准，未给出冲突口径。"),
        },
    ),
    InspectionTemplate(
        "overdue_boundary",
        "overdue_fee",
        (
            ("客户", "逾期一天会怎样？"),
            (
                "坐席",
                "逾期可能产生违约金和利息，具体以合同和账单为准；催收会按约定方式进行。",
            ),
        ),
        {
            "DEMO-FEE-01": _clear("会话没有承诺减免费用。"),
            "DEMO-FEE-02": _clear("坐席说明以合同和账单为准，未给出冲突口径。"),
        },
    ),
    InspectionTemplate(
        "billing_dispute",
        "billing_dispute",
        (
            ("客户", "我觉得这笔费用不对。"),
            ("坐席", "我帮您登记费用复核，请提供账单月份和交易明细，复核结果会书面答复。"),
        ),
        {
            "DEMO-FEE-01": _clear("会话没有承诺减免费用。"),
            "DEMO-FEE-02": _clear("坐席只说明复核流程，未给出费用口径。"),
        },
    ),
    InspectionTemplate(
        "annual_fee_explain",
        "fee_statement",
        (
            ("客户", "年费是按年收还是一次性收？"),
            ("坐席", "年费按年收取，具体金额和免收条件以当前合同与公告为准。"),
        ),
        {
            "DEMO-FEE-01": _clear("会话没有承诺减免费用。"),
            "DEMO-FEE-02": _clear("坐席指向当前合同与公告，未给出冲突口径。"),
        },
    ),
    InspectionTemplate(
        "late_fee_review",
        "late_fee",
        (
            ("客户", "违约金能不能少收点？"),
            ("坐席", "违约金调整需要提交申请并审批，我不能先承诺结果，帮您提交后以通知为准。"),
        ),
        {
            "DEMO-FEE-01": _clear("坐席说明需审批、不能先承诺，不构成承诺减免。"),
            "DEMO-FEE-02": _clear("会话没有出现与有效条款冲突的费用说明。"),
        },
    ),
    InspectionTemplate(
        "repayment_order",
        "repayment_order",
        (
            ("客户", "我还的钱先冲哪部分？"),
            ("坐席", "还款入账顺序按合同约定执行，通常先冲费用和利息，具体以账单展示为准。"),
        ),
        {
            "DEMO-FEE-01": _clear("会话没有承诺减免费用。"),
            "DEMO-FEE-02": _clear("坐席说明以合同与账单为准，未给出冲突口径。"),
        },
    ),
)

_FEE_HARD: tuple[InspectionTemplate, ...] = (
    InspectionTemplate(
        "negation_fee",
        "fee_waiver",
        (
            ("客户", "你们是不是要收我年费？"),
            ("坐席", "不会，不能直接免年费，减免也要先审批。"),
        ),
        {
            "DEMO-FEE-01": LabelSpec(
                "clear",
                "关键词“免年费”前有否定词“不能”，属于否定表述，不是承诺减免。",
                (1, "不能直接免年费"),
            ),
            "DEMO-FEE-02": _clear("会话没有给出与有效条款冲突的费用口径。"),
        },
        hard_categories=("negation",),
    ),
    InspectionTemplate(
        "quoted_waiver",
        "fee_waiver",
        (
            ("客户", "听说可以免年费，是真的吗？"),
            ("坐席", "能否减免要看审批，不能直接承诺。"),
        ),
        {
            "DEMO-FEE-01": LabelSpec(
                "clear",
                "关键词“可以免年费”出现在客户转述中，不是坐席承诺，不能按关键词判命中。",
                (0, "可以免年费"),
            ),
            "DEMO-FEE-02": _clear("会话没有给出与有效条款冲突的费用口径。"),
        },
        hard_categories=("quoted_speech",),
    ),
    InspectionTemplate(
        "partial_bill",
        "fee_statement",
        (
            ("客户", "我账单好像不对。"),
            ("坐席", "请具体说明是哪一笔费用，我再核对。"),
        ),
        {
            "DEMO-FEE-01": _insufficient("客户未提出减免请求，坐席也未作费用承诺，无法判断。"),
            "DEMO-FEE-02": _insufficient("会话未引用任何费用条款，无法判断费用说明是否冲突。"),
        },
        hard_categories=("insufficient_info",),
    ),
    InspectionTemplate(
        "empty_call",
        "no_content",
        (("客户", "喂？能听见吗？"),),
        {
            "DEMO-FEE-01": _insufficient("会话只有客户呼叫，没有可供判断的坐席发言。"),
            "DEMO-FEE-02": _insufficient("会话只有客户呼叫，没有可供判断的坐席发言。"),
        },
        hard_categories=("no_answer",),
    ),
    InspectionTemplate(
        "expired_fee",
        "fee_statement",
        (
            ("客户", "2024年的年费减免政策现在还能用吗？"),
            (
                "坐席",
                "那是2024年度版本，已经失效；现行是否减免要以最新公告和审批为准。",
            ),
        ),
        {
            "DEMO-FEE-01": _clear("坐席没有承诺减免结果，只说明政策已失效。"),
            "DEMO-FEE-02": _insufficient("引用的2024年度政策已失效，无法据以判断费用说明是否冲突。"),
        },
        hard_categories=("expired_knowledge",),
    ),
    InspectionTemplate(
        "mixed_fee_request",
        "fee_statement",
        (
            ("客户", "年费为什么收？我要投诉。"),
            ("坐席", "我先帮您核对费用构成，投诉登记需要您说明具体诉求。"),
        ),
        {
            "DEMO-FEE-01": _insufficient("客户只是询问费用并表达投诉，坐席未作减免承诺，无法判断。"),
            "DEMO-FEE-02": _insufficient("客户诉求混杂，坐席尚未给出费用说明，无法判断是否冲突。"),
        },
        hard_categories=("ambiguous_intent",),
    ),
)

_FEE_HARD_QUERIES: dict[str, tuple[HardQuery, ...]] = {
    "no_answer": (
        HardQuery("你们支持数字人民币钱包吗？"),
        HardQuery("可以办理公积金贷款吗？"),
        HardQuery("信用卡能绑定几个第三方支付？"),
        HardQuery("你们的营业网点几点关门？"),
        HardQuery("可以开企业基本户吗？"),
    ),
    "insufficient_info": (
        HardQuery("我这个账单，这个怎么办？"),
        HardQuery("费用那个可以吗？"),
        HardQuery("帮我算一下。"),
        HardQuery("行吧，你说是就是。"),
        HardQuery("那个费率怎么定？"),
    ),
    "expired_knowledge": tuple(
        HardQuery(text, excluded_doc_ids=("edge-expired-01",), reason="expired_knowledge")
        for text in (
            "2024年补卡工本费10元的旧规定还能用吗？",
            "2024年度的费用减免口径现在还有效吗？",
            "去年公告的年费减免办法还执行吗？",
            "2024年版本的收费标准还能引用吗？",
            "按2024年的旧费率算，手续费是多少？",
        )
    ),
    "negation": (
        HardQuery("我不是要免年费，我是问年费怎么收。", ("fee-demo-01",)),
        HardQuery("不用减免，我只想知道分期手续费。", ("fee-demo-02",)),
        HardQuery("不是永久免年费，是问有没有优惠活动。", ("fee-demo-01",)),
        HardQuery("我没说要投诉，我只问账单为什么多了。", ("fee-demo-06",)),
        HardQuery("不要给我免年费，先解释清楚费用构成。", ("fee-demo-01",)),
    ),
    "quoted_speech": (
        HardQuery("听说可以免年费，是真的吗？", ("fee-demo-01",)),
        HardQuery("客服说分期不要手续费，对吗？", ("fee-demo-02",)),
        HardQuery("朋友说最低还款没有利息，是吗？", ("fee-demo-03",)),
        HardQuery("有人说逾期一天没关系，是真的吗？", ("fee-demo-05",)),
        HardQuery("同事说年费可以永久免，是这样吗？", ("fee-demo-01",)),
    ),
    "ambiguous_intent": (
        HardQuery("年费为什么收，我要投诉。", ("fee-demo-01", "complaint-demo-01")),
        HardQuery("分期手续费和年费是不是重复收了？", ("fee-demo-02", "fee-demo-01")),
        HardQuery("我账单多了违约金，是不是逾期费用？", ("fee-pub-04", "fee-demo-05")),
        HardQuery("提前结清要收手续费还是要收违约金？", ("fee-demo-02", "fee-pub-04")),
        HardQuery("最低还款之后利息和年费怎么算？", ("fee-demo-03", "fee-demo-01")),
    ),
}

# ---------------------------------------------------------------------------
# 投诉受理
# ---------------------------------------------------------------------------

_COMPLAINT_INTENTS: tuple[IntentSpec, ...] = (
    IntentSpec("complaint_channel", "投诉渠道有哪些", "complaint-pub-01"),
    IntentSpec("complaint_timeline", "投诉多久要回复", "complaint-pub-01"),
    IntentSpec("complaint_privacy", "投诉中的个人信息怎么保护", "complaint-pub-02"),
    IntentSpec("complaint_material_security", "投诉材料会泄露吗", "complaint-pub-02"),
    IntentSpec("credit_complaint_bridge", "征信异议和投诉怎么衔接", "complaint-pub-03"),
    IntentSpec("credit_complaint_scope", "征信问题能不能投诉", "complaint-pub-03"),
    IntentSpec("complaint_mediation", "投诉调解途径", "complaint-pub-04"),
    IntentSpec("mediation_application", "调解申请怎么提交", "complaint-pub-04"),
    IntentSpec("complaint_registration", "投诉登记要哪些要素", "complaint-demo-01"),
    IntentSpec("complaint_grading", "投诉怎么分级", "complaint-demo-01"),
    IntentSpec("first_response_time", "投诉首次响应时限", "complaint-demo-02"),
    IntentSpec("response_clock", "响应时限从什么时候开始算", "complaint-demo-02"),
    IntentSpec("complaint_escalation", "投诉怎么升级转办", "complaint-demo-03"),
    IntentSpec("transfer_owner", "投诉转办给谁", "complaint-demo-03"),
    IntentSpec("complaint_callback", "投诉回访怎么做", "complaint-demo-04"),
    IntentSpec("satisfaction_record", "满意度记录怎么记", "complaint-demo-04"),
    IntentSpec("repeated_complaint", "重复投诉怎么处理", "complaint-demo-05"),
    IntentSpec("batch_complaint", "批量投诉怎么处理", "complaint-demo-05"),
    IntentSpec("complaint_language", "投诉沟通不能说什么", "complaint-demo-06"),
    IntentSpec("complaint_tone", "投诉沟通的语气要求", "complaint-demo-06"),
)

_COMPLAINT_NORMAL: tuple[InspectionTemplate, ...] = (
    InspectionTemplate(
        "obstruction",
        "complaint_obstruction",
        (
            ("客户", "我要投诉你们乱收费。"),
            ("坐席", "投诉也没用，结果不会变的，您别登记了。"),
        ),
        {
            "DEMO-CMP-01": _hit(
                "坐席用“投诉也没用”劝阻客户投诉，属于阻碍投诉。",
                1,
                "投诉也没用",
            ),
            "DEMO-ATT-01": _clear("坐席劝阻但未使用明显侮辱性表达。"),
        },
    ),
    InspectionTemplate(
        "insult",
        "insulting_language",
        (
            ("客户", "这个问题我已经说过三遍了。"),
            ("坐席", "这么简单的问题都说不清，这么笨。"),
        ),
        {
            "DEMO-CMP-01": _clear("会话没有阻碍客户投诉，只是重复解释时情绪化表达。"),
            "DEMO-ATT-01": _hit("坐席使用明显贬损表达“这么笨”。", 1, "这么笨"),
        },
    ),
    InspectionTemplate(
        "register",
        "complaint_registration",
        (
            ("客户", "我要投诉。"),
            ("坐席", "我马上为您登记投诉，登记后会转交专人处理。"),
        ),
        {
            "DEMO-CMP-01": _clear("坐席主动登记投诉，不构成阻碍。"),
            "DEMO-ATT-01": _clear("坐席表达中性，没有侮辱性用语。"),
        },
    ),
    InspectionTemplate(
        "channel_explain",
        "complaint_channel",
        (
            ("客户", "怎么投诉你们？"),
            (
                "坐席",
                "您可以通过客服电话、手机银行或网点提交投诉，受理后会在规定时限内响应。",
            ),
        ),
        {
            "DEMO-CMP-01": _clear("坐席完整说明投诉渠道，未劝阻或拒绝。"),
            "DEMO-ATT-01": _clear("坐席表达中性，没有侮辱性用语。"),
        },
    ),
    InspectionTemplate(
        "escalation",
        "complaint_escalation",
        (
            ("客户", "这个问题一直没人解决。"),
            ("坐席", "这个问题我帮您升级到投诉专席，由专人跟进。"),
        ),
        {
            "DEMO-CMP-01": _clear("坐席主动升级转办，不是阻碍投诉。"),
            "DEMO-ATT-01": _clear("坐席表达中性，没有侮辱性用语。"),
        },
    ),
    InspectionTemplate(
        "callback",
        "callback",
        (
            ("客户", "什么时候能给我回话？"),
            ("坐席", "投诉登记后会在规定时限内电话回访，回访结果会记录在案。"),
        ),
        {
            "DEMO-CMP-01": _clear("坐席说明回访安排，未阻碍投诉。"),
            "DEMO-ATT-01": _clear("坐席表达中性，没有侮辱性用语。"),
        },
    ),
    InspectionTemplate(
        "repeated_complaint",
        "repeated_complaint",
        (
            ("客户", "我上次投诉过，今天还要再说一次。"),
            ("坐席", "同一问题重复来电，我会把新情况补充到原投诉记录里，不再重复登记。"),
        ),
        {
            "DEMO-CMP-01": _clear("坐席补充原投诉记录，未拒绝受理。"),
            "DEMO-ATT-01": _clear("坐席表达中性，没有侮辱性用语。"),
        },
    ),
    InspectionTemplate(
        "privacy",
        "complaint_privacy",
        (
            ("客户", "我的信息会不会被传出去？"),
            ("坐席", "投诉材料只用于处理本次投诉，我们会按个人信息保护要求保管。"),
        ),
        {
            "DEMO-CMP-01": _clear("会话没有阻碍投诉。"),
            "DEMO-ATT-01": _clear("坐席表达中性，没有侮辱性用语。"),
        },
    ),
    InspectionTemplate(
        "mediation",
        "mediation",
        (
            ("客户", "调解要怎么申请？"),
            ("坐席", "可以向调解组织提交申请，也可以先由我们登记投诉，调解不影响投诉处理。"),
        ),
        {
            "DEMO-CMP-01": _clear("坐席说明调解与投诉并行，未阻碍投诉。"),
            "DEMO-ATT-01": _clear("坐席表达中性，没有侮辱性用语。"),
        },
    ),
    InspectionTemplate(
        "feedback",
        "satisfaction_record",
        (
            ("客户", "处理完了会问我满不满意吗？"),
            ("坐席", "处理完成后会回访并记录满意度，您的意见会进入服务改进。"),
        ),
        {
            "DEMO-CMP-01": _clear("会话没有阻碍投诉。"),
            "DEMO-ATT-01": _clear("坐席表达中性，没有侮辱性用语。"),
        },
    ),
    InspectionTemplate(
        "angry_customer",
        "service_recovery",
        (
            ("客户", "你们太差劲了！"),
            ("坐席", "非常抱歉，我理解您的心情，我马上帮您处理。"),
        ),
        {
            "DEMO-CMP-01": _clear("客户表达不满但未提出投诉，坐席致歉处理。"),
            "DEMO-ATT-01": _clear("客户有情绪，坐席未使用侮辱性表达。"),
        },
    ),
)

_COMPLAINT_HARD: tuple[InspectionTemplate, ...] = (
    InspectionTemplate(
        "negation_obstruction",
        "complaint_obstruction",
        (
            ("客户", "你们是不是不想受理我的投诉？"),
            ("坐席", "不会，我们不会拒绝受理投诉，马上为您登记。"),
        ),
        {
            "DEMO-CMP-01": LabelSpec(
                "clear",
                "关键词“拒绝受理”前有否定词“不会”，是防护性表述，不是阻碍投诉。",
                (1, "不会拒绝受理投诉"),
            ),
            "DEMO-ATT-01": _clear("坐席表达中性，没有侮辱性用语。"),
        },
        hard_categories=("negation",),
    ),
    InspectionTemplate(
        "quoted_obstruction",
        "complaint_obstruction",
        (
            ("客户", "听说投诉也没用，是真的吗？"),
            ("坐席", "这种说法不准确，投诉会按流程登记并答复。"),
        ),
        {
            "DEMO-CMP-01": LabelSpec(
                "clear",
                "关键词“投诉也没用”出现在客户转述中，不是坐席阻碍投诉。",
                (0, "投诉也没用"),
            ),
            "DEMO-ATT-01": _clear("坐席表达中性，没有侮辱性用语。"),
        },
        hard_categories=("quoted_speech",),
    ),
    InspectionTemplate(
        "partial_complaint",
        "complaint_registration",
        (
            ("客户", "我要投诉。"),
            ("坐席", "请说明投诉的具体事项，我才能登记。"),
        ),
        {
            "DEMO-CMP-01": _insufficient("客户只表达投诉意愿，坐席尚未作出受理或阻碍行为，无法判断。"),
            "DEMO-ATT-01": _insufficient("会话没有坐席对客户使用贬损表达的原文，无法判断。"),
        },
        hard_categories=("insufficient_info",),
    ),
    InspectionTemplate(
        "empty_call",
        "no_content",
        (("客户", "喂？在吗？"),),
        {
            "DEMO-CMP-01": _insufficient("会话只有客户呼叫，没有可供判断的坐席发言。"),
            "DEMO-ATT-01": _insufficient("会话只有客户呼叫，没有可供判断的坐席发言。"),
        },
        hard_categories=("no_answer",),
    ),
    InspectionTemplate(
        "future_policy",
        "complaint_timeline",
        (
            ("客户", "2027年投诉响应时间是1小时吗？"),
            ("坐席", "1小时响应是尚未生效的2027年版本，现行时限以有效制度为准。"),
        ),
        {
            "DEMO-CMP-01": _insufficient("引用的2027年版本尚未生效，不能据此判断受理是否规范。"),
            "DEMO-ATT-01": _clear("坐席表达中性，没有侮辱性用语。"),
        },
        hard_categories=("expired_knowledge",),
    ),
    InspectionTemplate(
        "mixed_complaint",
        "complaint_registration",
        (
            ("客户", "我要投诉，你们服务太差了。"),
            ("坐席", "我先了解具体情况，再决定是登记投诉还是转服务改进。"),
        ),
        {
            "DEMO-CMP-01": _insufficient("客户诉求在投诉与服务改进之间，坐席尚未明确受理，无法判断。"),
            "DEMO-ATT-01": _clear("坐席表达中性，没有侮辱性用语。"),
        },
        hard_categories=("ambiguous_intent",),
    ),
)

_COMPLAINT_HARD_QUERIES: dict[str, tuple[HardQuery, ...]] = {
    "no_answer": (
        HardQuery("你们周末营业吗？"),
        HardQuery("信用卡怎么开通境外支付？"),
        HardQuery("可以查询我的征信报告吗？"),
        HardQuery("你们有保管箱业务吗？"),
        HardQuery("手机银行怎么修改登录密码？"),
    ),
    "insufficient_info": (
        HardQuery("我要投诉，这个怎么办？"),
        HardQuery("我要投诉刚才那个事。"),
        HardQuery("你们服务太差了，我要投诉。"),
        HardQuery("我投诉了，然后呢？"),
        HardQuery("这事你们管不管？"),
    ),
    "expired_knowledge": tuple(
        HardQuery(text, excluded_doc_ids=("edge-future-01",), reason="expired_knowledge")
        for text in (
            "2027年投诉1小时响应是真的吗？",
            "按2027年新规，投诉是不是1小时回复？",
            "明年才生效的投诉响应时限现在适用吗？",
            "2027年版本的投诉时限能引用吗？",
            "1小时响应是现行规定吗？",
        )
    ),
    "negation": (
        HardQuery("我不是要投诉，我只是想问处理进度。", ("complaint-demo-03",)),
        HardQuery("不用登记投诉，先帮我查一下进度。", ("complaint-demo-03",)),
        HardQuery("我不是说你们服务差，我问的是时限。", ("complaint-demo-02",)),
        HardQuery("别转投诉，我只想补充材料。", ("complaint-demo-05",)),
        HardQuery("我没有投诉的意思，只想确认回访时间。", ("complaint-demo-04",)),
    ),
    "quoted_speech": (
        HardQuery("听说投诉也没用，是真的吗？", ("complaint-demo-06",)),
        HardQuery("有人说投诉会被拖着不处理，是吗？", ("complaint-demo-02",)),
        HardQuery("朋友说重复投诉会被拒绝，对吗？", ("complaint-demo-05",)),
        HardQuery("客服说投诉不用登记，是真的吗？", ("complaint-demo-01",)),
        HardQuery("同事说投诉回访只是走形式，是吗？", ("complaint-demo-04",)),
    ),
    "ambiguous_intent": (
        HardQuery("我要投诉，你们服务太差了。", ("complaint-demo-01", "complaint-demo-06")),
        HardQuery("我要投诉，顺便问下年费为什么收。", ("complaint-demo-01", "fee-demo-01")),
        HardQuery("投诉登记后能不能顺便查账单？", ("complaint-demo-01", "fee-demo-06")),
        HardQuery("我要投诉这个费用，算投诉还是算争议？", ("complaint-demo-01", "fee-demo-06")),
        HardQuery("我要投诉，但先告诉我处理要多久。", ("complaint-pub-01", "complaint-demo-02")),
    ),
}


SCENARIO_SPECS: dict[str, ScenarioSpec] = {
    "lost_card": ScenarioSpec(
        key="lost_card",
        label="挂失咨询",
        business_type="lost_card",
        rule_codes=("DEMO-PWD-01", "DEMO-CLAIM-01"),
        intents=_LOST_INTENTS,
        normal_templates=_LOST_NORMAL,
        hard_templates=_LOST_HARD,
        hard_queries=_LOST_HARD_QUERIES,
    ),
    "credit_card_fee": ScenarioSpec(
        key="credit_card_fee",
        label="信用卡费用与还款咨询",
        business_type="credit_card_fee",
        rule_codes=("DEMO-FEE-01", "DEMO-FEE-02"),
        intents=_FEE_INTENTS,
        normal_templates=_FEE_NORMAL,
        hard_templates=_FEE_HARD,
        hard_queries=_FEE_HARD_QUERIES,
    ),
    "complaint": ScenarioSpec(
        key="complaint",
        label="投诉受理",
        business_type="complaint",
        rule_codes=("DEMO-CMP-01", "DEMO-ATT-01"),
        intents=_COMPLAINT_INTENTS,
        normal_templates=_COMPLAINT_NORMAL,
        hard_templates=_COMPLAINT_HARD,
        hard_queries=_COMPLAINT_HARD_QUERIES,
    ),
}

# 变体结尾：同一模板家族的改写只改收尾措辞，标签与证据不随之改变。
VARIANT_TAILS: tuple[str, ...] = (
    "好的，谢谢。",
    "明白了，麻烦您。",
    "行，那我等通知。",
    "好的，我再确认一下。",
)
