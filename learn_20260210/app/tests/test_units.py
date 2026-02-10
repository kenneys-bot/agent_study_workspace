"""
单元测试
"""

import pytest
from unittest.mock import Mock, patch


class TestHelpers:
    """工具函数测试"""
    
    def test_datetime_utils_now(self):
        """测试日期时间工具"""
        from utils.helpers import DateTimeUtils
        
        now = DateTimeUtils.now()
        assert now is not None
    
    def test_datetime_utils_format(self):
        """测试日期格式化"""
        from utils.helpers import DateTimeUtils
        from datetime import datetime
        
        dt = datetime(2024, 1, 1, 12, 0, 0)
        formatted = DateTimeUtils.format_datetime(dt, "%Y-%m-%d")
        assert formatted == "2024-01-01"
    
    def test_string_utils_is_empty(self):
        """测试字符串空检查"""
        from utils.helpers import StringUtils
        
        assert StringUtils.is_empty("") is True
        assert StringUtils.is_empty("   ") is True
        assert StringUtils.is_empty("hello") is False
        assert StringUtils.is_empty(None) is True
    
    def test_string_utils_truncate(self):
        """测试字符串截断"""
        from utils.helpers import StringUtils
        
        result = StringUtils.truncate("hello world", 5, "...")
        assert result == "hello..."
    
    def test_crypto_utils_md5(self):
        """测试MD5加密"""
        from utils.helpers import CryptoUtils
        
        result = CryptoUtils.md5("test")
        assert len(result) == 32
    
    def test_validation_utils_phone(self):
        """测试手机号验证"""
        from utils.helpers import ValidationUtils
        
        assert ValidationUtils.is_valid_phone("13800138000") is True
        assert ValidationUtils.is_valid_phone("12345678900") is False
    
    def test_validation_utils_email(self):
        """测试邮箱验证"""
        from utils.helpers import ValidationUtils
        
        assert ValidationUtils.is_valid_email("test@example.com") is True
        assert ValidationUtils.is_valid_email("invalid") is False


class TestDataModels:
    """数据模型测试"""
    
    def test_extracted_question(self):
        """测试抽取问题模型"""
        from assistants.knowledge_base.question_extractor import ExtractedQuestion
        
        q = ExtractedQuestion(
            question="如何申请信用卡？",
            context="客户在询问信用卡申请流程",
            priority=1
        )
        assert q.question == "如何申请信用卡？"
        assert q.priority == 1
        data = q.to_dict()
        assert "question" in data
    
    def test_intent_classification(self):
        """测试意图分类模型"""
        from assistants.knowledge_base.intent_classifier import IntentClassification
        
        ic = IntentClassification(
            primary_intent="账户查询",
            confidence=0.95
        )
        assert ic.primary_intent == "账户查询"
        assert ic.confidence == 0.95
    
    def test_conversation_context(self):
        """测试对话情境模型"""
        from assistants.script_recommender.context_analyzer import ConversationContext
        
        ctx = ConversationContext(
            topic="账单查询",
            stage="主阶段"
        )
        assert ctx.topic == "账单查询"
        assert ctx.stage == "主阶段"
    
    def test_user_intent(self):
        """测试用户意图模型"""
        from assistants.script_recommender.intent_recognizer import UserIntent
        
        intent = UserIntent(
            intent_type="查询",
            confidence=0.9
        )
        assert intent.intent_type == "查询"
        assert intent.confidence == 0.9
    
    def test_quality_issue(self):
        """测试质量问题模型"""
        from assistants.quality_inspector.auto_inspector import QualityIssue
        
        issue = QualityIssue(
            issue_type="服务态度",
            description="回复不够热情",
            severity="中"
        )
        assert issue.issue_type == "服务态度"
        assert issue.severity == "中"
    
    def test_inspection_report(self):
        """测试质检报告模型"""
        from assistants.quality_inspector.auto_inspector import InspectionReport
        
        report = InspectionReport(
            session_id="test_001",
            overall_score=85.0
        )
        assert report.session_id == "test_001"
        assert report.overall_score == 85.0
    
    def test_customer_profile(self):
        """测试客户画像模型"""
        from assistants.script_recommender.personalization_adapter import CustomerProfile
        
        profile = CustomerProfile(
            customer_id="C001",
            name="张三",
            age=30
        )
        assert profile.customer_id == "C001"
        assert profile.name == "张三"


class TestPromptTemplates:
    """提示词模板测试"""
    
    def test_knowledge_base_extract_template(self):
        """测试知识库抽取模板"""
        from config.constants import PromptTemplates
        
        template = PromptTemplates.KNOWLEDGE_BASE_EXTRACT
        assert "{conversation}" in template
    
    def test_script_recommend_template(self):
        """测试话术推荐模板"""
        from config.constants import PromptTemplates
        
        template = PromptTemplates.SCRIPT_RECOMMEND
        assert "{question}" in template
        assert "{emotion}" in template
    
    def test_quality_inspect_template(self):
        """测试质检模板"""
        from config.constants import PromptTemplates
        
        template = PromptTemplates.QUALITY_INSPECT
        assert "{conversation}" in template


class TestSettings:
    """配置测试"""
    
    def test_settings_load(self):
        """测试配置加载"""
        from config.settings import settings
        
        assert settings.APP_NAME == "Customer Service AI"
        assert settings.APP_VERSION == "1.0.0"
    
    def test_settings_validation(self):
        """测试配置验证"""
        from config.settings import Settings
        
        # 测试有效的APP_ENV
        settings = Settings(APP_ENV="development")
        assert settings.APP_ENV == "development"
    
    def test_settings_log_level(self):
        """测试日志级别配置"""
        from config.settings import Settings
        
        settings = Settings(LOG_LEVEL="INFO")
        assert settings.LOG_LEVEL == "INFO"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
