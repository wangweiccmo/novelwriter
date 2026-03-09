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
