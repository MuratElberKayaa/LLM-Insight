"""Create the local, git-ignored .env from .env.example with generated dev secrets.

Standard library only. Never overwrites an existing .env and never prints secret values:

    python -I scripts/dev/init_env.py

Generated: POSTGRES_PASSWORD (also written into LLMI_DATABASE_URL), LLMI_KEY_PEPPER,
LLMI_ADMIN_TOKEN and LLMI_API_TOKEN (same value as LLMI_ADMIN_TOKEN, see SECURITY.md §2).
Left as placeholders: the ingest key header (printed by the Phase 1B init command) and
OPENAI_API_KEY (empty; real mode needs explicit approval).
"""

import os
import secrets
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
EXAMPLE = os.path.join(ROOT, ".env.example")
TARGET = os.path.join(ROOT, ".env")
PLACEHOLDER = "CHANGE_ME"


def render(example: str, values: dict[str, str]) -> tuple[str, list[str]]:
    out, filled = [], []
    for line in example.splitlines():
        key, sep, value = line.partition("=")
        if sep and not line.lstrip().startswith("#"):
            if key in values and value == PLACEHOLDER:
                line, value = f"{key}={values[key]}", values[key]
                filled.append(key)
            elif key == "LLMI_DATABASE_URL" and f":{PLACEHOLDER}@" in value:
                line = f"{key}={value.replace(f':{PLACEHOLDER}@', ':' + values['POSTGRES_PASSWORD'] + '@')}"
                filled.append(key)
        out.append(line)
    return "\n".join(out) + "\n", filled


def main() -> int:
    if os.path.exists(TARGET):
        print(".env already exists; left unchanged.")
        return 0
    admin_token = secrets.token_hex(32)
    values = {
        "POSTGRES_PASSWORD": secrets.token_hex(24),  # hex: URL-safe, no escaping needed
        "LLMI_KEY_PEPPER": secrets.token_hex(32),
        "LLMI_ADMIN_TOKEN": admin_token,
        "LLMI_API_TOKEN": admin_token,
    }
    with open(EXAMPLE, encoding="utf-8") as f:
        content, filled = render(f.read(), values)
    # O_EXCL: fail instead of overwriting if .env appears concurrently.
    fd = os.open(TARGET, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)
    print(f"Created .env with generated values for: {', '.join(filled)} (values not shown).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
