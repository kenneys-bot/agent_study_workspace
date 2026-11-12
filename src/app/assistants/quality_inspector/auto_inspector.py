"""
自动化质检器
基于大模型进行客服对话质量检查
"""

import logging
from typing import List, Dict, Any, Optional
from datetime import datetime

from core.llm_client import LLMClient
from utils.logger import LoggerMixin
from config.constants import PromptTemplates, InspectionDimensions, IssueSeverity

logger = logging.getLogger(__name__)


class QualityIssue:
    """
    质量问题数据模型
    """
    
    def __init__(
        self,
        issue_type: str,
        description: str,
        severity: str = IssueSeverity.MEDIUM,
        location: str = None,
        suggestion: str = None,
        evidence: str = None
    ):
        self.issue_type = issue_type        # 问题类型
        self.description = description      # 问题描述
        self.severity = severity           # 严重程度
        self.location = location           # 问题位置
        self.suggestion = suggestion       # 改进建议
        self.evidence = evidence           # 证据
        self.detected_at = datetime.now()  # 检测时间
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "issue_type": self.issue_type,
            "description": self.description,
            "severity": self.severity,
            "location": self.location,
            "suggestion": self.suggestion,
            "evidence": self.evidence,
            "detected_at": self.detected_at.isoformat()
        }


class InspectionReport:
    """
    质检报告数据模型
    """
    
    def __init__(
        self,
        session_id: str,
        overall_score: float = 0.0,
        attitude_score: float = 0.0,
        professionalism_score: float = 0.0,
        compliance_score: float = 0.0,
        issues: List[QualityIssue] = None,
        summary: str = None
    ):
        self.session_id = session_id                      # 会话ID
        self.overall_score = overall_score               # 总体评分
        self.attitude_score = attitude_score              # 态度评分
        self.professionalism_score = professionalism_score  # 专业性评分
        self.compliance_score = compliance_score          # 合规性评分
        self.issues = issues or []                        # 发现的问题
        self.summary = summary                            # 总结
        self.generated_at = datetime.now()                # 生成时间
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "session_id": self.session_id,
            "overall_score": self.overall_score,
            "attitude_score": self.attitude_score,
            "professionalism_score": self.professionalism_score,
            "compliance_score": self.compliance_score,
            "issues": [issue.to_dict() for issue in self.issues],
            "summary": self.summary,
            "generated_at": self.generated_at.isoformat()
        }
    
    def to_html(self) -> str:
        """转换为HTML格式"""
        issues_html = ""
        for issue in self.issues:
            issues_html += f'''
            <div class="issue {issue.severity}">
                <strong>{issue.issue_type}</strong> ({issue.severity})
                <p>{issue.description}</p>
                <p><em>建议: {issue.suggestion}</em></p>
            </div>
            '''
        
        html = f"""
<!DOCTYPE html>
<html>
<head>
    <title>质检报告 - {self.session_id}</title>
    <style>
        body {{ font-family: Arial, sans-serif; margin: 20px; }}
        h1 {{ color: #333; }}
        .score {{ font-size: 24px; font-weight: bold; }}
        .pass {{ color: green; }}
        .fail {{ color: red; }}
        .issue {{ margin: 10px 0; padding: 10px; border-left: 3px solid #ddd; }}
        .high {{ border-left-color: red; }}
        .medium {{ border-left-color: orange; }}
        .low {{ border-left-color: green; }}
    </style>
</head>
<body>
    <h1>质检报告</h1>
    <p>会话ID: {self.session_id}</p>
    <p>总体评分: <span class="score {'pass' if self.overall_score >= 60 else 'fail'}">{self.overall_score:.1f}分</span></p>
    <h2>各项评分</h2>
    <ul>
        <li>服务态度: {self.attitude_score:.1f}分</li>
        <li>专业性: {self.professionalism_score:.1f}分</li>
        <li>合规性: {self.compliance_score:.1f}分</li>
    </ul>
    <h2>发现问题</h2>
    {issues_html}
    <h2>总结</h2>
    <p>{self.summary}</p>
</body>
</html>
        """
        return html


class AutoInspector(LoggerMixin):
    """
    自动化质检器
    基于大模型进行客服对话质量检查
    """
    
    def __init__(self, llm_client: LLMClient):
        """
        初始化自动化质检器
        
        Args:
            llm_client (LLMClient): 大模型客户端
        """
        self.llm_client = llm_client
        self.logger.info("自动化质检器初始化完成")
    
    def inspect_conversation(
        self,
        conversation: "ParsedConversation"
    ) -> InspectionReport:
        """
        检查对话质量
        
        Args:
            conversation (ParsedConversation): 解析后的对话
            
        Returns:
            InspectionReport: 质检报告
        """
        try:
            # 转换对话为文本
            conversation_text = self._format_conversation(conversation)
            
            # 进行质检
            prompt = PromptTemplates.QUALITY_INSPECT.format(
                conversation=conversation_text
            )
            
            response = self.llm_client.generate_text(prompt)
            
            # 解析结果
            report = self._parse_inspection_response(
                response, conversation.session_id
            )
            
            self.logger.info(f"质检完成: 会话ID={conversation.session_id}, 评分={report.overall_score:.1f}")
            return report
            
        except Exception as e:
            self.logger.error(f"质检失败: {str(e)}")
            return InspectionReport(
                session_id=conversation.session_id if conversation else "unknown",
                summary=f"质检失败: {str(e)}"
            )
    
    def evaluate_service_attitude(
        self,
        agent_responses: List[str]
    ) -> Dict[str, Any]:
        """
        评估服务态度
        
        Args:
            agent_responses (List[str]): 客服回复列表
            
        Returns:
            Dict[str, Any]: 评估结果
        """
        try:
            responses_text = "\n".join(agent_responses)
            
            prompt = f"""
请评估以下客服回复的服务态度：

客服回复：
{responses_text}

请从以下几个方面进行评估：
1. 礼貌程度
2. 热情度
3. 耐心程度
4. 同理心

请按照JSON格式输出：
{{
    "score": 评分（0-100）,
    "strengths": ["优点列表"],
    "weaknesses": ["缺点列表"],
    "suggestions": ["改进建议"]
}}
            """
            
            response = self.llm_client.generate_text(prompt)
            return self._parse_json_response(response)
            
        except Exception as e:
            self.logger.error(f"服务态度评估失败: {str(e)}")
            return {"score": 0, "error": str(e)}
    
    def check_compliance(
        self,
        conversation: "ParsedConversation"
    ) -> Dict[str, Any]:
        """
        合规性检查
        
        Args:
            conversation (ParsedConversation): 解析后的对话
            
        Returns:
            Dict[str, Any]: 合规报告
        """
        try:
            conversation_text = self._format_conversation(conversation)
            
            prompt = PromptTemplates.COMPLIANCE_CHECK.format(
                conversation=conversation_text
            )
            
            response = self.llm_client.generate_text(prompt)
            
            result = self._parse_json_response(response)
            
            # 检查具体违规项
            compliance_issues = []
            if "违规" in response.lower() or "不合规" in response.lower():
                compliance_issues.append("存在潜在的合规性问题")
            
            return {
                "score": result.get("score", 100),
                "issues": compliance_issues,
                "details": result
            }
            
        except Exception as e:
            self.logger.error(f"合规性检查失败: {str(e)}")
            return {"score": 0, "error": str(e)}
    
    def generate_improvement_suggestions(
        self,
        issues: List[QualityIssue]
    ) -> List[str]:
        """
        生成改进建议
        
        Args:
            issues (List[QualityIssue]): 质量问题列表
            
        Returns:
            List[str]: 改进建议列表
        """
        if not issues:
            return ["继续保持良好的服务"]
        
        try:
            issues_text = "\n".join([
                f"- {issue.issue_type}: {issue.description}"
                for issue in issues
            ])
            
            prompt = f"""
请根据以下客服对话中发现的问题，提供具体的改进建议：

发现问题：
{issues_text}

请针对每个问题提供具体的改进建议，并按照JSON数组格式输出：
[
    {{
        "issue": "问题类型",
        "suggestion": "改进建议"
    }}
]
            """
            
            response = self.llm_client.generate_text(prompt)
            results = self._parse_json_array_response(response)
            
            return [
                result.get("suggestion", "")
                for result in results if result.get("suggestion")
            ]
            
        except Exception as e:
            self.logger.error(f"改进建议生成失败: {str(e)}")
            return [issue.suggestion for issue in issues if issue.suggestion]
    
    def batch_inspect(
        self,
        conversations: List["ParsedConversation"]
    ) -> List[InspectionReport]:
        """
        批量质检
        
        Args:
            conversations (List[ParsedConversation]): 对话列表
            
        Returns:
            List[InspectionReport]: 质检报告列表
        """
        reports = []
        for i, conversation in enumerate(conversations):
            self.logger.info(f"质检第 {i+1}/{len(conversations)} 个对话")
            report = self.inspect_conversation(conversation)
            reports.append(report)
        return reports
    
    def _format_conversation(self, conversation: "ParsedConversation") -> str:
        """格式化对话"""
        return "\n".join([
            f"{turn.speaker}: {turn.content}"
            for turn in conversation.turns
        ])
    
    def _parse_inspection_response(
        self,
        response: str,
        session_id: str
    ) -> InspectionReport:
        """解析质检响应"""
        import re
        
        # 提取评分
        overall_score = 0.0
        attitude_score = 0.0
        professionalism_score = 0.0
        compliance_score = 0.0
        
        score_patterns = {
            "overall": r'总体评分[：:\s]*(\d+\.?\d*)',
            "attitude": r'服务态度[：:\s]*(\d+\.?\d*)',
            "professionalism": r'专业性[：:\s]*(\d+\.?\d*)',
            "compliance": r'合规性[：:\s]*(\d+\.?\d*)',
        }
        
        for key, pattern in score_patterns.items():
            match = re.search(pattern, response)
            if match:
                try:
                    score = float(match.group(1))
                    if key == "overall":
                        overall_score = score
                    elif key == "attitude":
                        attitude_score = score
                    elif key == "professionalism":
                        professionalism_score = score
                    elif key == "compliance":
                        compliance_score = score
                except ValueError:
                    pass
        
        # 提取问题
        issues = []
        issue_blocks = re.split(r'\d+\.', response)
        for block in issue_blocks[1:]:
            if "问题" in block or "不足" in block:
                issue_type = "服务问题"
                description = block.strip()[:200]
                severity = IssueSeverity.MEDIUM
                
                if "严重" in block:
                    severity = IssueSeverity.HIGH
                elif "轻微" in block:
                    severity = IssueSeverity.LOW
                
                issues.append(QualityIssue(
                    issue_type=issue_type,
                    description=description,
                    severity=severity
                ))
        
        # 提取总结
        summary = ""
        summary_match = re.search(r'总结[：:\s]*(.+?)$', response, re.MULTILINE)
        if summary_match:
            summary = summary_match.group(1).strip()
        
        return InspectionReport(
            session_id=session_id,
            overall_score=overall_score,
            attitude_score=attitude_score,
            professionalism_score=professionalism_score,
            compliance_score=compliance_score,
            issues=issues,
            summary=summary
        )
    
    def _parse_json_response(self, response: str) -> Dict[str, Any]:
        """解析JSON响应"""
        import re
        import json
        
        json_match = re.search(r'\{[\s\S]*\}', response)
        if json_match:
            try:
                return json.loads(json_match.group())
            except json.JSONDecodeError:
                pass
        return {}
    
    def _parse_json_array_response(self, response: str) -> List[Dict[str, Any]]:
        """解析JSON数组响应"""
        import re
        import json
        
        array_match = re.search(r'\[[\s\S]*\]', response)
        if array_match:
            try:
                return json.loads(array_match.group())
            except json.JSONDecodeError:
                pass
        return []
