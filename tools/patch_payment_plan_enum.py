from __future__ import annotations
import re
from pathlib import Path

VERS = Path("migrations/versions")
if not VERS.exists():
    raise SystemExit("ERR: migrations/versions not found (run from project root).")

# найти целевую миграцию b213...
targets = sorted(VERS.glob("*b21363fb1d28*.py"))
if not targets:
    raise SystemExit("ERR: can't find b21363fb1d28 migration file in migrations/versions/")
target = targets[0]

# 1) попробуем найти CREATE TYPE payment_plan AS ENUM (...) в миграциях
create_enum_re = re.compile(
    r"CREATE\s+TYPE\s+payment_plan\s+AS\s+ENUM\s*\((?P<body>[^;]+)\)",
    re.IGNORECASE | re.DOTALL
)

enum_vals: list[str] = []

for p in sorted(VERS.glob("*.py")):
    txt = p.read_text(encoding="utf-8")
    m = create_enum_re.search(txt)
    if m:
        body = m.group("body")
        enum_vals = re.findall(r"'([^']+)'", body)
        break

# 2) если не нашли — попробуем вытащить из кода (sa.Enum(..., name="payment_plan"))
if not enum_vals:
    code_candidates = []
    for p in Path("app").rglob("*.py"):
        try:
            t = p.read_text(encoding="utf-8")
        except Exception:
            continue
        if "payment_plan" in t and "Enum" in t:
            code_candidates.append((p, t))

    enum_call_re = re.compile(
        r"Enum\s*\((?P<args>.*?)\)\s*",
        re.DOTALL
    )
    name_re = re.compile(r"name\s*=\s*['\"]payment_plan['\"]")

    for p, t in code_candidates:
        for m in enum_call_re.finditer(t):
            args = m.group("args")
            if not name_re.search(args):
                continue
            # вытащим все строковые литералы до name=
            # (простая эвристика, но обычно хватает)
            pre = args.split("name", 1)[0]
            vals = re.findall(r"['\"]([^'\"]+)['\"]", pre)
            vals = [v for v in vals if v not in ("payment_plan",)]
            if vals:
                enum_vals = vals
                break
        if enum_vals:
            break

# 3) если вообще нигде нет — fallback
if not enum_vals:
    enum_vals = ["month", "year"]

# гарантируем, что quarter не дублируем
base_vals = [v for v in enum_vals if v != "quarter"]

enum_sql = ", ".join(f"'{v}'" for v in base_vals)

new_block = f"""
op.execute(\"\"\"
DO $$
BEGIN
    -- create enum if missing (for fresh DB)
    IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'payment_plan') THEN
        CREATE TYPE payment_plan AS ENUM ({enum_sql});
    END IF;

    -- add quarter if missing
    IF NOT EXISTS (
        SELECT 1
        FROM pg_enum e
        JOIN pg_type t ON t.oid = e.enumtypid
        WHERE t.typname = 'payment_plan'
          AND e.enumlabel = 'quarter'
    ) THEN
        ALTER TYPE payment_plan ADD VALUE 'quarter';
    END IF;
END $$;
\"\"\")
""".strip()

txt = target.read_text(encoding="utf-8")

# Патчим: ищем DO $$ ... payment_plan ... ADD VALUE 'quarter' ... $$;
# и заменяем на наш блок
do_block_re = re.compile(r"op\.execute\(\s*([\"']{3}).*?payment_plan.*?quarter.*?\1\s*\)\s*", re.DOTALL)
m = do_block_re.search(txt)
if not m:
    raise SystemExit(f"ERR: can't find op.execute triple-quoted DO $$ block in {target.name}")

patched = do_block_re.sub(new_block, txt, count=1)
target.write_text(patched, encoding="utf-8")

print(f"OK: patched {target}")
print(f"OK: base enum values used: {base_vals}")
