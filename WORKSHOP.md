# OpenClaw SaaS - CN Workshop Deployment

一键部署 OpenClaw SaaS 平台到 AWS CN 区（cn-northwest-1）。

## 目录结构

```
cloudformation/
  cloudlab-template-china.yaml    ← Step 1: CFN 全栈模板 (EKS+RDS+SQS+VPC+IAM+EFS)
scripts/
  step2-k8s-components.sh         ← Step 2: K8s 组件部署脚本
  step3-platform-api.sh           ← Step 3: Platform API 部署脚本
  destroy.sh                      ← 全栈销毁脚本
  e2e-test.py                     ← Playwright 端到端测试
yaml/
  storage-classes.yaml            ← efs-sc + gp3 StorageClass
  openclaw-crd.yaml               ← OpenClaw CRD (openclawinstances.openclaw.rocks)
  openclaw-operator.yaml          ← OpenClaw Operator v0.20.0
  platform-api.yaml               ← Platform API Deployment + NLB Service
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

逆序销毁：Step 3 (Platform API) → Step 2 (K8s 组件) → Pod Identity → Step 1 (CloudFormation Stack)

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
│  │  │    openclaw-operator v0.20.0              │ │ │
│  │  │                                           │ │ │
│  │  │  tenant-*/                                │ │ │
│  │  │    openclaw agent instances               │ │ │
│  │  └───────────────────────────────────────────┘ │ │
│  │                                                │ │
│  │  RDS PostgreSQL 16 ◄──── Platform API          │ │
│  │  SQS Usage Events  ◄──── Metrics Exporter      │ │
│  │  EFS ◄──── Agent PVCs                          │ │
│  └────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────┘
```

## 镜像来源

所有镜像从 `public.ecr.aws/h4t8a9b8/openclaw-saas/` 拉取（中国区可访问）：

| 镜像 | Tag | 用途 |
|------|-----|------|
| `platform` | `v0.9.17` | Platform API |
| `openclaw-operator` | `v0.20.0` | OpenClaw Operator |
| `openclaw` | `latest` | Agent Runtime |
| `chromium` | `latest` | Browser Sidecar |
