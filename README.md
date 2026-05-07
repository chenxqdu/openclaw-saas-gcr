# OpenClaw SaaS on EKS — China Region

多租户 OpenClaw SaaS 平台，部署在 **AWS 中国区** (cn-northwest-1, 宁夏)。

> Global (us-west-2) 版本见 [`main`](https://github.com/chenxqdu/openclaw-saas-gcr/tree/main) 分支。

## 架构

```
                    Internet
                       │
                   NLB (TCP)
                       │
                ┌──────┴──────┐
                │  EKS Cluster │
                │  (Graviton)  │
                └──────┬──────┘
         ┌─────────────┼─────────────┐
         │             │             │
   ┌─────┴─────┐  ┌───┴───┐  ┌─────┴─────┐
   │ Platform   │  │Operator│  │  Tenant   │
   │ API + UI   │  │(Helm)  │  │ Namespaces│
   └─────┬─────┘  └───┬───┘  └─────┬─────┘
         │             │             │
         │        Creates CRD   ┌───┴────────┐
         │             │        │ Agent Pod   │
         │             └──────► │ ├ openclaw  │
         │                      │ ├ otel-coll.│
         │                      │ ├ metrics-  │
         │                      │ │ exporter  │
         │                      │ └ gw-proxy  │
    ┌────┴────┐                 └───┬────────┘
    │ RDS     │                     │
    │ Postgres│              otel-collector
    └─────────┘              :9090/metrics
         │                          │
    ┌────┴────┐              ┌──────┴──────┐
    │ SQS     │◄─────────────│metrics-exp. │
    │ Queue   │  usage deltas│(scrape+push)│
    └────┬────┘              └─────────────┘
         │
    ┌────┴────┐
    │ Billing │
    │Consumer │
    └─────────┘
```

## 区域差异

| | Global (`main`) | China (`cn`) |
|---|---|---|
| **Region** | us-west-2 | cn-northwest-1 |
| **Partition** | `aws` | `aws-cn` |
| **默认 LLM** | bedrock-irsa (无需 key) | openai-compatible |
| **Agent 镜像** | 上游 openclaw | Mirror /Custom (预装工具) |
| **渠道** | 全部 | 飞书、企业微信 |
| **Ingress** | NLB + CloudFront | NLB |
| **ECR** | Private (us-west-2) | Private (cn-northwest-1) |

## 部署方式

提供两种部署方式，资源完全一致：

| 方式 | 适用场景 | 说明 |
|------|---------|------|
| **CloudFormation** | Workshop / 快速演示 | 单模板一键部署，含 EC2 跳板机 |
| **CDK** | 生产 / 持续迭代 | 分 Stack 管理，`deploy.sh` 自动化 |

两种方式创建的 AWS 资源相同：VPC (3 AZ) → EKS → EFS → RDS → SQS → S3 → Karpenter → ALB Controller IAM。

---

## 镜像准备（两种方式通用）

> **⚠️ 需要能访问 ghcr.io、Docker Hub、public.ecr.aws 的网络环境。** 中国区 EC2 无法访问这些源。请在本地机器或 Global 区域 EC2 上完成以下所有镜像准备步骤（mirror 上游镜像 + 构建平台镜像 + mirror Helm charts），构建完直接 push 到 CN ECR。CN EC2 上只需运行 `deploy.sh` 部署 K8s 组件。

中国区 EKS 节点无法拉取 Docker Hub / ghcr.io 镜像。所有上游镜像必须 mirror 到 CN ECR，平台镜像需要构建后推送。

### 需要配置 aws configure, 环境准备参考 [EC2.md](EC2.md)。

### 1. 登录 ECR

```bash
export AWS_DEFAULT_REGION=cn-northwest-1
ECR=${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_DEFAULT_REGION}.amazonaws.com.cn

aws ecr get-login-password --region ${AWS_DEFAULT_REGION} | \
  docker login --username AWS --password-stdin ${ECR}
```

### 2. 创建 ECR 仓库

```bash
# Mirror 仓库（上游镜像）
for repo in openclaw/openclaw nginx astral-sh/uv otel/opentelemetry-collector \
            openclaw-rocks/openclaw-operator eks/aws-load-balancer-controller; do
  aws ecr create-repository --repository-name "$repo" \
    --image-scanning-configuration scanOnPush=false \
    --region ${AWS_DEFAULT_REGION} 2>/dev/null || true
done

# Helm chart 仓库
for repo in charts/aws-load-balancer-controller charts/openclaw-operator; do
  aws ecr create-repository --repository-name "$repo" \
    --image-scanning-configuration scanOnPush=false \
    --region ${AWS_DEFAULT_REGION} 2>/dev/null || true
done

# 平台镜像仓库
for repo in openclaw-saas-platform openclaw-saas-metrics-exporter openclaw-saas-billing-consumer; do
  aws ecr create-repository --repository-name "$repo" \
    --image-scanning-configuration scanOnPush=true \
    --region ${AWS_DEFAULT_REGION} 2>/dev/null || true
done
```

> CDK 方式中 `deploy.sh` 会自动创建这些仓库，可跳过此步。

### 3. Mirror 上游镜像

```bash
# Agent 镜像
docker pull --platform linux/arm64 ghcr.io/openclaw/openclaw:latest
docker tag  ghcr.io/openclaw/openclaw:latest ${ECR}/openclaw/openclaw:latest
docker push ${ECR}/openclaw/openclaw:latest

# Operator 镜像
docker pull --platform linux/arm64 ghcr.io/openclaw-rocks/openclaw-operator:v0.26.2
docker tag  ghcr.io/openclaw-rocks/openclaw-operator:v0.26.2 ${ECR}/openclaw-rocks/openclaw-operator:v0.26.2
docker push ${ECR}/openclaw-rocks/openclaw-operator:v0.26.2

# Sidecar 镜像
docker pull --platform linux/arm64 nginx:1.27-alpine
docker tag  nginx:1.27-alpine ${ECR}/nginx:1.27-alpine
docker push ${ECR}/nginx:1.27-alpine

docker pull --platform linux/arm64 ghcr.io/astral-sh/uv:0.6-bookworm-slim
docker tag  ghcr.io/astral-sh/uv:0.6-bookworm-slim ${ECR}/astral-sh/uv:0.6-bookworm-slim
docker push ${ECR}/astral-sh/uv:0.6-bookworm-slim

docker pull --platform linux/arm64 otel/opentelemetry-collector:0.120.0
docker tag  otel/opentelemetry-collector:0.120.0 ${ECR}/otel/opentelemetry-collector:0.120.0
docker push ${ECR}/otel/opentelemetry-collector:0.120.0

# ALB Controller 镜像
docker pull --platform linux/arm64 public.ecr.aws/eks/aws-load-balancer-controller:v3.2.1
docker tag  public.ecr.aws/eks/aws-load-balancer-controller:v3.2.1 ${ECR}/eks/aws-load-balancer-controller:v3.2.1
docker push ${ECR}/eks/aws-load-balancer-controller:v3.2.1

```

**镜像映射表：**

| 上游镜像 | CN ECR 路径 |
|---------|------------|
| `ghcr.io/openclaw/openclaw:latest` | `${ECR}/openclaw/openclaw:latest` |
| `ghcr.io/openclaw-rocks/openclaw-operator:v0.26.2` | `${ECR}/openclaw-rocks/openclaw-operator:v0.26.2` |
| `public.ecr.aws/eks/aws-load-balancer-controller:v3.2.1` | `${ECR}/eks/aws-load-balancer-controller:v3.2.1` |
| `nginx:1.27-alpine` | `${ECR}/nginx:1.27-alpine` |
| `ghcr.io/astral-sh/uv:0.6-bookworm-slim` | `${ECR}/astral-sh/uv:0.6-bookworm-slim` |
| `otel/opentelemetry-collector:0.120.0` | `${ECR}/otel/opentelemetry-collector:0.120.0` |

### 4. Mirror Helm Charts

```bash
# 登录 CN ECR（Helm OCI 需要单独登录）
aws ecr get-login-password --region ${AWS_DEFAULT_REGION} | \
  helm registry login --username AWS --password-stdin ${ECR}

# 查看 ALB Controller chart 可用版本（需要外网）
helm repo add eks https://aws.github.io/eks-charts
helm repo update
helm search repo eks/aws-load-balancer-controller --versions | head -5

# 下载并推送 ALB Controller chart
helm pull eks/aws-load-balancer-controller --version 3.2.1
helm push aws-load-balancer-controller-3.2.1.tgz oci://${ECR}/charts
rm -f aws-load-balancer-controller-3.2.1.tgz

# 下载并推送 OpenClaw Operator chart
helm pull oci://ghcr.io/openclaw-rocks/charts/openclaw-operator --version 0.26.2
helm push openclaw-operator-0.26.2.tgz oci://${ECR}/charts
rm -f openclaw-operator-0.26.2.tgz
```

> 以上步骤需要在能访问 `aws.github.io` 和 `ghcr.io` 的机器上执行。推送到 CN ECR 后，`deploy.sh` 会自动从 ECR 安装 chart。

### 5. 构建 & 推送平台镜像

```bash

git clone https://github.com/chenxqdu/openclaw-saas-gcr.git
cd openclaw-saas-gcr
git checkout cn

# arm64 匹配 Graviton 节点；x86 EC2 上构建需要先注册 QEMU：
# docker run --privileged --rm tonistiigi/binfmt --install arm64

docker buildx build --platform linux/arm64 --no-cache \
  -t ${ECR}/openclaw-saas-platform:v$(cat platform/VERSION) --push platform/

docker buildx build --platform linux/arm64 --no-cache \
  -t ${ECR}/openclaw-saas-metrics-exporter:v$(cat platform/metrics-exporter/VERSION) --push platform/metrics-exporter/

docker buildx build --platform linux/arm64 --no-cache \
  -t ${ECR}/openclaw-saas-billing-consumer:v$(cat platform/billing/VERSION) --push platform/billing/
```

---

## 方式一：CloudFormation 部署

适合 Workshop 和快速搭建。单个模板包含所有基础设施 + 一台 EC2 跳板机。

### 1. 部署 CloudFormation Stack （可通过 console 部署）

```bash
export AWS_PROFILE=default
export AWS_DEFAULT_REGION=cn-northwest-1

aws cloudformation create-stack \
  --stack-name openclaw-prod \
  --template-body file://cloudformation/cloudformation-ec2.yaml \
  --capabilities CAPABILITY_NAMED_IAM \
  --parameters \
    ParameterKey=ClusterName,ParameterValue=openclaw-prod \
    ParameterKey=AvailabilityZones,ParameterValue="cn-northwest-1a\,cn-northwest-1b\,cn-northwest-1c" \
    ParameterKey=KeyPairName,ParameterValue=your-key-name ## 注意替换
```

部署约 20-25 分钟。Stack 包含：

| 资源 | 说明 |
|------|------|
| VPC | 3 AZ，6 子网，1 NAT Gateway |
| EKS | K8s 1.31+，Graviton m6g.xlarge 节点 |
| EFS | 共享存储 |
| RDS | PostgreSQL 16，db.t4g.medium |
| SQS | Usage Events + DLQ + Karpenter 中断队列 |
| S3 | 备份桶 |
| EC2 | 跳板机 (t3.medium)，自带 EKS Admin 权限 |
| Karpenter | 完整 IAM + EventBridge 中断处理 |

### 2. EC2 环境准备

SSH 或 SSM 登录 EC2 后，安装部署工具。详见 [EC2.md](EC2.md)。

### 3. 配置 EKS 访问 (EC2 环境操作)

EC2 已通过 Access Entry 自动获得集群管理员权限，无需额外配置：

```bash
aws eks update-kubeconfig --name openclaw-prod --region cn-northwest-1
kubectl get nodes
```

### 4. 镜像准备 (已在准备阶段完成)

### 5. 部署 K8s 组件 & 平台 (EC2 环境操作)

```bash
git clone https://github.com/chenxqdu/openclaw-saas-gcr.git
cd openclaw-saas-gcr
git checkout cn

## 或者通过 sftp/scp 复制文件夹到工作路径

cd infra
cp .env.cn .env
vim .env    # 填写 ADMIN_PASSWORD, JWT_SECRET, AWS_ACCOUNT_ID

# CloudFormation 模式：设置 stack 名称（deploy.sh 从这个 stack 读取所有输出）
echo "CF_STACK_NAME=openclaw-prod" >> .env

./scripts/deploy.sh --skip-cdk
```

### 6. 验证

```bash
kubectl get pods -n openclaw-platform
kubectl port-forward -n openclaw-platform svc/platform-api 8890:8890
curl http://localhost:8890/health
# {"status":"ok","version":"0.9.53"}
```

---

## 方式二：CDK 部署 

适合生产环境和持续迭代。分 Stack 管理，支持增量更新。

以下代码的执行环境参考 EC2.md 要求

### 1. 配置环境

```bash
git clone https://github.com/chenxqdu/openclaw-saas-gcr.git
cd openclaw-saas-gcr
git checkout cn

## 或者通过 sftp/scp 复制文件夹到工作路径

cd infra
cp .env.cn .env
vim .env    # 填写 ADMIN_PASSWORD, JWT_SECRET, AWS_ACCOUNT_ID

export AWS_PROFILE=default
export AWS_DEFAULT_REGION=cn-northwest-1
```

### 2. 部署基础设施 + 构建镜像 + 部署平台

首次需要安装 CDK 依赖和 bootstrap：

```bash
cd infra/cdk && python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
cdk bootstrap aws://${AWS_ACCOUNT_ID}/${AWS_DEFAULT_REGION}
cd ..
```

推荐的首次部署流程——先部署基础设施，再准备镜像，最后部署 K8s：

```bash
# 步骤 1：部署 CDK + 创建 ECR 仓库（跳过 K8s，因为镜像还没构建）
./scripts/deploy.sh --skip-k8s

# 步骤 2：完成上方「镜像准备」章节的所有步骤（mirror + 构建平台镜像 + charts 等）

# 步骤 3：部署 K8s 组件 + 平台（跳过 CDK，已部署完成）
./scripts/deploy.sh --skip-cdk
```

后续更新只需：

```bash
./scripts/deploy.sh --skip-cdk
```

### 3. 手动分步部署（可选）

如果需要逐步控制，可以手动执行 CDK 部署：

```bash
cd infra/cdk
source .venv/bin/activate

cdk deploy openclaw-saas-vpc --require-approval never
cdk deploy openclaw-saas-karpenter-node openclaw-saas-sqs openclaw-saas-s3 --require-approval never --concurrency 3
cdk deploy openclaw-saas-eks --require-approval never                    # ~15 分钟
cdk deploy openclaw-saas-efs --require-approval never
cdk deploy openclaw-saas-iam openclaw-saas-rds --require-approval never --concurrency 2
cd ..

# 然后跳过 CDK 只部署 K8s 部分
./scripts/deploy.sh --skip-cdk
```

> **EC2 IAM Role 用户注意：** 如果在 EC2 上手动执行 `cdk deploy`（而非通过 `deploy.sh`），需要额外传入 role ARN 以获得 EKS 集群访问权限：
> ```bash
> cdk deploy openclaw-saas-eks -c deployer_role_arn=arn:aws-cn:iam::123456789012:role/MyEC2Role --require-approval never
> ```
> `deploy.sh` 会自动检测当前 IAM Role 并传入。

---

## 配置

### `.env` — 配置中心

所有部署配置集中在 `infra/.env`：

| 变量 | 必填 | 说明 |
|------|------|------|
| `ADMIN_EMAIL` | ✅ | 管理员邮箱 |
| `ADMIN_PASSWORD` | ✅ | 管理员密码 |
| `JWT_SECRET` | ✅ | JWT 签名密钥 (`openssl rand -hex 32`) |
| `AWS_REGION` | ✅ | `cn-northwest-1` |
| `AWS_PARTITION` | ✅ | `aws-cn` |
| `AWS_ACCOUNT_ID` | ✅ | AWS 账号 ID |
| `METRICS_EXPORTER_REPO` | ✅ | `openclaw-saas-metrics-exporter` |
| `METRICS_EXPORTER_TAG` | ✅ | `v0.3.3` |
| `DEFAULT_AGENT_IMAGE` | | CN custom image |
| `OPERATOR_IMAGE_REPO` | | ECR 中 operator 镜像路径 |
| `ALB_CONTROLLER_IMAGE` | | ECR 中 ALB controller 镜像路径 |

`deploy.sh` 自动从 CDK/CF 输出填充：`DATABASE_URL`、`SQS_QUEUE_URL`、`ECR_REGISTRY`。

### `cdk.json` — 基础设施参数

实例类型、集群规模、VPC CIDR 等在 `infra/cdk/cdk.json` 中配置。

---

## 组件

### Platform API (`platform/`)

FastAPI 后端 + React Web Console。管理租户、Agent、渠道、计费。

### Metrics Exporter (`platform/metrics-exporter/`)

Agent Pod Sidecar。抓取 otel-collector Prometheus 端点，计算用量增量，推送到 SQS。

**数据流：** OpenClaw → otel-collector `:9090/metrics` → metrics-exporter → SQS

### Billing Consumer (`platform/billing/`)

消费 SQS 用量事件，聚合为日/月计费记录。

### Agent Pod 架构

每个 Agent 以 StatefulSet 运行，包含 4 个容器：

| 容器 | 用途 |
|------|------|
| `openclaw` | OpenClaw Agent (Node.js, 3072MB heap) |
| `otel-collector` | OTLP → Prometheus metrics on `:9090` |
| `metrics-exporter` | 抓取 otel-collector → SQS 用量事件 |
| `gateway-proxy` | nginx 反向代理 |

### LLM 供应商 (CN)

| 供应商 | 认证方式 | 说明 |
|--------|---------|------|
| `openai-compatible` | API key + base URL | **CN 默认**，任何 OpenAI 兼容端点 |
| `bedrock-apikey` | `AWS_BEARER_TOKEN_BEDROCK` | Global Bedrock API key (跨区域) |
| `openai` | API key | |
| `anthropic` | API key | |

---

## 销毁

### CloudFormation

```bash
# 1. 清理 K8s 资源
kubectl delete openclawinstance --all --all-namespaces
kubectl delete ns openclaw-platform

# 2. 删除 Stack
aws cloudformation delete-stack --stack-name openclaw-prod

# 3. 手动删除 ECR 仓库（含镜像）
for repo in openclaw-saas-platform openclaw-saas-metrics-exporter openclaw-saas-billing-consumer; do
  aws ecr delete-repository --repository-name ${repo} --force --region cn-northwest-1
done

for repo in openclaw/openclaw nginx astral-sh/uv otel/opentelemetry-collector \
            openclaw-rocks/openclaw-operator eks/aws-load-balancer-controller; do
  aws ecr delete-repository --repository-name ${repo} --force --region cn-northwest-1
done

for repo in charts/aws-load-balancer-controller charts/openclaw-operator; do
  aws ecr delete-repository --repository-name ${repo} --force --region cn-northwest-1
done
```

### CDK

```bash
cd infra/cdk

# 1. 清理 K8s 资源
kubectl delete openclawinstance --all --all-namespaces
kubectl delete ns openclaw-platform

# 2. 反序销毁
cdk destroy openclaw-saas-rds openclaw-saas-iam --force
cdk destroy openclaw-saas-efs --force
BUCKET=$(aws s3 ls | grep openclaw-backups | awk '{print $3}')
aws s3 rm s3://${BUCKET} --recursive
cdk destroy openclaw-saas-s3 --force
cdk destroy openclaw-saas-eks --force    # ~15-20 min
cdk destroy openclaw-saas-sqs openclaw-saas-karpenter-node --force
cdk destroy openclaw-saas-vpc --force

# 3. 手动删除 ECR 仓库（含镜像）
for repo in openclaw-saas-platform openclaw-saas-metrics-exporter openclaw-saas-billing-consumer; do
  aws ecr delete-repository --repository-name ${repo} --force --region cn-northwest-1
done

for repo in openclaw/openclaw nginx astral-sh/uv otel/opentelemetry-collector \
            openclaw-rocks/openclaw-operator eks/aws-load-balancer-controller; do
  aws ecr delete-repository --repository-name ${repo} --force --region cn-northwest-1
done

for repo in charts/aws-load-balancer-controller charts/openclaw-operator; do
  aws ecr delete-repository --repository-name ${repo} --force --region cn-northwest-1
done

```

---

## 问题排查

| 问题 | 原因 | 解决 |
|------|------|------|
| `kubectl: Unauthorized` | CDK L2 集群需要 assume role | CDK 已改为 L1，直接访问；CF 模板通过 Access Entry 授权 |
| ECR push 报 `no basic auth credentials` | ECR 登录过期 (12h) | 重新 `aws ecr get-login-password` |
| Pod `CrashLoopBackOff` + `import psycopg2` | `DATABASE_URL` 缺少 `+asyncpg` | deploy.sh 已修复 |
| ALB Controller ImagePullBackOff | CN 无法拉取默认镜像 | 设置 `ALB_CONTROLLER_IMAGE` 指向 ECR mirror |
| Helm operator install 超时 | ghcr.io chart 拉取慢 | 重试；chart ~50KB 通常可成功 |
| `docker buildx --platform` 失败 | 缺少 QEMU | `docker run --privileged --rm tonistiigi/binfmt --install arm64` |

更多排查记录见 [troubleshooting.md](troubleshooting.md)。

## 分支策略

```
main (Global) → cn (China) → cn-workshop
```

通用修复：cherry-pick `main → cn`。CN 专属改动留在 `cn`。不反向合并。

## 版本

| 组件 | 文件 | 当前版本 |
|------|------|---------|
| Platform API | `platform/VERSION` | 0.9.54 |
| Metrics Exporter | `platform/metrics-exporter/VERSION` | 0.3.1 |
| Billing Consumer | `platform/billing/VERSION` | 0.1.1 |
| Operator | Helm chart | 0.26.2 |

### 镜像更新步骤

以 Platform API 从 v0.9.53 更新到 v0.9.54 为例：

```bash
# 1. 更新 VERSION 文件（代码已修改完毕的前提下）
echo "0.9.54" > platform/VERSION

# 2. 更新 .env.cn 中的版本号
# PLATFORM_VERSION=v0.9.54

# 3. 构建并推送新镜像
ECR=${AWS_ACCOUNT_ID}.dkr.ecr.${AWS_DEFAULT_REGION}.amazonaws.com.cn

aws ecr get-login-password --region ${AWS_DEFAULT_REGION} | \
  docker login --username AWS --password-stdin ${ECR}

docker buildx build --platform linux/arm64 --no-cache \
  -t ${ECR}/openclaw-saas-platform:v$(cat platform/VERSION) --push platform/

# 4a. 完整部署（会重建 secret + 重新 apply 所有 K8s 资源）
cd infra && cp .env.cn .env && ./scripts/deploy.sh --skip-cdk

# 4b. 或者快速更新（仅更新镜像，适合只改了代码没改环境变量的场景）
kubectl set image deployment/platform-api \
  api=${ECR}/openclaw-saas-platform:v$(cat platform/VERSION) \
  -n openclaw-platform

# 5. 验证
kubectl get pods -n openclaw-platform
curl http://localhost:8890/health
# {"status":"ok","version":"0.9.54"}
```

Metrics Exporter 和 Billing Consumer 同理，替换对应的 VERSION 文件、仓库名和 `.env` 变量即可。
