# backend/clients/vision.py
# Optional cloud extractor: sends page images to the Anthropic Messages API and requires output
# matching the medication schema. Used only when ANTHROPIC_API_KEY is set; nothing calls it
# otherwise. Logs the job reference and line count, never content.

import base64
import logging
import time

import requests

from core import config

log = logging.getLogger(__name__)

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"
DEFAULT_MODEL = "claude-opus-5-5"
TOOL_NAME = "record_medications"
MAX_PAGES = 5

PROMPT = (
    "This is a page of a prescription or discharge summary. Transcribe the MEDICATION lines only, "
    "by calling the record_medications tool. For each medication give the name exactly as written "
    "and, where legible, strength, dose form, dose, route, frequency, duration and whether it is "
    "as-needed. Leave a field null when it is not legible or not written: never guess or complete "
    "it from knowledge of typical doses. Set status to 'stopped' for medications the text says to "
    "stop, hold or avoid, and 'allergy' for allergies. Give a confidence from 0 to 1 per line. "
    "List anything you could not read in unreadable_regions. Do not transcribe patient or "
    "prescriber names, identifiers, addresses or dates of birth."
)


class VisionError(RuntimeError):
    """The cloud extractor failed or returned something that does not match the schema."""


class VisionClient:
    def __init__(self, api_key=None, model=None, session=None, timeout=90, retries=1, sleep=time.sleep):
        self.api_key = api_key or config.get("ANTHROPIC_API_KEY") or None
        self.model = model or config.get("RX_VISION_MODEL") or DEFAULT_MODEL
        self.session = session or requests
        self.timeout = timeout
        self.retries = retries
        self._sleep = sleep
        if not self.api_key:
            raise VisionError("ANTHROPIC_API_KEY is not set")

    def _body(self, images, input_schema):
        content = [{"type": "image", "source": {"type": "base64", "media_type": mime,
                                                "data": base64.b64encode(data).decode("ascii")}}
                   for mime, data in images[:MAX_PAGES]]
        content.append({"type": "text", "text": PROMPT})
        return {"model": self.model, "max_tokens": 4096,
                "tools": [{"name": TOOL_NAME, "description": "Record the medication lines found on the page(s).",
                           "input_schema": input_schema}],
                "tool_choice": {"type": "tool", "name": TOOL_NAME},
                "messages": [{"role": "user", "content": content}]}

    def extract(self, images, input_schema, job_ref=""):
        """images: [(mime, bytes)]; input_schema: JSON schema of the result.

        Returns the model's structured output as a dict (the caller validates it against its own
        schema), or raises VisionError.
        """
        headers = {"x-api-key": self.api_key, "anthropic-version": API_VERSION,
                   "content-type": "application/json"}
        body = self._body(images, input_schema)
        last = None
        for attempt in range(self.retries + 1):
            try:
                resp = self.session.post(API_URL, headers=headers, json=body, timeout=self.timeout)
            except requests.RequestException as exc:
                last = f"network error: {type(exc).__name__}"
            else:
                if resp.status_code == 200:
                    return self._parse(resp.json(), job_ref)
                last = f"HTTP {resp.status_code}"
                if resp.status_code not in (429, 500, 502, 503, 529):
                    break
            if attempt < self.retries:
                self._sleep(2 ** attempt)
        raise VisionError(f"vision request failed ({last})")

    def _parse(self, data, job_ref):
        blocks = [b for b in (data.get("content") or []) if b.get("type") == "tool_use" and b.get("name") == TOOL_NAME]
        if not blocks:
            raise VisionError("vision response contained no structured result")
        payload = blocks[0].get("input")
        if not isinstance(payload, dict):
            raise VisionError("vision response was not an object")
        log.info(f"[VISION] job {job_ref}: {len(payload.get('lines') or [])} line(s) returned")
        return payload
