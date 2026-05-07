# OpenClaw SaaS - CN Workshop Deployment

一键部署 OpenClaw SaaS 平台到 AWS CN 区（cn-northwest-1）。

## 目录结构

```
cloudformation/
  cloudlab-template-china.yaml    ← Step 1: CFN 全栈模板 (EKS+RDS+SQS+VPC+IAM+EFS)
scripts/
  step2-k8s-components.sh         ← Step 2: K8s 组件部署脚本
  step3-platform-api.sh           ← Step 3: Platform API 部署脚本
  step4-hermes-sandbox.sh         ← Step 4 (可选): Hermes Agent Sandbox 部署
  destroy.sh                      ← 全栈销毁脚本
  e2e-test.py                     ← Playwright 端到端测试
yaml/
  storage-classes.yaml            ← efs-sc + gp3 StorageClass
  openclaw-crd.yaml               ← OpenClaw CRD (openclawinstances.openclaw.rocks)
  openclaw-operator.yaml          ← OpenClaw Operator v0.26.2
  platform-api.yaml               ← Platform API Deployment + NLB Service
  hermes-config.yaml.tpl          ← Step 4: Hermes ConfigMap 模板
  hermes-sandbox.yaml.tpl         ← Step 4: Hermes Sandbox CRD 模板
```

## 前置条件

- AWS CLI 已配置 cn-northwest-1 区域凭证
- `kubectl`、`helm` 已安装
- S3 工具 bucket `openclaw-cfn-cn-north-1` 已就绪（含 code-server 等工具）

## 部署步骤

### Step 1: CloudFormation 全栈创建 (~20 分钟)

```bash
# 上传模板
aws s3 cp cloudformation/cloudlab-template-china.yaml \
  s3://cf-templates-19geb88zjzj45-cn-northwest-1/cloudlab-template-china.yaml

# 创建 Stack
aws cloudformation create-stack \
  --stack-name openclaw-cn-workshop \
  --template-url https://cf-templates-19geb88zjzj45-cn-northwest-1.s3.cn-northwest-1.amazonaws.com.cn/cloudlab-template-china.yaml \
  --capabilities CAPABILITY_NAMED_IAM CAPABILITY_AUTO_EXPAND \
  --parameters \
    "ParameterKey=AvailabilityZones,ParameterValue=cn-northwest-1a\,cn-northwest-1b\,cn-northwest-1c" \
    "ParameterKey=ClusterName,ParameterValue=openclaw-cn-workshop"

# 等待完成
aws cloudformation wait stack-create-complete --stack-name openclaw-cn-workshop
```

**创建的资源：** VPC (3 AZ)、EKS Cluster (K8s 1.34, 2× m6g.xlarge Graviton)、RDS PostgreSQL 16、SQS 队列、EFS、IAM Roles、IDE (code-server)

### Step 2: K8s 组件部署 (~2 分钟)

```bash
# 配置 kubeconfig
aws eks update-kubeconfig --name openclaw-cn-workshop --region cn-northwest-1

# 执行脚本
export STACK_NAME=openclaw-cn-workshop
export REGION=cn-northwest-1
bash scripts/step2-k8s-components.sh
```

**安装的组件：**
1. EFS CSI Driver (Helm 3.4.1) + Pod Identity
2. ALB Controller (Helm 3.1.0)
3. StorageClasses (efs-sc + gp3)
4. OpenClaw CRD
5. OpenClaw Operator v0.20.0

### Step 3: Platform API 部署 (~2 分钟)

```bash
export STACK_NAME=openclaw-cn-workshop
export REGION=cn-northwest-1
export ADMIN_EMAIL="admin@openclaw.cn"
export ADMIN_PASSWORD="YourPassword"
bash scripts/step3-platform-api.sh
```

**部署的内容：**
1. RDS 密码获取
2. `openclaw-platform` Namespace
3. Pod Identity Association
4. RBAC (cluster-admin)
5. K8s Secrets (DB, config, admin seed)
6. Platform API Deployment (2 replicas) + NLB Service
7. 数据库 Migration (usage tables)

### Step 4 (可选): Hermes Agent Sandbox 部署 (~5 分钟，首次需等 Karpenter 起节点)

独立部署一个基于 [Agent Sandbox CRD](https://agent-sandbox.sigs.k8s.io/)
的 [Hermes Agent](https://github.com/NousResearch/hermes-agent) 实例，
通过飞书交互。Hermes 运行在独立的 Karpenter 节点池中（不使用 Kata
Containers），通过外部 LiteLLM 代理访问模型。

**前置条件：**
- Step 1-3 已完成
- 已有可用的 LiteLLM 代理（URL + Bearer token）
- 已创建 **独立** 的飞书应用（不能复用 Step 3 的 OpenClaw 飞书 App）

**必填环境变量：**

| 变量 | 示例 | 用途 |
|------|------|------|
| `LITELLM_BASE_URL` | `https://litellm.example.com` | LiteLLM 代理地址（不含 `/v1`） |
| `LITELLM_API_KEY` | `sk-xxxx...` | LiteLLM Bearer token |
| `FEISHU_APP_ID` | `cli_xxxxxxxx` | 飞书应用 ID（Hermes 专用） |
| `FEISHU_APP_SECRET` | `xxxxxxxxxxxx` | 飞书应用 Secret |

**可选环境变量：**

| 变量 | 默认 | 说明 |
|------|------|------|
| `HERMES_MODEL` | `claude-sonnet-4-5` | LiteLLM 中的模型名 |
| `HERMES_IMAGE` | `${ECR_REGISTRY}/nousresearch/hermes-agent:latest` | Hermes 镜像 |
| `KARPENTER_VERSION` | `1.9.0` | Karpenter Helm chart 版本 |
| `AGENT_SANDBOX_VERSION` | `v0.3.10` | agent-sandbox 上游 release tag |

**执行：**

```bash
export STACK_NAME=openclaw-cn-workshop
export REGION=cn-northwest-1
export LITELLM_BASE_URL="https://litellm.example.com"
export LITELLM_API_KEY="sk-..."
export FEISHU_APP_ID="cli_..."
export FEISHU_APP_SECRET="..."

bash scripts/step4-hermes-sandbox.sh
```

**部署的内容：**
1. Karpenter Helm chart 到 `kube-system`（IRSA 绑定已由 CFN 创建的
   `KarpenterControllerRole`）
2. `sandbox` NodePool + EC2NodeClass（arm64, m6g/m7g on-demand，
   taint `sandbox=true:NoSchedule`）
3. Agent Sandbox Controller (`v0.3.10`) 到 `agent-sandbox-system` —
   controller 镜像从 `${ECR_REGISTRY}/agent-sandbox/agent-sandbox-controller`
   镜像源拉（原 `registry.k8s.io` 在 CN 不稳）
4. `hermes` namespace + 两个 Secret（`hermes-litellm-key`、`hermes-feishu`）
5. `hermes-config` ConfigMap（`config.yaml` 指向 LiteLLM）
6. `hermes-feishu-sandbox` Sandbox CRD 实例（nodeSelector 和
   toleration 匹配 sandbox nodepool）

**验证：**

```bash
kubectl get nodepool sandbox
kubectl -n hermes get sandbox,pod
kubectl -n hermes logs -l sandbox=hermes-feishu-sandbox -f --tail=50
```

成功启动后日志中会出现 `[Lark] connected to wss://msg-frontier.feishu.cn/...`。
在飞书中给 Hermes bot 发 "hello" 应收到回复。

## 端到端测试

```bash
# 端口转发
kubectl port-forward svc/platform-api -n openclaw-platform 8890:8890 &

# 运行测试 (需要 playwright: pip install playwright && playwright install chromium)
python3 scripts/e2e-test.py
```

测试覆盖：登录、Dashboard、Tenant CRUD、Agent CRUD、Usage/Billing/Quota、Members、Admin Overview、Web Console 导航。

## 销毁

```bash
# 预览 (不删除)
DRY_RUN=true bash scripts/destroy.sh

# 真正删除 (需要输入 'destroy' 确认)
bash scripts/destroy.sh
```

逆序销毁：Step 4 (Hermes + Karpenter，若部署) → Step 3 (Platform API) → Step 2 (K8s 组件) → Pod Identity → Step 1 (CloudFormation Stack)

## 架构

```
┌─────────────────────────────────────────────────────┐
│                    AWS CN (cn-northwest-1)           │
│                                                     │
│  ┌──────────── VPC (172.31.0.0/16) ──────────────┐ │
│  │                                                │ │
│  │  ┌─── EKS Cluster (Graviton ARM64) ─────────┐ │ │
│  │  │                                           │ │ │
│  │  │  openclaw-platform/                       │ │ │
│  │  │    platform-api (2 replicas) ──── NLB     │ │ │
│  │  │                                           │ │ │
│  │  │  openclaw-operator-system/                │ │ │
│  │  │    openclaw-operator v0.26.2              │ │ │
│  │  │                                           │ │ │
│  │  │  tenant-*/                                │ │ │
│  │  │    openclaw agent instances               │ │ │
│  │  │                                           │ │ │
│  │  │  [可选 Step 4]                             │ │ │
│  │  │  agent-sandbox-system/                    │ │ │
│  │  │    agent-sandbox-controller v0.3.10       │ │ │
│  │  │  hermes/  (Karpenter sandbox nodepool)    │ │ │
│  │  │    hermes-feishu-sandbox (Sandbox CRD)    │ │ │
│  │  └───────────────────────────────────────────┘ │ │
│  │                                                │ │
│  │  RDS PostgreSQL 16 ◄──── Platform API          │ │
│  │  SQS Usage Events  ◄──── Metrics Exporter      │ │
│  │  EFS ◄──── Agent PVCs                          │ │
│  └────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────┘
```

## 镜像来源

所有镜像从 `public.ecr.aws/i4x4j7g8/openclaw-saas/` 拉取（中国区可访问）：

| 镜像 | Tag | 用途 |
|------|-----|------|
| `openclaw-saas-platform` | `v0.9.61` | Platform API |
| `openclaw-saas-billing-consumer` | `v0.1.1` | Billing Consumer |
| `openclaw-saas-metrics-exporter` | `v0.3.1` | Metrics Exporter Sidecar |
| `openclaw-rocks/openclaw-operator` | `v0.26.2` | OpenClaw Operator |
| `openclaw/openclaw` | `2026.4.14` | Agent Runtime (operator 默认) |
| `openclaw-custom` | `2026.4.14` | Agent Runtime (workshop 默认，含 kiro-cli/acpx) |
| `chromedp/headless-shell` | `stable` | Browser Sidecar |
| `otel/opentelemetry-collector` | `0.120.0` | OTLP metrics sidecar |
| `nousresearch/hermes-agent` | `latest` | Step 4: Hermes Agent |
| `agent-sandbox/agent-sandbox-controller` | `v0.3.10` | Step 4: Agent Sandbox controller |
