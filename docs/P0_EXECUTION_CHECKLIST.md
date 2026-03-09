# P0 落地清单（2026-03-09）

本文档用于把“专业网络小说续写工具”优化建议转成两周内可执行的工程清单。

## 0. 目标

1. 提升续写可控性（长度、版本、严格一致性）。
2. 降低设定漂移与生成失败率。
3. 保持 hosted 配额计费语义一致（按成功结果计费）。
4. 不破坏现有接口兼容性（默认行为保持不变）。

## 1. 两周节奏

第 1 周：先完成接口与核心链路改造（P0-1/2/3/4），并补齐单测。  
第 2 周：完成配额一致性与发布治理（P0-5/6），进行灰度与回滚演练。

## 2. 任务卡

### P0-1 续写参数升级（专业模式）

状态：`DONE (2026-03-09)`  
目标：在不破坏旧参数的前提下，支持更细粒度控制。  
改动文件：`app/schemas.py`、`app/config.py`、`app/api/novels.py`、`app/core/generator.py`、`web/src/pages/WritingWorkspace.tsx`、`web/src/types/api.ts`、`web/src/services/api.ts`。  
接口/数据：
- 在 `ContinueRequest` 增加 `length_mode`（`preset|custom`）与 `strict_mode`（默认 `false`）。
- `target_chars` 从固定档位扩展为区间（建议 `800..8000`），保留旧档位兼容。
- `num_versions` 上限从 `2` 提升到 `4`（由配置控制最大值）。
测试：
- 后端：扩展 `tests/test_continue_endpoint.py` 参数校验与 token 预算断言。
- 前端：新增 `WritingWorkspace` 参数交互测试（自定义字数、上限拦截）。
验收：
- 旧请求体可直接使用，无行为回归。
- 新参数可生效并写入 `prompt_used`。
排期：第 1 周 Day 1-2。

### P0-2 Postcheck 严格模式 + 自动修复

状态：`DONE (2026-03-09)`  
目标：把“只告警”升级为“可选阻断并自动二次修复”。  
改动文件：`app/core/continuation_postcheck.py`、`app/api/novels.py`、`app/core/generator.py`、`docs/CONTINUATION_RULES.md`。  
接口/数据：
- `strict_mode=true` 时，若命中高风险漂移（命名词/称谓词），触发一次修复重生成。
- 重试仍失败时，非流式返回 422；流式发送 `error` 事件并附 `code=postcheck_strict_failed`。
测试：
- 已在 `tests/test_continue_endpoint.py` 增补 strict mode 用例。
- 覆盖非流式/流式、成功修复、修复失败三条路径。
验收：
- 默认模式保持当前“仅 warning”行为。
- 严格模式下不再落库明显漂移文本。
排期：第 1 周 Day 3-4。

### P0-3 Lorebook 注入策略可配置化

状态：`DONE (2026-03-09)`  
目标：恢复并可控启用 Lorebook 注入，支持调试可见性。  
改动文件：`app/api/novels.py`、`app/core/generator.py`、`app/config.py`、`web/src/pages/GenerationResults.tsx`。  
接口/数据：
- `ContinueRequest` 增加 `use_lorebook`（默认跟随服务端配置）。
- `ContinueDebugSummary` 增加 `lore_hits`、`lore_tokens_used`。
测试：
- 已扩展 `tests/test_continue_endpoint.py`，验证 `use_lorebook=true/false` 的 prompt 差异。
- 已扩展 `tests/test_continue_endpoint.py`，验证 debug 字段与配置解析行为。
验收：
- 默认关闭时与当前行为一致。
- 开启后能稳定注入并在结果页显示命中摘要。
排期：第 1 周 Day 5。

### P0-4 AI 客户端生产级容错

状态：`DONE (2026-03-09)`  
目标：补齐 429/5xx/网络异常重试、超时、抖动退避。  
改动文件：`app/core/ai_client.py`、`app/config.py`、`tests/test_ai_client.py`。  
接口/数据：
- 增加配置项：`llm_request_timeout_seconds`、`llm_retry_attempts`、`llm_retry_base_ms`。
- 对 `generate/generate_stream/generate_structured` 统一重试策略（仅瞬时错误重试）。
测试：
- 已在 `tests/test_ai_client.py` 新增 429、503、超时、重试次数上限测试。
- 保留现有 `max_tokens` 回退测试并兼容通过。
验收：
- 出错率下降且平均延迟在可控范围（重试上限后快速失败）。
- 日志包含 request_id、attempt、error_class。
排期：第 1 周 Day 6-7。

### P0-5 Bootstrap 配额语义与续写对齐

状态：`DONE (2026-03-09)`  
目标：bootstrap 计费改为“预留-成功结算-失败返还”。  
改动文件：`app/models.py`、`alembic/versions/*`（新增迁移）、`app/core/auth.py`、`app/core/bootstrap.py`、`app/api/world.py`、`tests/test_bootstrap_contract.py`。  
接口/数据：
- `bootstrap_jobs` 新增 `quota_reservation_id`（可空）。
- 触发时仅预留，任务 `completed` 后 charge，`failed` 后 finalize 返还。
测试：
- 新增 `tests/test_bootstrap_quota.py`，覆盖任务失败退款、重试不重复扣费。
- 保证现有 bootstrap contract 用例通过。
验收：
- hosted 模式下 bootstrap 与 continue 的配额语义一致。
- 异常/重启后不会产生“悬挂扣费”。
排期：第 2 周 Day 1-3。

### P0-6 发布与观测闭环

状态：`DONE (2026-03-09)`  
目标：上线可灰度、可回滚、可量化。  
改动文件：`docs/CONTINUATION_RULES.md`、`docs/ARCHITECTURE.md`、`README.md`、`app/core/events.py`（补充事件字段）  
接口/数据：
- 新增事件：`continue_strict_retry`、`continue_strict_fail`、`continue_lore_enabled`。
- 生成链路日志统一输出 `request_id/novel_id/user_id/variant/attempt`。
测试：
- 新增 `tests/test_events_transaction.py` 断言新事件入库。
- 冒烟：上传小说 -> 续写 -> 结果页 -> 采纳流程回归。
验收：
- 能按用户与模型维度统计失败率、重试率、严格模式命中率。
- 具备“一键关闭 strict_mode/lorebook”的配置回退能力。
排期：第 2 周 Day 4-5。

## 3. 上线顺序

1. 先发后端兼容改动（P0-1/2/3/4 的 schema 向后兼容部分）。  
2. 再发前端参数面板和结果页展示。  
3. 最后启用 hosted 配额一致性（P0-5）和观测开关（P0-6）。  
4. 严格模式先灰度给内部账号，观测 24 小时后全量。  

## 4. 回滚策略

1. 保留旧字段解析路径，前端可立即降级为旧请求体。  
2. `strict_mode`、`use_lorebook` 通过配置可全局关闭。  
3. 若配额迁移异常，回滚到“bootstrap 直接扣费”逻辑并保留补偿脚本。  

## 5. Definition of Done

1. 新增/改动测试全部通过。  
2. 关键链路有结构化日志与事件指标。  
3. 文档 `CONTINUATION_RULES` 与 `ARCHITECTURE` 已同步更新。  
4. 至少完成一次灰度与回滚演练记录。  

## 6. 竞品对比归档（2026-03-09）

对比对象（同类小说/网文生成工具）：`ChatGPT`、`Claude`、`Sudowrite`、`NovelAI`、`Novelcrafter`。  
本次归档目标：明确本工程在“长篇网文生产”场景下的功能竞争位势，用于后续版本取舍。

### 6.1 总体定位结论

本工程更接近“可自部署、可治理、强调一致性的专业续写内核”，而非“协作优先的通用 SaaS 写作平台”。

### 6.2 功能优势（相对竞品）

1. 一致性治理能力强：世界模型（实体/关系/体系）+ 叙事约束 + strict postcheck 重试/阻断，能显式抑制设定漂移。  
2. 续写可控参数完整：支持长度模式、目标字数、版本数、多版本流式产出、Lorebook 开关与注入调试摘要。  
3. 工程化与可运维性好：瞬时错误重试、`max_tokens` 上限回退、事件入库、结构化日志、灰度/回滚策略明确。  
4. 成本与计费语义清晰：hosted 路径采用“预留 -> 按成功版本计费 -> 未用返还”，用户只为成功产出付费。  
5. 私有化友好：`BYOK` + selfhost 模式可直接落地，对数据主权和模型可替换性更友好。

### 6.3 功能劣势（相对竞品）

1. 协作产品能力偏弱：缺少成熟的多人协同、角色权限、团队知识空间与共享工作流。  
2. 世界观生态闭环未完全显式：当前主链路以 `worldpack import` 为主，开放导出能力与生态分发能力仍需增强。  
3. 规模化架构上限已知：LLM 并发闸门为进程内实现，bootstrap 与应用进程同生命周期，横向扩展成本较高。  
4. 平台生态厚度不足：相较头部 SaaS，在插件/连接器、模板市场、创作者社区沉淀方面仍有差距。

### 6.4 后续优先改进建议（面向竞争力）

1. P1：补齐世界观双向流通（标准化导出 + 包版本管理 + 冲突策略可视化）。  
2. P1：引入协作基础能力（项目共享、只读/编辑权限、操作审计）。  
3. P2：将并发闸门与后台任务外部化（队列/worker），提升 hosted 场景稳定性上限。  

推断（基于以上事实）：如果你的目标用户是重视私有部署、可审计、可控续写的网文作者/团队，本工程竞争力很高；如果目标是大众 SaaS 协作写作市场，短板会主要体现在协作与产品化层。

外部对比来源（官方）

Sudowrite pricing: https://www.sudowrite.com/pricing
Sudowrite docs: https://docs.sudowrite.com
NovelAI docs (Editor/Lorebook): https://docs.novelai.net/en/text/editor/ , https://docs.novelai.net/en/text/lorebook/
NovelAI 官网: https://novelai.net/
Novelcrafter pricing: https://www.novelcrafter.com/pricing
ChatGPT pricing: https://openai.com/chatgpt/pricing
ChatGPT Plus/Pro: https://help.openai.com/en/articles/6950777-what-is-chatgpt-plus , https://help.openai.com/en/articles/9793128-what-is-chatgpt-pro
ChatGPT Projects: https://help.openai.com/en/articles/10169521-projects-in-chatgpt
Claude pricing: https://www.anthropic.com/pricing
Claude Projects/RAG/Artifacts: https://support.claude.com/en/articles/9517075-what-are-projects , https://support.claude.com/en/articles/11473015-retrieval-augmented-generation-rag-for-projects , https://support.claude.com/en/articles/9487310-what-are-artifacts-and-how-do-i-use-them


层级图
网络图
时间表。QC 模块是个很好的思路

TODO:
1、交互式小说允许