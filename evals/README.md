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
- 当前多 Agent 专家可看到前序结果。此处故意保留现状作为基线，下一阶段再对比独立分析与分歧处理。
- 暂无 LLM 裁判。工程检查验证合成私密标记没有进入上下文/输出、未批准不创建任务、未伪造成员确认、预算计算一致等；不是全面安全评测。
- 以 succeeded 或 waiting_approval 判断执行完成，不表示建议正确或用户任务已经完成。
- 未锁定供应商 seed/temperature；真实 API 即使相同输入也可能变化，需多次重复，不能声称结果逐字可复现。
- Claude 输入合计包含单独返回的缓存读写 Token；Gemini 输出包含单独返回的思考 Token，同时保留供应商原始 usage。缓存定义和价格因供应商而异，失败请求可能不回报用量，不能把 Token 数直接当作价格。
- 本版没有输入 Token 总预算或自动美元费用估算；调用次数与单次输出上限只是工程限制，不是金额上限。
