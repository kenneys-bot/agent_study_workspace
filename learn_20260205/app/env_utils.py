from __future__ import annotations

import os

"""
简单的环境变量工具模块。

在 `src/agent/my_llm.py` 中会从这里导入 `API_KEY` 和 `BASE_URL`：

    from env_utils import API_KEY, BASE_URL

你可以在运行前设置环境变量，或者直接修改下面的默认值：

    export API_KEY="your_api_key"
    export BASE_URL="https://api.xxx.com"
"""

API_KEY: str = os.getenv("API_KEY", "")
BASE_URL: str = os.getenv("BASE_URL", "")


