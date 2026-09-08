#!/usr/bin/env bash
# 一键推送到 GitHub（Windows 用 Git Bash 运行：bash tools/push_github.sh <你的用户名>）
#
# 前置条件（必须先做，脚本做不了这两步，需要在网页上操作）：
#   1) GitHub 网页已建好 public 仓库（名称默认 yang_newspipeline，不要勾 README/.gitignore/license）
#   2) 已生成 Personal access token（classic），只勾 repo，复制好 ghp_ 开头的那串
#
# 用法：
#   bash tools/push_github.sh yzh                # 仓库名默认 yang_newspipeline
#   bash tools/push_github.sh yzh my-news        # 指定仓库名
#
# 脚本依次做四件事：
#   1) 检查工作区是否干净（有未提交改动会先提示）
#   2) git remote add origin <地址>   —— 把远端地址登记为别名 origin（纯本地配置）
#   3) git branch -M main             —— 统一分支名为 main
#   4) git push -u origin main        —— 上传全部提交；-u 记住对应关系，以后只需 git push

set -e

USER_NAME="$1"
REPO_NAME="${2:-yang_newspipeline}"

if [ -z "$USER_NAME" ]; then
  echo "用法：bash tools/push_github.sh <你的GitHub用户名> [仓库名]"
  exit 1
fi

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "==> 项目目录：$ROOT"
echo "==> 目标仓库：https://github.com/$USER_NAME/$REPO_NAME.git"
echo

# 1) 工作区检查
if [ -n "$(git status --porcelain)" ]; then
  echo "!! 工作区有未提交的改动，先提交再推送："
  git status --short
  echo
  echo "   执行： git add -A && git commit -m \"说明\" "
  exit 1
fi
echo "[1/4] 工作区干净 ✓"

# 2) 远端登记
if git remote get-url origin >/dev/null 2>&1; then
  OLD="$(git remote get-url origin)"
  echo "[2/4] 已存在远端 origin：$OLD"
  echo "      如需更换，先执行： git remote remove origin"
else
  git remote add origin "https://github.com/$USER_NAME/$REPO_NAME.git"
  echo "[2/4] 已登记远端 origin ✓"
fi

# 3) 分支名
git branch -M main
echo "[3/4] 当前分支：$(git rev-parse --abbrev-ref HEAD) ✓"

# 4) 推送（这里会要求认证：用户名/GitHub 用户名，密码填 ghp_ 令牌）
echo "[4/4] 开始推送……（弹窗时 Username 填 GitHub 用户名，Password 粘贴 ghp_ 令牌）"
git push -u origin main

echo
echo "==> 推送完成。接下来在 GitHub 网页做两步设置："
echo "    1) Settings → Actions → General → Workflow permissions → Read and write → Save"
echo "    2) Settings → Pages → Source: Deploy from a branch → 分支 main / 目录 /docs → Save"
echo
echo "==> 订阅地址（Pages 生效约 1-2 分钟后可用）："
echo "    https://$USER_NAME.github.io/$REPO_NAME/feed.xml"
echo
echo "==> 验证：仓库页 Actions → daily-news-digest → Run workflow，对照 DEPLOY.md 的验收清单核对。"
