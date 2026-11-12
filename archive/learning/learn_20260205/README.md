# AI Agent 学习项目

这是一个个人向的 AI Agent 学习项目，基于 LangGraph 和 LangChain 框架构建，用于学习和实践 AI Agent 的开发。

## 项目概述

本项目是一个基于 LangGraph 的 AI Agent 模板项目，展示了如何创建一个具有工具调用能力的智能助手。Agent 可以通过调用自定义工具函数来完成用户的需求，如数学计算、邮件发送等任务。

## 项目结构

```
learn_20260205/
├── app/                          # 主应用目录
│   ├── src/
│   │   ├── agent/                # Agent 核心代码
│   │   │   ├── my_agent.py      # Agent 主文件，定义工具和创建 Agent
│   │   │   ├── my_llm.py        # LLM 配置，支持多种模型接入
│   │   │   └── env_utils.py     # 环境变量工具模块
│   │   └── test_llm/            # LLM 测试代码
│   │       └── format_output.py # 结构化输出测试示例
│   ├── env_utils.py             # 环境变量工具（项目根目录）
│   ├── pyproject.toml           # 项目配置文件
│   ├── requirements.txt         # Python 依赖列表
│   └── langgraph.json           # LangGraph 配置文件
├── Dockerfile                    # Docker 镜像构建文件
├── docker-compose.yml           # Docker Compose 配置
└── README.md                    # 项目说明文档
```

## 核心功能

### 1. Agent 定义 (`app/src/agent/my_agent.py`)

- **自定义工具函数**：
  - `calculateAdd`: 支持加、减、乘、除四种数学运算
  - `send_email`: 模拟邮件发送功能（示例实现）

- **Agent 创建**：
  - 使用 `langchain.agents.create_agent` 创建 Agent
  - 配置系统提示词，定义 Agent 的角色和行为
  - 集成自定义工具函数

### 2. LLM 配置 (`app/src/agent/my_llm.py`)

支持多种 LLM 接入方式：

- **OpenAI 兼容 API**：通过 `ChatOpenAI` 接入
  - 支持硅基流动 API（DeepSeek 模型）
  - 支持 Qwen 模型（通过兼容 API）
  
- **DeepSeek 原生支持**：通过 `ChatDeepSeek` 接入
  - 支持推理步骤（reasoning steps）的流式输出

当前配置使用 Qwen3-30B-A3B-Thinking-2507 模型。

### 3. 环境变量管理

项目提供两处环境变量工具：

- `app/env_utils.py`: 使用 `python-dotenv` 从 `.env` 文件加载配置
- `app/src/agent/env_utils.py`: 直接从系统环境变量读取

需要配置的环境变量：
- `API_KEY`: API 密钥
- `BASE_URL`: API 基础 URL

### 4. 结构化输出测试 (`app/src/test_llm/format_output.py`)

演示如何使用 Pydantic 模型实现结构化输出：
- 定义 `Movie` 数据模型
- 使用 `with_structured_output` 方法获取结构化响应
- 示例：获取电影《黑客帝国》的详细信息

## 技术栈

### 核心依赖

- **LangGraph** (1.0.7): Agent 工作流编排框架
- **LangChain** (1.2.8): LLM 应用开发框架
- **LangChain OpenAI** (1.1.7): OpenAI 兼容 API 支持
- **LangChain DeepSeek** (1.0.1): DeepSeek 模型原生支持
- **Python-dotenv** (1.2.1): 环境变量管理

### 开发依赖

- **LangGraph CLI** (0.4.12): 本地开发工具
- **Ruff**: 代码格式化和 Lint 工具
- **MyPy**: 类型检查
- **Pytest**: 单元测试框架

## 环境要求

- **Python**: >= 3.11
- **操作系统**: Ubuntu 23.04+（Docker 环境为 Ubuntu 24.04）

## 快速开始

### 1. 安装依赖

```bash
cd learn_20260205/app
pip install -r requirements.txt
```

如果需要开发工具：

```bash
pip install -e ".[dev]"
```

### 2. 配置环境变量

在 `app` 目录下创建 `.env` 文件：

```env
API_KEY=your_api_key_here
BASE_URL=https://api.example.com
```

或直接设置系统环境变量：

```bash
export API_KEY="your_api_key_here"
export BASE_URL="https://api.example.com"
```

### 3. 运行 Agent

使用 LangGraph CLI 启动开发服务器：

```bash
cd learn_20260205/app
langgraph dev
```

或直接运行 Python 代码：

```bash
python -m agent.my_agent
```

## Docker 部署

### 构建镜像

```bash
cd learn_20260205
docker build -t agent-study .
```

### 使用 Docker Compose

```bash
docker-compose up -d
```

这将启动一个 Ubuntu 24.04 容器，Python 3.13 环境已配置完成。

## 项目特点

1. **模块化设计**：代码结构清晰，易于扩展和维护
2. **多模型支持**：支持多种 LLM 接入方式，灵活切换
3. **工具扩展**：可以轻松添加自定义工具函数
4. **结构化输出**：支持使用 Pydantic 模型获取结构化响应
5. **Docker 支持**：提供完整的容器化部署方案

## 使用示例

### Agent 工具调用示例

Agent 可以理解自然语言并调用相应的工具：

- "帮我计算 123 加 456"
- "发送一封邮件给 user@example.com，主题是'测试'，内容是'这是一封测试邮件'"

### 结构化输出示例

```python
from my_llm import llm
from pydantic import BaseModel, Field

class Movie(BaseModel):
    title: str = Field(description="电影标题")
    director: str = Field(description="导演")
    year: int = Field(description="上映年份")
    rating: float = Field(description="评分(0-10)")

model = llm.with_structured_output(Movie, include_raw=True)
response = model.invoke("提供《黑客帝国》的详细信息")
```

## 注意事项

1. **API 密钥安全**：请妥善保管 API 密钥，不要提交到版本控制系统
2. **依赖版本**：建议使用项目指定的依赖版本，避免兼容性问题
3. **Python 版本**：确保使用 Python 3.10 或更高版本
4. **环境变量**：运行前确保正确配置了 `API_KEY` 和 `BASE_URL`

## 学习资源

- [LangGraph 官方文档](https://langchain-ai.github.io/langgraph/)
- [LangChain 官方文档](https://python.langchain.com/)
- [LangGraph CLI 文档](https://langchain-ai.github.io/langgraph/cli/)

## 许可证

MIT License

## 作者

kenneys-bot
