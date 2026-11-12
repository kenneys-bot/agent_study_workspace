"""
测试模块
"""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    """创建测试客户端"""
    from main import app
    return TestClient(app)


@pytest.fixture
def sample_conversation():
    """示例对话"""
    return """
客户：您好，我想查询一下我的信用卡账单
客服：好的，请问您想查询什么时候的账单？
客户：我想查一下上个月的账单
客服：好的，我帮您查询
    """


class TestHealthEndpoints:
    """健康检查接口测试"""
    
    def test_root(self, client):
        """测试根路径"""
        response = client.get("/")
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "Customer Service AI"
        assert data["status"] == "running"
    
    def test_health_check(self, client):
        """测试健康检查"""
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
    
    def test_app_info(self, client):
        """测试应用信息"""
        response = client.get("/info")
        assert response.status_code == 200


class TestKnowledgeBaseAPI:
    """客服知识库助手API测试"""
    
    def test_extract_questions(self, client, sample_conversation):
        """测试问题抽取"""
        response = client.post(
            "/api/v1/knowledge-base/extract-questions",
            json={
                "conversation": sample_conversation,
                "max_questions": 3
            }
        )
        assert response.status_code == 200
        data = response.json()
        assert "questions" in data
        assert "key_info" in data
    
    def test_classify_intent(self, client):
        """测试意图分类"""
        response = client.post(
            "/api/v1/knowledge-base/classify-intent",
            json={
                "query": "我想查询信用卡账单",
                "categories": ["账户查询", "业务办理", "投诉建议"]
            }
        )
        assert response.status_code == 200
        data = response.json()
        assert "primary_intent" in data
        assert "confidence" in data
    
    def test_generate_questions(self, client):
        """测试问题生成"""
        response = client.post(
            "/api/v1/knowledge-base/generate-questions",
            json={
                "topic": "信用卡申请",
                "question_type": "standard",
                "count": 3
            }
        )
        assert response.status_code == 200
        data = response.json()
        assert "questions" in data
    
    def test_generate_scripts(self, client):
        """测试话术生成"""
        response = client.post(
            "/api/v1/knowledge-base/generate-scripts",
            json={
                "script_type": "call",
                "parameters": {
                    "scenario": "信用卡账单查询",
                    "customer_type": "普通客户"
                },
                "count": 2
            }
        )
        assert response.status_code == 200
        data = response.json()
        assert "scripts" in data


class TestScriptRecommenderAPI:
    """话术推荐助手API测试"""
    
    def test_analyze_context(self, client):
        """测试情境分析"""
        response = client.post(
            "/api/v1/script-recommender/analyze-context",
            json={
                "conversation_history": [
                    {"role": "customer", "content": "我想查账单"},
                    {"role": "agent", "content": "好的，我来帮您查"}
                ]
            }
        )
        assert response.status_code == 200
        data = response.json()
        assert "topic" in data
        assert "stage" in data
    
    def test_recognize_intent(self, client):
        """测试意图识别"""
        response = client.post(
            "/api/v1/script-recommender/recognize-intent",
            json={
                "current_query": "我的账单什么时候能出来",
                "context": {"topic": "账单查询", "stage": "主阶段"}
            }
        )
        assert response.status_code == 200
        data = response.json()
        assert "intent_type" in data
        assert "confidence" in data
    
    def test_recommend_scripts(self, client):
        """测试话术推荐"""
        response = client.post(
            "/api/v1/script-recommender/recommend-scripts",
            json={
                "context": {
                    "topic": "账单查询",
                    "stage": "主阶段",
                    "customer_satisfaction": 0.8
                },
                "intent": {
                    "intent_type": "查询",
                    "confidence": 0.9
                },
                "count": 3
            }
        )
        assert response.status_code == 200
        data = response.json()
        assert "scripts" in data
    
    def test_personalize_script(self, client):
        """测试话术个性化"""
        response = client.post(
            "/api/v1/script-recommender/personalize-script",
            json={
                "script": "您好，请问有什么可以帮助您的？",
                "customer_profile": {
                    "customer_id": "C001",
                    "name": "张三",
                    "customer_type": "VIP客户"
                }
            }
        )
        assert response.status_code == 200
        data = response.json()
        assert "personalized_script" in data


class TestQualityInspectorAPI:
    """质检助手API测试"""
    
    def test_parse_conversation(self, client, sample_conversation):
        """测试对话解析"""
        response = client.post(
            "/api/v1/quality-inspector/parse-conversation",
            json={
                "content": sample_conversation,
                "format": "text"
            }
        )
        assert response.status_code == 200
        data = response.json()
        assert "session_id" in data
        assert "participants" in data
        assert "turns" in data
    
    def test_inspect_conversation(self, client, sample_conversation):
        """测试对话质检"""
        response = client.post(
            "/api/v1/quality-inspector/inspect",
            json={
                "conversation": {
                    "session_id": "test_001",
                    "participants": ["客户", "客服"],
                    "turns": [
                        {"speaker": "客户", "content": "我想查账单"},
                        {"speaker": "客服", "content": "好的，我来帮您查"}
                    ]
                }
            }
        )
        assert response.status_code == 200
        data = response.json()
        assert "overall_score" in data
        assert "issues" in data


class TestValidation:
    """输入验证测试"""
    
    def test_missing_required_field(self, client):
        """测试缺少必需字段"""
        response = client.post(
            "/api/v1/knowledge-base/extract-questions",
            json={}  # 缺少conversation字段
        )
        assert response.status_code == 422  # Validation Error
    
    def test_invalid_conversation_format(self, client):
        """测试无效的对话格式"""
        response = client.post(
            "/api/v1/quality-inspector/parse-conversation",
            json={
                "content": "",
                "format": "text"
            }
        )
        # 空内容应该能处理，但可能返回不同的结果


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
