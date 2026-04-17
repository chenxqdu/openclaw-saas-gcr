# OpenClaw Custom Image

基于 `ghcr.io/openclaw/openclaw:latest` 的自定义镜像，预装 Kiro CLI + Tavily Search skill。

## 运行时文件系统

Operator 设置 `readOnlyRootFilesystem=true`，`HOME=/home/openclaw`。

| 路径 | 来源 | 读写 | 用途 |
|------|------|------|------|
| `/home/openclaw/` | image layer | ❌ 只读 | HOME 目录 |
| `/home/openclaw/.openclaw/` | PVC 挂载 | ✅ 可写 | OpenClaw 数据根目录 |
| `/home/openclaw/.openclaw/workspace/` | PVC | ✅ 可写 | 用户工作空间 |
| `/home/openclaw/.openclaw/skills/` | PVC | ✅ 可写 | 用户 skills（init container 拷入） |
| `/home/openclaw/.openclaw/identity/` | PVC | ✅ 可写 | 设备身份（gateway pairing 用） |
| `/home/openclaw/.openclaw/devices/` | PVC | ✅ 可写 | 已配对/待配对设备列表 |
| `/home/openclaw/.cache/` | PVC subPath | ✅ 可写 | npm/npx 缓存 |
| `/home/openclaw/.local/` | PVC subPath | ✅ 可写 | 用户本地数据 |
| `/home/openclaw/.kiro/` | image layer | ❌ 只读 | Kiro agent 配置 |
| `/home/openclaw/.acpx` | symlink → `.openclaw/.acpx` | ✅ 可写 | acpx session 数据 |
| `/app/` | image layer | ❌ 只读 | OpenClaw 代码 + 内置 extensions |
| `/app/extensions/acpx/node_modules/` | image layer | ❌ 只读 | 预装的 acpx binary |
| `/opt/openclaw-custom/` | image layer | ❌ 只读 | PVC 内容暂存区（init container 源） |
| `/tmp/` | emptyDir | ✅ 可写 | 临时文件 |

> **注意**：`/home/node/` 是基础镜像的原始 HOME 目录，运行时无人使用，不要往这里写任何内容。

## Dockerfile 职责

Dockerfile **只负责** bake 到 image layer 的内容：

1. **系统工具** → `/usr/local/bin/kiro-cli`、`jq`
2. **acpx 预装** → `/app/extensions/acpx/node_modules/`（匹配 pinned version）
3. **Kiro 配置** → `/opt/openclaw-custom/.kiro/`（init container 拷到 PVC）
4. **acpx symlink** → `/home/openclaw/.acpx` → `.openclaw/.acpx`
5. **PVC 内容暂存** → `/opt/openclaw-custom/skills/`、`KIRO-PLAYBOOK.md`、`kiro-wrapper.sh`

**不要**在 Dockerfile 中往 PVC 路径（`/home/openclaw/.openclaw/`）写文件，build time 写入会被运行时 PVC 挂载覆盖。

**不要**覆盖 `ENTRYPOINT` 或 `CMD`。基础镜像的 entrypoint 是 `docker-entrypoint.sh`，CMD 是 `node openclaw.mjs gateway --allow-unconfigured`。Docker 行为：子镜像设置 ENTRYPOINT 会清空基础镜像的 CMD。

## Init Container 职责

`init-custom` 容器在每次 pod 启动时执行，使用 custom image 自身作为镜像：

```sh
# 1. 拷贝 skills、kiro 配置、playbook、wrapper 到 PVC
mkdir -p /data/skills /data/.acpx/sessions /data/.kiro
cp -r /opt/openclaw-custom/skills/* /data/skills/
cp -r /opt/openclaw-custom/.kiro/* /data/.kiro/
cp /opt/openclaw-custom/KIRO-PLAYBOOK.md /data/workspace/KIRO-PLAYBOOK.md
cp /opt/openclaw-custom/.kiro-wrapper.sh /data/.kiro-wrapper.sh

# 2. 清理旧的 identity/devices（防止 pod 重启后配对不一致）
rm -rf /data/identity /data/devices

# 3. 向 AGENTS.md 注入 Kiro 引导（幂等）
grep -q 'KIRO-PLAYBOOK' /data/workspace/AGENTS.md || \
  printf '\n## Kiro 调用\n...\n' >> /data/workspace/AGENTS.md

# 4. 修正文件所有权
chown -R 1000:1000 /data/skills /data/.acpx /data/.kiro /data/.kiro-wrapper.sh
```

Init container 的 `/data/` 挂载点对应运行时的 `/home/openclaw/.openclaw/`（同一块 PVC）。

## Gateway Pairing（已知问题）

OpenClaw gateway 使用设备配对机制。`sessions_spawn`（acpx 调用 Kiro）连接 gateway 时需要已配对的设备身份。

**现状**：`openclaw.json` 中配置了 `gateway.mode: "local"`，但仍然需要手动完成设备配对。新创建的 agent 首次 `sessions_spawn` 会报 `pairing required (1008)`。

**临时解决方法**：在 pod 内手动执行配对脚本：

```bash
kubectl exec -n <namespace> <pod> -- bash -c '
  HOME=/home/openclaw/.openclaw
  # 等待 identity 生成（首次 sessions_spawn 触发）
  # 然后手动将 pending 设备批准到 paired
  node -e "
    const fs = require(\"fs\");
    const pending = JSON.parse(fs.readFileSync(\"$HOME/devices/pending.json\"));
    const paired = {};
    for (const [k, v] of Object.entries(pending)) {
      paired[v.deviceId] = { ...v, pairedAt: Date.now() };
    }
    fs.writeFileSync(\"$HOME/devices/paired.json\", JSON.stringify(paired, null, 2));
    fs.writeFileSync(\"$HOME/devices/pending.json\", \"{}\");
    console.log(\"Paired\", Object.keys(paired).length, \"devices\");
  "
'
```

**根因**：gateway 的 device pairing 是纯被动的（pending → 需要显式 approve → paired），没有自动批准逻辑。`gateway.mode=local` 不触发 auto-self-pair。

## acpx 特殊处理

acpx 需要写 session 数据到 `~/.acpx/`，但 `/home/openclaw/` 是只读的。解决方案：

1. **Dockerfile**：创建 symlink `/home/openclaw/.acpx → /home/openclaw/.openclaw/.acpx`
2. **Init Container**：在 PVC 上创建 `.acpx/sessions/` 目录
3. **运行时**：acpx 写 `~/.acpx/sessions/` → 跟随 symlink → 写入 PVC ✅

## kiro-wrapper.sh

Kiro CLI 在 ACP 模式下需要写 `~/.kiro/sessions/`。由于 `/home/openclaw/.kiro/` 是只读 image layer，需要 wrapper 脚本重定向 HOME：

```bash
#!/bin/bash
HOME=/home/openclaw/.openclaw exec kiro-cli "$@"
```

这样 kiro-cli 写入 `/home/openclaw/.openclaw/.kiro/sessions/`（PVC，可写）。

## 预装的 acpx 版本

基础镜像的 `/app/extensions/acpx/src/config.ts` 定义了 `ACPX_PINNED_VERSION`。Dockerfile 中用 `grep` 提取并安装匹配版本。版本不匹配会触发运行时 re-install → 因 readOnlyRootFilesystem 失败 → ACP 不可用。

升级基础镜像时，检查新的 pinned version 并更新 Dockerfile。

## 构建

```bash
# arm64 (Graviton)
docker build --platform linux/arm64 \
  -t public.ecr.aws/h4t8a9b8/openclaw-saas/openclaw-custom:TAG \
  -t public.ecr.aws/h4t8a9b8/openclaw-saas/openclaw-custom:latest \
  .

# 推送
aws ecr-public get-login-password --region us-east-1 | docker login --username AWS --password-stdin public.ecr.aws/h4t8a9b8
docker push public.ecr.aws/h4t8a9b8/openclaw-saas/openclaw-custom:TAG
docker push public.ecr.aws/h4t8a9b8/openclaw-saas/openclaw-custom:latest
```

## 部署更新

镜像 `pullPolicy: Always`，删除 pod 即可拉取最新镜像：

```bash
kubectl delete pod -n tenant-<name> <agent>-0
```
