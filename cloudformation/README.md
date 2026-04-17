# 运行 cloudlab-template-china-ec2.yaml
# 使用EC2环境手动安装指南

通过 SSH 登录 EC2 后，按以下步骤安装所需工具。

> 以下命令假设 EC2 使用 Amazon Linux 2023 (x86_64)，以 `ec2-user` 身份登录。

## 1. 系统工具

```bash
sudo yum install -y git tar gzip vim nodejs npm make gcc g++ jq unzip
```

## 2. AWS CLI v2

```bash
curl -fsSL "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o /tmp/awscliv2.zip
unzip -qo /tmp/awscliv2.zip -d /tmp
sudo /tmp/aws/install --update
rm -rf /tmp/aws /tmp/awscliv2.zip
aws --version
```

## 3. kubectl

```bash
curl -fsSL https://dqz9mdd9dvd79.cloudfront.net/tools/kubectl -o /tmp/kubectl
sudo mv /tmp/kubectl /usr/local/bin/kubectl
sudo chmod +x /usr/local/bin/kubectl
kubectl version --client
```

## 4. Helm

```bash
curl -fsSL https://dqz9mdd9dvd79.cloudfront.net/tools/helm.tar.gz -o /tmp/helm.tar.gz
tar xzf /tmp/helm.tar.gz -C /tmp
sudo mv /tmp/linux-amd64/helm /usr/local/bin/helm
sudo chmod +x /usr/local/bin/helm
rm -rf /tmp/helm.tar.gz /tmp/linux-amd64
helm version --short
```

## 5. eksctl

```bash
curl -fsSL https://dqz9mdd9dvd79.cloudfront.net/tools/eksctl.tar.gz -o /tmp/eksctl.tar.gz
sudo tar xzf /tmp/eksctl.tar.gz -C /usr/local/bin
sudo chmod +x /usr/local/bin/eksctl
rm -f /tmp/eksctl.tar.gz
eksctl version
```

## 6. 配置环境变量

根据实际部署的 Region 和集群名称修改：

```bash
# 替换为实际值
export AWS_REGION=cn-northwest-1
export EKS_CLUSTER_NAME=openclaw-prod

# 写入 profile 持久化
cat <<'EOF' | sudo tee /etc/profile.d/openclaw_env.sh
export PROMPT_COMMAND='export PS1="\u@openclaw-ide:\w$ "'
export AWS_REGION=$AWS_REGION
export EKS_CLUSTER_NAME=$EKS_CLUSTER_NAME
EOF
source /etc/profile.d/openclaw_env.sh
```

## 7. 配置 kubeconfig

```bash
aws eks update-kubeconfig --name $EKS_CLUSTER_NAME --region $AWS_REGION
kubectl get nodes
```

## 8. 验证所有工具

```bash
echo "=== Tool versions ==="
aws --version
kubectl version --client
helm version --short
eksctl version
echo "=== Cluster access ==="
kubectl get nodes
```
