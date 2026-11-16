# 供水漏损分诊实验

决策树与模型选择的真实业务分析小型项目。完整模拟客户、痛点、上下游、算法公式与一天工作剧本见 [BUSINESS.md](BUSINESS.md)。

## 模块与业务流程
数据接入（JSON/CSV、字段验证、批次指纹）→ 参数化分析（异步 Python worker）→ 结果复核（指标、图形、明细、解释、批准）→ 定时分发（本地收件箱、CSV/JSON、SHA256）→ 限时共享包；另有业务总览和审计轨迹。
三个贯穿全栈的实体：Dataset 数据批次、Job 分析任务、Report 分发计划；辅助 User、Audit、Delivery、SharePackage/ShareCode。

### 限时共享包（外部核对）
负责人对已批准试验生成共享包，把**当时的输入记录与计算结果冻结为快照**，仅在包内用稳定代号替换真实管号：

- 代号由包专属随机盐派生（`S-XXXXXX`），同包内同一管段代号恒定；不同包使用不同盐，代号无法跨包串联。服务端保存管段原文→代号的对照表，供负责人内部追溯。
- 包名、说明、摘要、结论与 CSV/JSON 下载内容都经过同一套脱敏：已知管号替换为本包代号，同前缀的其他编号（如猜的 `NC-999`）替换为「样本」，文件内不出现原始编号。
- 领取凭据为 32 字节随机串，只保存 SHA-256 摘要（另以服务端密钥加密保存，便于负责人再次查看）；凭据与包一一绑定，拿 A 包凭据请求 B 包或猜编号一律 404。公共接口不暴露 job_id、dataset_id 等内部标识。
- 制包为后台任务并按租约加锁：中断后可由负责人「接续制包」或由 worker 自动接续，**已生成的代号原样沿用**；同一 `idempotency_key` 的重试返回原共享包，不新增。内容（CSV/JSON/快照/指纹）全部就绪后才变为可领取。
- 终结为条件更新：生成与撤销/过期并发时，撤销与过期优先，迟到的制包成果会被丢弃并清理已写文件，不会重新开放。每次制包写入带独立随机后缀的文件名，竞争失败方只清理自己这一轮的产物。
- 负责人可撤销；过期或撤销后领取与下载返回 410，已写出的共享文件随即删除。内部数据接入、分析、结果复核与内部归档不受影响。
- 页面（限时共享）可生成、查看有效期与剩余时间、复制领取凭据、接续制包、撤销并下载；外部合作方通过 `#/claim` 领取页用公开标识 + 凭据核对状态并下载。

## 快速启动
```bash
cp .env.example .env
docker compose up -d --build --wait
docker compose exec -T backend python -m scripts.seed_demo
```
访问 http://localhost:18208；后端文档 http://localhost:19208/docs；数据库宿主端口 17708。
`SEED_DEMO=true` 自动幂等 seed；不覆盖用户修改。手动 seed 命令可重复执行。

## 演示账号与权限
|账号|密码|真实权限|
|---|---|---|
|admin@demo.local|Demo123!|接入、执行、批准、分发、暂停、读取与下载|
|analyst@demo.local|Demo123!|接入、执行、读取与下载；不能批准或安排分发|
|viewer@demo.local|Demo123!|只读与下载|

共享包管理仅限 admin；外部合作方不需要账号，使用公开标识与领取凭据访问 `/shares/claim/...`。

## 技术与本地开发
Python 3.12 / FastAPI / SQLAlchemy / NumPy / SciPy / scikit-learn；PostgreSQL 16；Redis 7；Nginx；原生 HTML CSS JavaScript ES modules（用户指定）。后端所有校验、持久化和算法在服务端。无前端打包步骤。
后端：安装 backend/requirements.txt，设置 DATABASE_URL、REDIS_URL、JWT_SECRET、ARTIFACT_DIR 后在 backend/ 执行 `python -m migrations.initial`、`python -m scripts.seed_demo`、`uvicorn src.main:app`，另开 `python -m src.worker`。前端使用带 /api 反向代理的 Nginx，直接打开 index.html 不支持 API。

## 目录与工程约定
backend/src/{models,schemas,services,routes,controllers,middlewares,constants,config,utils}；backend/scripts/seed_demo.py；backend/migrations/initial.py。共享包相关：`models/share.py`、`services/shares.py`、`routes/shares.py`、`schemas/share.py`。
frontend/src/{api,stores,types,components/common,hooks,pages,router,utils,constants}。
Role 枚举：admin/analyst/viewer；JobStatus：queued/running/completed/failed。后端 backend/src/constants/enums.py，前端 frontend/src/constants/enums.js；路由权限在 middlewares/auth.py；页面按相同角色控制操作入口。

## 环境变量与 Docker
COMPOSE_PROJECT_NAME=water-triage；FRONTEND_PORT=18208；BACKEND_PORT=19208；DB_PORT=17708；DB_PASSWORD、JWT_SECRET 见 .env.example；SEED_DEMO 默认 true。共享命名产物卷供后端下载与 worker 写入，数据库/Redis 各持久卷。
恰好五服务均有健康检查。Redis 队列加 SQL 排队状态兜底；worker 按 next_run 生成本地收件箱文件。周期 60 秒至 30 天。分发结果为批准分析的固定快照；若需新数据重新计算并建立新计划。当前不连接邮件或企业聊天。
错误响应、请求编号、Redis 每 IP 每分钟 240 次限流、JWT 和数据库操作审计。演示密钥与密码仅供本机模拟；生产部署前配置独立密钥与账号。

## 数据与验收
所有数据为固定随机种子生成的模拟业务数据，不能作为实际生产决策证据。项目独立算法、输入边界及业务局限详见 BUSINESS.md。测试与截图统一存放 ../../test-artifacts/gb-1009/，源码不含日志/截图/测试报告。
清理本项目演示数据：`docker compose down -v --remove-orphans`（会删除本项目命名卷）。

## License
MIT，见 LICENSE。
