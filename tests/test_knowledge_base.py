"""T32：知识整理与话术生成修通（M1-01 剩余缺陷）。

覆盖 issue #33 的四个缺陷：
1. 意图分类默认类别可正常构造；
2. 话术格式化（完整/催收/问候）不再抛 NameError；
3. 自定义数量/语气参数真正传到模型提示词；
4. 相似问过滤校验新生成的问题，空库不清空结果。

所有模型与向量库都用假实现，测试不联网。
"""

import pytest
from fastapi.testclient import TestClient


class FakeLLMClient:
    """假模型客户端：记录提示词并按预设返回文本。"""

    def __init__(self, response: str = "", intent: str = None):
        self.response = response
        self.intent = intent
        self.prompts = []
        self.classify_calls = []

    def generate_text(self, prompt: str, **kwargs) -> str:
        self.prompts.append(prompt)
        return self.response

    def classify_intent(self, text, categories):
        self.classify_calls.append((text, categories))
        return {"intent": self.intent or categories[0], "confidence": 0.9}


class FakeVectorDB:
    """假向量库：按查询文本返回预设最近邻，并记录查询内容。"""

    def __init__(self, neighbors=None):
        self.neighbors = neighbors or {}
        self.queries = []

    def query_with_score(self, query_text: str, n_results: int = 5):
        self.queries.append(query_text)
        return self.neighbors.get(query_text, [])


class TestIntentClassifierDefaults:
    def test_default_categories_are_the_renamed_domain_categories(self):
        """默认类别使用用户重命名后的常量，构造不再抛 AttributeError。"""
        from bank_assistant.assistants.knowledge_base.intent_classifier import (
            IntentClassifier,
        )

        classifier = IntentClassifier(FakeLLMClient())

        assert classifier.get_categories() == [
            "账户查询",
            "业务办理",
            "投诉建议",
            "意见反馈",
            "技术支持",
            "其他",
        ]

    def test_classify_uses_default_categories(self):
        """不传 categories 时，默认类别被传给模型。"""
        from bank_assistant.assistants.knowledge_base.intent_classifier import (
            IntentClassifier,
        )

        fake_llm = FakeLLMClient(intent="账户查询")
        classifier = IntentClassifier(fake_llm)

        result = classifier.classify_intent("我想查询信用卡账单")

        assert result.primary_intent == "账户查询"
        assert fake_llm.classify_calls[0][1] == classifier.get_categories()


class TestScriptFormatting:
    def test_call_script_formats_greeting_body_and_closing(self):
        from bank_assistant.assistants.knowledge_base.script_generator import CallScript

        script = CallScript(
            greeting="您好，我是银行客服。",
            main_content="您的信用卡账单已发送至预留邮箱。",
            closing="感谢您的来电，再见。",
            scenario="账单查询",
        )

        assert script.format_full_script() == (
            "您好，我是银行客服。\n"
            "您的信用卡账单已发送至预留邮箱。\n"
            "感谢您的来电，再见。"
        )

    def test_call_script_with_only_greeting_still_formats(self):
        """问候分支（其余字段为空）也要正常格式化。"""
        from bank_assistant.assistants.knowledge_base.script_generator import CallScript

        script = CallScript(
            greeting="您好，请问有什么可以帮您？",
            main_content="",
            closing="",
            scenario="问候",
        )

        assert script.format_full_script() == "您好，请问有什么可以帮您？"

    def test_collection_script_formats_opening_negotiation_and_commitment(self):
        from bank_assistant.assistants.knowledge_base.script_generator import (
            CollectionScript,
        )

        script = CollectionScript(
            opening="您好，关于您的逾期账单。",
            negotiation="您当前可申请分期还款。",
            commitment_request="请问您最晚何时可以还款？",
            risk_level="低风险",
            overdue_days=30,
        )

        assert script.format_full_script() == (
            "您好，关于您的逾期账单。\n"
            "您当前可申请分期还款。\n"
            "请问您最晚何时可以还款？"
        )


THREE_CALL_SCRIPT_BLOCKS = (
    "1. 问候：您好，我是银行客服。\n"
    "2. 内容：您的信用卡账单已发送。\n"
    "3. 结束：感谢来电，再见。\n"
    "\n"
    "1. 问候：您好，很高兴为您服务。\n"
    "2. 内容：账单可在手机银行查询。\n"
    "3. 结束：如有疑问请随时联系。\n"
    "\n"
    "1. 问候：您好。\n"
    "2. 内容：已为您登记。\n"
    "3. 结束：再见。"
)


class TestScriptGeneratorParameters:
    def test_api_forwards_count_and_tone_to_generator(self):
        """请求模型里的 count/tone 必须一路传到模型调用，并限制候选数。"""
        from bank_assistant.assistants.knowledge_base.script_generator import (
            ScriptGenerator,
        )
        from bank_assistant.api.routers.knowledge_base import get_script_generator
        from bank_assistant.main import app

        fake_llm = FakeLLMClient(response=THREE_CALL_SCRIPT_BLOCKS)
        app.dependency_overrides[get_script_generator] = lambda: ScriptGenerator(
            fake_llm
        )
        try:
            client = TestClient(app)
            response = client.post(
                "/api/v1/knowledge-base/generate-scripts",
                json={
                    "script_type": "call",
                    "parameters": {
                        "scenario": "信用卡账单查询",
                        "customer_type": "普通客户",
                    },
                    "count": 1,
                    "tone": "简洁专业",
                },
            )
        finally:
            app.dependency_overrides.clear()

        assert response.status_code == 200
        assert len(response.json()["scripts"]) == 1
        prompt = fake_llm.prompts[0]
        assert "简洁专业" in prompt
        assert "1个" in prompt

    def test_collection_scripts_use_count_and_tone(self):
        from bank_assistant.assistants.knowledge_base.script_generator import (
            ScriptGenerator,
        )

        fake_llm = FakeLLMClient(
            response=(
                "1. 开场：您好，关于您的逾期账单。\n"
                "2. 协商：可申请分期。\n"
                "3. 承诺：请确认还款时间。"
            )
        )

        scripts = ScriptGenerator(fake_llm).generate_collection_scripts(
            overdue_days=30, count=1, tone="严肃"
        )

        assert len(scripts) == 1
        prompt = fake_llm.prompts[0]
        assert "严肃" in prompt
        assert "1个" in prompt


SIMILAR_QUESTIONS_RESPONSE = (
    "1. 信用卡怎么挂失\n"
    "2. 银行卡丢了怎么办\n"
    "3. 挂失需要什么材料"
)


class TestSimilarQuestionFilter:
    def test_empty_knowledge_base_keeps_generated_questions(self):
        """空库没有可比较的旧问题，不得清空新生成的结果。"""
        from bank_assistant.assistants.knowledge_base.question_generator import (
            QuestionGenerator,
        )

        fake_db = FakeVectorDB()
        generator = QuestionGenerator(
            FakeLLMClient(response=SIMILAR_QUESTIONS_RESPONSE), fake_db
        )

        questions = generator.generate_similar_questions("信用卡挂失", count=3)

        assert questions == [
            "信用卡怎么挂失",
            "银行卡丢了怎么办",
            "挂失需要什么材料",
        ]

    def test_filter_validates_generated_questions_not_old_query_result(self):
        """过滤逐个校验新生成的问题，并剔除知识库中已有的重复问题。"""
        from bank_assistant.assistants.knowledge_base.question_generator import (
            QuestionGenerator,
        )

        fake_db = FakeVectorDB(
            neighbors={
                "信用卡挂失": [{"document": "旧库已有问题", "distance": 0.1}],
                "信用卡怎么挂失": [{"document": "信用卡怎么挂失", "distance": 0.1}],
                "银行卡丢了怎么办": [{"document": "银行卡丢了怎么办", "distance": 0.95}],
                "挂失需要什么材料": [{"document": "挂失流程", "distance": 0.7}],
            }
        )
        generator = QuestionGenerator(
            FakeLLMClient(response=SIMILAR_QUESTIONS_RESPONSE), fake_db
        )

        questions = generator.generate_similar_questions("信用卡挂失", count=3)

        assert "旧库已有问题" not in questions
        assert "信用卡挂失" not in fake_db.queries
        assert fake_db.queries == [
            "信用卡怎么挂失",
            "银行卡丢了怎么办",
            "挂失需要什么材料",
        ]
        # 距离 0.95 表示知识库没有等价的既有问题，应保留。
        assert questions == ["银行卡丢了怎么办"]
