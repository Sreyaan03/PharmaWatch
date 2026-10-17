# backend/services/prescription/schema.py
# The one shape every extractor returns, so the provider can change without touching callers.

from dataclasses import asdict, dataclass, field
from typing import List, Optional

TEXT_SOURCES = ("text_layer", "ocr", "vision", "plain_text")
STATUSES = ("active", "stopped", "allergy")
EXTRACTORS = ("local_ocr", "local_text", "cloud_vision")


class SchemaError(ValueError):
    """An extractor produced something that does not match the schema."""


@dataclass
class MedicationLine:
    drug_text: str                         # the name as written
    strength: Optional[str] = None         # "500 mg"
    dose_form: Optional[str] = None        # "tablet"
    dose: Optional[str] = None             # "1 tablet"
    route: Optional[str] = None            # "oral"
    frequency: Optional[str] = None        # "twice daily"
    duration: Optional[str] = None         # "5 days"
    as_needed: bool = False
    confidence: float = 0.0                # 0..1, how sure the extractor is about this line
    notes: str = ""
    status: str = "active"                 # active | stopped | allergy
    ingredient: Optional[str] = None       # lexicon match, when there is one
    match: Optional[str] = None            # exact | fuzzy | None

    def validate(self):
        if not self.drug_text or not self.drug_text.strip():
            raise SchemaError("a medication line needs drug_text")
        if not 0.0 <= float(self.confidence) <= 1.0:
            raise SchemaError(f"confidence out of range: {self.confidence}")
        if self.status not in STATUSES:
            raise SchemaError(f"unknown status {self.status!r}")
        return self


@dataclass
class ExtractionResult:
    lines: List[MedicationLine] = field(default_factory=list)
    unreadable_regions: List[str] = field(default_factory=list)
    extractor: str = "local_ocr"
    text_source: str = "ocr"
    pages: int = 0

    def to_dict(self):
        return asdict(self)


LINE_FIELDS = [f for f in MedicationLine.__dataclass_fields__]


def result_from_dict(payload):
    """Build an ExtractionResult from a dict (for example a model's output), or raise SchemaError."""
    if not isinstance(payload, dict) or not isinstance(payload.get("lines"), list):
        raise SchemaError("payload must be an object with a 'lines' list")
    lines = []
    for raw in payload["lines"]:
        if not isinstance(raw, dict):
            raise SchemaError("each line must be an object")
        unknown = set(raw) - set(LINE_FIELDS)
        if unknown:
            raise SchemaError(f"unknown fields in a line: {sorted(unknown)}")
        try:
            line = MedicationLine(**{k: v for k, v in raw.items() if k in LINE_FIELDS})
            line.confidence = float(line.confidence)
        except (TypeError, ValueError) as exc:
            raise SchemaError(f"bad line: {exc}") from exc
        lines.append(line.validate())
    result = ExtractionResult(
        lines=lines,
        unreadable_regions=[str(x) for x in payload.get("unreadable_regions", [])],
        extractor=str(payload.get("extractor", "cloud_vision")),
        text_source=str(payload.get("text_source", "vision")),
        pages=int(payload.get("pages", 0)))
    if result.extractor not in EXTRACTORS:
        raise SchemaError(f"unknown extractor {result.extractor!r}")
    if result.text_source not in TEXT_SOURCES:
        raise SchemaError(f"unknown text_source {result.text_source!r}")
    return result


def tool_schema():
    """JSON schema for a model asked to return an ExtractionResult's lines."""
    line_props = {
        "drug_text": {"type": "string", "description": "Drug name exactly as written"},
        "strength": {"type": ["string", "null"]}, "dose_form": {"type": ["string", "null"]},
        "dose": {"type": ["string", "null"]}, "route": {"type": ["string", "null"]},
        "frequency": {"type": ["string", "null"]}, "duration": {"type": ["string", "null"]},
        "as_needed": {"type": "boolean"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "notes": {"type": "string"},
        "status": {"type": "string", "enum": list(STATUSES)},
    }
    return {
        "type": "object",
        "properties": {
            "lines": {"type": "array", "items": {"type": "object", "properties": line_props,
                                                 "required": ["drug_text", "confidence"]}},
            "unreadable_regions": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["lines"],
    }
