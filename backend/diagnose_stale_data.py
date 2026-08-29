#!/usr/bin/env python3
"""
Diagnostic script to verify stale data in the ingestion pipeline.

Run this to check:
1. Is mock Gemini enabled?
2. What topics are in the database?
3. What content is in Chroma?
4. Is there a mismatch (stale data)?
"""

import asyncio
import sys
import uuid
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).parent))


async def main():
    from core.config import settings
    from core.database import AsyncSessionFactory
    from retrieval.service import RetrievalService
    from db.models import Course, Chapter, Concept
    from sqlalchemy import select
    import chromadb

    print("=" * 80)
    print("INGESTION STALE DATA DIAGNOSTIC")
    print("=" * 80)

    # 1. Check provider
    print("\n[1] CHECKING LLM PROVIDER")
    print(f"    LLM_PROVIDER: {settings.llm_provider}")
    if settings.llm_provider == "mock":
        print("    ⚠️  WARNING: the mock provider is enabled!")
        print("    This is the likely cause of stale data (canned hierarchies).")
        print("    Fix: Set LLM_PROVIDER=gemini in .env")
    elif settings.llm_provider == "ollama":
        print("    ⚠️  NOTE: the local Ollama dev provider is enabled.")
        print("    Hierarchies are real model output, but this is a dev-only provider.")
    else:
        print("    ✓ Using the real Gemini API")

    # 2. List courses and their concepts
    print("\n[2] CHECKING DATABASE COURSE STRUCTURE")
    async with AsyncSessionFactory() as db:
        courses_result = await db.execute(select(Course))
        courses = courses_result.scalars().all()

        if not courses:
            print("    No courses found in database.")
        else:
            for course in courses:
                print(f"\n    Course: {course.name} (id={course.id})")
                print(f"    Status: {course.status}")

                chapters_result = await db.execute(
                    select(Chapter).where(Chapter.course_id == course.id)
                )
                chapters = chapters_result.scalars().all()

                if not chapters:
                    print("      No chapters found.")
                else:
                    for chapter in chapters:
                        print(f"      Chapter: {chapter.title}")

                        concepts_result = await db.execute(
                            select(Concept).where(Concept.chapter_id == chapter.id)
                        )
                        concepts = concepts_result.scalars().all()

                        if not concepts:
                            print("        No concepts found.")
                        else:
                            for concept in concepts:
                                print(f"          - {concept.name} ({concept.difficulty})")

    # 3. Check Chroma collections and content
    print("\n[3] CHECKING CHROMA COLLECTIONS")
    chroma_client = chromadb.PersistentClient(path=settings.chroma_persist_path)

    collections = chroma_client.list_collections()
    if not collections:
        print("    No collections found in Chroma.")
    else:
        for collection in collections:
            print(f"\n    Collection: {collection.name}")
            try:
                # Get first 3 documents as sample
                result = collection.get(limit=3)
                if result["documents"]:
                    print(f"    Sample chunks (showing first 3):")
                    for i, doc in enumerate(result["documents"], 1):
                        preview = doc[:100].replace("\n", " ")
                        print(f"      {i}. {preview}...")
                else:
                    print("      No documents in collection.")
            except Exception as e:
                print(f"      Error reading collection: {e}")

    # 4. Verify mismatch
    print("\n[4] STALE DATA DETECTION")
    async with AsyncSessionFactory() as db:
        courses_result = await db.execute(select(Course))
        courses = courses_result.scalars().all()

        for course in courses:
            concepts_result = await db.execute(
                select(Concept).join(Chapter).where(Chapter.course_id == course.id)
            )
            concepts = concepts_result.scalars().all()
            concept_names = [c.name for c in concepts]

            # Check if course has "Dynamic Programming" concept
            # (the hardcoded concept in mock Gemini)
            if "Dynamic Programming" in concept_names:
                print(
                    f"\n    ⚠️  STALE DATA DETECTED in course '{course.name}':"
                )
                print(f"        Course ID: {course.id}")
                print(f"        Found DAA concept 'Dynamic Programming' in database")
                print(f"        All concepts: {', '.join(concept_names[:5])}")
                print(f"        Likely cause: the mock provider was used for ingestion")
                print(f"        Action: Re-upload with LLM_PROVIDER=gemini")

    print("\n" + "=" * 80)
    print("DIAGNOSTIC COMPLETE")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
