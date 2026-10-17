# backend/services/prescription/report.py
# The report: one structure, rendered on screen (JSON) and as a PDF from that same structure.
# It holds no patient or prescriber identifiers; the optional reference label the user typed is
# the only identifier it can carry.

from datetime import datetime, timezone

RESEARCH_USE = ("Research and education use only. This is not clinical advice and does not replace "
                "review by a pharmacist or physician.")
METHODS_NOTE = ("Medications were transcribed from the uploaded document and confirmed by a person. "
                "Interactions: FDA label text and the interaction database; pairs with neither are scored "
                "by a structure-based model. Boxed warnings and pharmacological classes: FDA label. "
                "Reactions and signals: FAERS (de-duplicated reports; signal = at least 3 cases, PRR >= 2, "
                "chi-square >= 4). Details: docs/METHODS.md.")


def build_report(confirmed_lines, review, meta):
    """meta: extractor, text_source, data_version, model_version, reference, cloud_used."""
    return {
        "reference": (meta.get("reference") or "").strip()[:80] or None,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "medications": [
            {"index": i, "drug_text": l.get("drug_text"), "ingredient": l.get("ingredient"),
             "strength": l.get("strength"), "dose": l.get("dose"), "route": l.get("route"),
             "frequency": l.get("frequency"), "duration": l.get("duration"),
             "as_needed": bool(l.get("as_needed")), "status": l.get("status", "active")}
            for i, l in enumerate(confirmed_lines)],
        "summary": review["summary"],
        "findings": review["findings"],
        "counts": review["counts"],
        "drug_sections": review["drug_sections"],
        "pairs": [{k: p.get(k) for k in ("drug_a", "drug_b", "tier", "interaction_likelihood_score", "calibrated",
                                         "label_documented", "label_snippet", "faers_reports")}
                  for p in review["pairs"]],
        "not_analysed": review["not_analysed"],
        "stopped": review["stopped"], "allergies": review["allergies"], "truncated": review["truncated"],
        "limitations": review["limitations"],
        "methods": METHODS_NOTE,
        "sources": {"extractor": meta.get("extractor"), "text_source": meta.get("text_source"),
                    "cloud_used": bool(meta.get("cloud_used")), "faers_data": meta.get("data_version"),
                    "interaction_model": meta.get("model_version")},
        "research_use_statement": RESEARCH_USE,
    }


# ── PDF ──────────────────────────────────────────────────────────────────────
_PAGE_W, _PAGE_H, _MARGIN = 595, 842, 54
_REPLACE = {"≥": ">=", "≤": "<=", "µ": "u", "–": "-", "—": "-", "‘": "'", "’": "'", "“": '"', "”": '"',
            "•": "-", "→": "->", "…": "...", "±": "+/-"}


def _latin(text):
    text = str(text if text is not None else "")
    for k, v in _REPLACE.items():
        text = text.replace(k, v)
    return text.encode("latin-1", "replace").decode("latin-1")


class _Writer:
    def __init__(self):
        import fitz
        self.fitz = fitz
        self.doc = fitz.open()
        self.page = None
        self.y = 0
        self._new_page()

    def _new_page(self):
        self.page = self.doc.new_page(width=_PAGE_W, height=_PAGE_H)
        self.y = _MARGIN

    def _wrap(self, text, size, font, width):
        words, lines, cur = _latin(text).split(), [], ""
        for w in words:
            trial = (cur + " " + w).strip()
            if self.fitz.get_text_length(trial, fontname=font, fontsize=size) <= width:
                cur = trial
            else:
                if cur:
                    lines.append(cur)
                cur = w
        if cur:
            lines.append(cur)
        return lines or [""]

    def text(self, text, size=10, bold=False, indent=0, gap=3):
        font = "hebo" if bold else "helv"
        for line in self._wrap(text, size, font, _PAGE_W - 2 * _MARGIN - indent):
            if self.y + size * 1.3 > _PAGE_H - _MARGIN:
                self._new_page()
            self.page.insert_text((_MARGIN + indent, self.y + size), line, fontname=font, fontsize=size)
            self.y += size * 1.3
        self.y += gap

    def heading(self, text):
        self.y += 6
        self.text(text, size=12, bold=True, gap=2)

    def bytes(self):
        n = len(self.doc)
        for i, page in enumerate(self.doc):
            page.insert_text((_MARGIN, _PAGE_H - 28), f"PharmaWatch prescription review - research use only - page {i + 1}/{n}",
                             fontname="helv", fontsize=7.5, color=(0.4, 0.4, 0.4))
        return self.doc.tobytes()


def _med_line(m):
    parts = [m["drug_text"] or "?"]
    if m.get("ingredient") and (m["ingredient"] or "").lower() != (m["drug_text"] or "").lower():
        parts[0] += f" ({m['ingredient']})"
    for key in ("strength", "dose", "route", "frequency", "duration"):
        if m.get(key):
            parts.append(m[key])
    if m.get("as_needed"):
        parts.append("as needed")
    if m.get("status") != "active":
        parts.append(f"[{m['status']}]")
    return " | ".join(parts)


def render_pdf(report):
    """PDF bytes with every section of the report."""
    w = _Writer()
    w.text("Prescription safety review", size=18, bold=True, gap=4)
    w.text(f"Generated {report['generated_at']}" + (f" - reference: {report['reference']}" if report["reference"] else ""),
           size=9)
    w.text(report["research_use_statement"], size=9, bold=True, gap=6)

    w.heading("Summary")
    w.text(report["summary"])

    w.heading("Confirmed medication list")
    for m in report["medications"]:
        w.text(f"{m['index'] + 1}. {_med_line(m)}", indent=8, gap=1)

    w.heading("Findings")
    if not report["findings"]:
        w.text("No findings in the sources used. This is not a statement that the combination is acceptable.")
    for f in report["findings"]:
        w.text(f"{f['id']}. [{f['category_title']}] {f['title']}", bold=True, gap=1)
        w.text(f["detail"], indent=14, gap=1)
        ev = f["evidence"]
        w.text(f"Evidence: {ev['type']} - {ev['source']}", size=8.5, indent=14, gap=4)

    w.heading("Pairs checked")
    for p in report["pairs"]:
        score = f" (score {p['interaction_likelihood_score']})" if p["tier"] == "model_predicted" else ""
        w.text(f"{p['drug_a']} + {p['drug_b']}: {p['tier'].replace('_', ' ')}{score}", indent=8, gap=1)
    if not report["pairs"]:
        w.text("No pairs (fewer than two analysed medications).", indent=8)

    w.heading("Not analysed")
    for n in report["not_analysed"]:
        w.text(f"Line {n['line'] + 1} ({n['drug_text']}): {n['reason']}", indent=8, gap=1)
    for name in report["truncated"]:
        w.text(f"{name}: beyond the limit on medications analysed per review", indent=8, gap=1)
    if not (report["not_analysed"] or report["truncated"]):
        w.text("All confirmed lines were analysed.", indent=8)

    w.heading("Methods and limitations")
    w.text(report["methods"], size=9)
    for lim in report["limitations"]:
        w.text("- " + lim, size=9, indent=8, gap=1)

    w.heading("Sources")
    s = report["sources"]
    w.text(f"Extraction: {s['extractor']} ({s['text_source']})" + ("; used a cloud service" if s["cloud_used"] else "; processed on this machine"),
           size=9, gap=1)
    w.text(f"FAERS data: {s['faers_data']}", size=9, gap=1)
    w.text(f"Interaction model: {s['interaction_model']}", size=9)
    return w.bytes()
