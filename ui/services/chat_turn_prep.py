"""Turn preparation helpers for websocket chat requests."""


import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path

from config import (
    IMAGE_ALLOWED_EXTENSIONS,
    IMAGES_DIR,
    PUBLIC_BASE_URL,
    USE_DIRECT_API,
)
from services.attachment_context import build_audio_note, build_document_note
from services.chat_flow import build_user_message_metadata
from services.session_lifecycle import (
    SessionMode,
    build_messages_array,
    build_orientation_context,
)
from services.session_manager import (
    auto_title_conversation,
    get_messages,
    save_message,
)
from services.skill_runtime import build_skill_catalog_hint, build_skill_injection


@dataclass
class PreparedChatTurn:
    prompt: str
    context_block: str
    image_content_blocks: list[dict]
    skill_context: str
    db_messages: list[dict] | None
    context_notice: dict | None
    active_categories: set[str]
    user_message_id: str | None = None


def build_image_content_block(image_path: Path) -> dict | None:
    """Build an Anthropic-format image content block from a local file."""
    if not image_path.exists():
        return None
    fname = image_path.name
    image_url = f"{PUBLIC_BASE_URL}/api/images/file/{fname}"
    return {
        "type": "image",
        "source": {"type": "url", "url": image_url},
    }


from services.runtime_metrics import measure as _measure_runtime


@_measure_runtime("turn_preparation")
async def prepare_chat_turn(
    *,
    db,
    conversation_id: str,
    identity: str,
    text: str,
    images_info: list[dict],
    documents_info: list[dict],
    active_categories: set[str],
    log: logging.Logger,
    skip_user_save: bool = False,
    audio_info: list[dict] | None = None,
) -> PreparedChatTurn:
    """Persist the user turn and build all inputs needed for streaming."""
    from services.provider_router import resolve_provider_for_identity

    audio_info = audio_info or []

    user_message_id = None
    if not skip_user_save:
        user_metadata = build_user_message_metadata(
            images_info, documents_info, audio_info,
        )

        user_message_id = await save_message(
            db,
            conversation_id,
            "user",
            text,
            identity=identity,
            metadata=user_metadata,
        )

        if text:
            await auto_title_conversation(db, conversation_id, text)
    else:
        source_rows = await db.execute_fetchall(
            "SELECT id FROM messages WHERE conversation_id=? AND role='user' ORDER BY rowid DESC LIMIT 1",
            (conversation_id,),
        )
        if source_rows:
            user_message_id = source_rows[0]["id"]

    prompt = text
    image_content_blocks: list[dict] = []

    # Provider routing decides BOTH image handling and history injection.
    # API-routed providers (direct Anthropic, OpenAI-compatible) need real
    # image content blocks and a pre-built history transcript; CLI providers
    # (claude-code PTY, codex) read images from disk via their own tools and
    # own history injection internally. Resolved per-identity so an override
    # (e.g. one identity on codex/openai) gets the right treatment.
    provider, _provider_config = await resolve_provider_for_identity(identity)
    api_routed = USE_DIRECT_API or provider in (
        "anthropic", "openai", "openrouter", "lmstudio", "ollama"
    )

    if images_info:
        image_notes = []
        for img in images_info:
            safe_img_name = Path(img.get("filename", "")).name
            img_path = IMAGES_DIR / safe_img_name
            if api_routed:
                block = build_image_content_block(img_path)
                if block:
                    image_content_blocks.append(block)
            elif img_path.suffix.lower() in IMAGE_ALLOWED_EXTENSIONS:
                image_notes.append(
                    f"[Owner shared an image: {img_path}\n"
                    f"Use the Read tool to view it.]"
                )
        if image_notes:
            image_block = "\n\n".join(image_notes)
            prompt = f"{image_block}\n\n{text}" if text else image_block

    if documents_info:
        doc_notes = await asyncio.to_thread(
            lambda: [build_document_note(doc) for doc in documents_info]
        )
        docs_block = "\n\n".join(doc_notes)
        prompt = f"{docs_block}\n\n{prompt}" if prompt else docs_block

    if audio_info:
        audio_notes = [build_audio_note(clip) for clip in audio_info]
        audio_block = "\n\n".join(audio_notes)
        prompt = f"{audio_block}\n\n{prompt}" if prompt else audio_block


    from services.reaction_notices import (
        pop_reaction_notices, format_reaction_notices,
    )
    reaction_notice = format_reaction_notices(
        pop_reaction_notices(conversation_id, identity)
    )
    if reaction_notice:
        prompt = f"{reaction_notice}\n\n{prompt}" if prompt else reaction_notice

    context_block = await build_orientation_context(
        db=db,
        conversation_id=conversation_id,
        identity=identity,
        query_text=text,
        mode=SessionMode.INTERACTIVE,
        owner_connected=True,
    )


    auto_skill_context, matched_skills = build_skill_injection(
        text or "", identity=identity
    )
    # Codex already receives its native skill catalog from the runtime. Sending
    # Anam's second full menu as well costs ~6.5K characters every turn and
    # presents two partly-overlapping maps. Keep matched Claude skills (their
    # instructions may not exist in Codex's roots), but do not duplicate the
    # menu itself on the Codex lane.
    catalog_context = (
        "" if provider == "codex" else build_skill_catalog_hint(identity=identity)
    )
    if auto_skill_context and catalog_context:
        skill_context = f"{catalog_context}\n\n{auto_skill_context}"
    else:
        skill_context = auto_skill_context or catalog_context

    if matched_skills:
        log.info(
            "Auto-loaded %d skill(s) for %s: %s",
            len(matched_skills),
            identity,
            ", ".join(matched_skills),
        )

    db_messages = None
    context_notice = None

    if api_routed or provider == "codex":
        # Codex ignores this when its own thread resumes. If that thread is
        # missing or cannot resume, app-server uses the real transcript to
        # bootstrap instead of depending on oversized orientation summaries.
        db_messages = await build_messages_array(
            db,
            conversation_id,
            exclude_last_user=True,
        )

    return PreparedChatTurn(
        user_message_id=user_message_id,
        prompt=prompt,
        context_block=context_block,
        image_content_blocks=image_content_blocks,
        skill_context=skill_context,
        db_messages=db_messages,
        context_notice=context_notice,
        active_categories=set(active_categories),
    )
