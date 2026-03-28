# 需求文档：多模态内容检索应用

## 简介

本系统是一个多模态内容检索应用，支持用户上传文本、语音、视频、文档、图片等多种模态的内容，利用 Amazon Bedrock Nova MME（Multimodal Embedding）模型将内容转换为向量嵌入，并存储到 S3 向量数据库中。用户可以使用任意模态的数据进行跨模态检索，获取语义相关的内容。系统提供 Web 前端界面，支持用户认证、任务状态追踪、结果可视化和内容预览，并具备容错机制和并发处理能力。

## 术语表

- **System**: 多模态内容检索应用的整体系统
- **Frontend**: 基于 Web 的前端用户界面
- **Backend**: 处理业务逻辑、嵌入生成和检索的后端服务
- **Auth_Service**: 用户认证与授权服务
- **Embedding_Service**: 调用 Amazon Bedrock Nova MME 模型生成多模态向量嵌入的服务
- **Vector_Store**: 基于 S3 的向量数据库，用于存储和检索向量嵌入
- **Task**: 用户发起的内容上传或检索操作的工作单元
- **Content**: 用户上传的任意模态数据（文本、语音、视频、文档、图片）
- **Modality**: 内容的类型分类，包括文本、语音、视频、文档、图片
- **Cross_Modal_Retrieval**: 使用一种模态的查询数据检索其他模态的相关内容

## 需求

### 需求 1：用户认证与授权

**用户故事：** 作为用户，我希望通过安全的登录机制访问系统，以保护我的数据和操作安全。

#### 验收标准

1. WHEN 用户访问 Frontend 且未登录, THE Auth_Service SHALL 将用户重定向到登录页面
2. WHEN 用户提交有效的用户名和密码, THE Auth_Service SHALL 验证凭据并返回认证令牌
3. WHEN 用户提交无效的凭据, THE Auth_Service SHALL 返回明确的错误提示信息
4. WHILE 用户已认证, THE Frontend SHALL 在每个 API 请求中携带认证令牌
5. WHEN 认证令牌过期, THE Auth_Service SHALL 返回 401 状态码，Frontend SHALL 引导用户重新登录
6. THE Auth_Service SHALL 使用行业标准加密算法存储用户密码

### 需求 2：多模态内容上传

**用户故事：** 作为用户，我希望上传多种模态的内容（文本、语音、视频、文档、图片），以便系统对其进行索引和后续检索。

#### 验收标准

1. THE Frontend SHALL 提供统一的上传界面，支持文本、语音、视频、文档、图片五种模态的内容上传
2. WHEN 用户选择文件上传, THE Frontend SHALL 自动识别文件的 Modality 类型
3. WHEN 用户提交文本内容, THE Frontend SHALL 支持直接输入文本或上传文本文件两种方式
4. WHEN 用户上传内容, THE Backend SHALL 创建一个 Task 记录并返回任务 ID
5. THE Backend SHALL 支持以下文件格式：图片（PNG、JPEG、WEBP、GIF）、语音（MP3、WAV、OGG）、视频（MP4、MOV、MKV、WEBM、FLV、MPEG、MPG、WMV、3GP）、文档（PDF、DOCX、TXT），与 Amazon Bedrock Nova MME 支持的输入格式保持一致
6. IF 用户上传的文件格式不在支持列表中, THEN THE Backend SHALL 返回明确的错误信息，说明支持的文件格式
7. THE Backend SHALL 基于 Nova MME 异步 API 限制执行以下文件大小限制：图片最大 50MB、语音最大 1GB（最长 2 小时）、视频最大 2GB（最长 2 小时）、文档（文本）最大 634MB、文本输入最大 50,000 字符。IF 用户上传的文件超过对应限制, THEN THE Backend SHALL 返回错误信息，说明具体的大小限制

### 需求 3：向量嵌入生成

**用户故事：** 作为用户，我希望上传的内容被自动转换为向量嵌入，以便后续进行语义检索。

#### 验收标准

1. WHEN 内容上传成功, THE Embedding_Service SHALL 调用 Amazon Bedrock Nova MME 模型生成向量嵌入
2. WHEN 任意模态内容（文本、语音、视频、文档、图片）上传, THE Embedding_Service SHALL 将原始内容直接传递给 Nova MME 模型生成向量嵌入，无需进行模态转换或预处理
3. WHEN 向量嵌入生成完成, THE Embedding_Service SHALL 将嵌入向量及元数据存储到 Vector_Store 中
4. THE Embedding_Service SHALL 为每个 Content 保留原始文件的引用路径，存储在元数据中
5. IF 嵌入生成过程中发生错误, THEN THE Embedding_Service SHALL 将 Task 状态标记为失败，并记录错误详情

### 需求 4：跨模态内容检索

**用户故事：** 作为用户，我希望使用任意模态的数据进行检索，获取语义相关的多模态内容。

#### 验收标准

1. THE Frontend SHALL 提供检索界面，支持文本输入、文件上传（图片、语音、视频、文档）作为查询条件
2. WHEN 用户提交检索请求, THE Embedding_Service SHALL 将查询内容转换为向量嵌入
3. WHEN 查询向量生成完成, THE Backend SHALL 在 Vector_Store 中执行相似度搜索，返回 Top-K 相关结果
4. THE Backend SHALL 返回检索结果的相似度分数、内容元数据和预览信息
5. WHEN 检索完成, THE Frontend SHALL 按相似度分数降序展示检索结果
6. THE Frontend SHALL 支持用户配置返回结果数量（Top-K 值）
7. IF 检索过程中未找到相关结果, THEN THE Backend SHALL 返回空结果集和提示信息

### 需求 5：任务状态管理

**用户故事：** 作为用户，我希望查看所有上传和检索任务的状态，以了解处理进度。

#### 验收标准

1. THE Frontend SHALL 提供任务列表页面，展示当前用户的所有 Task
2. THE Backend SHALL 为每个 Task 维护以下状态：待处理、处理中、已完成、失败
3. WHEN Task 状态发生变化, THE Backend SHALL 更新 Task 记录中的状态和时间戳
4. THE Frontend SHALL 展示每个 Task 的类型（上传/检索）、模态类型、创建时间、当前状态
5. WHEN 用户查看任务列表, THE Frontend SHALL 支持按状态和时间筛选 Task
6. WHEN Task 处理完成, THE Frontend SHALL 展示任务结果的摘要信息

### 需求 6：内容预览

**用户故事：** 作为用户，我希望在网站上直接预览检索到的内容，无需下载文件。

#### 验收标准

1. WHEN 检索结果包含图片内容, THE Frontend SHALL 以缩略图形式展示，支持点击放大查看
2. WHEN 检索结果包含文本内容, THE Frontend SHALL 展示文本摘要，支持展开查看完整内容
3. WHEN 检索结果包含语音内容, THE Frontend SHALL 提供内嵌音频播放器进行播放
4. WHEN 检索结果包含视频内容, THE Frontend SHALL 提供内嵌视频播放器进行播放
5. WHEN 检索结果包含文档内容, THE Frontend SHALL 提供文档预览功能，展示文档的前几页内容
6. THE Frontend SHALL 为每个预览内容展示文件名、模态类型、上传时间等元数据信息
7. WHEN 用户点击下载按钮, THE Frontend SHALL 提供源文件下载功能，允许用户将检索到的原始文件下载到本地

### 需求 7：容错机制

**用户故事：** 作为用户，我希望系统在遇到错误时能够自动恢复或提供清晰的错误信息，保证服务的可靠性。

#### 验收标准

1. IF Embedding_Service 调用 Bedrock API 失败, THEN THE Backend SHALL 自动重试，最多重试 3 次，每次间隔采用指数退避策略
2. IF 重试 3 次后仍然失败, THEN THE Backend SHALL 将 Task 标记为失败，并记录完整的错误信息
3. IF 文件上传过程中网络中断, THEN THE Backend SHALL 支持断点续传功能
4. IF Vector_Store 暂时不可用, THEN THE Backend SHALL 将嵌入数据写入临时队列，待 Vector_Store 恢复后自动写入
5. THE Backend SHALL 记录所有错误日志，包含时间戳、错误类型、请求上下文信息
6. IF 系统发生未预期的错误, THEN THE Frontend SHALL 展示用户友好的错误提示，避免暴露技术细节

### 需求 8：并发处理能力

**用户故事：** 作为用户，我希望系统能够同时处理多个上传和检索请求，不会因为其他用户的操作而阻塞我的请求。

#### 验收标准

1. THE Backend SHALL 支持同时处理多个用户的上传和检索请求
2. THE Backend SHALL 使用异步任务队列处理嵌入生成任务，避免阻塞 API 响应
3. WHEN 多个用户同时上传内容, THE Backend SHALL 独立处理每个用户的 Task，互不影响
4. THE Backend SHALL 对 Bedrock API 调用实施速率限制，防止超出服务配额
5. WHILE 系统负载较高, THE Backend SHALL 通过任务队列进行流量削峰，保证系统稳定性
6. THE Backend SHALL 支持水平扩展，通过增加服务实例提升并发处理能力
