SHELL := /bin/bash
.DEFAULT_GOAL := help
UV := uv

.PHONY: help doctor test-unit test-integration test-e2e build up down smoke seed-demo sync-check eval train backup restore-check clean

help: ## 列出可用命令
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

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

up: ## [M5] 启动开发/演示环境
	@echo "NOT_IMPLEMENTED: up 在 M5 实现" >&2; exit 2

down: ## [M5] 停止开发/演示环境
	@echo "NOT_IMPLEMENTED: down 在 M5 实现" >&2; exit 2

smoke: ## [M1] 存活/就绪/正常与异常路径冒烟
	@echo "NOT_IMPLEMENTED: smoke 在 M1 实现" >&2; exit 2

seed-demo: ## [M3] 只导入合成演示数据，幂等
	@echo "NOT_IMPLEMENTED: seed-demo 在 M3 实现" >&2; exit 2

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
