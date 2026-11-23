from unirag.config import get_settings
from unirag.ingest.chunk_docs import chunk_all


def _chunks():
    return chunk_all(get_settings().docs_dir)


def test_every_grant_has_its_own_chunk_with_metadata():
    grant_chunks = [c for c in _chunks() if c.doc_type == "grant_policy" and len(c.grant_codes) == 1]
    codes = {c.grant_codes[0] for c in grant_chunks}
    assert len(codes) == 15
    ai = next(c for c in grant_chunks if c.grant_codes == ["GR-AI-01"])
    assert ai.year_applicability == [3, 4]
    assert ai.metadata["departments"] == ["CS", "IT"]


def test_chunks_have_context_header_and_section_numbers():
    for c in _chunks():
        assert c.content.startswith(f"[{c.doc_title} > ")
        assert c.effective_date is not None
    handbook = [c for c in _chunks() if c.doc_type == "handbook"]
    assert any(c.section.startswith("§4 ") and "0.4" in c.content for c in handbook)


def test_chunks_are_bounded():
    assert max(len(c.content) for c in _chunks()) < 2600
