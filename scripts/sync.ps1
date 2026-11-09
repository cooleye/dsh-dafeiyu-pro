# 一键同步到 GitHub
#
# 用法：
#   .\scripts\sync.ps1 "feat: 描述你改了什么"
#   .\scripts\sync.ps1 "fix: 修了什么" -Push
#
# 不带 -Push 只提交不推送；带了才推。

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$Message,

    [switch]$Push
)

$ErrorActionPreference = 'Stop'

# 确保在仓库根目录
$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

if (-not (Test-Path '.git')) {
    Write-Error "当前目录不是 git 仓库：$repoRoot"
    exit 1
}

# 检查有没有改动
$changes = git status --porcelain
if ([string]::IsNullOrWhiteSpace($changes)) {
    Write-Host '没有需要提交的改动。' -ForegroundColor Yellow
    exit 0
}

Write-Host '=== 即将提交的改动 ===' -ForegroundColor Cyan
git status --short | ForEach-Object { Write-Host "  $_" }

git add -A
git commit -m $Message

if ($LASTEXITCODE -ne 0) {
    Write-Error '提交失败'
    exit 1
}

Write-Host "`n已提交：" -ForegroundColor Green
git log --oneline -1

if ($Push) {
    Write-Host "`n推送到 origin/main ..." -ForegroundColor Cyan
    git push origin main
    if ($LASTEXITCODE -ne 0) {
        Write-Error '推送失败。检查 SSH 密钥或网络。'
        exit 1
    }
    Write-Host '推送完成。' -ForegroundColor Green
} else {
    Write-Host "`n（未推送。加 -Push 参数可直接推送到 GitHub）" -ForegroundColor Yellow
}
