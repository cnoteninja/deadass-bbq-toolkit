#!/usr/bin/env python3
"""Prerequisite checker for deadass-bbq-toolkit. Plain-English PASS/FAIL, no tracebacks."""
import json
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")
REQUIRED_NUTRIENTS = ["calories", "fat_g", "sat_fat_g", "cholesterol_mg",
                      "sodium_mg", "carbs_g", "fiber_g", "sugars_g",
                      "protein_g", "potassium_mg"]

results = []


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    print("[%s] %s%s" % ("PASS" if ok else "FAIL", name,
                         (" — " + detail) if detail else ""))


def main():
    check("Python 3.8+", sys.version_info >= (3, 8),
          "found %d.%d" % sys.version_info[:2])

    ok = True
    for rel in ["nutrition_table.json", "ingredients.json",
                "recipes.json", "config.json",
                os.path.join("demo", "ingredients.json"),
                os.path.join("demo", "recipes.json")]:
        p = os.path.join(DATA, rel)
        try:
            with open(p, encoding="utf-8") as f:
                json.load(f)
        except Exception as e:
            ok = False
            check("data file valid: " + rel, False, str(e))
            break
    else:
        check("data files are valid JSON", True)

    if ok:
        with open(os.path.join(DATA, "nutrition_table.json"), encoding="utf-8") as f:
            table = json.load(f)
        entries = [k for k in table if not k.startswith("_")]
        missing = [k for k, v in table.items() if not k.startswith("_")
                   for n in REQUIRED_NUTRIENTS if n not in v]
        check("nutrition table entries complete",
              len(entries) >= 25 and not missing,
              "%d entries" % len(entries) if not missing
              else "missing keys: %s" % sorted(set(missing)))

    print()
    failed = [r for r in results if not r[1]]
    if failed:
        print("%d check(s) failed — fix the items above, then re-run." % len(failed))
        sys.exit(1)
    print("All checks passed. Run:  python3 app.py")


if __name__ == "__main__":
    main()
