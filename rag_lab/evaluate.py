"""R6 — Measure retrieval quality (the most important stage).

RAG is an information-retrieval problem: measure it, don't vibe it. Score the
index against a fixed eval set and compute hit@k and MRR. Then tune ONE variable
at a time (chunk size, top_k, embedding model, re-ranking) and watch the numbers.

Eval set format (see eval_set.example.json):
  [{"question": "...", "expected_doc": "rag_lab/data/x.pdf", "expected_page": 12}]

A hit = a retrieved chunk whose (document_id, page) matches the expected pair.
"""
import json

from rag_lab.retrieve import build_index


def evaluate(eval_path: str = "rag_lab/eval_set.json", top_k: int = 5) -> dict:
    """Return {'hit@k': float, 'mrr': float}. TODO: implement (R6).

    For each question: run index.search(q, top_k); find the rank of the first hit.
      hit@k  = (# questions with a hit in top_k) / total
      MRR    = mean of 1/rank over questions (0 if no hit)
    """
    cases = json.load(open(eval_path))
    index = build_index()

    hitk = []
    mrr = []
    num_cases = len(cases)
    
    for case in cases:
        
        question = case['question']
        expected_doc = case['expected_doc']
        expected_page = case['expected_page']

        hits = []
        qmrr = 0
        results = index.search(query=question, top_k=top_k)
        
        for i in range(len(results)):
            found_doc = results[i].chunk.document_id
            found_page = results[i].chunk.page
            if found_doc == expected_doc and expected_page == found_page:
                hits.append(results[i])
                if qmrr == 0:
                    qmrr = 1 / (i + 1)

        # hit@k is BINARY per question: did ANY relevant chunk land in top_k?
        # (num_hits / top_k would be precision@k, a different metric.)
        hitk.append(1.0 if hits else 0.0)
        mrr.append(qmrr)

    return {'mean hit@k': sum(hitk) / num_cases,
            'mean mrr': sum(mrr) / num_cases}


if __name__ == "__main__":
    print(evaluate())
