"""The pages never tell a user that a combination or drug is safe: absence of evidence is not safety."""

import os
import re

SRC = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "src"))
BANNED = [r"safe together", r"appear safe", r"&#x2713;\s*Safe", r">\s*Safe\s*<", r"Show safe pairs",
          r"is safe to", r"safe to combine", r"safe to take", r">OK<"]


def test_visible_text_never_claims_safety():
    problems = []
    for name in sorted(os.listdir(SRC)):
        if not name.endswith((".js", ".html")):
            continue
        text = open(os.path.join(SRC, name), encoding="utf-8").read()
        for pattern in BANNED:
            for m in re.finditer(pattern, text, re.I):
                line = text[:m.start()].count("\n") + 1
                problems.append(f"{name}:{line}: {m.group(0)!r}")
    assert not problems, problems
