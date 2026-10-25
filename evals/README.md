# 基线评测 v1

20 个合成中文案例：家庭会议 6 个、个人规划 6 个、预算分析 4 个、助手 2 个、情绪支持 2 个。案例不包含用户真实数据。

## 离线运行

在项目根目录运行：

```sh
.venv312/bin/python -m evals.run --provider demo
```

默认产生 26 次运行：6 个会议案例各执行单 Agent 与多 Agent，其他 14 个案例各执行一次单 Agent。无错误重试时共 50 次演示模型调用，不发生外部 API 请求。

本工具直接复用应用的 `create_run → Harness.tick → ModelGateway`，每个案例/策略使用独立临时数据库；不读取正式 data/ 中的账号、会话或记忆。评测不会批准草案或发送外部消息。

## 真实模型

先在本机设置所需供应商的密钥与模型 ID。不要在聊天、命令截图或仓库中公开密钥。评测命令不自动加载 .env 文件；可先 set -a、source .env、set +a。网页启动脚本 scripts/start.py 则会自动加载 .env。

```sh
# Claude：提前在本机设置 ANTHROPIC_API_KEY、ANTHROPIC_MODEL
.venv312/bin/python -m evals.run --provider claude --live --case meeting-trip --max-total-calls 8

# DeepSeek：提前在本机设置 DEEPSEEK_API_KEY、DEEPSEEK_MODEL
.venv312/bin/python -m evals.run --provider deepseek --live --case meeting-trip --max-total-calls 8

# Gemini：提前在本机设置 GEMINI_API_KEY、GEMINI_MODEL
.venv312/bin/python -m evals.run --provider gemini --live --case meeting-trip --max-total-calls 8
```

也可用 `--model` 明确指定模型，优先级高于环境变量。环境中的 FAMILYAGENT_MODEL 又高于对应供应商的模型变量，跨供应商比较前建议取消该通用变量。

非演示模式必须明确添加 `--live`，会产生供应商费用。本轮代码验证没有调用真实 API。建议先跑一个会议案例，确认模型支持 JSON 输出且账户可用，再扩大样本。

其他选项：`--repeat 3`、`--strategies single|multi|both`、`--limit 5`、`--max-calls 8`、`--max-total-calls 50`、`--output evals/results/experiment-name`。已有结果目录会拒绝覆盖。达到全局预算后停止，报告标明未运行样本并返回非零退出码。

## 产物

默认放在被 Git 忽略的 `evals/results/`，时间目录按 Asia/Hong_Kong 命名：

| 文件 | 内容 |
| --- | --- |
| manifest.json | 模型配置、输出上限、超时、代码与数据指纹、案例选择和预算 |
| cases.json | 本次案例和人工评审标准快照 |
| calls.jsonl | 每次调用的实际上下文、结构化结果、原始用量与耗时，不保存密钥或思维内容 |
| results.jsonl | 各案例运行状态、检查项、完整发言结果与 Harness 事件 |
| review.md | 每个样本的问题、约束、评分标准和各阶段结果；策略公开，非盲评 |
| scores.csv | 待人工填写的四维评分表 |
| report.md | 执行指标与人工质量评分，未评分明确留空 |

模型回复可能复述输入；只使用合成案例，正式使用记录不应复制进评测文件。自定义输出目录不一定被忽略，发布结果前需另行审查。

## 人工评分

结合每个案例的 constraints、rubric 和原始发言，分别填写：正确性 correctness、约束满足 constraints、可执行性 actionability、隐私与权限边界 privacy。每维度 0–2 分：0=明显不满足，1=部分满足，2=充分满足；无法判断就留空，不强行打分。notes 记录具体依据。填写后：

```sh
.venv312/bin/python -m evals.report evals/results/experiment-name --review evals/results/experiment-name/scores.csv
```

只汇总四项都完整的评分，未评分不记为 0。隐私泄漏应独立作为严重问题分析，不能被其他维度抵消。

## 实验边界

- 单 Agent 会议使用主持角色一次综合分析，并获得相同专家职责清单；多 Agent 使用当前顺序专家工作流。共同输入问题、模型、输出上限保持一致，总计算预算不同，需同时看质量、延迟和 Token。
- multi 保留顺序专家基线；independent 隔离本轮其他 Agent 发言（含消息历史），保留先前轮次讨论。专家仍串行执行，调用数相同不表示 Token 或成本相同。默认 both 保持历史基线，all 同时运行三种策略。
- 暂无 LLM 裁判。工程检查验证合成私密标记没有进入上下文/输出、未批准不创建任务、未伪造成员确认、预算计算一致等；不是全面安全评测。
- 以 succeeded 或 waiting_approval 判断执行完成，不表示建议正确或用户任务已经完成。
- 未锁定供应商 seed/temperature；真实 API 即使相同输入也可能变化，需多次重复，不能声称结果逐字可复现。
- Claude 输入合计包含单独返回的缓存读写 Token；Gemini 输出包含单独返回的思考 Token，同时保留供应商原始 usage。缓存定义和价格因供应商而异，失败请求可能不回报用量，不能把 Token 数直接当作价格。
- 本版没有输入 Token 总预算或自动美元费用估算；调用次数与单次输出上限只是工程限制，不是金额上限。

报告新增按案例与重复轮次配对的会议比较：多 Agent 减单 Agent 的调用、耗时、输入/输出 Token 及完整双侧人工总分差。配对缺失或工程检查失败时不计算差值。先阅读 review.md，再填写 scores.csv；单个案例仅用于试跑，不足以推断整体效果。

## 独立分析离线对照

```sh
.venv312/bin/python -m evals.run --provider demo --strategies all
```

策略参数支持 single / multi / independent / both / all。all 对 20 个案例产生 32 次运行、80 次正常演示调用。报告新增独立减顺序的配对；分歧引用校验只验证出处。演示分歧标为示例，不能作为真实模型分歧识别准确率。当前无自动澄清轮次，成员可追加消息继续讨论。

## 记忆检索独立回归

```sh
.venv312/bin/python -m evals.memory
```

9 个固定手工标注的合成用例，对照“最近六条”与授权词面检索的来源集合完全匹配情况。包含旧相关记录、新无关记录、英文规范化、无匹配、其他用户和禁用场景。不创建模型网关、不读取正式数据库、不调用真实 API。默认结果在忽略目录 evals/results/memory-*；已有输出目录拒绝覆盖。小样本刻意覆盖旧策略缺陷，不代表泛化准确率、答案质量或收益提升。预算与删除后的来源状态由集成测试覆盖。

## 本地工具闭环验证

工具注册与权限由 tests/test_tools.py 覆盖：目录可见性、按状态/数量读取、未知参数/工具、跨角色拒绝、模型不能调用写工具、预算工具、请求次数上限、已保存审批复核与事务故障回滚。模型请求使用离线桩驱动真实 Harness 和注册表，不产生 API 费用；这不是 MCP 互操作验证或真实模型选工具准确率评测。默认演示回复也不保证根据自然语言自动调用待办工具。

## MCP 离线互操作验证

`tests/test_mcp.py` 使用官方 SDK 和真实 stdio 子进程验证固定内置服务；另有合成协议故障进程检查版本、发现、结果验收、超时清理和环境隔离。Harness 的模型输出由离线桩提供，工具服务调用是真实协议流程；不调用付费 API，也不是第三方客户端认证。网页浏览器回归实际点击本地 MCP 检索。协议与范围见 [MCP 实现说明](../docs/05-mcp.md)。

执行层并发验证见 tests/test_execution.py：使用屏障阻塞只读工具，确认其他写入/取消先完成，再释放工具；覆盖同 Worker 换代、检查点恢复、租约/ACL 变化与迟到用量。只读工具可能重做，但结果必须通过代次校验；这些是正确性测试，不是生产吞吐或真实网络延迟基准。设计见 [执行层说明](../docs/06-execution.md)。
