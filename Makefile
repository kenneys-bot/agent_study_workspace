SHELL := /bin/bash
.DEFAULT_GOAL := help
UV := uv
COMPOSE := docker compose -f deploy/compose.yml

.PHONY: help doctor test-unit test-integration test-e2e build up down smoke seed-demo seed-demo-accounts index-rebuild index-info sync-check eval train backup restore-check clean

help: ## 列出可用命令
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

doctor: ## 检查本机依赖、配置与端口（不打印任何秘密值）
	@bash scripts/doctor.sh

test-unit: ## 无网络、无真实凭据的单元测试（不含 integration 标记）
	@$(UV) run pytest tests -q -m "not integration"

sync-check: ## 报告本机与 Ubuntu 的 SHA 及未提交状态，不自动覆盖
	@bash scripts/sync-check.sh

build: ## 锁定依赖安装（M5 起追加前端构建）
	@$(UV) sync --locked
	@echo "build: 后端依赖已按 uv.lock 安装（前端构建在 M5 加入）"

# ---- 以下命令在对应里程碑实现，未实现时必须非零退出，不能假装成功 ----
test-integration: ## [M2] PostgreSQL 空库迁移与数据库连通性集成测试
	@bash scripts/test-integration.sh

test-e2e: ## [M5] 隔离演示环境端到端测试
	@echo "NOT_IMPLEMENTED: test-e2e 在 M5 实现" >&2; exit 2

up: ## 构建并启动 API 容器（需要 .env 或环境变量提供必填配置）
	@$(COMPOSE) up -d --build api
	@echo "up: API 容器已启动 → http://127.0.0.1:$${API_PORT:-8000}/health/live"

down: ## 停止并移除 API 容器
	@$(COMPOSE) down --remove-orphans
	@echo "down: 已停止"

smoke: ## [M1] 真实启动容器，断言存活、就绪与错误路径
	@bash scripts/smoke.sh

seed-demo: ## [M3] 只导入合成演示数据，幂等
	@$(UV) run python scripts/seed_demo_accounts.py
	@$(UV) run python scripts/seed_demo.py

seed-demo-accounts: ## [M3] 显式创建演示作者/审核人账号（不导入知识）
	@$(UV) run python scripts/seed_demo_accounts.py

index-rebuild: ## [M3] 重建版本化索引，校验后原子切换活动指针
	@$(UV) run python scripts/index_rebuild.py

index-info: ## [M3] 打印当前活动索引版本与清单摘要
	@$(UV) run python scripts/index_info.py

eval: ## [M6] 固定评测（Ubuntu）
	@echo "NOT_IMPLEMENTED: eval 在 M6 实现；需要 EVAL_CONFIG=path" >&2; exit 2

train: ## [M6] 条件任务：4-bit QLoRA 微调（Ubuntu）
	@echo "NOT_IMPLEMENTED: train 为 M6 条件任务；需先冻结测试集" >&2; exit 2

backup: ## [M7] 备份数据库与产物
	@echo "NOT_IMPLEMENTED: backup 在 M7 实现" >&2; exit 2

restore-check: ## [M7] 在独立目标恢复验证，不覆盖生产
	@echo "NOT_IMPLEMENTED: restore-check 在 M7 实现" >&2; exit 2

clean: ## 清理解释器缓存与测试缓存（不动数据与归档）
	@find . -name '__pycache__' -type d -not -path './archive/*' -prune -exec rm -rf {} + 2>/dev/null || true
	@rm -rf .pytest_cache .ruff_cache
	@echo "clean: 已清理缓存"
