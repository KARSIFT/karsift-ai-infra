#!/usr/bin/env bash
# Resolves a role (e.g. "implementer", "reviewer", "planner", or one of the
# retry/escalation variants - see config/roles.yml's own key names, which are
# the actual source of truth) to a model string from config/roles.yml. Prints
# an empty string if the role maps to "" (meaning: use the tool's own current
# default), or if the role key isn't present and a caller is checking for an
# optional variant with `|| echo ""` (see implement.yml/review.yml's
# escalation/fast-retry resolution steps for that pattern).
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
