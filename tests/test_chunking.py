"""Tests for the token-aware chunker.

Uses a fake whitespace tokenizer (1 token == 1 word) so the packing/overlap logic
can be tested without downloading the real transformers model.
"""
import pytest

import app.services.chunking as ck


class _FakeTokenizer:
    def encode(self, text, add_special_tokens=False):
        return text.split()

    def decode(self, ids):
        return " ".join(ids)


@pytest.fixture(autouse=True)
def _fake_tokenizer(monkeypatch):
    monkeypatch.setattr(ck, "_tokenizer", lambda: _FakeTokenizer())


def test_basic_packing_and_sentence_overlap():
    text = " ".join(f"a{i} b{i}." for i in range(8))  # 8 two-token sentences
    chunks = ck.chunk_page(text, page=3, start_ordinal=0, chunk_tokens=6, overlap_tokens=2)

    assert all(c.token_count <= 6 for c in chunks)          # never over budget
    assert [c.ordinal for c in chunks] == list(range(len(chunks)))  # contiguous
    assert all(c.page == 3 for c in chunks)                 # page metadata kept
    for a, b in zip(chunks, chunks[1:]):                    # one-sentence overlap
        assert a.text.split()[-2:] == b.text.split()[:2]
    assert chunks[-1].text.endswith("b7.")                  # full coverage to the end


@pytest.mark.parametrize("overlap", [0, 3, 8])
def test_long_unit_is_hard_split_within_budget(overlap):
    mono = " ".join(f"t{i}" for i in range(25))  # one 25-token "sentence"
    chunks = ck.chunk_page(mono, page=1, start_ordinal=0, chunk_tokens=10, overlap_tokens=overlap)

    assert all(c.token_count <= 10 for c in chunks)      # budget respected
    assert sum(c.token_count for c in chunks) >= 25      # nothing dropped


def test_explicit_zero_overlap_is_not_overridden_by_default():
    mono = " ".join(f"t{i}" for i in range(25))
    chunks = ck.chunk_page(mono, page=1, start_ordinal=0, chunk_tokens=10, overlap_tokens=0)
    # With true zero overlap the 25 tokens pack into exactly [10, 10, 5].
    assert [c.token_count for c in chunks] == [10, 10, 5]


def test_blank_page_yields_no_chunks():
    assert ck.chunk_page("   \n\n  ", page=1, start_ordinal=0) == []
