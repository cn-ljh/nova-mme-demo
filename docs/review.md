# 架构审查报告：多模态内容检索应用

## 优点总结

1. **全栈 Serverless 架构合理**：Lambda + API Gateway + SQS 组合完全消除运维负担，DynamoDB On-Demand 自动应对流量峰值，天然符合 AWS Well-Architected Framework 的运维卓越和可靠性支柱。

2. **SQS 解耦设计出色**：嵌入生成通过 SQS 异步处理，Content Lambda 立即返回 task_id，用户体验流畅；SQS DLQ 提供天然的错误隔离和重试机制，符合需求 7 的容错要求。

3. **DynamoDB 单表设计合理**：基于 PK/SK 和 GSI1 的访问模式覆盖了所有业务查询需求，按需容量模式避免了容量规划的麻烦。`TASK#{created_at}#{task_id}` 的 SK 设计天然支持时间排序查询。

4. **Cognito + API Gateway Authorizer**：零代码实现令牌验证，符合最小权限原则。Cognito 的 SRP 协议保证密码安全传输，无需自行管理密码哈希。

5. **正确性属性设计完善**：21 个属性覆盖了所有关键业务场景，属性测试与需求一一对应，对系统正确性的形式化验证有显著价值。

6. **CloudFront 统一入口**：前端静态资源、API 代理、内容预览三合一，减少跨域问题，签名 URL 保证内容访问安全。

---

## 问题与风险清单（按严重程度排序）

### Critical（阻塞性）

#### C1. 异步 Bedrock 作业完成通知机制缺失
**问题**：设计中 Embedding Lambda 对大文件调用 `StartAsyncInvoke` 后需要轮询 `GetAsyncInvoke` 来获取结果。Lambda 最大执行时间为 15 分钟（与 SQS 可见性超时一致），而 2GB 视频的异步嵌入可能需要 30 分钟以上。Lambda 将超时，SQS 消息重新可见后会重复触发，造成无限循环。

**修复方案**：增加 **Embedding Poller Lambda**，由 EventBridge Scheduler 每分钟触发，查询 DynamoDB 中 `status=processing` 且 `async_invocation_arn` 非空的任务，调用 `GetAsyncInvoke` 检查状态，完成后处理结果写入 S3 Vectors。Embedding Lambda 对大文件只负责启动异步作业并更新 DynamoDB。

#### C2. S3 Vectors CloudFormation 资源类型不确定
**问题**：S3 Vectors 是 2024 年底发布的新服务，CloudFormation 原生资源类型（`AWS::S3Vectors::VectorBucket`）支持可能有限或存在 API 变更风险。

**修复方案**：使用 Lambda-backed Custom Resource 创建 Vector Bucket 和 Vector Index，通过 boto3 `s3vectors` 客户端实现，确保幂等性（先检查是否存在再创建）。同时在 template.yaml 中注释说明如何通过 CLI 手动创建作为备选。

#### C3. 大文件上传流程设计不完整
**问题**：设计提到 API Gateway 有 10MB 有效载荷限制，大文件需要 S3 预签名 URL，但前后端的完整交互协议未定义：前端如何知道何时使用预签名 URL？完成上传后如何通知后端？

**修复方案**：明确上传流程：
- 小文件（≤ 10MB）：直接通过 API Gateway 上传
- 大文件（> 10MB）：`POST /api/content/request-upload` 获取 S3 预签名 POST URL → 前端直接上传到 S3 → `POST /api/content/confirm-upload` 通知后端完成处理

---

### High（高优先级）

#### H1. S3 Vectors 元数据过滤支持需验证
**问题**：`SearchRequest.modality_filter` 需要在向量搜索时按模态过滤。S3 Vectors 的 `QueryVectors` API 支持过滤表达式，但过滤发生在 ANN 搜索之后，当匹配结果少于 Top-K 时会减少返回数量，影响召回率。

**修复方案**：在 `query_vectors` 中使用 `filter` 参数（若 API 支持），同时在 Search Lambda 中实现后处理过滤作为兜底。如 S3 Vectors 不支持原生过滤，则在 DynamoDB 中按 content_id 批量查询模态类型后过滤。文档中需说明此限制对召回率的影响。

#### H2. CloudFront 签名 URL 私钥管理
**问题**：Search Lambda 需要 CloudFront 密钥对的私钥来生成签名 URL。设计中未说明密钥的创建方式、存储位置和轮换策略。私钥若硬编码或放在环境变量中是严重安全漏洞。

**修复方案**：
1. 在 AWS 控制台创建 CloudFront Key Group 和公/私钥对
2. 私钥存储在 AWS Secrets Manager（加密存储）
3. Lambda 通过 IAM 权限访问 Secrets Manager，并缓存私钥（每次调用不重复拉取）
4. 定期轮换私钥（使用 Secrets Manager 的自动轮换功能）

#### H3. `USER_PASSWORD_AUTH` 安全性
**问题**：`USER_PASSWORD_AUTH` 认证流程将密码以明文（Base64 编码）发送到 Cognito，虽然通过 HTTPS 加密，但不如 `USER_SRP_AUTH`（Secure Remote Password 协议）安全，后者密码从不离开客户端。

**修复方案**：前端使用 AWS Amplify v6（`signIn` API 默认使用 SRP 流程），后端的 Auth Lambda 注册功能保持不变。API Gateway Cognito Authorizer 验证逻辑无需修改。这样无需任何架构变更，只需调整前端 Cognito 配置。

#### H4. 缺少 API Gateway 流控和 WAF 配置
**问题**：设计中需求 8.4 要求对 Bedrock API 调用实施速率限制，但 API Gateway 层面没有配置 Usage Plan、API Keys 或 WAF 规则，无法防止恶意用户发起大量上传请求耗尽 Bedrock 配额。

**修复方案**：
- API Gateway 配置 Default Throttling（例如 100 RPS，1000 Burst）
- 对 `/api/content/upload` 端点单独设置更严格的限流（10 RPS）
- 可选：添加 AWS WAF Web ACL，配置 IP 速率限制规则（每 IP 每 5 分钟最多 100 次上传）

---

### Medium（中优先级）

#### M1. CORS 配置未明确
**问题**：设计中没有明确 API Gateway 的 CORS 配置。前端域名（CloudFront 域名）需要被允许访问 API Gateway。

**修复方案**：在 API Gateway 的 SAM 模板中配置 `Cors`，允许 CloudFront 域名（`https://{distribution}.cloudfront.net`）作为 `AllowOrigin`，并在所有 Lambda 响应头中包含 `Access-Control-Allow-Origin`。

#### M2. 异步嵌入结果存储流程
**问题**：异步 API 的嵌入结果输出到 S3（`embeddings-output/` 路径），但设计中未明确：
1. 异步结果是单个向量还是分段向量数组？
2. 分段向量如何聚合（平均池化？最大池化？存储所有分段？）
3. S3 Vectors 中如何存储多个分段向量（按 content_id + segment_index 作为 key？）

**修复方案**：明确异步结果处理策略：每个分段作为独立向量存储（key: `{content_id}#seg{i}`），检索时查询所有分段向量取最高相似度。或者对所有分段向量做平均池化后存一个向量（简单但损失精度）。建议采用前者，并在 Search Lambda 中对同一 content_id 的多分段结果去重（保留最高分）。

#### M3. DynamoDB 按状态筛选性能
**问题**：需求 5.5 要求按状态筛选任务。当前设计是查询所有用户任务后在 DynamoDB 中使用 FilterExpression，这会造成 RCU 浪费（读取所有条目后过滤）。

**修复方案**：增加 GSI2，`GSI2PK = USER#{user_id}#STATUS#{status}`，`GSI2SK = TASK#{created_at}`，这样可以直接按用户+状态高效查询。对于任务量较少的用户，过滤也可接受，是否增加 GSI 视实际数据量决定。

#### M4. S3 内容桶缺少生命周期策略
**问题**：用户上传的原始文件存储在 S3，没有生命周期规则，长期积累会增加存储成本。

**修复方案**：配置 S3 生命周期规则：
- 30 天后迁移到 S3 Intelligent-Tiering
- 对标记为删除的内容设置 TTL 过期删除

#### M5. SQS 可见性超时与大文件异步处理冲突
**问题**：SQS 可见性超时设为 900 秒（Lambda 最大执行时间），但对于启动异步 Bedrock 作业的消息，Lambda 可以在几秒内完成（发送请求后立即返回），900 秒超时会导致其他消息被不必要地延迟处理。

**修复方案**：拆分 SQS 队列为两类：
1. **同步嵌入队列**（小文件）：可见性超时 120 秒，批处理大小 1
2. **异步嵌入队列**（大文件，启动 Bedrock 异步作业后即删除消息）：可见性超时 30 秒，批处理大小 1

---

### Low（低优先级）

#### L1. CloudFront 签名 URL 有效期
评估：1 小时对大文件（2GB 视频）下载可能不够，建议对视频/音频内容设置 4-12 小时有效期。

#### L2. DLQ 自动重处理
设计中 DLQ 消息需要手动重处理。建议配置 EventBridge 告警（DLQ depth > 0）触发告警，并提供 DLQ 重处理 Lambda。

#### L3. 前端断点续传
需求 7.3 要求断点续传。S3 Multipart Upload + 浏览器的 File Slice API 可以实现，但前端实现复杂。建议初版使用整文件上传，断点续传作为后续优化项。

#### L4. 缺少 Bedrock 异步 API 的 S3 输出桶权限
Bedrock 异步 API 需要输出 S3 桶配置特定的桶策略，允许 Bedrock 服务写入结果。设计中未明确这个桶策略，会导致异步作业权限错误。

---

## 具体优化建议

### 建议 1：增加 Embedding Poller Lambda（解决 C1）

```
EventBridge Scheduler（每1分钟）
         |
         v
EmbeddingPollerFunction
  - 查询 DynamoDB: status=processing AND async_job_arn IS NOT NULL
  - 调用 Bedrock GetAsyncInvoke(arn)
  - 如果 Completed: 读取 S3 结果 → 写入 S3 Vectors → 更新 DynamoDB
  - 如果 Failed: 更新 Task status=failed
  - 如果 InProgress: 跳过（下一分钟再检查）
```

### 建议 2：大文件上传两阶段协议（解决 C3）

```
前端                    Content Lambda              S3
  |                          |                       |
  |-- POST /content/upload-url -->|                  |
  |       (filename, size, type)  |                  |
  |<-- presigned_post_url --------|                  |
  |                          |                       |
  |-- PUT  S3 (直接上传) ------------------>|
  |                                         |
  |-- POST /content/confirm-upload -------> |
  |       (upload_id, s3_key)     Content Lambda      |
  |                               创建 Task, 发 SQS   |
  |<-- task_id -------------------|                  |
```

### 建议 3：修改后的架构补充组件

在原架构基础上新增：

| 新增组件 | 说明 |
|---------|------|
| EmbeddingPollerFunction | EventBridge 触发，检查 Bedrock 异步作业状态 |
| VectorBucketSetupFunction | CloudFormation 自定义资源，创建 S3 Vector Bucket 和 Index |
| CloudFront Key Group | 取代旧式 Key Pair，与 Trusted Key Groups 配合使用 |
| Secrets Manager Secret | 存储 CloudFront 签名私钥 |
| WAF Web ACL | API Gateway 上游，防止滥用 |

---

## 修改后的架构建议

```
变更点 1: 嵌入处理流程
  原流程: SQS → EmbeddingFn → Bedrock(sync/async) → S3Vectors
  新流程:
    小文件: SQS → EmbeddingFn → Bedrock sync → S3Vectors → DDB(completed)
    大文件: SQS → EmbeddingFn → Bedrock StartAsyncInvoke → DDB(processing+arn)
             EventBridge(1min) → PollerFn → Bedrock GetAsyncInvoke
                                          → S3(read result)
                                          → S3Vectors → DDB(completed)

变更点 2: 大文件上传流程
  原流程: 未明确
  新流程: Frontend → ContentFn(request presigned URL)
                   → S3(direct upload)
                   → ContentFn(confirm upload) → SQS

变更点 3: 认证流程
  原流程: USER_PASSWORD_AUTH (直接发密码)
  新流程: Amplify SRP flow (密码不离开客户端) + Cognito Authorizer不变

变更点 4: 向量索引创建
  原流程: 未定义如何创建 Vector Bucket 和 Index
  新流程: SAM Custom Resource Lambda 在部署时创建
```

---

## 总体评价

设计文档质量较高，整体架构选型合理，充分利用了 AWS 托管服务降低运维负担。主要风险集中在以下三点：

1. **异步 Bedrock 作业的 Lambda 超时**：这是最关键的技术风险，必须通过 Poller 模式解决，否则大文件嵌入功能无法正常工作。

2. **大文件上传协议**：需要在前后端明确约定两阶段上传流程，这是功能完整性的必要条件。

3. **S3 Vectors API 的确认**：服务较新，需要在开发阶段尽早验证 CloudFormation 资源类型和 boto3 客户端 API 签名，避免后期返工。

其余问题均可在 v1.0 发布后迭代优化。建议按本文档的优先级逐步解决。
