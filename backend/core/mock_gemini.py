"""
core/mock_gemini.py
-------------------
Mock Gemini clients for development and testing.
Simulates API responses without consuming quota.

IMPORTANT: These mocks are development-only. For production or final testing,
use real Gemini API by setting USE_MOCK_GEMINI=false in .env

FIX FOR STALE DATA BUG:
The previous version returned the same hardcoded DAA hierarchy regardless of input.
This version detects the course topic from the prompt and returns appropriate hierarchies.
"""

import hashlib
import json
import logging
import re

logger = logging.getLogger(__name__)

# Embedding dimension of the real model (text-embedding-004) we're mocking.
_MOCK_EMBED_DIM = 768

# Minimal stopword list — removes high-frequency filler tokens that otherwise
# dilute the bag-of-words vector and drown out the topical signal.
_MOCK_STOPWORDS = frozenset({
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "to", "of", "in", "on", "for", "and", "or", "but", "as", "at", "by",
    "with", "from", "that", "this", "these", "those", "it", "its", "into",
    "over", "under", "which", "where", "how", "when", "what", "who",
    "along", "during", "most", "through", "than", "then", "so", "such",
})


def _token_hash_embed(text: str) -> list[float]:
    """
    Deterministic bag-of-words feature-hashing embedding (like scikit-learn's
    HashingVectorizer). Same text → same vector across process restarts, and
    texts sharing vocabulary have positive cosine similarity — unlike a
    whole-string hash, whose avalanche effect destroys any lexical relationship.

    Used only by the mock Gemini clients for local development. Its similarity
    scale is far lower than a trained dense model, so callers use a separate
    (lower) groundedness threshold when USE_MOCK_GEMINI is enabled.
    """
    vector = [0.0] * _MOCK_EMBED_DIM
    tokens = re.findall(r"[a-z0-9]+", text.lower())
    for token in tokens:
        if token in _MOCK_STOPWORDS or len(token) < 2:
            continue
        # MD5-of-token (not Python's salted hash()) so the mock is deterministic
        # across separate process/background-task invocations, not just within one.
        digest = hashlib.md5(token.encode()).digest()
        index = int.from_bytes(digest[:4], byteorder="big") % _MOCK_EMBED_DIM
        sign = 1.0 if digest[-1] % 2 == 0 else -1.0
        vector[index] += sign

    # L2-normalize so cosine distance behaves as expected downstream.
    norm = sum(v * v for v in vector) ** 0.5
    if norm > 0:
        vector = [v / norm for v in vector]
    return vector


class MockGeminiProClient:
    """Mock Gemini Pro client that generates realistic mock course hierarchies."""

    def __init__(self, api_key: str = "", model: str = "gemini-2.5-pro") -> None:
        self._model_name = model
        logger.info(f"[MOCK] Initialized {model}")

    def generate(
        self,
        prompt: str,
        temperature: float = 0.7,
        system_instruction: str = "",
    ) -> str:
        """
        Generate mock hierarchy JSON from course material.

        Attempts to detect topic from prompt and return appropriate hierarchy.
        Falls back to generic hierarchy if detection fails.
        """
        logger.debug("[MOCK] generate() called with prompt (first 200 chars): %r", prompt[:200])

        # Detect course topic from prompt content
        hierarchy = self._detect_and_generate_hierarchy(prompt)

        result = json.dumps(hierarchy)
        logger.debug("[MOCK] Returning mock hierarchy with %d chapters", len(hierarchy.get("chapters", [])))
        return result

    def _detect_and_generate_hierarchy(self, prompt: str) -> dict:
        """
        Detect course topic from prompt and return appropriate mock hierarchy.

        Looks for keywords in the prompt to determine what kind of hierarchy to return.
        """
        prompt_lower = prompt.lower()

        # Detect course type from keywords
        if self._contains_keywords(prompt_lower, ["earth", "geology", "tectonic", "plate", "rock", "mineral", "fossil"]):
            logger.info("[MOCK] Detected Earth Science course")
            return self._generate_earth_science_hierarchy()

        elif self._contains_keywords(prompt_lower, ["physics", "force", "motion", "energy", "wave", "quantum"]):
            logger.info("[MOCK] Detected Physics course")
            return self._generate_physics_hierarchy()

        elif self._contains_keywords(prompt_lower, ["biology", "cell", "organism", "ecosystem", "protein", "dna"]):
            logger.info("[MOCK] Detected Biology course")
            return self._generate_biology_hierarchy()

        elif self._contains_keywords(prompt_lower, ["history", "revolution", "civilization", "war", "empire", "dynasty"]):
            logger.info("[MOCK] Detected History course")
            return self._generate_history_hierarchy()

        elif self._contains_keywords(prompt_lower, ["algorithm", "dynamic", "sort", "search", "tree", "graph", "data", "structure"]):
            logger.info("[MOCK] Detected Data Structures & Algorithms course")
            return self._generate_dsa_hierarchy()

        else:
            logger.info("[MOCK] No specific course detected, returning generic hierarchy")
            return self._generate_generic_hierarchy()

    @staticmethod
    def _contains_keywords(text: str, keywords: list[str]) -> bool:
        """Check if text contains at least 2 of the given keywords."""
        count = sum(1 for kw in keywords if kw in text)
        return count >= 2

    @staticmethod
    def _generate_earth_science_hierarchy() -> dict:
        """Generate a mock Earth Science hierarchy."""
        return {
            "course_title": "Introduction to Earth Science",
            "chapters": [
                {
                    "title": "Plate Tectonics",
                    "order_index": 0,
                    "concepts": [
                        {
                            "name": "Plate Boundaries",
                            "description": "Types and characteristics of plate boundaries",
                            "keywords": ["convergent", "divergent", "transform", "boundary"],
                            "difficulty": "beginner",
                            "order_index": 0,
                            "prerequisites": [],
                        },
                        {
                            "name": "Continental Drift",
                            "description": "Movement of continents over geological time",
                            "keywords": ["drift", "pangaea", "seafloor", "spreading"],
                            "difficulty": "intermediate",
                            "order_index": 1,
                            "prerequisites": ["Plate Boundaries"],
                        },
                    ],
                },
                {
                    "title": "Rocks and Minerals",
                    "order_index": 1,
                    "concepts": [
                        {
                            "name": "Mineral Classification",
                            "description": "Properties and classification of minerals",
                            "keywords": ["hardness", "crystal", "luster", "cleavage"],
                            "difficulty": "beginner",
                            "order_index": 0,
                            "prerequisites": [],
                        },
                        {
                            "name": "Rock Cycle",
                            "description": "Formation and transformation of rocks",
                            "keywords": ["igneous", "sedimentary", "metamorphic", "weathering"],
                            "difficulty": "intermediate",
                            "order_index": 1,
                            "prerequisites": ["Mineral Classification"],
                        },
                    ],
                },
            ],
        }

    @staticmethod
    def _generate_physics_hierarchy() -> dict:
        """Generate a mock Physics hierarchy."""
        return {
            "course_title": "Fundamentals of Physics",
            "chapters": [
                {
                    "title": "Mechanics",
                    "order_index": 0,
                    "concepts": [
                        {
                            "name": "Newton's Laws",
                            "description": "Fundamental principles of classical mechanics",
                            "keywords": ["force", "acceleration", "inertia"],
                            "difficulty": "beginner",
                            "order_index": 0,
                            "prerequisites": [],
                        },
                        {
                            "name": "Energy Conservation",
                            "description": "Conservation of mechanical energy",
                            "keywords": ["kinetic", "potential", "work", "energy"],
                            "difficulty": "intermediate",
                            "order_index": 1,
                            "prerequisites": ["Newton's Laws"],
                        },
                    ],
                },
                {
                    "title": "Waves and Oscillations",
                    "order_index": 1,
                    "concepts": [
                        {
                            "name": "Simple Harmonic Motion",
                            "description": "Periodic oscillatory motion",
                            "keywords": ["oscillation", "amplitude", "frequency", "period"],
                            "difficulty": "intermediate",
                            "order_index": 0,
                            "prerequisites": [],
                        },
                        {
                            "name": "Wave Properties",
                            "description": "Characteristics and behavior of waves",
                            "keywords": ["wavelength", "frequency", "interference", "diffraction"],
                            "difficulty": "advanced",
                            "order_index": 1,
                            "prerequisites": ["Simple Harmonic Motion"],
                        },
                    ],
                },
            ],
        }

    @staticmethod
    def _generate_biology_hierarchy() -> dict:
        """Generate a mock Biology hierarchy."""
        return {
            "course_title": "General Biology",
            "chapters": [
                {
                    "title": "Cell Biology",
                    "order_index": 0,
                    "concepts": [
                        {
                            "name": "Cell Structure",
                            "description": "Prokaryotic and eukaryotic cell components",
                            "keywords": ["nucleus", "organelle", "membrane", "cytoplasm"],
                            "difficulty": "beginner",
                            "order_index": 0,
                            "prerequisites": [],
                        },
                        {
                            "name": "Cell Division",
                            "description": "Mitosis and meiosis processes",
                            "keywords": ["mitosis", "meiosis", "chromosome", "prophase"],
                            "difficulty": "intermediate",
                            "order_index": 1,
                            "prerequisites": ["Cell Structure"],
                        },
                    ],
                },
                {
                    "title": "Genetics",
                    "order_index": 1,
                    "concepts": [
                        {
                            "name": "DNA Structure",
                            "description": "Molecular structure and function of DNA",
                            "keywords": ["dna", "helix", "nucleotide", "base", "pair"],
                            "difficulty": "intermediate",
                            "order_index": 0,
                            "prerequisites": [],
                        },
                        {
                            "name": "Inheritance Patterns",
                            "description": "Mendel's laws and genetic inheritance",
                            "keywords": ["allele", "genotype", "phenotype", "dominant"],
                            "difficulty": "intermediate",
                            "order_index": 1,
                            "prerequisites": ["DNA Structure"],
                        },
                    ],
                },
            ],
        }

    @staticmethod
    def _generate_history_hierarchy() -> dict:
        """Generate a mock History hierarchy."""
        return {
            "course_title": "World History",
            "chapters": [
                {
                    "title": "Ancient Civilizations",
                    "order_index": 0,
                    "concepts": [
                        {
                            "name": "Egyptian Civilization",
                            "description": "Development and culture of ancient Egypt",
                            "keywords": ["pharaoh", "pyramid", "nile", "hieroglyph"],
                            "difficulty": "beginner",
                            "order_index": 0,
                            "prerequisites": [],
                        },
                        {
                            "name": "Classical Greece",
                            "description": "Ancient Greek city-states and culture",
                            "keywords": ["athens", "sparta", "democracy", "philosophy"],
                            "difficulty": "intermediate",
                            "order_index": 1,
                            "prerequisites": [],
                        },
                    ],
                },
                {
                    "title": "Medieval Period",
                    "order_index": 1,
                    "concepts": [
                        {
                            "name": "Feudalism",
                            "description": "Social and political structure of medieval Europe",
                            "keywords": ["feudal", "vassal", "lord", "manor"],
                            "difficulty": "intermediate",
                            "order_index": 0,
                            "prerequisites": [],
                        },
                    ],
                },
            ],
        }

    @staticmethod
    def _generate_dsa_hierarchy() -> dict:
        """Generate a mock Data Structures & Algorithms hierarchy."""
        return {
            "course_title": "Introduction to Data Structures and Algorithms",
            "chapters": [
                {
                    "title": "Fundamentals",
                    "order_index": 0,
                    "concepts": [
                        {
                            "name": "Arrays",
                            "description": "Contiguous memory storage for homogeneous elements",
                            "keywords": ["array", "index", "element", "access"],
                            "difficulty": "beginner",
                            "order_index": 0,
                            "prerequisites": [],
                        },
                        {
                            "name": "Linked Lists",
                            "description": "Dynamic memory allocation using pointers",
                            "keywords": ["node", "pointer", "link", "traversal"],
                            "difficulty": "beginner",
                            "order_index": 1,
                            "prerequisites": ["Arrays"],
                        },
                    ],
                },
                {
                    "title": "Searching and Sorting",
                    "order_index": 1,
                    "concepts": [
                        {
                            "name": "Binary Search",
                            "description": "Efficient search on sorted data",
                            "keywords": ["binary", "divide", "conquer", "log(n)"],
                            "difficulty": "intermediate",
                            "order_index": 0,
                            "prerequisites": ["Arrays"],
                        },
                        {
                            "name": "Quicksort",
                            "description": "Fast divide-and-conquer sorting algorithm",
                            "keywords": ["partition", "divide", "conquer", "O(n log n)"],
                            "difficulty": "intermediate",
                            "order_index": 1,
                            "prerequisites": ["Arrays"],
                        },
                    ],
                },
                {
                    "title": "Advanced Topics",
                    "order_index": 2,
                    "concepts": [
                        {
                            "name": "Dynamic Programming",
                            "description": "Optimization technique using memoization",
                            "keywords": ["memoization", "subproblems", "optimization"],
                            "difficulty": "advanced",
                            "order_index": 0,
                            "prerequisites": ["Binary Search", "Quicksort"],
                        },
                        {
                            "name": "Graph Algorithms",
                            "description": "Traversal and pathfinding in graph structures",
                            "keywords": ["BFS", "DFS", "shortest", "path"],
                            "difficulty": "advanced",
                            "order_index": 1,
                            "prerequisites": ["Linked Lists"],
                        },
                    ],
                },
            ],
        }

    @staticmethod
    def _generate_generic_hierarchy() -> dict:
        """Generate a generic mock hierarchy when no specific course is detected."""
        return {
            "course_title": "Introduction to General Topics",
            "chapters": [
                {
                    "title": "Fundamentals",
                    "order_index": 0,
                    "concepts": [
                        {
                            "name": "Introduction",
                            "description": "Basic concepts and overview",
                            "keywords": ["basics", "overview", "introduction"],
                            "difficulty": "beginner",
                            "order_index": 0,
                            "prerequisites": [],
                        },
                        {
                            "name": "Core Principles",
                            "description": "Core principles and foundational ideas",
                            "keywords": ["principles", "foundation", "core"],
                            "difficulty": "beginner",
                            "order_index": 1,
                            "prerequisites": ["Introduction"],
                        },
                    ],
                },
                {
                    "title": "Advanced Topics",
                    "order_index": 1,
                    "concepts": [
                        {
                            "name": "Applications",
                            "description": "Real-world applications and practice",
                            "keywords": ["application", "practice", "real-world"],
                            "difficulty": "intermediate",
                            "order_index": 0,
                            "prerequisites": ["Core Principles"],
                        },
                    ],
                },
            ],
        }

    def generate_deterministic(self, prompt: str) -> str:
        """Deterministic generation (used for grading)."""
        return self.generate(prompt, temperature=0.0)

    def embed(self, text: str) -> list[float]:
        """Deterministic bag-of-words mock embedding — see _token_hash_embed."""
        return _token_hash_embed(text)


class MockGeminiFlashClient:
    """Mock Gemini Flash client for high-throughput operations."""

    def __init__(self, api_key: str = "", model: str = "gemini-2.5-flash") -> None:
        self._model_name = model
        logger.info(f"[MOCK] Initialized {model}")

    def generate(
        self,
        prompt: str,
        temperature: float = 0.7,
        system_instruction: str = "",
    ) -> str:
        """Generate mock assessment response."""
        logger.debug("[MOCK] Flash generate() called")
        # For assessment generation, return a mock assessment with empty questions
        # The real generation happens via Gemini Flash, not this mock
        return json.dumps({
            "questions": []
        })

    def generate_deterministic(self, prompt: str) -> str:
        """Deterministic generation."""
        return self.generate(prompt, temperature=0.0)

    def embed(self, text: str) -> list[float]:
        """Deterministic bag-of-words mock embedding — see _token_hash_embed."""
        return _token_hash_embed(text)
