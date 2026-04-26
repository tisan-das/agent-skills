#!/usr/bin/env bash
# setup-project.sh
# Wires a project to use agent-skills from this central repo via symlinks.
#
# Usage:
#   ./setup-project.sh                     # wire current directory
#   ./setup-project.sh /path/to/project    # wire specific project

set -euo pipefail

AGENT_SKILLS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET_DIR="${1:-$(pwd)}"
TARGET_DIR="$(cd "$TARGET_DIR" && pwd)"

if [[ "$TARGET_DIR" == "$AGENT_SKILLS_DIR" ]]; then
  echo "Error: target cannot be the agent-skills repo itself." >&2
  exit 1
fi

echo "agent-skills : $AGENT_SKILLS_DIR"
echo "target       : $TARGET_DIR"
echo ""

linked=0
updated=0
skipped=0

# Create symlink at $dst pointing to $src.
# - Updates existing symlinks.
# - Skips real files with a warning (no overwrite).
link() {
  local src="$1"
  local dst="$2"
  local label="${dst#"$TARGET_DIR/"}"

  if [[ -L "$dst" ]]; then
    ln -sf "$src" "$dst"
    echo "  updated  $label"
    (( updated++ )) || true
  elif [[ -e "$dst" ]]; then
    echo "  skipped  $label  (real file — remove it manually to replace with symlink)"
    (( skipped++ )) || true
  else
    ln -s "$src" "$dst"
    echo "  linked   $label"
    (( linked++ )) || true
  fi
}

# ── .github/copilot-instructions.md ──────────────────────────────────────────
mkdir -p "$TARGET_DIR/.github"
link "$AGENT_SKILLS_DIR/copilot-instructions.md" \
     "$TARGET_DIR/.github/copilot-instructions.md"

# ── .github/agents/*.md ──────────────────────────────────────────────────────
mkdir -p "$TARGET_DIR/.github/agents"
for agent_file in "$AGENT_SKILLS_DIR/agents"/*.md; do
  [[ -f "$agent_file" ]] || continue
  link "$agent_file" "$TARGET_DIR/.github/agents/$(basename "$agent_file")"
done

# ── .github/skills/<name>/ (whole directory per skill) ───────────────────────
mkdir -p "$TARGET_DIR/.github/skills"
for skill_dir in "$AGENT_SKILLS_DIR/skills"/*/; do
  [[ -d "$skill_dir" ]] || continue
  skill_name="$(basename "$skill_dir")"
  link "$skill_dir" "$TARGET_DIR/.github/skills/$skill_name"
done

# ── .github/prompts/*.prompt.md ──────────────────────────────────────────────
mkdir -p "$TARGET_DIR/.github/prompts"
for prompt_file in "$AGENT_SKILLS_DIR/.github/prompts"/*.prompt.md; do
  [[ -f "$prompt_file" ]] || continue
  link "$prompt_file" "$TARGET_DIR/.github/prompts/$(basename "$prompt_file")"
done

# ── .claude/commands/*.md ────────────────────────────────────────────────────
mkdir -p "$TARGET_DIR/.claude/commands"
for cmd_file in "$AGENT_SKILLS_DIR/.claude/commands"/*.md; do
  [[ -f "$cmd_file" ]] || continue
  link "$cmd_file" "$TARGET_DIR/.claude/commands/$(basename "$cmd_file")"
done

echo ""
echo "Done — $linked linked, $updated updated, $skipped skipped."
echo ""
echo "Next steps:"
echo ""
echo "  1. Commit the symlinks (or add .github/agents/ .github/skills/ .github/prompts/"
echo "     .claude/commands/ to .gitignore if you don't want them tracked in the project repo)."
echo ""
echo "  2. For global Copilot instructions across ALL projects (no per-project setup),"
echo "     add this to your VSCode user settings (%APPDATA%/Code/User/settings.json"
echo "     on Windows, or the equivalent via File > Preferences > Settings > Open JSON):"
echo ""
echo "       \"github.copilot.chat.codeGeneration.instructions\": ["
echo "         { \"file\": \"$(wslpath -w "$AGENT_SKILLS_DIR" 2>/dev/null || echo "$AGENT_SKILLS_DIR")/copilot-instructions.md\" }"
echo "       ]"
echo ""
echo "     Update $AGENT_SKILLS_DIR/copilot-instructions.md once → all projects pick it up."
