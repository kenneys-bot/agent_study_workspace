"""``GET /system/version`` 的响应模型（契约 §5，M5-01/T22）。

字段是显式白名单：只描述可展示的运行版本信息，绝不包含 API key、数据库
URL、令牌或其它配置。
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class SystemVersionResponse(BaseModel):
    """版本页展示的授权可见运行信息。"""

    code_sha: str = Field(..., description="应用代码 SHA（GIT_SHA 或 git HEAD）")
    git_branch: str = Field(..., description="当前代码分支")
    model_provider: str = Field(..., description="当前模型供应商")
    model_chat: str | None = Field(None, description="当前对话模型标识")
    embedding_model: str = Field(..., description="嵌入模型标识")
    index_version: str | None = Field(None, description="当前活动索引版本；未构建为 null")
    index_calibration_sha256: str | None = Field(
        None, description="活动索引的校准向量 sha256（T29/M7-01 索引身份）"
    )
    index_embedding_model: str | None = Field(
        None, description="活动索引实际使用的 embedding 模型标识"
    )
    mode: str = Field(..., description="运行模式：live / replay")
    app_version: str = Field(..., description="应用版本号")
