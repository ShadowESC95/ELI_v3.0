"""
HyDE — Hypothetical Document Embeddings for ELI.
Generates a hypothetical answer to a query, then uses that answer's
embedding to search the vector store. Improves recall for vague queries.
"""
from __future__ import annotations
from typing import List


from eli.utils.log import get_logger
log = get_logger(__name__)

def expand_query_hyde(
    query: str,
    inference_fn,  # callable(prompt) -> str
    n_hypothetical: int = 1,
) -> List[str]:
    """
    Given a query, generate n_hypothetical synthetic answers using the LLM,
    then return those answers as expanded search queries.
    Falls back to [query] if generation fails.
    """
    prompt = (
        f"Write a short, factual answer (2-3 sentences) to this question "
        f"as if you already know the answer from memory:\n\n{query}\n\nAnswer:"
    )
    hypotheticals = [query]  # always include original
    for _ in range(n_hypothetical):
        try:
            hyp = inference_fn(prompt)
            if hyp and len(hyp.strip()) > 10:
                hypotheticals.append(hyp.strip())
        except Exception:
            log.debug("suppressed exception", exc_info=True)
    return hypotheticals

