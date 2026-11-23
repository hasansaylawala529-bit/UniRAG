"""Document path: hybrid search over policy chunks.

score = 0.75 * vector similarity + 0.25 * trigram word similarity + 0.20 if the chunk carries a
grant code named in the query. Candidates come from the vector index (top 40) after metadata
filters, then the hybrid score re-ranks them. Exact codes such as "GR-CY-03" or terms such as
"condonation" are often missed by embeddings alone, which is what the keyword part fixes.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

from ..db import connect
from ..ingest.chunk_docs import GRANT_CODE_RE
from ..llm import get_llm

W_VEC, W_KW, W_CODE = 0.75, 0.25, 0.20


@dataclass
class DocHit:
    chunk_id: int
    doc_title: str
    section: str
    content: str
    doc_type: str
    grant_codes: list[str]
    vec_score: float
    kw_score: float
    score: float
    relevant: bool | None = None
    grade_reason: str | None = None

    @property
    def citation(self) -> str:
        return f"{self.doc_title}, {self.section}"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class DocFilters:
    doc_types: list[str] | None = None
    department: str | None = None          # CS / IT / ENTC: keeps chunks for that dept or for all depts
    year: int | None = None                # keeps chunks for that year or for all years
    grant_codes: list[str] | None = None   # keeps chunks mentioning any of these codes
    extra: dict = field(default_factory=dict)


def search_docs(query: str, *, k: int = 6, filters: DocFilters | None = None) -> list[DocHit]:
    f = filters or DocFilters()
    qv = get_llm().embed_one(query, task_type="RETRIEVAL_QUERY")
    query_codes = sorted(set(GRANT_CODE_RE.findall(query.upper())))

    where, params = [], []
    if f.doc_types:
        where.append("doc_type = ANY(%s)")
        params.append(f.doc_types)
    if f.department:
        where.append("(jsonb_array_length(metadata->'departments') = 0 OR metadata->'departments' ? %s)")
        params.append(f.department.upper())
    if f.year:
        where.append("(year_applicability IS NULL OR %s = ANY(year_applicability))")
        params.append(f.year)
    if f.grant_codes:
        where.append("grant_codes && %s::text[]")
        params.append(f.grant_codes)
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    sql = f"""
        WITH cand AS (
            SELECT chunk_id, doc_title, section, content, doc_type, grant_codes,
                   1 - (embedding <=> %s::vector) AS vec_score
            FROM doc_chunks {where_sql}
            ORDER BY embedding <=> %s::vector
            LIMIT 40
        )
        SELECT *, word_similarity(%s, content) AS kw_score,
               CASE WHEN grant_codes && %s::text[] THEN 1 ELSE 0 END AS code_hit
        FROM cand
    """
    with connect() as conn, conn.cursor() as cur:
        cur.execute(sql, [qv, *params, qv, query, query_codes])
        rows = cur.fetchall()

    hits = []
    for cid, title, section, content, dtype, codes, vs, ks, code_hit in rows:
        score = W_VEC * float(vs) + W_KW * float(ks) + W_CODE * code_hit
        hits.append(DocHit(cid, title, section, content, dtype, list(codes or []),
                           round(float(vs), 4), round(float(ks), 4), round(score, 4)))
    hits.sort(key=lambda h: h.score, reverse=True)
    return hits[:k]



def grant_chunks(*, department: str | None = None, year: int | None = None) -> list[DocHit]:
    """Every per-grant eligibility chunk that passes the metadata filters (no top-k cut-off).

    Grant questions need ALL candidate grants, not the k most similar ones, so this is a pure
    metadata query: department and year filtering is done by the database, CGPA by the rule engine.
    """
    where, params = ["doc_type = 'grant_policy'", "cardinality(grant_codes) = 1"], []
    if department:
        where.append("(jsonb_array_length(metadata->'departments') = 0 OR metadata->'departments' ? %s)")
        params.append(department.upper())
    if year:
        where.append("(year_applicability IS NULL OR %s = ANY(year_applicability))")
        params.append(year)
    sql = ("SELECT chunk_id, doc_title, section, content, doc_type, grant_codes FROM doc_chunks WHERE "
           + " AND ".join(where) + " ORDER BY grant_codes[1]")
    with connect() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    return [DocHit(cid, t, sec, c, dt, list(codes or []), 1.0, 0.0, 1.0) for cid, t, sec, c, dt, codes in rows]
