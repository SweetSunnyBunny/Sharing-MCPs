"""Vision sidecar -- lets a text-only chat model "see" images.

When the active chat model can't process images (DeepSeek, most local
models), this routes each image to a vision-capable model (default
google/gemini-2.0-flash via OpenRouter) and returns a rich text
description that gets injected into the text model's prompt.

This is *captioning*, not embeddings: the description is plain text the
chat model reads, the same way you'd describe a photo to someone over the
phone. (Embedding-for-search lives separately in Qualia's
mind_store_image.)

Used by services/openai_provider.py. The describer reuses the same
OpenAI-compatible client/base_url/key as the main provider, so on
OpenRouter no extra credentials are needed -- just a model id.
"""

# ANAM GUIDE: EYES FOR TEXT-ONLY MODELS
# What: When the active model can't see pictures, this sends each image to a vision
#       model (Gemini Flash by default) and hands back a written description the
#       text-only model can read instead.
# Called by: services/openai_provider.py only (Claude models see images natively).
# Edit here when: You want a different describer model, a richer description prompt,
#                 or to mark a new model id as "can see images" in _VISION_MODEL_MARKERS.

import logging

log = logging.getLogger(__name__)

# Substrings that mark a model as natively vision-capable. If the active
# chat model matches one of these, we send the image directly and skip the
# sidecar entirely. Kept as substrings so provider-prefixed ids match too
# (e.g. "openai/gpt-4o", "anthropic/claude-3.5-sonnet").
_VISION_MODEL_MARKERS = (
    "gpt-4o", "gpt-4.1", "gpt-4-turbo", "gpt-5", "chatgpt-4o",
    "o1", "o3", "o4-",
    "claude-3", "claude-opus", "claude-sonnet", "claude-haiku", "claude-4",
    "gemini",
    "pixtral", "llava",
    "-vl", "vision",
    "grok-2-vision", "grok-4",
    "internvl", "molmo",
)

# In-process cache: image key -> description. Avoids re-describing the same
# image on every turn (history replay) within a server lifetime.
_DESC_CACHE: dict[str, str] = {}
_CACHE_MAX = 512

_DESCRIBE_PROMPT = (
    "You are the eyes for a text-only AI companion who cannot see images. "
    "Describe this image in rich, faithful detail so they can respond as if "
    "they had seen it themselves. Cover: the main subject and what is "
    "happening; any people (appearance, expression, clothing, body language); "
    "setting, mood, colors, and lighting; and any text visible in the image "
    "(transcribe it exactly). If it is a screenshot, chart, or document, "
    "convey the actual content and data, not just that it exists. Be vivid "
    "but accurate -- never invent details you cannot actually see. Write 1-3 "
    "paragraphs."
)


def model_can_see(model_id: str) -> bool:
    """True if the model id looks natively vision-capable."""
    if not model_id:
        return False
    m = model_id.lower()
    return any(marker in m for marker in _VISION_MODEL_MARKERS)


def _image_key(image_block: dict) -> str:
    source = image_block.get("source", {})
    if source.get("type") == "url":
        return source.get("url", "")
    if source.get("type") == "base64":
        data = source.get("data", "")
        return f"b64:{len(data)}:{data[:48]}"
    return ""


def _to_openai_image_part(image_block: dict) -> dict | None:
    source = image_block.get("source", {})
    if source.get("type") == "url":
        return {"type": "image_url", "image_url": {"url": source.get("url", "")}}
    if source.get("type") == "base64":
        media_type = source.get("media_type", "image/png")
        data = source.get("data", "")
        return {
            "type": "image_url",
            "image_url": {"url": f"data:{media_type};base64,{data}"},
        }
    return None


async def describe_image_block(
    image_block: dict, *, api_key: str, base_url: str, model: str, hint: str = ""
) -> str:
    """Describe a single Anthropic-format image block. Cached by image key."""
    key = _image_key(image_block)
    if key and key in _DESC_CACHE:
        return _DESC_CACHE[key]

    img_part = _to_openai_image_part(image_block)
    if img_part is None:
        return ""

    from services.openai_provider import _get_openai_client

    client = _get_openai_client(api_key, base_url)

    prompt = _DESCRIBE_PROMPT
    if hint:
        prompt += f"\n\nContext -- what Owner said with the image: {hint[:500]}"
    user_content = [img_part, {"type": "text", "text": prompt}]

    try:
        resp = await client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": user_content}],
            max_tokens=700,
            stream=False,
        )
        desc = (resp.choices[0].message.content or "").strip()
    except Exception:
        log.exception("Vision sidecar failed (model=%s)", model)
        return ""

    if desc and key:
        if len(_DESC_CACHE) >= _CACHE_MAX:
            _DESC_CACHE.pop(next(iter(_DESC_CACHE)))
        _DESC_CACHE[key] = desc
    return desc


async def describe_images(
    image_blocks: list[dict] | None,
    *,
    api_key: str,
    base_url: str,
    model: str,
    hint: str = "",
) -> str:
    """Describe the current turn's images and return injectable text."""
    if not image_blocks:
        return ""
    blocks = [b for b in image_blocks if b.get("type") == "image"]
    descs = []
    for i, block in enumerate(blocks):
        d = await describe_image_block(
            block, api_key=api_key, base_url=base_url, model=model, hint=hint
        )
        if d:
            label = f"Image {i + 1}" if len(blocks) > 1 else "Image"
            descs.append(f"[{label} -- described by vision model:\n{d}]")
    return "\n\n".join(descs)


async def enrich_history_images(
    db_messages: list[dict] | None,
    *,
    api_key: str,
    base_url: str,
    model: str,
) -> list[dict]:
    """Replace image blocks in history with their text descriptions.

    Without this, openai_provider._convert_db_messages turns past images
    into the literal placeholder "[image]" and the chat model forgets it
    ever saw anything. Descriptions are cached, so a given history image is
    only sent to the vision model once per server lifetime.
    """
    if not db_messages:
        return db_messages or []

    out: list[dict] = []
    for msg in db_messages:
        content = msg.get("content")
        if not isinstance(content, list):
            out.append(msg)
            continue

        new_content = []
        changed = False
        for block in content:
            if isinstance(block, dict) and block.get("type") == "image":
                desc = await describe_image_block(
                    block, api_key=api_key, base_url=base_url, model=model
                )
                if desc:
                    new_content.append(
                        {"type": "text", "text": f"[Image previously shared:\n{desc}]"}
                    )
                    changed = True
                    continue
            new_content.append(block)

        if changed:
            m = dict(msg)
            m["content"] = new_content
            out.append(m)
        else:
            out.append(msg)
    return out
