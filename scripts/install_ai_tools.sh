#!/usr/bin/env bash
# AI 에이전트 도구 4종 설치 스크립트 (Graft, Agency Agents, Codebase Memory MCP, OpenMontage)
# 사용법:  bash scripts/install_ai_tools.sh            # 4종 모두
#         bash scripts/install_ai_tools.sh graft       # 하나만 (graft|agency|cbm|openmontage)
# 요구사항: Node 20+, Python 3.10+, git, curl. OpenMontage는 ffmpeg도 필요.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
TOOLS_DIR="${TOOLS_DIR:-$HOME/tools}"
mkdir -p "$TOOLS_DIR"

install_graft() {
  echo "==> [1/4] Graft (코드베이스 지도 + Claude Code MCP/훅)"
  npm install -g @nanonets/graft
  cd "$REPO_DIR"
  # .claude/ 와 .mcp.json 은 이미 커밋돼 있으므로 그래프만 다시 생성
  graft build
  echo "    graft map 으로 확인 가능. 세션을 다시 시작하면 MCP 도구가 보임."
}

install_agency() {
  echo "==> [2/4] Agency Agents (282개 전문가 에이전트 → ~/.claude/agents)"
  if [ -d "$TOOLS_DIR/agency-agents/.git" ]; then
    git -C "$TOOLS_DIR/agency-agents" pull --ff-only
  else
    git clone --depth 1 https://github.com/msitarzewski/agency-agents.git "$TOOLS_DIR/agency-agents"
  fi
  "$TOOLS_DIR/agency-agents/scripts/install.sh" --tool claude-code
  echo "    설치된 에이전트 수: $(ls "$HOME/.claude/agents" | wc -l)"
}

install_cbm() {
  echo "==> [3/4] Codebase Memory MCP (코드 지식 그래프 MCP 서버)"
  # 공식 설치 스크립트: GitHub 릴리스 바이너리를 받아 SHA-256 검증 후 ~/.local/bin 에 설치,
  # 감지된 에이전트(Claude Code 등)에 MCP 서버를 자동 등록한다.
  curl -fsSL https://raw.githubusercontent.com/DeusData/codebase-memory-mcp/main/install.sh | bash
  echo "    PATH 에 ~/.local/bin 이 없으면: export PATH=\"\$HOME/.local/bin:\$PATH\""
}

install_openmontage() {
  echo "==> [4/4] OpenMontage (AI 영상 제작 파이프라인)"
  if [ -d "$TOOLS_DIR/OpenMontage/.git" ]; then
    git -C "$TOOLS_DIR/OpenMontage" pull --ff-only
  else
    git clone --depth 1 https://github.com/calesthio/OpenMontage.git "$TOOLS_DIR/OpenMontage"
  fi
  cd "$TOOLS_DIR/OpenMontage"
  make setup   # venv + pip requirements + remotion npm install + piper-tts + .env 생성
  echo "    사용법: cd $TOOLS_DIR/OpenMontage 에서 Claude Code 를 열고 영상 주제를 말하면 됨."
  echo "    유료 생성 모델을 쓰려면 .env 에 API 키 입력 (무료 구성으로도 동작)."
}

targets=("$@")
[ ${#targets[@]} -eq 0 ] && targets=(graft agency cbm openmontage)
for t in "${targets[@]}"; do
  case "$t" in
    graft) install_graft ;;
    agency) install_agency ;;
    cbm) install_cbm ;;
    openmontage) install_openmontage ;;
    *) echo "알 수 없는 대상: $t (graft|agency|cbm|openmontage)"; exit 2 ;;
  esac
  echo
done
echo "완료. Claude Code 세션을 다시 시작하면 새 MCP 서버/에이전트가 적용됩니다."
