# 数据库版本、索引与升级

当前使用 SQLite：运行与消息已拆为独立实体表，复杂负载仍保留 JSON，其余业务对象暂存在 records。R9 建立迁移体系，R10 增加网页分页，R11 完成 v3 实体迁移。这是分步结构化，不是完整关系范式重构。

## 当前表结构

| 表 | 核心字段与用途 |
| --- | --- |
| users | 账户、密码哈希与家庭归属 |
| sessions / invitations | 登录会话 / 家庭邀请 |
| records | id、kind、data；会话、任务、计划、记忆、角色与通知等剩余业务对象 |
| runs | seq、唯一 id、data；生成列 conversation_id、status、next_attempt_at、lease_until |
| messages | seq、唯一 id、data；生成列 conversation_id、可空 run_id |
| events | 自增事件序号、run_id、type、data、created_at |
| schema_migrations | version、name、checksum、applied_at |

runs/messages 的 seq 为显式 INTEGER PRIMARY KEY AUTOINCREMENT，顺序不会因正文更新改变。核心查询字段采用 STORED 生成列：写入 JSON 时由数据库计算、参与索引，业务代码无需再更新第二份字段。复杂结果、工具检查点、权限快照仍保留在 JSON 中。采用该功能及其版本要求依据 [SQLite 生成列说明](https://www.sqlite.org/gencol.html)；启动要求 SQLite 3.31+ 与 JSON 支持。

约束校验 JSON 对象、负载 ID 与行 ID 一致、非空文本 conversation_id、运行状态枚举，以及租约/重试时间为数值或缺省/null。消息 run_id 为文本或缺省/null；旧人类消息不强制补值。跨 records/runs/messages 的 ID 通过触发器防重，ID、会话归属与 seq 不可更改；records 中其他实体的 kind 同样不可换类。往旧 records 写 run/message 会明确失败。

会话尚在 records，当前没有 conversation/run/owner 外键或级联删除，也不校验所有 JSON 业务字段。孤立引用、JSON 状态机合法跳转等仍需应用检查，不能称数据库已涵盖完整业务一致性。

## 版本与切换

迁移位于 services/api/app/migrations.py；已发布的 v1/v2 不修改，仅追加 v3。

1. v1：接纳旧五表结构。
2. v2：增加旧 JSON 表达式/部分索引。
3. v3：新建 runs/messages 与索引，按旧 rowid 写入新 seq，原样复制 id/data；核对总条数、每行 seq/id 和 JSON 原文字节一致后，才删除 records 的两类旧记录及已替代索引，并建立写入保护。不双写或保留可误读的旧正文副本。

迁移由 BEGIN IMMEDIATE 串行执行，DDL、数据搬迁、旧记录删除和版本记账一起提交；任何异常整体回滚。测试注入删除后的故障与搬迁正文漂移，均确认旧行和 v2 索引恢复，v3 不记账。旧记录不满足新约束时停止升级并保留原库，不猜测或静默修补数据。

启动先启用 WAL，切换锁冲突有 15 秒重试期限；连接锁等待还有独立超时，总启动时间不保证正好 15 秒。未知未来版本、版本缺口或历史校验和不一致拒绝启动。迁移校验和不是全库结构/内容完整性校验，手工改坏结构仍需另外排查。没有自动降级。

## 查询与事务

| 查询 | 当前实现 |
| --- | --- |
| 消息/运行会话范围 | messages_conversation / runs_conversation：conversation_id、seq |
| 分页游标 | 消息 ID 定位 seq，查询同一会话的 seq 范围，先过滤再 LIMIT |
| 最近十条模型历史 | 排除本轮专家消息后取最近十条，再还原正序；缺省 run_id 的人类消息保留 |
| Worker 领取 | runs_ready：仅 queued/running 的 seq 部分索引；按生成列检查重试时间与租约 |
| 私密记忆 | records_private_memory：owner_id 与缺省 private 的 scope |
| 运行事件 | events_run：run_id、事件序号 |

Store 的 get/all/put 接口统一路由到对应实体表；调用方不能自行向 records 写这两类实体。详情、来源复核、摘要和 Harness 均已使用新布局，公开 API 结构保持兼容。表名来自固定映射，动态值仍用参数化 SQL。

候选领取与代次写入继续在同一写事务；任务、运行、消息与事件在同一 SQLite 连接上提交，拆表没有拆散原业务事务。只读工具仍在事务外等待。内部会话查询方法不提供 ACL，API 与 Harness 必须先授权。

R11 的两 Worker 小型并发测试验证八条待执行记录各领取一次；R12 另加入独立子进程续租、强制结束与接管验证，见 [Worker 租约说明](09-worker-leases.md)。这些均不是压力测试、外部 exactly-once 或生产吞吐保证。大量活跃/延迟重试任务仍需过滤。工作台其他列表尚全量返回，详见 [数据加载说明](08-data-loading.md)。

## 升级操作

1. 停止共享数据库的 API 与全部 Worker；不支持混合版本滚动升级。旧代码读取旧表可能看不到运行，写入将被拒绝，不能继续使用。
2. 用 SQLite backup API 或经验证的离线备份留存可恢复副本；有 WAL 时不要只复制主文件。升级需要足够空间临时容纳新表与索引，删除旧行不保证文件立即缩小。
3. 新版本启动自动迁移。失败时检查旧数据/环境，保留备份，不删除迁移历史强行启动。
4. 核对 schema_migrations、health 指纹及合成流程。迁移成功后回退应用须恢复升级前的完整备份，而不是只回退代码。

R9 曾暴露 pytest 导入默认建库的问题，已备份并仅撤销草稿迁移元数据/索引，保留业务表；tests/conftest.py 现在在测试收集前指定临时库、demo 与禁用 Worker。本轮继续只升级临时测试/预览数据库，不打开默认业务库。暂无自动备份/恢复管理界面或大型数据库迁移耗时测试。
