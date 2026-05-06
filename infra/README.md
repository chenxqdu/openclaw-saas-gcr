# OpenClaw SaaS Infrastructure — China Region

AWS CDK stacks + Kubernetes manifests + 部署自动化脚本，部署 OpenClaw SaaS 到 EKS (cn-northwest-1)。

## 目录结构

| 目录 | 说明 |
|------|------|
| `cdk/` | CDK stacks: VPC, EKS, EFS, RDS, S3, SQS, IAM, Karpenter |
| `k8s/platform/` | Kubernetes manifests (deployment, service, rbac, ingress) |
| `k8s-platform/` | 平台级 K8s 资源 (billing-consumer, ingress, karpenter, operator, storage) |
| `scripts/deploy.sh` | 自动化部署脚本 |
| `observability/` | Prometheus + Grafana 配置 |
| `docs/` | 架构图、Runbook |
| `.env.cn` | 中国区环境变量模板 |

## 配置

### `.env` — 配置中心

所有部署配置集中在 `.env`，`deploy.sh` 自动加载。

```bash
cp .env.cn .env
vim .env    # 填写 ADMIN_PASSWORD, JWT_SECRET, AWS_ACCOUNT_ID
```

详细变量说明见根目录 [README.md](../README.md#env--配置中心)。

### `cdk.json` — 基础设施参数

实例类型、集群规模、VPC CIDR 等在 `cdk/cdk.json` context 中配置。

关键参数：

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `cluster_name` | `openclaw-prod` | EKS 集群名 |
| `eks_version` | `1.31` | Kubernetes 版本 |
| `eks_node_instance_type` | `m6g.xlarge` | 节点实例类型 (Graviton) |
| `eks_node_min/max/desired` | 2/4/2 | 节点组伸缩配置 |
| `vpc_cidr` | `172.31.0.0/16` | VPC CIDR |
| `vpc_max_azs` | 3 | 可用区数量 |
| `db_instance_class` | `db.t4g.medium` | RDS 实例类型 |

## 部署

### 前置条件

- AWS CLI 已配置 (`~/.aws/config`)
- CDK CLI, kubectl, helm, jq, Docker with buildx
- IAM 身份具备 Admin 或足够权限

### 完整部署

```bash
export AWS_PROFILE=default
export AWS_DEFAULT_REGION=cn-northwest-1

# 1. 配置
cd infra && cp .env.cn .env && vim .env

# 2. CDK 部署
cd cdk && python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
cdk bootstrap aws://${AWS_ACCOUNT_ID}/${AWS_DEFAULT_REGION}

cdk deploy openclaw-saas-vpc --require-approval never
cdk deploy openclaw-saas-karpenter-node openclaw-saas-sqs openclaw-saas-s3 --require-approval never --concurrency 3
cdk deploy openclaw-saas-eks --require-approval never                    # ~15 min
cdk deploy openclaw-saas-efs --require-approval never
cdk deploy openclaw-saas-iam openclaw-saas-rds --require-approval never --concurrency 2
cd ..

# 3. 构建 & 推送镜像（见根目录 README）

# 4. 部署 K8s 组件 + 平台
./scripts/deploy.sh --skip-cdk
```

> **EC2 IAM Role 用户注意：** 如果在 EC2 上手动执行 `cdk deploy`（而非通过 `deploy.sh`），需要额外传入 role ARN：
> ```bash
> cdk deploy openclaw-saas-eks -c deployer_role_arn=arn:aws-cn:iam::123456789012:role/MyEC2Role
> ```
> 如果改用 `./scripts/deploy.sh`（不带 `--skip-cdk`）来部署，脚本会自动检测当前 IAM Role 并传入，无需手动指定。

### 仅更新平台

```bash
./scripts/deploy.sh --skip-cdk
```

### CDK Stacks

| Stack | 资源 | 部署时间 |
|-------|------|---------|
| vpc | VPC, 3 AZ, NAT, VPC Endpoints (S3/ECR/STS) | ~3 min |
| karpenter-node | Karpenter Node Role + Instance Profile | ~30s |
| sqs | Usage Events + DLQ + Karpenter 中断队列 + 4 条 EventBridge 规则 | ~30s |
| s3 | 备份桶 + Backup Role (Pod Identity) | ~30s |
| eks | EKS 集群 (L1 CfnCluster), 节点组, Addons, OIDC, Access Entry | ~15 min |
| efs | EFS 共享存储 + Mount Targets | ~2 min |
| iam | ALB Controller (IRSA), Karpenter Controller (IRSA), EFS CSI (Pod Identity), Platform API (Pod Identity), 节点 SQS/ALB 权限 | ~1 min |
| rds | PostgreSQL 16, db.t4g.medium, gp3 加密 | ~5 min |

> **注意：** CDK 使用 L1 construct (CfnCluster)，不创建 Lambda。EKS 集群创建者是你的 IAM 身份，`kubectl` 直接可用。ECR 仓库由 `deploy.sh` 创建（不在 CDK 中）。

## deploy.sh 流程

```
main()
  ├── check_prerequisites
  │
  ├── deploy_cdk (--skip-cdk 跳过)
  │     ├── 激活 Python venv（不存在则自动创建）
  │     ├── cdk bootstrap
  │     ├── 自动检测 deployer IAM Role → 传入 CDK 创建 Access Entry
  │     └── cdk deploy --all（VPC, EKS, EFS, RDS, SQS, S3, IAM, Karpenter）
  │
  ├── create_ecr_repos（始终执行，幂等）
  │     ├── 平台镜像仓库（platform, metrics-exporter, billing-consumer）
  │     ├── Mirror 仓库（openclaw, nginx, uv, otel-collector, operator, alb-controller）
  │     └── Helm chart 仓库（charts/aws-load-balancer-controller, charts/openclaw-operator）
  │
  └── K8s 部署 (--skip-k8s 跳过)
        ├── configure_kubectl         ← 无需 --role-arn
        ├── ensure_storage_class      ← apply gp3 StorageClass（EBS CSI Driver）
        ├── install_alb_controller    ← 优先从 CN ECR 安装 chart，配置 IRSA
        ├── install_openclaw_operator ← 优先从 CN ECR 安装 chart
        ├── create_platform_secret    ← 从 CDK/CF 输出 + .env 组装 K8s Secret
        ├── deploy_platform_api       ← 自动检测 NLB SG → LoadBalancer / ClusterIP
        ├── deploy_billing_consumer
        └── verify_deployment         ← 输出 NLB / Ingress 访问地址
```

**参数组合：**

| 命令 | 效果 |
|------|------|
| `deploy.sh` | 全量部署：CDK + ECR + K8s |
| `deploy.sh --skip-cdk` | 跳过 CDK，只部署 ECR + K8s（基础设施已存在时） |
| `deploy.sh --skip-k8s` | 只部署 CDK + ECR，不操作 K8s（镜像还没准备好时） |

## EKS 访问

CDK 部署时自动检测当前 IAM 身份，通过 EKS Access Entry 授予 cluster-admin 权限。无需手动 assume role 或修改 trust policy。

```bash
# 直接访问，无需 --role-arn
aws eks update-kubeconfig --name openclaw-prod --region cn-northwest-1
kubectl get nodes
```

## 与 CloudFormation 模板的关系

`cloudformation/cloudformation-ec2.yaml` 和 CDK 创建的资源完全一致：

| 资源 | CloudFormation | CDK |
|------|---------------|-----|
| VPC (3 AZ, NAT) | ✅ | ✅ |
| EKS + Addons + Pod Identity Agent | ✅ | ✅ |
| EFS | ✅ | ✅ |
| Karpenter (Node Role + Controller + SQS + EventBridge) | ✅ | ✅ |
| ALB Controller IAM (IRSA) | ✅ | ✅ |
| RDS PostgreSQL 16 | ✅ | ✅ |
| SQS (Usage + DLQ) | ✅ | ✅ |
| S3 + Backup Role | ✅ | ✅ |
| EC2 跳板机 | ✅ | ❌ (不需要) |
| ECR 仓库 | ❌ | ❌ (deploy.sh 创建) |

CloudFormation 额外包含 EC2 跳板机（含完整 IAM 权限），适合 Workshop 场景。CDK 适合生产环境持续迭代。

## 销毁

```bash
export AWS_PROFILE=default AWS_DEFAULT_REGION=cn-northwest-1

# 1. 清理 K8s 资源
kubectl delete openclawinstance --all --all-namespaces
kubectl delete ns openclaw-platform

# 2. CDK 反序销毁
cd cdk
cdk destroy openclaw-saas-rds openclaw-saas-iam --force
cdk destroy openclaw-saas-efs --force
# 清空 S3 桶
BUCKET=$(aws s3 ls | grep openclaw-backups | awk '{print $3}')
aws s3 rm s3://${BUCKET} --recursive
cdk destroy openclaw-saas-s3 --force
cdk destroy openclaw-saas-eks --force    # ~15-20 min
cdk destroy openclaw-saas-sqs openclaw-saas-karpenter-node --force
cdk destroy openclaw-saas-vpc --force

# 3. 手动删除 ECR 仓库
for repo in openclaw-saas-platform openclaw-saas-metrics-exporter openclaw-saas-billing-consumer; do
  aws ecr delete-repository --repository-name ${repo} --force --region cn-northwest-1
done
```

> ECR 仓库含镜像和 S3 桶含数据时不会被自动删除，需手动清理。

## 成本估算 (CN)

~$150-200/月：

| 资源 | 月费 |
|------|------|
| EKS 控制面 | ~$73 |
| 2× m6g.xlarge 节点 | ~$60 |
| RDS db.t4g.medium | ~$25 |
| NAT Gateway | ~$32 |
| 其他 (S3, SQS, EFS, VPC Endpoints) | ~$20 |
