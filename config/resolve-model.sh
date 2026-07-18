#!/usr/bin/env bash
# Resolves a role ("codex" or "claude-verifier") to a model string from
# config/roles.yml. Prints an empty string if the role maps to "" (meaning:
# use the tool's own current default).
#
# Usage: resolve-model.sh <role> <roles-path>
set -euo pipefail

ROLE="$1"
ROLES="${2:-config/roles.yml}"

pip install --quiet pyyaml

python3 - "$ROLE" "$ROLES" <<'PYEOF'
import sys
import yaml

role, roles_path = sys.argv[1:3]
roles = yaml.safe_load(open(roles_path))
if role not in roles:
    sys.exit(f"resolve-model: unknown role '{role}'")
print(roles[role] or "")
PYEOF
