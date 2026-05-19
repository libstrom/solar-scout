#!/usr/bin/env bash
# Sets up the prompt-optimizer MCP server and registers it in ~/.claude/.mcp.json.
# Run once on each machine. Requires Node >=22 and pnpm.
# Usage: OPENAI_API_KEY=sk-... ./scripts/setup-prompt-optimizer-mcp.sh
set -euo pipefail

REPO_URL="https://github.com/linshenkx/prompt-optimizer.git"
INSTALL_DIR="${HOME}/prompt-optimizer"
MCP_JSON="${HOME}/.claude/.mcp.json"

if [[ -z "${OPENAI_API_KEY:-}" ]]; then
  echo "Error: OPENAI_API_KEY is not set." >&2
  echo "Usage: OPENAI_API_KEY=sk-... $0" >&2
  exit 1
fi

# Clone or update
if [[ -d "${INSTALL_DIR}/.git" ]]; then
  echo "Updating existing clone at ${INSTALL_DIR}..."
  git -C "${INSTALL_DIR}" pull --ff-only
else
  echo "Cloning ${REPO_URL} into ${INSTALL_DIR}..."
  git clone "${REPO_URL}" "${INSTALL_DIR}"
fi

# Install deps and build
echo "Installing dependencies..."
pnpm --dir "${INSTALL_DIR}" install --frozen-lockfile

echo "Building core and mcp-server packages..."
pnpm --dir "${INSTALL_DIR}" --filter @prompt-optimizer/core build
pnpm --dir "${INSTALL_DIR}" --filter @prompt-optimizer/mcp-server build

# Write .env with the API key
ENV_FILE="${INSTALL_DIR}/packages/mcp-server/.env"
cat > "${ENV_FILE}" <<EOF
VITE_OPENAI_API_KEY=${OPENAI_API_KEY}
MCP_HTTP_PORT=3000
MCP_LOG_LEVEL=info
MCP_DEFAULT_LANGUAGE=en-US
EOF
echo "Wrote API key to ${ENV_FILE}"

# Register in ~/.claude/.mcp.json
mkdir -p "$(dirname "${MCP_JSON}")"

PRELOAD="${INSTALL_DIR}/packages/mcp-server/preload-env.cjs"
ENTRY="${INSTALL_DIR}/packages/mcp-server/dist/start.cjs"

ENTRY_JSON=$(cat <<EOF
{
  "mcpServers": {
    "prompt-optimizer": {
      "command": "node",
      "args": ["-r", "${PRELOAD}", "${ENTRY}"]
    }
  }
}
EOF
)

if [[ -f "${MCP_JSON}" ]]; then
  # Merge if file already exists (requires jq)
  if command -v jq &>/dev/null; then
    TMP=$(mktemp)
    jq --argjson new "${ENTRY_JSON}" \
      '.mcpServers += $new.mcpServers' \
      "${MCP_JSON}" > "${TMP}" && mv "${TMP}" "${MCP_JSON}"
    echo "Merged prompt-optimizer into existing ${MCP_JSON}"
  else
    echo "Warning: jq not found. Skipping merge — add the entry manually:" >&2
    echo "${ENTRY_JSON}" >&2
  fi
else
  echo "${ENTRY_JSON}" > "${MCP_JSON}"
  echo "Created ${MCP_JSON}"
fi

echo ""
echo "Done. Restart Claude Code — the 'prompt-optimizer' MCP server will be available."
