#!/usr/bin/env bash
# 本地复现 build.yml 的「算发布版本与发布说明」那一步（去掉 GitHub 专有的
# $GITHUB_ENV / $RUNNER_TEMP 注入，判据逐字照抄），用来在推送前验证
# sed / awk / grep 真的能算出正确的 tag 与正文。
#
# 为什么要有这个脚本：workflow 里的 awk 之前写错过一次 —— 进入小节后没有把 s 关掉，
# 而 awk 对同一行会**依次**判每条规则，于是后面的 `### ` 又命中「第一条小节」规则，
# 结果整份 CHANGELOG 都被当成发布正文（742 行）。这种错在 CI 上不会报失败，
# 只会安静地发一个正文全是历史版本的 release。所以本地跑一遍，红就别推。
#
# 运行：& "D:\Git\bin\bash.exe" localtest/check_release_step.sh
set -euo pipefail
RUNNER_TEMP="$(mktemp -d)"
cd "$(dirname "$0")/.."

VER=$(sed -n 's/^version: \([0-9][0-9.]*\)+[0-9]*/\1/p' pubspec.yaml | head -1)
BUILD=$(sed -n 's/^version: [0-9][0-9.]*+\([0-9]*\).*/\1/p' pubspec.yaml | head -1)
if [ -z "$VER" ] || [ -z "$BUILD" ]; then
  echo "::error::pubspec.yaml 里没解析出 version（VER=$VER BUILD=$BUILD）"
  exit 1
fi
awk '/^## \[未发布\]/{s=1; next} s && /^### /{t=1; s=0; print; next} t && /^#/{exit} t{print}' \
  CHANGELOG.md > "$RUNNER_TEMP/section.md"
if [ ! -s "$RUNNER_TEMP/section.md" ]; then
  echo "::error::CHANGELOG 没有与本次发布对应的「未发布」小节，拒绝发布空正文的 release"
  exit 1
fi
LINES=$(wc -l < "$RUNNER_TEMP/section.md")
if [ "$LINES" -gt 80 ]; then
  echo "::error::发布正文抽了 $LINES 行（>80），节选规则坏了"
  exit 1
fi
if ! grep -q "v$VER" "$RUNNER_TEMP/section.md"; then
  echo "::error::CHANGELOG 首节未提及 v$VER（pubspec 当前版本），发布说明与包不是同一版"
  exit 1
fi
echo "awk=$(awk --version 2>&1 | head -1)"
echo "RELEASE_TAG=v$VER  构建号=$BUILD  正文=$LINES 行"
echo "--- 正文首 3 行 ---"
head -3 "$RUNNER_TEMP/section.md"
