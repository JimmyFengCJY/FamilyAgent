# R15 验收入口

日期：2026-10-26。保留之前各轮记录，本页区分已完成与待验收。

| 项目 | 已完成 | 尚未完成 |
|---|---|---|
| Docker | 云端构建、API/Worker/MCP/任务及重启持久化全部通过 | 本机 Docker Hub TLS 超时 |
| GitHub CI | 已推送 main，三作业全部 success（运行 40226597155） | 最终文档和资产上传后的 CI 待执行 |
| 真实对照 | 40 次授权调用已执行并停止，保留成功与失败报告 | 供应商故障导致配对不完整，人工质量评分未完成 |
| 开源展示 | MIT、贡献/安全指南、架构与面试材料、合成截图与 44 秒录屏随本次交付提交发布 | 真实模型配对与人工评分仍待后续完成 |

最新代码验证运行：[Worker 健康检查修复后的 CI](https://github.com/JimmyFengCJY/FamilyAgent/actions/runs/40226597155)。之前失败的 CI 保留用于回看修复证据，不应删除。

本机运行 `python3 scripts/start.py`；该启动器会读取本机配置并检查服务运行时配置。容器使用 `docker compose --env-file /dev/null up --build -d --wait`，成功后访问 127.0.0.1:8080 注册账号。当前不承诺本机容器已运行。

展示材料：[架构](12-architecture.md)、[演示和面试](13-demo-and-interview.md)、[真实评测](14-evaluation.md)、[录屏](assets/demo.webm)。录屏为独立临时数据库中的确定性 demo，不能当作真实模型效果演示。

上传阻塞处理记录：重新核验后 GitHub 连接器已恢复；素材以 Git blob 上传，并随 README、评测汇总、进度和优化对比一起纳入交付提交。运行时行为未变。本次提交的 CI 状态见 [工作流列表](https://github.com/JimmyFengCJY/FamilyAgent/actions/workflows/ci.yml)，上表固定运行号对应此前已验证代码。
