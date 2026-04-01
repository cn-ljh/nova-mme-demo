# Nova MME Demo — 多模态内容检索

[English](README.md) | **中文**

基于 AWS 无服务器架构的多模态内容上传与语义检索应用。支持文本、图片、音频、视频和文档的统一向量空间检索。

## 核心技术栈

- **[Amazon Bedrock Nova MME](https://docs.aws.amazon.com/nova/latest/nova2-userguide/embeddings.html)** — 多模态统一嵌入模型，支持文本、图片、音频、视频、文档五种模态
- **[Amazon S3 Vectors](https://aws.amazon.com/s3/features/vectors/)** — 向量存储与余弦相似度检索
- **[Amazon Transcribe](https://aws.amazon.com/transcribe/)** — 语音转文字，让文本查询能匹配音频/视频中的语音内容
- **[AWS SAM](https://aws.amazon.com/serverless/sam/)** — 无服务器基础设施（Lambda、API Gateway、DynamoDB、SQS、EventBridge）
- **React 18 + TypeScript** 前端，使用 AWS Amplify v6 认证

## 架构概览

```
React SPA ──► CloudFront ──► API Gateway (Cognito 认证) ──► Lambda 函数
                                                              ├─ auth/              注册/登录/用户信息
                                                              ├─ content/           预签名上传、确认上传
                                                              ├─ search/            向量检索、转录文本检索
                                                              └─ task/              任务状态查询

上传流程:
  content Lambda ──► SQS ──► embedding Lambda ──► Bedrock Nova MME ──► S3 Vectors
                        │         └──► (音频/视频) ──► Amazon Transcribe
                        │                                    └──► EventBridge 每分钟 ──► transcribe_poller
                        │                                                                    └──► S3 Vectors (转录文本向量)
                        └──► (大文件) ──► Bedrock 异步调用
                                                └──► EventBridge 每分钟 ──► embedding_poller
```

完整架构文档：[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)

![架构图](docs/nova-mme-architecture.png)

## 支持的格式

| 模态 | 格式 | 大小限制 |
|------|------|----------|
| 图片 | PNG, JPEG, WEBP, GIF | 50 MB |
| 音频 | MP3, WAV, OGG | 1 GB (≤ 2小时) |
| 视频 | MP4, MOV, MKV, WEBM, FLV, MPEG, WMV, 3GP | 2 GB (≤ 2小时) |
| 文档 | PDF, DOCX, TXT | 634 MB |
| 文本 | 直接输入 | 50,000 字符 |

- 音频/视频 >30s 或 >100MB → 异步 Bedrock 分段嵌入（每分钟轮询）
- 音频/视频 → Amazon Transcribe 语音转文字；转录文本分块后生成可检索的文本向量

## 检索原理

### 跨模态检索

Nova MME 将所有模态映射到统一的 1024 维向量空间：

- **文本查询 → 图片/文档**：语义级匹配（如 "供应链架构图" → 匹配相关图片）
- **文本查询 → 音频/视频（音频嵌入）**：主题级匹配，粒度较粗（30 秒一个分段）
- **文本查询 → 音频/视频（转录文本嵌入）**：精确内容匹配，能匹配具体的用词和表述

### 双路径音频检索

音频/视频内容同时存储两种向量：

1. **原始音频嵌入** — 捕获语义主题和音调特征
2. **转录文本嵌入** — 捕获具体的语音内容（依赖 Transcribe 转录）

检索时两路合并排序，兼顾主题匹配和精确内容匹配。

### 重复检测

上传时自动检测同名同大小的文件，避免重复处理浪费资源。

## 前置条件

- **Python 3.12+**
- **Node.js 20+**
- **AWS CLI** 已配置凭证 (`aws configure`)
- **AWS SAM CLI** (`pip install aws-sam-cli`)
- AWS 账号需开通以下服务：
  - Amazon Bedrock Nova MME（在 `us-east-1` 控制台申请模型访问权限）
  - Amazon S3 Vectors
  - Amazon Transcribe

## 快速开始

### 1. 安装依赖

```bash
./scripts/setup-dev.sh
source .venv/bin/activate
```

### 2. 运行测试

```bash
pytest backend/tests/ -v
pytest backend/tests/property/ -v  # Hypothesis 属性测试
```

### 3. 部署后端

```bash
# 首次部署（交互式，SAM 会提示输入参数）
sam build --parallel
sam deploy --guided
```

首次部署时 SAM 会询问：
- **Stack name**：栈名称（如 `multimodal-retrieval-dev`）
- **AWS region**：推荐 `us-west-2`（Bedrock 调用始终走 `us-east-1`）
- **Stage**：环境（如 `dev`）
- **CloudFrontDomain**：首次留空，部署后从输出中获取

首次部署后，配置 `samconfig.toml`：

```bash
cp samconfig.toml.example samconfig.toml
# 编辑 samconfig.toml，填入你的 bucket 名称、region 和 CloudFront 域名
sam build --parallel && sam deploy
```

### 4. 配置并部署前端

```bash
# 复制环境变量模板
cp frontend/.env.example frontend/.env.local

# 从 CloudFormation 输出获取配置值
aws cloudformation describe-stacks \
  --stack-name multimodal-retrieval-dev \
  --query 'Stacks[0].Outputs'
```

编辑 `frontend/.env.local`：
```
VITE_API_URL=https://<你的CloudFront域名>
VITE_USER_POOL_ID=<UserPoolId>
VITE_USER_POOL_CLIENT_ID=<UserPoolClientId>
VITE_AWS_REGION=<你的region>
VITE_CLOUDFRONT_DOMAIN=https://<你的CloudFront域名>
```

部署前端：
```bash
./scripts/deploy-frontend.sh dev
```

### 5. 本地前端开发

```bash
cd frontend && npm run dev
```

## 项目结构

```
nova-mme-demo/
├── backend/
│   ├── functions/
│   │   ├── auth/               Cognito 注册/登录
│   │   ├── content/            上传管理、SQS 入队、重复检测
│   │   ├── embedding/          Bedrock 同步嵌入 + Transcribe 启动（SQS 触发）
│   │   ├── embedding_poller/   异步 Bedrock 任务轮询（EventBridge 触发）
│   │   ├── transcribe_poller/  Transcribe 任务轮询、转录分块+嵌入（EventBridge 触发）
│   │   ├── search/             向量检索（音频/视频 + 转录文本）
│   │   ├── task/               任务状态 API
│   │   └── vector_setup/       S3 Vectors 初始化（CloudFormation 自定义资源）
│   ├── layers/shared/python/shared/
│   │   ├── models.py           数据模型、DynamoDB 键辅助函数
│   │   ├── dynamodb.py         DynamoDB CRUD + 重复检测
│   │   ├── s3_client.py        S3 预签名 URL、S3 Vectors 读写
│   │   ├── bedrock_client.py   Nova MME 同步/异步嵌入
│   │   └── logger.py           结构化 JSON 日志
│   └── tests/                  单元测试 + Hypothesis 属性测试
├── frontend/src/
│   ├── pages/                  登录、仪表盘、上传、检索、任务列表
│   ├── components/             文件上传、搜索框、结果卡片、媒体预览
│   ├── services/               API 客户端、认证服务
│   └── types/                  TypeScript 类型定义
├── docs/                       架构文档、API 参考、变更日志
├── scripts/                    部署和清理脚本
│   ├── setup-dev.sh            开发环境初始化
│   ├── deploy.sh               后端部署
│   ├── deploy-frontend.sh      前端部署
│   └── cleanup_duplicates.py   重复内容清理工具
└── template.yaml               AWS SAM 模板
```

## API 概览

所有接口需要在 `Authorization` 请求头中携带 Cognito IdToken（无需 "Bearer" 前缀）。

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/auth/register` | 注册 |
| POST | `/api/auth/login` | 登录 |
| GET | `/api/auth/me` | 获取用户信息 |
| POST | `/api/content/request-upload` | 获取 S3 预签名上传 URL |
| POST | `/api/content/confirm-upload` | 确认上传，启动嵌入处理 |
| POST | `/api/content/upload-text` | 直接上传文本 |
| GET | `/api/content` | 列出用户内容 |
| GET | `/api/content/{id}` | 获取内容详情 |
| DELETE | `/api/content/{id}` | 删除内容 |
| POST | `/api/search` | 语义检索 |
| GET | `/api/tasks` | 列出任务 |
| GET | `/api/tasks/{id}` | 获取任务详情 |

完整 API 参考：[docs/API.md](docs/API.md)

## 注意事项

- Bedrock Nova MME 目前仅在 `us-east-1` 可用，SAM 模板中 Bedrock 调用始终指向 `us-east-1`
- S3 Vectors 目前仅在部分 Region 可用
- 音频 `.m4a` 格式暂不支持同步嵌入（Nova MME 不识别 `x-m4a` MIME 类型），但支持 Transcribe 转录
- 大文件使用异步 API，处理时间取决于文件大小

## 已知问题

详见 [docs/KNOWN_ISSUES.md](docs/KNOWN_ISSUES.md)

## 许可证

MIT — 详见 [LICENSE](LICENSE)
