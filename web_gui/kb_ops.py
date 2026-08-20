"""Visual Business Rule & Policy Rule Manager backing operations (Task 2).

Reads/rewrites Knowledge_Base/knowledge_base.md surgically -- never a full
overwrite -- so non-technical operators can adjust key governance parameters
through the Web GUI instead of hand-editing markdown. Two kinds of edits:

1. The discount ceiling: an existing numeric value that appears in TWO
   places in the KB's prose (the "Autonomous Authorization" line and the
   "Escalation Constraint" line) -- both must be kept in sync, since this is
   the single most important business rule in the vault (see CLAUDE.md's
   "Core business rule: the discount ceiling").
2. Fields that don't exist in the KB yet (Escalation Domains / Operating
   Tone / Business Operating Hours) -- stored in a new, idempotently-created
   "## Operating Parameters" section, appended once and thereafter edited
   in place, leaving every other section of the file untouched.
"""
import re
from pathlib import Path

VAULT_PATH = Path(__file__).resolve().parent.parent
KB_PATH = VAULT_PATH / "Knowledge_Base" / "knowledge_base.md"

DEFAULT_ESCALATION_DOMAINS = "Sales, Support"
DEFAULT_OPERATING_TONE = "Professional and warm"
DEFAULT_OPERATING_HOURS = "9:00 AM - 6:00 PM, Monday-Friday"

CEILING_RE = re.compile(r"\*\*(\d{1,3})%\*\*")
OPERATING_SECTION_RE = re.compile(
    r"\n## Operating Parameters\n(.*?)(?=\n## |\Z)", re.DOTALL
)
BULLET_RE = re.compile(r"^- \*\*([^:*]+)\*\*:\s*(.*)$")


def _read() -> str:
    if not KB_PATH.exists():
        return ""
    return KB_PATH.read_text(encoding="utf-8")


def _write(text: str) -> None:
    KB_PATH.write_text(text, encoding="utf-8")


def get_discount_ceiling() -> int:
    """First **NN%** match in the KB is the ceiling value (the "Autonomous
    Authorization" line always precedes the "Escalation Constraint" line
    that restates the same number)."""
    text = _read()
    match = CEILING_RE.search(text)
    return int(match.group(1)) if match else 20


def set_discount_ceiling(new_pct: int, text: str | None = None) -> str:
    """Replaces every occurrence of the *current* ceiling number (bolded,
    e.g. **20%**) with new_pct, so the Autonomous Authorization and
    Escalation Constraint lines never drift out of sync with each other."""
    if text is None:
        text = _read()
    old_pct = get_discount_ceiling()
    old_marker = f"**{old_pct}%**"
    new_marker = f"**{new_pct}%**"
    return text.replace(old_marker, new_marker)


def _parse_operating_section(text: str) -> dict:
    match = OPERATING_SECTION_RE.search(text)
    values = {}
    if match:
        for line in match.group(1).splitlines():
            bullet = BULLET_RE.match(line.strip())
            if bullet:
                values[bullet.group(1).strip()] = bullet.group(2).strip()
    return {
        "escalation_domains": values.get("Escalation Domains", DEFAULT_ESCALATION_DOMAINS),
        "operating_tone": values.get("Operating Tone", DEFAULT_OPERATING_TONE),
        "operating_hours": values.get("Business Operating Hours", DEFAULT_OPERATING_HOURS),
    }


def _render_operating_section(escalation_domains: str, operating_tone: str, operating_hours: str) -> str:
    return (
        "\n## Operating Parameters\n"
        f"- **Escalation Domains**: {escalation_domains}\n"
        f"- **Operating Tone**: {operating_tone}\n"
        f"- **Business Operating Hours**: {operating_hours}\n"
    )


def _upsert_operating_section(text: str, escalation_domains: str, operating_tone: str, operating_hours: str) -> str:
    section = _render_operating_section(escalation_domains, operating_tone, operating_hours)
    if OPERATING_SECTION_RE.search(text):
        return OPERATING_SECTION_RE.sub(section, text, count=1)
    return text.rstrip("\n") + "\n" + section


def read_kb_rules() -> dict:
    text = _read()
    operating = _parse_operating_section(text)
    return {
        "discount_ceiling": get_discount_ceiling(),
        "raw_markdown": text,
        **operating,
    }


def update_kb_rules(discount_ceiling: int, escalation_domains: str, operating_tone: str, operating_hours: str) -> None:
    if not (0 <= discount_ceiling <= 100):
        raise ValueError("Discount ceiling must be between 0 and 100.")
    text = _read()
    text = set_discount_ceiling(discount_ceiling, text=text)
    text = _upsert_operating_section(
        text,
        escalation_domains.strip() or DEFAULT_ESCALATION_DOMAINS,
        operating_tone.strip() or DEFAULT_OPERATING_TONE,
        operating_hours.strip() or DEFAULT_OPERATING_HOURS,
    )
    _write(text)
