"""
报告生成器
生成质检报告和统计分析
"""

import logging
from typing import List, Dict, Any, Optional
from datetime import datetime
from pathlib import Path

from utils.logger import LoggerMixin

logger = logging.getLogger(__name__)


class SummaryReport:
    """
    汇总报告数据模型
    """
    
    def __init__(
        self,
        report_period: str = None,
        total_sessions: int = 0,
        avg_score: float = 0.0,
        score_distribution: Dict[str, int] = None,
        top_issues: List[Dict[str, Any]] = None,
        recommendations: List[str] = None
    ):
        self.report_period = report_period                  # 报告周期
        self.total_sessions = total_sessions                 # 总会话数
        self.avg_score = avg_score                          # 平均分
        self.score_distribution = score_distribution or {}   # 分数分布
        self.top_issues = top_issues or []                   # 主要问题
        self.recommendations = recommendations or []         # 建议
        self.generated_at = datetime.now()                   # 生成时间
    
    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "report_period": self.report_period,
            "total_sessions": self.total_sessions,
            "avg_score": self.avg_score,
            "score_distribution": self.score_distribution,
            "top_issues": self.top_issues,
            "recommendations": self.recommendations,
            "generated_at": self.generated_at.isoformat()
        }


class ReportGenerator(LoggerMixin):
    """
    报告生成器
    生成质检报告和统计分析
    """
    
    def __init__(self, output_dir: str = "./reports"):
        """
        初始化报告生成器
        
        Args:
            output_dir (str): 报告输出目录
        """
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.logger.info(f"报告生成器初始化完成，输出目录: {output_dir}")
    
    def generate_detailed_report(
        self,
        inspection_result: "InspectionReport"
    ) -> str:
        """
        生成详细质检报告
        
        Args:
            inspection_result (InspectionReport): 质检结果
            
        Returns:
            str: 详细报告内容
        """
        try:
            # 生成报告内容
            report_content = self._build_detailed_report(inspection_result)
            
            # 保存报告
            report_path = self.output_dir / f"report_{inspection_result.session_id}.txt"
            self._save_report(report_path, report_content)
            
            return report_content
            
        except Exception as e:
            self.logger.error(f"详细报告生成失败: {str(e)}")
            return str(inspection_result.to_dict())
    
    def generate_summary_report(
        self,
        results: List["InspectionReport"]
    ) -> SummaryReport:
        """
        生成汇总报告
        
        Args:
            results (List[InspectionReport]): 质检结果列表
            
        Returns:
            SummaryReport: 汇总报告
        """
        try:
            if not results:
                return SummaryReport()
            
            # 计算统计数据
            total_sessions = len(results)
            avg_score = sum(r.overall_score for r in results) / total_sessions
            
            # 分数分布
            score_distribution = {
                "优秀(90-100)": 0,
                "良好(80-89)": 0,
                "合格(60-79)": 0,
                "不合格(<60)": 0
            }
            
            for r in results:
                if r.overall_score >= 90:
                    score_distribution["优秀(90-100)"] += 1
                elif r.overall_score >= 80:
                    score_distribution["良好(80-89)"] += 1
                elif r.overall_score >= 60:
                    score_distribution["合格(60-79)"] += 1
                else:
                    score_distribution["不合格(<60)"] += 1
            
            # 主要问题统计
            issue_stats = {}
            for r in results:
                for issue in r.issues:
                    issue_type = issue.issue_type
                    if issue_type not in issue_stats:
                        issue_stats[issue_type] = 0
                    issue_stats[issue_type] += 1
            
            top_issues = [
                {"type": k, "count": v}
                for k, v in sorted(issue_stats.items(), key=lambda x: -x[1])[:5]
            ]
            
            # 生成建议
            recommendations = self._generate_recommendations(results)
            
            report = SummaryReport(
                report_period="最近7天",
                total_sessions=total_sessions,
                avg_score=avg_score,
                score_distribution=score_distribution,
                top_issues=top_issues,
                recommendations=recommendations
            )
            
            # 保存汇总报告
            summary_path = self.output_dir / "summary_report.txt"
            summary_content = self._build_summary_report(report)
            self._save_report(summary_path, summary_content)
            
            return report
            
        except Exception as e:
            self.logger.error(f"汇总报告生成失败: {str(e)}")
            return SummaryReport()
    
    def export_report(
        self,
        report: "InspectionReport",
        format_type: str = "json"
    ) -> bytes:
        """
        导出报告
        
        Args:
            report (InspectionReport): 质检报告
            format_type (str): 导出格式
            
        Returns:
            bytes: 导出的文件数据
        """
        try:
            if format_type == "json":
                import json
                return json.dumps(report.to_dict(), ensure_ascii=False, indent=2).encode()
            
            elif format_type == "html":
                return report.to_html().encode()
            
            elif format_type == "text":
                return self.generate_detailed_report(report).encode()
            
            else:
                raise ValueError(f"不支持的格式: {format_type}")
                
        except Exception as e:
            self.logger.error(f"报告导出失败: {str(e)}")
            return b""
    
    def _build_detailed_report(
        self,
        inspection_result: "InspectionReport"
    ) -> str:
        """构建详细报告"""
        report = f"""
================================================================================
                          客服对话质检报告
================================================================================

会话ID: {inspection_result.session_id}
生成时间: {inspection_result.generated_at.strftime('%Y-%m-%d %H:%M:%S')}

--------------------------------------------------------------------------------
                              总体评分
--------------------------------------------------------------------------------
总体评分: {inspection_result.overall_score:.1f}分
服务态度: {inspection_result.attitude_score:.1f}分
专业性: {inspection_result.professionalism_score:.1f}分
合规性: {inspection_result.compliance_score:.1f}分

--------------------------------------------------------------------------------
                              发现问题
--------------------------------------------------------------------------------
"""
        for i, issue in enumerate(inspection_result.issues, 1):
            report += f"""
问题{i}: [{issue.severity}] {issue.issue_type}
描述: {issue.description}
位置: {issue.location or "未指定"}
建议: {issue.suggestion or "无"}
"""
        
        report += f"""
--------------------------------------------------------------------------------
                                总结
--------------------------------------------------------------------------------
{inspection_result.summary or "无"}

================================================================================
                              报告结束
================================================================================
        """
        return report
    
    def _build_summary_report(self, summary: SummaryReport) -> str:
        """构建汇总报告"""
        report = f"""
================================================================================
                          质检汇总报告
================================================================================

报告周期: {summary.report_period}
生成时间: {summary.generated_at.strftime('%Y-%m-%d %H:%M:%S')}

--------------------------------------------------------------------------------
                              统计概览
--------------------------------------------------------------------------------
总会话数: {summary.total_sessions}
平均分: {summary.avg_score:.1f}分

分数分布:
"""
        for category, count in summary.score_distribution.items():
            percentage = (count / summary.total_sessions * 100) if summary.total_sessions > 0 else 0
            report += f"  {category}: {count} ({percentage:.1f}%)\n"
        
        report += """
--------------------------------------------------------------------------------
                              主要问题
--------------------------------------------------------------------------------
"""
        for i, issue in enumerate(summary.top_issues, 1):
            report += f"{i}. {issue['type']}: {issue['count']}次\n"
        
        report += """
--------------------------------------------------------------------------------
                              改进建议
--------------------------------------------------------------------------------
"""
        for i, rec in enumerate(summary.recommendations, 1):
            report += f"{i}. {rec}\n"
        
        report += """
================================================================================
                            报告结束
================================================================================
        """
        return report
    
    def _save_report(self, path: Path, content: str):
        """保存报告"""
        with open(path, 'w', encoding='utf-8') as f:
            f.write(content)
        self.logger.info(f"报告已保存: {path}")
    
    def _generate_recommendations(
        self,
        results: List["InspectionReport"]
    ) -> List[str]:
        """生成改进建议"""
        recommendations = []
        
        avg_attitude = sum(r.attitude_score for r in results) / len(results)
        avg_professional = sum(r.professionalism_score for r in results) / len(results)
        avg_compliance = sum(r.compliance_score for r in results) / len(results)
        
        if avg_attitude < 80:
            recommendations.append("加强客服人员服务态度培训，提高客户满意度")
        if avg_professional < 80:
            recommendations.append("加强产品知识培训，提高客服专业水平")
        if avg_compliance < 80:
            recommendations.append("强化合规意识培训，确保服务符合规范")
        
        if not recommendations:
            recommendations.append("继续保持良好的服务质量")
            recommendations.append("定期进行质检，及时发现和解决问题")
        
        return recommendations


class ReviewWorkflow(LoggerMixin):
    """
    复核工作流
    管理质检结果的复核流程
    """
    
    def __init__(self):
        """初始化复核工作流"""
        self.pending_reviews = {}
        self.completed_reviews = {}
        self.logger.info("复核工作流初始化完成")
    
    def submit_for_review(
        self,
        report: "InspectionReport",
        reviewer: str
    ) -> bool:
        """
        提交复核
        
        Args:
            report (InspectionReport): 质检报告
            reviewer (str): 复核人
            
        Returns:
            bool: 提交是否成功
        """
        try:
            review_id = f"review_{report.session_id}_{datetime.now().strftime('%Y%m%d%H%M%S')}"
            
            self.pending_reviews[review_id] = {
                "report": report,
                "reviewer": reviewer,
                "submitted_at": datetime.now(),
                "status": "pending"
            }
            
            self.logger.info(f"复核已提交: {review_id}")
            return True
            
        except Exception as e:
            self.logger.error(f"复核提交失败: {str(e)}")
            return False
    
    def approve_report(
        self,
        review_id: str,
        approver: str,
        comments: str = None
    ) -> bool:
        """
        批准报告
        
        Args:
            review_id (str): 复核ID
            approver (str): 批准人
            comments (str): 批准意见
            
        Returns:
            bool: 批准是否成功
        """
        try:
            if review_id not in self.pending_reviews:
                raise ValueError(f"复核ID不存在: {review_id}")
            
            review = self.pending_reviews[review_id]
            review["status"] = "approved"
            review["approved_by"] = approver
            review["approved_at"] = datetime.now()
            review["comments"] = comments
            
            self.completed_reviews[review_id] = review
            del self.pending_reviews[review_id]
            
            self.logger.info(f"报告已批准: {review_id}")
            return True
            
        except Exception as e:
            self.logger.error(f"报告批准失败: {str(e)}")
            return False
    
    def reject_report(
        self,
        review_id: str,
        rejector: str,
        reasons: List[str]
    ) -> bool:
        """
        拒绝报告
        
        Args:
            review_id (str): 复核ID
            rejector (str): 拒绝人
            reasons (List[str]): 拒绝原因
            
        Returns:
            bool: 拒绝是否成功
        """
        try:
            if review_id not in self.pending_reviews:
                raise ValueError(f"复核ID不存在: {review_id}")
            
            review = self.pending_reviews[review_id]
            review["status"] = "rejected"
            review["rejected_by"] = rejector
            review["rejected_at"] = datetime.now()
            review["reasons"] = reasons
            
            self.completed_reviews[review_id] = review
            del self.pending_reviews[review_id]
            
            self.logger.info(f"报告已拒绝: {review_id}")
            return True
            
        except Exception as e:
            self.logger.error(f"报告拒绝失败: {str(e)}")
            return False
    
    def get_pending_reviews(self) -> List[Dict[str, Any]]:
        """获取待复核列表"""
        return [
            {
                "review_id": k,
                "session_id": v["report"].session_id,
                "score": v["report"].overall_score,
                "reviewer": v["reviewer"],
                "submitted_at": v["submitted_at"].isoformat()
            }
            for k, v in self.pending_reviews.items()
        ]
