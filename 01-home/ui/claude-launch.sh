#!/bin/bash
# claude-launch.sh — OAuth-first launcher with API key fallback
# Usage: ./claude-launch.sh [any claude args]
#
# Primary:  Uses Claude Code OAuth token from ~/.claude/.credentials.json
# Fallback: If OAuth is expired/missing, uses CLAUDE_FALLBACK_API_KEY from .env

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CREDS_FILE="$HOME/.claude/.credentials.json"
ENV_FILE="$SCRIPT_DIR/.env"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

# Load .env if it exists
load_env() {
    if [ -f "$ENV_FILE" ]; then
        while IFS='=' read -r key value; do
            # Skip comments and empty lines
            [[ "$key" =~ ^#.*$ || -z "$key" ]] && continue
            # Remove surrounding quotes from value
            value="${value%\"}"
            value="${value#\"}"
            value="${value%\'}"
            value="${value#\'}"
            if [ "$key" = "CLAUDE_FALLBACK_API_KEY" ]; then
                FALLBACK_KEY="$value"
            fi
        done < "$ENV_FILE"
    fi
}

# Check if OAuth token is valid (not expired, with 5 min buffer)
check_oauth() {
    if [ ! -f "$CREDS_FILE" ]; then
        echo -e "${YELLOW}No OAuth credentials found${NC}"
        return 1
    fi

    # Extract expiresAt using python
    EXPIRES_AT=$(python -c "
import json, time
try:
    with open('$CREDS_FILE') as f:
        creds = json.load(f)
    expires = creds.get('claudeAiOauth', {}).get('expiresAt', 0)
    now_ms = int(time.time() * 1000)
    buffer = 5 * 60 * 1000  # 5 minute buffer
    if expires > (now_ms + buffer):
        remaining = (expires - now_ms) / 1000 / 3600
        print(f'VALID:{remaining:.1f}')
    else:
        print('EXPIRED')
except Exception as e:
    print(f'ERROR:{e}')
" 2>/dev/null)

    if [[ "$EXPIRES_AT" == VALID:* ]]; then
        HOURS="${EXPIRES_AT#VALID:}"
        echo -e "${GREEN}OAuth token valid (${HOURS}h remaining)${NC}"
        return 0
    elif [[ "$EXPIRES_AT" == "EXPIRED" ]]; then
        echo -e "${YELLOW}OAuth token expired${NC}"
        return 1
    else
        echo -e "${YELLOW}Could not check OAuth: $EXPIRES_AT${NC}"
        return 1
    fi
}

# Main
echo -e "${NC}Checking Claude Code auth...${NC}"

load_env

if check_oauth; then
    # OAuth is good — launch normally (no API key override)
    unset ANTHROPIC_API_KEY
    echo -e "${GREEN}Using OAuth authentication${NC}"
    echo ""
    claude "$@"
else
    # OAuth failed — try fallback
    if [ -n "$FALLBACK_KEY" ]; then
        echo -e "${YELLOW}Falling back to API key from .env${NC}"
        echo ""
        export ANTHROPIC_API_KEY="$FALLBACK_KEY"
        claude "$@"
    else
        echo -e "${RED}OAuth expired and no CLAUDE_FALLBACK_API_KEY in .env${NC}"
        echo ""
        echo "To fix, add this to $ENV_FILE:"
        echo "  CLAUDE_FALLBACK_API_KEY=YOUR-ANTHROPIC-KEY"
        echo ""
        echo "Get an API key at: https://console.anthropic.com/settings/keys"
        echo ""
        echo "Or try refreshing OAuth manually:"
        echo "  claude login"
        exit 1
    fi
fi
