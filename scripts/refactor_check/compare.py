import json, sys
a = json.load(open(sys.argv[1])); b = json.load(open(sys.argv[2]))
bad = [k for k in sorted(set(a) | set(b)) if a.get(k) != b.get(k)]
print(f"{len(a)-len(bad)}/{len(a)} requests identical (status, body, SQL log)")
for k in bad:
    x, y = a.get(k), b.get(k)
    print("DIFF", k)
    if x is None or y is None: print("   missing in", "after" if y is None else "before"); continue
    for f in ("status", "type", "body", "sql", "keys"):
        if x.get(f) != y.get(f):
            sx, sy = json.dumps(x.get(f), sort_keys=True), json.dumps(y.get(f), sort_keys=True)
            i = next((n for n, (p, q) in enumerate(zip(sx, sy)) if p != q), min(len(sx), len(sy)))
            print(f"   {f}: before …{sx[max(0,i-60):i+120]}\n   {' '*len(f)}  after  …{sy[max(0,i-60):i+120]}")
sys.exit(1 if bad else 0)
