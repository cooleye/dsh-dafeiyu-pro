# 上架到插件市场

DSH 的市场读的是社区精选列表 **Awesome DSH Plugin**：

```
dsh-plugin-catalog（npm 包）
    └── index.json ← 由 awesome-dsh-plugin 仓库自动生成
            ├── https://github.com/awesome-dsh-plugin/awesome-dsh-plugin
            └── https://awesome-dsh-plugin.com
```

所以「上架」= 往那个仓库提一个 PR，加**一个 YAML 文件**。
不需要联系官方，也不需要发邮件。

---

## 收录条件（逐条对照）

| 条件 | 我们 |
|---|---|
| `package.json` 声明 `dsh.bundle` | ✅ `{"bundle":{"patch":"./cordis.patch.yml"}}` |
| 仓库根有 `cordis.patch.yml` | ✅ |
| 真实可用的代码（非占位） | ✅ |
| 仓库创建满 **1 天** | ⏳ 见下方时间 |
| 仓库加 `dsh-plugin` topic | ❗待办 |
| 描述属实、不带营销词 | ✅ 见 `docs/catalog/*.yml` |
| 一个 PR 最多 3 条 | ✅ 我们只有 1 条 |

> ⚠️ 最常见被拒原因是**只声明了 `dsh.client`**。我们两个都声明了，没问题。

---

## 步骤

### 1. 给仓库加 topic（必做）

GitHub 仓库页 → 右上 ⚙️（About 旁的齿轮）→ Topics 填 `dsh-plugin` → 保存。

或命令行：

```powershell
$u = 'https://api.github.com/repos/cooleye/dsh-dafeiyu-pro/topics'
Invoke-WebRequest -Uri $u -Method Put -Headers @{
  Authorization = "Bearer $env:GITHUB_TOKEN"
  Accept = 'application/vnd.github+json'
} -Body (@{ names = @('dsh-plugin','dsh','deepseek-harness','desktop-pet','voice') } | ConvertTo-Json)
```

### 2. 等仓库满 1 天

仓库创建于 `2026-11-09T23:26Z`，所以要等到：

```
2026-11-10T23:26Z  =  北京时间 2026-11-11 07:26
```

CI 会自动检查这一条。没到就提，会被 gate 拦下（过几小时自动重跑就过了，不用推空提交）。

### 3. 提 PR

1. Fork `awesome-dsh-plugin/awesome-dsh-plugin`
2. 新建文件 `data/plugins/cooleye__dsh-dafeiyu-pro.yml`
3. 内容照抄 [`docs/catalog/cooleye__dsh-dafeiyu-pro.yml`](catalog/cooleye__dsh-dafeiyu-pro.yml)
4. 提 PR，标题比如 `Add cooleye/dsh-dafeiyu-pro`

**只加这一个文件。** 两个 README 是自动生成的，不要手工编辑——
这是历史上 PR 互相冲突的主要原因。

### 4. 等合并

CI 会依次检查：条目数 → `dsh.bundle` → 仓库年龄 → awesome-lint。
全绿之后维护者会读代码核对描述，通过就合并。
合并后网站和 `dsh-plugin-catalog` 自动重建，市场里就能搜到了。

如果被打上 `held: author` 标签：**不是拒绝**，是让你改。改完往同一分支推一次，
留一条评论说明改了什么即可。

---

## 已经准备好的东西

| 文件 | 用途 |
|---|---|
| `screenshots.json` | 市场详情页的截图清单（市场按这个取图） |
| `assets/catalog/screenshot-1.png` | 「你说：…」+ 蓝色声波 |
| `assets/catalog/screenshot-2.png` | 「大肥鱼：…」+ 橙色声波 |
| `assets/catalog/icon-180.png` | 市场卡片图标 |
| `docs/catalog/cooleye__dsh-dafeiyu-pro.yml` | 要提交的那个条目文件 |

截图和图标是 `scripts/make_catalog_assets.py` 从 helper 的诊断快照裁出来的，想换就重跑：

```powershell
python scripts/make_catalog_assets.py
```

> 截图放在**自己仓库**里（相对路径），以后换图推自己仓库就行，不用再提 PR。

---

## 可选：发 npm

发 npm 不是收录条件，但有两个好处：

- 市场能显示下载量
- 用户装的时候是预构建包，跳过「构建授权」那一步

要发的话，`package.json` 的 `repository` 字段必须指回本仓库
（我们的已经指了），否则市场和包关联不上。

```powershell
npm publish --access public
```

⚠️ 发布前确认 `.npmignore` / `files` 没把 `lib/`、`assets/`、`runtime/` 漏掉。
`runtime/bin/` 里的 46MB exe 是 gitignore 的，从源码装的用户需要自己打包
（见 `docs/SYNC.md`），这也是推荐发 npm + 附带预构建 tarball 的原因。

---

## 常见打回原因

| 现象 | 原因 |
|---|---|
| `dsh.bundle` 检查失败 | 只声明了 `dsh.client` |
| 条目数/格式失败 | 一个 PR 加了超过 3 条，或 YAML 里 `: ` 没加引号 |
| 描述被打回 | 写了解释不了的数字或 API 名——评审会对着代码核 |
| PR 里改了别人的条目 | 只改自己那条 |
| 仓库年龄不够 | 等够 1 天，CI 会自动重跑 |
