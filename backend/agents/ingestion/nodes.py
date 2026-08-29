"""
agents/ingestion/nodes.py
--------------------------
Six LangGraph node functions for the course ingestion graph.

Execution order enforced by graph.py:
    validate → parse → build_hierarchy → create_course_collection
             → chunk_and_embed → persist_to_db

IMPORTANT: create_course_collection_node MUST run before chunk_and_embed_node
because add_chunks() requires the ChromaDB collection to already exist.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import TYPE_CHECKING, Any

from agents.ingestion.state import IngestionState
from retrieval.models import Chunk

if TYPE_CHECKING:
    from retrieval.service import RetrievalService
    from main import GeminiProClient
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# Allowed MIME types for uploaded course files
_ALLOWED_MIME = {"application/pdf", "application/vnd.ms-powerpoint",
                 "application/vnd.openxmlformats-officedocument.presentationml.presentation",
                 "text/plain"}
_MAX_FILE_BYTES = 50 * 1024 * 1024  # 50 MB per file


# ── Node 1: validate_files_node ───────────────────────────────────────────────

def validate_files_node(state: IngestionState) -> IngestionState:
    """
    Validate each uploaded file for MIME type and size.
    Sets status="failed" and error on first violation.
    """
    for f in state["files"]:
        if f["mime_type"] not in _ALLOWED_MIME:
            return {**state, "status": "failed",
                    "error": f"Unsupported file type: {f['mime_type']} for {f['filename']}"}
        if len(f["content"]) > _MAX_FILE_BYTES:
            return {**state, "status": "failed",
                    "error": f"File too large: {f['filename']} exceeds 50 MB"}
    return {**state, "status": "running"}


# ── Node 2: parse_content_node ────────────────────────────────────────────────

def parse_content_node(state: IngestionState) -> IngestionState:
    """
    Extract text from each file using the appropriate parser.
      - PDF  → pdfplumber (page-by-page)
      - PPTX → python-pptx (slide-by-slide)
      - TXT  → plain decode
    """
    import io
    parsed: list[dict[str, Any]] = []
    course_id = state["course_id"]

    for f in state["files"]:
        mime = f["mime_type"]
        raw = f["content"]
        filename = f["filename"]

        logger.info(
            "[INGESTION] parse_content_node: course_id=%s filename=%s mime_type=%s",
            course_id, filename, mime
        )

        if mime == "application/pdf":
            import pdfplumber
            with pdfplumber.open(io.BytesIO(raw)) as pdf:
                for page_num, page in enumerate(pdf.pages, start=1):
                    text = page.extract_text() or ""
                    if text.strip():
                        # Log first 500 chars of extracted text
                        preview = text[:500].replace("\n", "\\n")
                        logger.debug(
                            "[INGESTION] PDF page extracted: file=%s page=%d len=%d preview=%r",
                            filename, page_num, len(text), preview
                        )
                        parsed.append({
                            "source_file": filename,
                            "page_or_slide": page_num,
                            "text": text,
                        })

        elif "presentationml" in mime or mime == "application/vnd.ms-powerpoint":
            from pptx import Presentation
            prs = Presentation(io.BytesIO(raw))
            for slide_num, slide in enumerate(prs.slides, start=1):
                texts = []
                for shape in slide.shapes:
                    if hasattr(shape, "text") and shape.text.strip():
                        texts.append(shape.text.strip())
                if texts:
                    combined_text = "\n".join(texts)
                    preview = combined_text[:500].replace("\n", "\\n")
                    logger.debug(
                        "[INGESTION] PPTX slide extracted: file=%s slide=%d len=%d preview=%r",
                        filename, slide_num, len(combined_text), preview
                    )
                    parsed.append({
                        "source_file": filename,
                        "page_or_slide": slide_num,
                        "text": combined_text,
                    })

        else:  # text/plain
            text = raw.decode("utf-8", errors="replace")
            preview = text[:500].replace("\n", "\\n")
            logger.debug(
                "[INGESTION] TXT file extracted: file=%s len=%d preview=%r",
                filename, len(text), preview
            )
            parsed.append({
                "source_file": filename,
                "page_or_slide": None,
                "text": text,
            })

    total_text = "".join(p["text"] for p in parsed)
    logger.info(
        "[INGESTION] parse_content_node complete: course_id=%s pages=%d total_chars=%d",
        course_id, len(parsed), len(total_text)
    )

    return {**state, "parsed_content": parsed}


# ── Node 3: build_hierarchy_node ──────────────────────────────────────────────

_HIERARCHY_PROMPT = """\
You are a course architect. Analyze the following course material and produce a structured hierarchy.

Return ONLY valid JSON with this exact shape:
{{
  "course_title": "...",
  "chapters": [
    {{
      "title": "...",
      "order_index": 0,
      "concepts": [
        {{
          "name": "...",
          "description": "...",
          "keywords": ["..."],
          "difficulty": "beginner|intermediate|advanced",
          "order_index": 0,
          "prerequisites": []
        }}
      ]
    }}
  ]
}}

Course material:
{content}
"""


def build_hierarchy_node(
    state: IngestionState,
    gemini_pro: "GeminiProClient",
) -> IngestionState:
    """
    Call Gemini Pro to generate a course→chapters→concepts JSON hierarchy
    from the parsed content.
    """
    course_id = state["course_id"]

    # Combine all parsed text for the prompt (truncated to avoid token limits)
    combined = "\n\n".join(
        f"[{p['source_file']}, p.{p['page_or_slide']}]\n{p['text']}"
        for p in state["parsed_content"]
    )[:32_000]  # ~8k tokens; Gemini Pro context window is much larger

    # Log the combined content that will be sent to LLM
    content_preview = combined[:500].replace("\n", "\\n")
    logger.info(
        "[INGESTION] build_hierarchy_node: course_id=%s combined_content_chars=%d preview=%r",
        course_id, len(combined), content_preview
    )

    prompt = _HIERARCHY_PROMPT.format(content=combined)

    # Log the exact prompt being sent
    logger.debug(
        "[INGESTION] build_hierarchy_node: prompt_chars=%d first_500_chars=%r",
        len(prompt), prompt[:500].replace("\n", "\\n")
    )

    logger.info(
        "[INGESTION] Calling gemini_pro.generate() for course_id=%s", course_id
    )
    raw = gemini_pro.generate(prompt, temperature=0.3)

    # Log the response from Gemini
    logger.info(
        "[INGESTION] gemini_pro.generate() response received: course_id=%s response_chars=%d",
        course_id, len(raw)
    )
    logger.debug(
        "[INGESTION] Raw Gemini response (first 1000 chars): %r",
        raw[:1000].replace("\n", "\\n")
    )

    # Strip markdown code fences if Gemini wraps the JSON
    raw = raw.strip()
    if raw.startswith("```"):
        raw = "\n".join(raw.split("\n")[1:])
    if raw.endswith("```"):
        raw = raw[: raw.rfind("```")]

    try:
        hierarchy = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.error(
            "[INGESTION] JSON parse error for course_id=%s: %s\nRaw response: %r",
            course_id, exc, raw[:500]
        )
        return {**state, "status": "failed",
                "error": f"Hierarchy JSON parse error: {exc}"}

    # Log extracted hierarchy
    chapters = hierarchy.get("chapters", [])
    total_concepts = sum(len(ch.get("concepts", [])) for ch in chapters)
    logger.info(
        "[INGESTION] Hierarchy extracted: course_id=%s chapters=%d total_concepts=%d",
        course_id, len(chapters), total_concepts
    )

    # Log chapter/concept names for verification
    for ch_idx, ch in enumerate(chapters):
        ch_name = ch.get("title", "")
        concepts = [c.get("name", "") for c in ch.get("concepts", [])]
        logger.debug(
            "[INGESTION] Chapter %d: %r concepts=%r",
            ch_idx, ch_name, concepts
        )

    return {**state, "hierarchy": hierarchy}


# ── Node 4: create_course_collection_node ─────────────────────────────────────

def create_course_collection_node(
    state: IngestionState,
    retrieval_service: "RetrievalService",
) -> IngestionState:
    """
    Create the ChromaDB collection for this course.

    This node MUST execute before chunk_and_embed_node.
    add_chunks() will raise if the collection does not exist.
    """
    course_id = state["course_id"]

    # Log collection name derivation
    collection_name = f"course_{str(course_id).replace('-', '_')}"
    logger.info(
        "[INGESTION] create_course_collection_node: course_id=%s collection_name=%s",
        course_id, collection_name
    )

    retrieval_service.create_course_collection(course_id)

    logger.info(
        "[INGESTION] ChromaDB collection created/verified: course_id=%s collection_name=%s",
        course_id, collection_name
    )

    return state


# ── Node 5: chunk_and_embed_node ──────────────────────────────────────────────

_CHUNK_SIZE = 400   # characters
_CHUNK_OVERLAP = 60


def chunk_and_embed_node(
    state: IngestionState,
    retrieval_service: "RetrievalService",
) -> IngestionState:
    """
    Split parsed content into fixed-size overlapping chunks and call
    retrieval_service.add_chunks() to embed and persist them.

    REQUIRES: create_course_collection_node has already executed.
    """
    course_id = state["course_id"]
    chunks: list[Chunk] = []

    logger.info(
        "[INGESTION] chunk_and_embed_node: course_id=%s parsed_pages=%d chunk_size=%d overlap=%d",
        course_id, len(state["parsed_content"]), _CHUNK_SIZE, _CHUNK_OVERLAP
    )

    for page in state["parsed_content"]:
        text: str = page["text"]
        start = 0
        chunk_index = 0
        while start < len(text):
            end = start + _CHUNK_SIZE
            chunk_text = text[start:end].strip()
            if chunk_text:
                chunk_id = f"{course_id}_{page['source_file']}_{page['page_or_slide']}_{chunk_index}"
                chunk = Chunk(
                    text=chunk_text,
                    source_file=page["source_file"],
                    page_or_slide=page["page_or_slide"],
                    course_id=course_id,
                    chunk_id=chunk_id,
                )
                chunks.append(chunk)

                # Log first chunk from this page as verification
                if chunk_index == 0:
                    logger.debug(
                        "[INGESTION] First chunk from file=%s page=%d: %r",
                        page['source_file'], page['page_or_slide'],
                        chunk_text[:100].replace("\n", "\\n")
                    )

                chunk_index += 1
            start += _CHUNK_SIZE - _CHUNK_OVERLAP

    logger.info(
        "[INGESTION] Chunks created: course_id=%s total_chunks=%d total_chars=%d",
        course_id, len(chunks), sum(len(c.text) for c in chunks)
    )

    # Add to Chroma with logging
    logger.info(
        "[INGESTION] Adding chunks to ChromaDB: course_id=%s chunk_count=%d",
        course_id, len(chunks)
    )
    retrieval_service.add_chunks(course_id, chunks)
    logger.info(
        "[INGESTION] Chunks embedded and persisted: course_id=%s chunk_count=%d",
        course_id, len(chunks)
    )

    return {**state, "chunks": [{"chunk_id": c.chunk_id, "text": c.text} for c in chunks]}


# ── Node 6: persist_to_db_node ────────────────────────────────────────────────

async def persist_to_db_node(
    state: IngestionState,
    db: "AsyncSession",
) -> IngestionState:
    """
    Write Chapter, Concept, and ConceptPrerequisite ORM records to PostgreSQL.
    Update IngestionJob.status to "complete" and Course.status to "ready" —
    this is the only node that runs on the success path after all content has
    been persisted, so it is the correct place to mark the course usable.
    """
    from db.models import Chapter, Concept, ConceptPrerequisite, Course, IngestionJob
    from sqlalchemy import select

    hierarchy = state["hierarchy"]
    course_id = state["course_id"]
    job_id = state["job_id"]

    logger.info(
        "[INGESTION] persist_to_db_node: course_id=%s job_id=%s chapters=%d",
        course_id, job_id, len(hierarchy.get("chapters", []))
    )

    chapter_count = 0
    concept_count = 0

    for ch_data in hierarchy.get("chapters", []):
        ch_title = ch_data["title"]
        chapter = Chapter(
            course_id=course_id,
            title=ch_title,
            order_index=ch_data.get("order_index", 0),
        )
        db.add(chapter)
        await db.flush()  # get chapter.id
        chapter_count += 1

        logger.debug(
            "[INGESTION] Chapter inserted: course_id=%s chapter_id=%s title=%r",
            course_id, chapter.id, ch_title
        )

        concept_map: dict[str, uuid.UUID] = {}
        for c_data in ch_data.get("concepts", []):
            c_name = c_data["name"]
            concept = Concept(
                chapter_id=chapter.id,
                name=c_name,
                description=c_data.get("description"),
                keywords=json.dumps(c_data.get("keywords", [])),
                difficulty=c_data.get("difficulty"),
                order_index=c_data.get("order_index", 0),
            )
            db.add(concept)
            await db.flush()
            concept_map[c_name] = concept.id
            concept_count += 1

            logger.debug(
                "[INGESTION] Concept inserted: course_id=%s chapter=%r concept_id=%s name=%r",
                course_id, ch_title, concept.id, c_name
            )

        # Prerequisite links (second pass after all concept IDs are known)
        for c_data in ch_data.get("concepts", []):
            for prereq_name in c_data.get("prerequisites", []):
                if prereq_name in concept_map:
                    link = ConceptPrerequisite(
                        concept_id=concept_map[c_data["name"]],
                        prerequisite_id=concept_map[prereq_name],
                    )
                    db.add(link)
                    logger.debug(
                        "[INGESTION] Prerequisite link created: %r → %r",
                        c_data["name"], prereq_name
                    )

    logger.info(
        "[INGESTION] Database records prepared: course_id=%s chapters=%d concepts=%d",
        course_id, chapter_count, concept_count
    )

    # Mark ingestion job complete
    result = await db.execute(
        select(IngestionJob).where(IngestionJob.id == job_id)
    )
    job = result.scalar_one_or_none()
    if job:
        job.status = "complete"
        logger.info("[INGESTION] IngestionJob marked complete: job_id=%s", job_id)

    # Mark the course ready — this is the transition that unblocks
    # assessment generation and tutoring session creation.
    course_result = await db.execute(select(Course).where(Course.id == course_id))
    course = course_result.scalar_one_or_none()
    if course:
        old_status = course.status
        course.status = "ready"
        logger.info(
            "[INGESTION] Course status updated: course_id=%s %s → ready",
            course_id, old_status
        )

    await db.commit()
    logger.info(
        "[INGESTION] persist_to_db_node complete: course_id=%s chapters=%d concepts=%d",
        course_id, chapter_count, concept_count
    )

    return {**state, "status": "complete"}
