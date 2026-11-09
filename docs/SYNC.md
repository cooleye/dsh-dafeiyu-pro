# 同步到 GitHub

本项目每次有改动都同步到 [cooleye/dsh-dafeiyu-pro](https://github.com/cooleye/dsh-dafeiyu-pro)。

## 快速推送

```powershell
# 在仓库根目录执行
git add -A
git commit -m "feat: 描述你改了什么"
git push
```

或者用自带的辅助脚本：

```powershell
.\scripts\sync.ps1 "feat: 描述你改了什么"
```

## 远程配置

远程地址用 **SSH**（token 不落盘）：

```bash
git remote -v
# origin  git@github.com:cooleye/dsh-dafeiyu-pro.git (fetch)
# origin  git@github.com:cooleye/dsh-dafeiyu-pro.git (push)
```

如果没有配 SSH，可以用 HTTPS + token，但**不要把 token 写进 remote URL**，改用凭据管理器：

```powershell
git config --global credential.helper manager
```

## 与上游同步

上游偶尔会更新，可以这样拉取：

```bash
# 加一个 upstream 远程（只需一次）
git remote add upstream https://github.com/QCYTSN/dsh-dafeiyu.git

# 拉取上游
git fetch upstream

# 看看上游有什么新东西
git log --oneline upstream/main -10

# 合并（注意冲突，我们改了 package.json / cordis.patch.yml / README）
git merge upstream/main
```

### 上游合并时的注意点

我们改动了这些上游也有的文件，合并时容易冲突：

| 文件 | 我们的改动 |
|---|---|
| `package.json` | name / author / repository / keywords |
| `cordis.patch.yml` | 插件 id 改为 `dsh-dafeiyu-pro` |
| `README.md` | 完全重写 |
| `README_EN.md` | 待处理 |
| `.gitignore` | 扩展了语音开发相关条目 |

**建议**：合并时优先接受上游的实现改动，保留我们的元数据改动。

## 提交信息规范

用 [Conventional Commits](https://www.conventionalcommits.org/)：

| 前缀 | 用途 |
|---|---|
| `feat:` | 新功能 |
| `fix:` | 修 bug |
| `docs:` | 文档 |
| `refactor:` | 重构（不改行为） |
| `test:` | 测试 |
| `chore:` | 构建/工具 |
| `perf:` | 性能 |

示例：

```
feat: 唤醒词检测跑通，支持配置关键词
fix: 修复 ASR 会话被误清空导致识别中断
docs: 补充阿里云 WebSocket 协议说明
```
