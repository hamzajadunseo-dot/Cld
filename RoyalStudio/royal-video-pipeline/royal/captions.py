"""Short descriptions + tags for each asset via Claude vision (structured JSON output)."""
import base64
import json
import logging
import os

import anthropic

log = logging.getLogger("index")

MOODS = ["formal", "celebratory", "joyful", "warm", "solemn", "sad", "tense", "dramatic", "casual", "neutral"]
SHOTS = ["close-up", "medium", "wide", "group", "crowd", "detail"]
KINDS = ["photo", "painting", "illustration", "document", "screenshot", "other"]

SCHEMA = {
    "type": "object",
    "properties": {
        "caption": {"type": "string"},
        "event": {"type": "string"},
        "location": {"type": "string"},
        "year_hint": {"type": "integer"},
        "mood": {"type": "string", "enum": MOODS},
        "shot_type": {"type": "string", "enum": SHOTS},
        "image_kind": {"type": "string", "enum": KINDS},
        "people_visible": {"type": "integer"},
        "tags": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["caption", "event", "location", "year_hint", "mood", "shot_type",
                 "image_kind", "people_visible", "tags"],
    "additionalProperties": False,
}

SYSTEM = """You catalogue a stock-media library for documentary videos about the British Royal Family.
For each image you get the picture plus the source's own metadata (folder, title, description, date).

Write fields for search and editing:
- caption: one sentence, max 25 words, describing what is visibly happening (action, setting, attire, crowd, weather, emotion). Name a person only if the source title/description names them; otherwise describe them ("a man in naval uniform").
- event: the named event if the metadata or visible content makes it clear (e.g. "Coronation of Charles III", "Trooping the Colour"), else "".
- location: place if stated or clearly recognisable (e.g. "Buckingham Palace balcony"), else "".
- year_hint: the year the photo was TAKEN if stated in the title/description or visible text, else 0. Upload dates are not capture dates.
- mood: the emotional tone an editor would use it for.
- shot_type: framing of the main subject.
- image_kind: what the asset is.
- people_visible: approximate number of clearly visible people (0 if none).
- tags: 5-10 short lowercase keywords (objects, setting, occasion, clothing, emotion)."""


class Captioner:
    def __init__(self, model: str):
        if not os.getenv("ANTHROPIC_API_KEY"):
            raise RuntimeError("ANTHROPIC_API_KEY missing - add it to the .env file (see README).")
        self.client = anthropic.Anthropic(max_retries=5)
        self.model = model

    def describe(self, jpeg: bytes, context: dict) -> dict | None:
        ctx = "\n".join(f"{k}: {v}" for k, v in context.items() if v)
        try:
            resp = self.client.messages.create(
                model=self.model,
                max_tokens=1024,
                system=SYSTEM,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                     "data": base64.standard_b64encode(jpeg).decode()}},
                        {"type": "text", "text": f"Source metadata:\n{ctx or '(none)'}"},
                    ],
                }],
                output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
            )
        except anthropic.AuthenticationError:
            raise
        except anthropic.RateLimitError as e:
            log.warning("  caption rate-limited, will retry next run: %s", e.message)
            return None
        except anthropic.APIStatusError as e:
            log.warning("  caption API error %s: %s", e.status_code, e.message)
            return None
        except anthropic.APIConnectionError:
            log.warning("  caption network error, will retry next run")
            return None
        if resp.stop_reason == "refusal":
            log.warning("  caption declined by the model")
            return None
        text = next((b.text for b in resp.content if b.type == "text"), "")
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            log.warning("  caption returned invalid JSON")
            return None
        data["tags"] = [t.lower().strip() for t in data.get("tags", [])][:10]
        data["_usage"] = {"in": resp.usage.input_tokens, "out": resp.usage.output_tokens}
        return data
