# 09-03 Summary: interface, synthetic set, accuracy benchmark

- Prescription Review page (upload, confirm and edit rows, report, PDF link, delete). Checked in real headless Chrome: 16/16 checks passed (before the last parser fix; rerun after restart).
- Brand-name map built from FAERS (6,247 brands) plus a synonym list; dictionary 4,754 -> 9,676 names.
- 36 synthetic documents (text PDF, scanned PDF, image), about 1.1 MB. Benchmark with local OCR:
  line recall 0.968, precision 1.0, 0 invented lines, drug identified 0.948 (every wrong one flagged for checking),
  strength 0.994, frequency 0.987, duration 1.0, route 0.68 (weakest; not fixed).
- Benchmark found "twice daily" read as "once daily" (fixed, regression tests) and OCR "ODx"/"BDx" gluing (fixed).
- CI gate runs the text-PDF subset with a fixture dictionary (`prescription_smoke`).

## Not done / limits
- Handwriting untested. Indian brands (Glycomet, Ecosprin, Dolo, Pan-D) and combination products are not recognised.
- Review-finding benchmark (`--review`, needs network) not run. Cloud extractor unmeasured (no key).
- Synthetic printed documents only; real-world accuracy will be lower.
