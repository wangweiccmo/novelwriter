# 续写接口内部规则说明

本文档专门说明续写接口（普通模式与流式模式）的内部执行逻辑与规则约束，内容以当前代码实现为准。

## 1. 适用接口

- 非流式续写：`POST /api/novels/{novel_id}/continue`
- 流式续写：`POST /api/novels/{novel_id}/continue/stream`

两者共享同一套核心续写规则，差异主要在返回方式和多版本产出节奏。

## 2. 输入参数规则（ContinueRequest）

请求体字段与约束：

- `num_versions`
  - 默认 `1`
  - 范围：`1..2`
- `prompt`
  - 可选
  - 最大长度 `2000`
- `max_tokens`
  - 可选
  - 范围：`100..16000`
- `target_chars`
  - 可选
  - 仅允许：`2000`、`3000`、`4000`
- `context_chapters`
  - 可选
  - 最小 `1`
  - 超过系统上限时会被自动截断到 `MAX_CONTEXT_CHAPTERS`
- `temperature`
  - 可选
  - 范围：`0.0..2.0`

校验失败时直接返回 `400/422`，不会进入模型调用阶段。

## 3. 前置检查与上下文准备

续写前会执行以下步骤：

1. 小说访问校验
   - 小说不存在：`404`
   - hosted 模式下校验 owner 隔离

2. 上下文章节选取
   - 按章节号倒序取最近 N 章（N 由 `context_chapters` + 配置共同决定）
   - 再反转为正序拼接，保证上下文阅读顺序正确
   - 若小说没有任何章节：`400 Novel has no chapters`

3. 相关世界知识装配（Writer Context）
   - 仅从 `confirmed` 世界实体中做相关性命中
   - 使用实体名/别名做关键词匹配（Aho-Corasick）
   - 歧义关键词（映射到多个实体）禁用
   - relationship 仅注入“已命中实体之间”的关系
   - system 仅注入 `confirmed + active` 的体系

4. 上下文预算裁剪（防提示词过大）
   - 超预算后按顺序裁剪：
     - 先删 `reference` 关系
     - 再删 `reference` 属性
     - 最后从尾部删实体，并同步清理悬挂关系

5. 续写调试摘要生成
   - 记录实际注入的 systems/entities/relationships
   - 记录 relevant entity ids 与被禁用歧义词

## 4. Prompt 组装规则

续写 prompt 由以下部分构成：

- `SYSTEM_PROMPT`（系统规则）
  - 角色一致性、情节推进、不重复、冲突与悬念等写作原则
  - 视角纪律（world_knowledge 是作者视角，不可越角色认知）
  - 反幻觉规则（避免引入未在上下文出现的专有名词）
  - 风格纪律（必须贴合 recent_chapters 语体）
  - 输出格式纪律（只输出正文，不输出“第X章”标题）

- 长度纪律补充
  - 当给定 `target_chars` 时，系统会计算：
    - 最低建议字数（按 `continuation_min_target_ratio`）
    - 推荐目标字数（可带 overrun）
    - 自然上浮上限

- 基础模板信息
  - 书名
  - 目标章节号（下一章）
  - 最近 outline（最多 2 条）

- 世界知识块 `<world_knowledge>`
  - 来自 context assembly 后的人类可读文本化结果

- 叙事约束块 `<narrative_constraints>`
  - 从 active system 的 `constraints` 抽取
  - 自动编号注入

- 用户指令块 `<user_instruction>`
  - `prompt` 非空时注入

- 风格锚点 `<recent_chapters>`
  - 始终放在 prompt 尾部，作为风格与语体锚定
  - 最终以“请续写第X章：”收束

## 5. 目标章节与长度/Token 规则

### 5.1 下一章规则

- 续写目标章号并不是“最大章号 + 1”
- 使用“最小缺失正整数”策略（可填补中间断档章节）

### 5.2 max_tokens 计算规则

- 若有 `target_chars`：
  - `estimated_tokens = ceil(target_chars * chars_to_tokens_ratio)`
  - 再加 buffer：`ceil(estimated_tokens * (1 + token_buffer_ratio))`
  - 最终夹在 `[100, max_continuation_tokens]`
- 若无 `target_chars` 且传入 `max_tokens`：
  - 直接使用请求值
- 否则：
  - 使用 `default_continuation_tokens`

### 5.3 provider 上限回退

若模型服务返回“`max_tokens` 超上限”错误（如 valid range `[1, 8192]`）：

- 客户端会从错误信息解析上限
- 自动降到上限重试一次
- 流式与非流式都支持该回退

## 6. 续写结果处理规则

每个版本生成后都会做统一后处理：

1. 清理思维链泄露
   - 删除 `<think>...</think>`
   - 去掉前缀 `Final:` / `Answer:`（若存在）

2. 按目标字数裁剪（仅在指定 `target_chars` 时）
   - 优先在句末标点处截断
   - 尽量保持语句完整

3. 入库保存
   - 写入 `continuations` 表
   - 记录 `chapter_number`、`content`、`prompt_used`

## 7. 非流式与流式差异

## 7.1 非流式（/continue）

- 同步等待所有版本生成完成后一次性返回
- 返回结构：
  - `continuations[]`
  - `debug`（注入摘要 + 可能的 postcheck 警告）

## 7.2 流式（/continue/stream）

- 返回 `application/x-ndjson`
- 事件协议：
  - `start`
  - `token`（仅 variant 0 实时流出）
  - `variant_done`
  - `error`
  - `done`

多版本行为：

- `variant=0` 走真正流式 token 输出
- `variant>=1` 在后台并发非流式生成，完成后发 `variant_done`

错误行为：

- 失败尽量转为流内 `error` 事件而非直接 HTTP 失败
- 常见 code：
  - `llm_stream_failed`
  - `llm_generate_failed`
  - `db_persist_failed`

## 8. Postcheck（非阻断一致性检查）

生成完成后会执行 postcheck，用于发现疑似“设定漂移”：

- 检测来源：
  - 引号包裹词
  - 命名提示词结构
  - 对话称谓词
- 对照集合：
  - 注入的 world context 术语
  - recent chapters 文本
  - 用户 prompt
- 不在上述集合中的可疑词，会产出 warning：
  - `code`
  - `term`
  - `message`
  - `version`
  - `evidence`

该检查只告警，不阻断返回。

## 9. 配额与并发规则

### 9.1 并发闸门

- 续写前必须获取 LLM 槽位
- 若当前满载，直接返回 `503`，并带 `Retry-After`

### 9.2 配额结算（QuotaScope）

续写采用“预留 -> 按成功版本计费 -> 结束返还未用额度”：

- `reserve()`：按请求版本数预扣
- `charge(1)`：每成功产出一个版本才计费一次
- `finalize()`：返还未产出的差额

因此用户只为“实际收到的版本”付费（hosted 模式）。

## 10. 错误语义总结

非流式常见错误：

- `400`：参数非法、小说无章节、值错误
- `404`：小说不存在或无访问权限
- `429`：配额不足（hosted）
- `503`：并发槽位已满
- `500`：续写内部异常（统一 `Continuation generation failed`）

流式常见特性：

- HTTP 可能是 `200`，但流内出现 `error` 事件
- 客户端应同时处理“HTTP 错误”和“流内错误事件”两类失败

## 11. 实现锚点（代码位置）

- 接口编排：`app/api/novels.py`
  - `continue_novel_endpoint`
  - `continue_novel_stream_endpoint`
  - `_prepare_continuation_context`
- 生成核心：`app/core/generator.py`
  - `_build_continuation_prompt`
  - `continue_novel`
  - `continue_novel_stream`
- 相关性与注入：`app/core/context_assembly.py`
- 生成后检查：`app/core/continuation_postcheck.py`
- 模型调用容错：`app/core/ai_client.py`
- 并发闸门：`app/core/llm_semaphore.py`
- 配额生命周期：`app/core/auth.py` (`QuotaScope`)
- 请求模型：`app/schemas.py` (`ContinueRequest`)
- 提示词规则：`app/utils/prompts.py`

