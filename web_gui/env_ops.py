"""Visual Setup Wizard backing operations -- read/update the root `.env`.

Values are only ever shown to the browser in masked form (last 4 chars),
and an update only overwrites a key when the submitted field is non-empty --
leaving a field blank keeps the existing secret rather than wiping it, since
this form is re-rendered with masked placeholders, not real values.
"""
import re
from pathlib import Path

VAULT_PATH = Path(__file__).resolve().parent.parent
ENV_PATH = VAULT_PATH / ".env"

# Keys intentionally left out of the Setup Wizard entirely -- runtime
# constants that CLAUDE.md documents as Python-level, not env-driven
# (VAULT_PATH/PYTHON_EXEC are handled by Phase 1's dynamic-fallback code, not
# meant to be hand-edited here).
#
# EXECUTION_ZONE/CLOUD_ZONE are excluded for a safety reason, not a
# convenience one: the entire Cloud/Local zone boundary (draft-only vs. live
# send/post/payment) is gated on these two values, and per CLAUDE.md's Known
# Constraints they're meant to be a physically-set, per-machine value ("set
# manually in each machine's own .env... both zones must be updated by hand"),
# never something editable through a browser form. Before this exclusion
# existed, anyone logged into the Web GUI (including with the shipped
# changeme-admin demo password) could persist EXECUTION_ZONE=local into a
# Cloud VM's .env via POST /settings -- silently flipping the VM into
# live-send mode the next time any process there re-read .env (a GUI
# restart, or agent-runner.service's Restart=always after any crash) --
# found in code review (2026-08-04), fixed here.
HIDDEN_KEYS = {"VAULT_PATH", "PYTHON_EXEC", "EXECUTION_ZONE", "CLOUD_ZONE"}

LINE_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$")


def _mask(value: str) -> str:
    value = value.strip()
    if not value:
        return ""
    if len(value) <= 4:
        return "*" * len(value)
    return "*" * (len(value) - 4) + value[-4:]


def read_env_masked() -> list[dict]:
    """Returns [{key, masked_value, is_set}], preserving file order,
    comments/blank lines dropped (this view is form-editable keys only)."""
    if not ENV_PATH.exists():
        return []
    rows = []
    seen = set()
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        match = LINE_RE.match(line.strip())
        if not match:
            continue
        key, value = match.group(1), match.group(2)
        if key in HIDDEN_KEYS or key in seen:
            continue
        seen.add(key)
        rows.append({"key": key, "masked_value": _mask(value), "is_set": bool(value.strip())})
    return rows


def update_env(updates: dict) -> list[str]:
    """Applies non-empty updates in place, preserving comments/ordering/blank
    lines for every line that isn't being changed. Returns the list of keys
    actually changed."""
    updates = {k: v for k, v in updates.items() if v is not None and v.strip() != "" and k not in HIDDEN_KEYS}
    if not updates:
        return []

    lines = ENV_PATH.read_text(encoding="utf-8").splitlines() if ENV_PATH.exists() else []
    changed = []
    seen_keys = set()
    new_lines = []
    for line in lines:
        match = LINE_RE.match(line.strip())
        if match and match.group(1) in updates:
            key = match.group(1)
            new_lines.append(f"{key}={updates[key].strip()}")
            seen_keys.add(key)
            changed.append(key)
        else:
            new_lines.append(line)

    for key, value in updates.items():
        if key not in seen_keys:
            new_lines.append(f"{key}={value.strip()}")
            changed.append(key)

    ENV_PATH.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    return changed
