# Docker配置说明

## Dockerfile

```dockerfile
# 使用Python 3.9作为基础镜像
FROM python:3.9-slim

# 设置工作目录
WORKDIR /app

# 设置环境变量
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# 安装系统依赖
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
        gcc \
    && rm -rf /var/lib/apt/lists/*

# 复制依赖文件
COPY requirements.txt .

# 安装Python依赖
RUN pip install --no-cache-dir -r requirements.txt

# 复制应用代码
COPY app/ ./app/

# 创建非root用户
RUN adduser --disabled-password --gecos '' appuser \
    && chown -R appuser:appuser /app
USER appuser

# 暴露端口
EXPOSE 8000

# 启动命令
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

## docker-compose.yml

```yaml
version: '3.8'

services:
  # 应用服务
  customer-service-ai:
    build: .
    ports:
      - "8000:8000"
    environment:
      - DASHSCOPE_API_KEY=${DASHSCOPE_API_KEY}
      - CHROMA_HOST=chroma
      - CHROMA_PORT=8000
      - DATABASE_URL=postgresql://postgres:password@postgres:5432/customer_service
      - REDIS_HOST=redis
      - REDIS_PORT=6379
    depends_on:
      - chroma
      - postgres
      - redis
    volumes:
      - ./app:/app/app
    env_file:
      - .env

  # Chroma向量数据库
  chroma:
    image: chromadb/chroma:0.4.0
    ports:
      - "8001:8000"
    environment:
      - IS_PERSISTENT=TRUE
    volumes:
      - chroma_data:/chroma/chroma_data

  # PostgreSQL数据库
  postgres:
    image: postgres:15
    ports:
      - "5432:5432"
    environment:
      - POSTGRES_DB=customer_service
      - POSTGRES_USER=postgres
      - POSTGRES_PASSWORD=password
    volumes:
      - postgres_data:/var/lib/postgresql/data
      - ./init.sql:/docker-entrypoint-initdb.d/init.sql

  # Redis缓存
  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
    volumes:
      - redis_data:/data

  # Redis Commander (Redis管理界面)
  redis-commander:
    image: rediscommander/redis-commander:latest
    ports:
      - "8081:8081"
    environment:
      - REDIS_HOSTS=local:redis:6379
    depends_on:
      - redis

  # PGAdmin (PostgreSQL管理界面)
  pgadmin:
    image: dpage/pgadmin4
    ports:
      - "8082:80"
    environment:
      - PGADMIN_DEFAULT_EMAIL=admin@example.com
      - PGADMIN_DEFAULT_PASSWORD=admin
    depends_on:
      - postgres

volumes:
  chroma_data:
  postgres_data:
  redis_data:
```

## .dockerignore

```dockerignore
# Python相关
__pycache__
*.pyc
*.pyo
*.pyd
.Python
.pytest_cache
.coverage
.gitignore
.env

# IDE相关
.idea/
.vscode/
*.swp
*.swo

# 日志文件
*.log

# 测试相关
tests/
test_*.py

# 文档
docs/

# Docker相关
Dockerfile
docker-compose.yml

# 其他
.DS_Store
```

## 部署说明

### 构建和运行

1. **构建镜像**:
```bash
docker-compose build
```

2. **启动所有服务**:
```bash
docker-compose up -d
```

3. **查看服务状态**:
```bash
docker-compose ps
```

4. **查看日志**:
```bash
docker-compose logs -f customer-service-ai
```

### 环境变量配置

创建 `.env` 文件:
```env
DASHSCOPE_API_KEY=your_dashscope_api_key_here
APP_ENV=production
LOG_LEVEL=INFO
```

### 服务访问

- **API服务**: http://localhost:8000
- **Chroma数据库**: http://localhost:8001
- **Redis Commander**: http://localhost:8081
- **PGAdmin**: http://localhost:8082

### 数据持久化

所有服务的数据都通过Docker volumes进行持久化:
- `chroma_data`: Chroma向量数据库数据
- `postgres_data`: PostgreSQL数据库数据
- `redis_data`: Redis缓存数据

### 扩展部署

#### 水平扩展应用服务
```bash
docker-compose up -d --scale customer-service-ai=3
```

#### 生产环境配置
```yaml
# docker-compose.prod.yml
version: '3.8'

services:
  customer-service-ai:
    # 生产环境特定配置
    restart: always
    environment:
      - APP_ENV=production
      - LOG_LEVEL=WARNING
    deploy:
      replicas: 3
      resources:
        limits:
          memory: 1G
          cpus: '0.5'
```

### 监控和日志

#### 日志收集
```yaml
# 添加到docker-compose.yml
logging:
  driver: "json-file"
  options:
    max-size: "10m"
    max-file: "3"
```

#### 健康检查
```yaml
# 为应用服务添加健康检查
healthcheck:
  test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
  interval: 30s
  timeout: 10s
  retries: 3
```

### 故障排除

#### 常见问题

1. **端口冲突**:
   - 修改docker-compose.yml中的端口映射

2. **内存不足**:
   - 调整Docker资源限制
   - 优化应用内存使用

3. **依赖服务连接失败**:
   - 检查网络配置
   - 确认服务启动顺序

#### 重置环境
```bash
# 停止并删除所有容器
docker-compose down

# 删除数据卷(谨慎操作)
docker-compose down -v

# 重新构建并启动
docker-compose up -d --build
```

### 安全考虑

1. **API密钥管理**:
   - 使用Docker secrets或外部密钥管理服务
   - 避免将密钥硬编码在配置文件中

2. **网络隔离**:
   - 使用自定义网络隔离服务
   - 限制外部访问端口

3. **镜像安全**:
   - 定期更新基础镜像
   - 使用官方镜像
   - 扫描镜像漏洞

### 备份和恢复

#### 数据备份
```bash
# 备份PostgreSQL数据
docker-compose exec postgres pg_dump -U postgres customer_service > backup.sql

# 备份Chroma数据
docker-compose exec chroma tar -czf /tmp/chroma_backup.tar.gz /chroma/chroma_data
docker cp chroma:/tmp/chroma_backup.tar.gz ./chroma_backup.tar.gz
```

#### 数据恢复
```bash
# 恢复PostgreSQL数据
docker-compose exec -T postgres psql -U postgres customer_service < backup.sql

# 恢复Chroma数据
docker cp ./chroma_backup.tar.gz chroma:/tmp/chroma_backup.tar.gz
docker-compose exec chroma tar -xzf /tmp/chroma_backup.tar.gz -C /