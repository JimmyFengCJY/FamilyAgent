# Docker / Compose 本地部署

R14 提供镜像、多服务启动、隔离持久卷及独立冒烟脚本。**本轮本机尚未成功构建镜像**：Docker Hub 匿名认证请求 TLS 超时，宿主机仓库/公共缓存连通性探测也超时，且没有基础镜像缓存。以下为已完成的部署配置与操作方式，不代表当前机器已运行容器；验证范围见文末。

## 默认 demo 部署

需要运行中的 Docker Engine / Docker Desktop，推荐 Compose 2.30+；启用模型覆盖文件必须满足该版本。

```bash
# 在项目根目录运行；默认无需任何模型密钥
# --env-file /dev/null 避免加载本机 .env，默认端口 8080
docker compose --env-file /dev/null up --build -d --wait

# 查看服务状态；构建和启动成功后访问 http://127.0.0.1:8080/
docker compose --env-file /dev/null ps

# 停止服务，保留命名卷
docker compose --env-file /dev/null down
```

Windows 可使用一个空的临时 env 文件替代 /dev/null；本轮没有验证 Windows。修改端口可在命令前设置 FAMILYAGENT_DOCKER_PORT，例如 8088；默认不占用已有 8000/8011 服务。

网页通过注册创建本地账号；默认模型是确定性 demo，不产生真实模型调用。体验账号入口关闭，因为容器网络中的请求来源不同于原有回环地址条件；没有为了 Docker 放宽 /api/auth/demo 的来源校验。

## 服务与存储

| 部分 | 行为 |
| --- | --- |
| 多阶段镜像 | Node 22 构建 TS/Vite，Python 3.12 Bookworm runtime 安装 requirements.lock，复制后端与 dist；最终阶段不复制 Node、node_modules、实验与本机虚拟环境 |
| api | 提供静态网页和 HTTP API，容器内监听 8000，宿主机仅绑定 127.0.0.1；关闭嵌入 Worker |
| worker | 同一镜像执行原独立 Worker，等待 api 健康后启动，不发布端口 |
| /data | 两服务共享项目命名卷 familyagent-data，数据库固定 /data/familyagent.sqlite3；不绑定主机 data/ 或 .env 中的 DB 路径 |
| 执行身份 | UID/GID 10001，数据目录预先赋权；只读根文件系统，/tmp 为临时可写挂载，移除 capabilities，禁止提权 |
| 停止 | init 处理子进程；入口通过 exec 交接信号，75 秒停止宽限；崩溃仍按既有租约接管，不能保证立即取消底层请求 |

API 启动时完成原子迁移与角色种子初始化；worker 的 Store 同样验证数据库版本。共享卷只适合同一 Docker 主机的 SQLite 布局，不是跨主机数据库或标准 A2A 服务。升级应停止共享库的全部旧进程，备份数据后统一镜像版本。

新建命名卷预期沿用镜像 /data 的非 root 所有权；实际挂载权限、只读 root 与 MCP 子进程组合尚待真实容器运行确认。已有卷的权限不会因重建镜像自动修复。`down` 保留卷，`down --volumes` 删除卷；后者仅在 CI 的独立合成项目中自动执行。

## 配置、健康与构建上下文

默认 Compose 对 provider/demo_login/embedded_worker/DB 使用固定值，不因终端或本机 .env 中的模型变量而启用付费模型。允许列表式 .dockerignore 排除个人 .env、数据库、notebook、Git、报告及本地依赖；文件不作为 build args 传入，模型密钥不烘焙进镜像。

容器入口只接受 api/worker 两种角色，在 exec 前验证实际 ModelGateway 配置，打印非敏感 provider/model。它不会读取本机 .env 或发起生成请求。

API 健康探针直连容器内回环 HTTP，核对服务、模型配置快照和当前代码/网页 runtime_revision，不只判断首页 200；不调用模型。Compose 使用 service_healthy 控制依赖启动，依据 [Docker 官方启动顺序说明](https://docs.docker.com/compose/how-tos/startup-order/)。Worker 禁用继承的 HTTP 健康探针，不能把 API 健康等同于 Worker 已消费任务，需实际冒烟验证。

基础镜像使用版本标签而非 digest，因此不承诺不同日期构建得到相同字节；Python/npm 依赖按锁文件安装。没有测试多架构或测量最终镜像大小，首次构建需要镜像仓库及依赖源网络。多阶段与上下文机制参考 [官方多阶段构建](https://docs.docker.com/build/building/multi-stage/)和[构建上下文说明](https://docs.docker.com/build/concepts/context/)。

## 可选真实模型配置（本轮不执行）

1. 复制 deploy/model.env.example 到 deploy/model.env，填写一个供应商及该账号实际可用的模型/密钥；文件已加入 gitignore。
2. 不给值加引号、不在值后追加注释，保持 FAMILYAGENT_MODEL 为空或明确填写通用覆盖模型。
3. 使用显式覆盖文件：

```bash
docker compose --env-file /dev/null -f compose.yaml -f compose.model.yaml config --quiet
docker compose --env-file /dev/null -f compose.yaml -f compose.model.yaml up --build -d --wait
```

覆盖用 !override 替换默认环境映射，raw env_file 读取 deploy/model.env，API 与 Worker 使用同一文件；旧 shell 通用模型变量不会自动注入容器。数据库路径、demo_login、embedded_worker 仍强制固定。raw/替换语义依据 [env_file 格式](https://docs.docker.com/reference/compose-file/services/#env_file)与 [Compose 合并规则](https://docs.docker.com/reference/compose-file/merge/#replace-value)，要求 Compose 2.30+。

不要把 `config --quiet` 通过当作模型可用：它只验证解析。健康检查只验证运行时快照。真正生成仍需要另行授权、最小合成调用与业务验证。本轮仅用合成文件验证覆盖规则，没有使用真实密钥或生成接口。此 env_file 方式面向本地部署，未接入 secrets manager。

## 冒烟与持久化验收

只在独立 demo 测试项目运行，不用于真实模型部署：

```bash
FAMILYAGENT_DOCKER_PORT=8088 docker compose --env-file /dev/null -p familyagent-smoke up --build -d --wait
python3 scripts/container_smoke.py --base-url http://127.0.0.1:8088 --state test-results/container/state.json
FAMILYAGENT_DOCKER_PORT=8088 docker compose --env-file /dev/null -p familyagent-smoke restart
FAMILYAGENT_DOCKER_PORT=8088 docker compose --env-file /dev/null -p familyagent-smoke up -d --wait
python3 scripts/container_smoke.py --base-url http://127.0.0.1:8088 --state test-results/container/state.json --verify
```

脚本仅接受回环地址、先拒绝非 demo 健康状态，创建合成账号并校验静态 JS、独立 Worker 完成交流、MCP stdio 和任务写入；第二阶段登录原账号验证两条消息、运行、任务与指纹仍在。不保存 Cookie 或真实模型密钥，不覆盖旧 smoke state，重新运行时使用新的 state 文件/项目。

CI 新增 containers 作业，配置上述构建、冒烟、重启验证、合成日志产物及自身项目卷清理；工作流尚未推送，不能称 CI 已完成容器验收。

## 本轮证据

- Compose v2.32.4 静态解析通过；默认配置忽略宿主机供应商/DB 路径，模型覆盖用合成文件验证，两服务得到相同有效模型配置；config 展示中的美元符会为往返序列化转义，不把这种展示直接当作真实容器环境证据。
- 入口角色/非法配置、健康指纹与配置匹配测试通过。后端完整 211 项通过；缺少 Compose CLI 的环境会跳过其中两项解析测试，真正容器 CI 不以跳过代替运行。
- 用临时 SQLite、临时端口、独立 API/Worker 原生进程执行 smoke 及重启，网页资源、注册、任务消费、MCP、数据保留均通过。它验证脚本和应用拓扑，**不证明镜像、容器用户/挂载/信号/网络已生效**。
- 本机 Docker Engine 已启动，但构建在读取 node/python 基础镜像元数据时因匿名令牌 TLS 超时失败，未进入依赖安装。宿主机对应域名与公共缓存探测也超时，没有修改全局代理、替换镜像来源或继续盲重试；具体网络根因未确定。
- 没有成功启动本项目容器，8080 页面尚未运行。没有推送镜像/代码、触发云端 CI、使用付费模型或打开默认业务库。8000/8011 原服务未重启，不宣称已加载本轮容器模块。

下一步先在镜像仓库可达的环境完成实际 build/up/smoke/restart 验收，再确认非 root 卷权限、只读文件系统与 MCP 子进程；随后完善开源演示和贡献材料。

## R15 云端验收更新（2026-10-26）

[运行 40226597155](https://github.com/JimmyFengCJY/FamilyAgent/actions/runs/40226597155) 三作业全部成功，容器作业真实完成 build/up、网页资源/注册/独立 Worker/MCP/任务冒烟、重启与持久化检查，随后清理专用卷。步骤证据见 r15-ci-evidence.json。

运行暴露并修复了流式 YAML tmpfs 逗号解析问题与 Compose --wait 的 Worker 健康检查问题。当前 Worker 使用独立 worker_health 模块检查 /proc 进程参数，不向不存在的 API 端口请求；该探针只代表进程存在，业务可用性由冒烟检查覆盖，不能检测所有死锁。本机 Docker Hub TLS 超时仍未解决，云端成功不代表本机已经启动容器。
