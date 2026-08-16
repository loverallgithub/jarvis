"""Solution research — evidence for the REMEDY, not for the gap.

WHY IT EXISTS
─────────────
`gap_analysis` extracts what a page is MISSING. That is the right input for
deciding what to build, and every claim on need 13 was therefore a gap claim.
Phase F then tried to write sales copy from them.

Measured 2026-08-09: `headline`, `subhead` and `objections` reached 100%
citation coverage while `benefits` and `faq` sat at 0-50% — because the first
three describe the PROBLEM, which gap claims evidence perfectly, and the last
two describe the SOLUTION, which they cannot evidence at all. The copy fell back
to asserting expertise the evidence base did not contain: "a written record
gives you grounds to move", cited to nothing.

No amount of regeneration or metric-tuning fixes that. The evidence base has to
contain solution evidence before copy can cite any.
"""
from __future__ import annotations

import pytest

from jarvis.research import dossier


@pytest.fixture
def stub(monkeypatch):
    """Positioning present, one evidence page, capturing LLM calls."""
    calls: list[str] = []

    async def fetchrow(q, *a):
        if "positioning" in q:
            return {"pain_phrase": "locked out and still charged",
                    "promise": "regain access or stop the charge",
                    "audience": "owner-operators"}
        return {"title": "payabl / automat / account", "pain_statement": "p"}

    async def fetch(q, *a):
        return [{"id": 1, "url": "https://gov.example/chargeback",
                 "title": "Chargeback rules", "body": "Cardholders have 120 days."}]

    async def params():
        return {"max_claims_per_domain": 3}

    monkeypatch.setattr(dossier.db, "fetchrow", fetchrow)
    monkeypatch.setattr(dossier.db, "fetch", fetch)
    monkeypatch.setattr(dossier.ev, "params", params)
    return calls


# ── queries ────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_queries_come_from_the_positioning_not_the_cluster_label(stub, monkeypatch):
    """`needs.title` is a cluster label — on need 13 it is
    "payabl / automat / account", and the capture step turns that into
    "payabl / automat / account software", which finds the VENDOR, not the
    answer."""
    seen = {}

    async def llm(prompt, **kw):
        seen["prompt"] = prompt
        return ("how to dispute a recurring subscription charge\n"
                "card scheme chargeback time limits\n"
                "statutory notice period to cancel a service contract\n")
    monkeypatch.setattr(dossier, "_llm", llm)

    out = await dossier.solution_queries(13, n=6)
    assert "regain access or stop the charge" in seen["prompt"]
    assert "REMEDY" in seen["prompt"]
    assert len(out) == 3
    assert not any("payabl / automat" in q for q in out)


@pytest.mark.asyncio
async def test_commentary_and_numbering_are_stripped(stub, monkeypatch):
    async def llm(prompt, **kw):
        return ("- how to dispute a recurring subscription charge\n"
                "• card scheme chargeback time limits\n"
                "ok\n"                       # too short, dropped
                + "x" * 200 + "\n")          # too long, dropped
    monkeypatch.setattr(dossier, "_llm", llm)
    out = await dossier.solution_queries(13)
    assert out == ["how to dispute a recurring subscription charge",
                   "card scheme chargeback time limits"]


@pytest.mark.asyncio
async def test_the_query_count_is_respected(stub, monkeypatch):
    async def llm(prompt, **kw):
        return "\n".join(f"a query about remedy number {i}" for i in range(20))
    monkeypatch.setattr(dossier, "_llm", llm)
    assert len(await dossier.solution_queries(13, n=4)) == 4


@pytest.mark.asyncio
async def test_no_llm_reply_yields_no_queries_rather_than_junk(stub, monkeypatch):
    async def llm(prompt, **kw):
        return None
    monkeypatch.setattr(dossier, "_llm", llm)
    assert await dossier.solution_queries(13) == []


# ── extraction ─────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_supporting_claims_are_stored_with_the_support_kind(stub, monkeypatch):
    """A support claim must be distinguishable from a gap claim, or the copy
    generator cannot prefer the ones that evidence a remedy."""
    added: list = []

    async def llm(prompt, **kw):
        return ("Cardholders have 120 days from the transaction to raise a "
                "chargeback under the scheme rules.")

    async def add(need_id, text, evidence_id, kind, confidence=0.7, run_id=None):
        added.append((kind, text))
        return len(added)

    monkeypatch.setattr(dossier, "_llm", llm)
    monkeypatch.setattr(dossier, "_add_claim", add)

    out = await dossier.support_analysis(13)
    assert out["claims"] == 1
    assert added[0][0] == dossier.SOLUTION_KIND == "fact"


@pytest.mark.asyncio
async def test_a_page_supporting_nothing_produces_NO_claims(stub, monkeypatch):
    """"NONE" must mean none. Inventing a claim from a page that supports
    nothing is exactly the failure the whole verifier exists to prevent."""
    async def llm(prompt, **kw):
        return "NONE"

    async def add(*a, **k):
        pytest.fail("a claim was recorded from a page that supports nothing")

    monkeypatch.setattr(dossier, "_llm", llm)
    monkeypatch.setattr(dossier, "_add_claim", add)
    assert (await dossier.support_analysis(13))["claims"] == 0


@pytest.mark.asyncio
async def test_short_fragments_are_not_stored_as_claims(stub, monkeypatch):
    async def llm(prompt, **kw):
        return "yes\nok\n120 days\n"
    async def add(*a, **k):
        pytest.fail("a fragment was stored as a claim")
    monkeypatch.setattr(dossier, "_llm", llm)
    monkeypatch.setattr(dossier, "_add_claim", add)
    assert (await dossier.support_analysis(13))["claims"] == 0


@pytest.mark.asyncio
async def test_duplicate_statements_are_recorded_once(stub, monkeypatch):
    async def llm(prompt, **kw):
        line = ("Cardholders have 120 days from the transaction to raise a "
                "chargeback under scheme rules.")
        return f"{line}\n{line}\n"
    added: list = []

    async def add(need_id, text, evidence_id, kind, confidence=0.7, run_id=None):
        added.append(text)
        return len(added)

    monkeypatch.setattr(dossier, "_llm", llm)
    monkeypatch.setattr(dossier, "_add_claim", add)
    await dossier.support_analysis(13)
    assert len(added) == 1


# ── a refusal is not a claim ───────────────────────────────────────────────
@pytest.mark.asyncio
async def test_a_model_REFUSAL_is_never_stored_as_a_claim(stub, monkeypatch):
    """🔴 Observed 2026-08-09: the model replied "I cannot extract checkable
    statements from this page because the..." and that sentence was written to
    `claims` as a cited fact.

    This is the exact Sintra/LinkedIn shape the platform exists to prevent — an
    error string persisted as content and then cited by a product.
    """
    async def llm(prompt, **kw):
        return ("I cannot extract checkable statements from this page because "
                "the content is navigation only.")

    async def add(*a, **k):
        pytest.fail("a model refusal was stored as a claim")

    monkeypatch.setattr(dossier, "_llm", llm)
    monkeypatch.setattr(dossier, "_add_claim", add)
    assert (await dossier.support_analysis(13))["claims"] == 0


@pytest.mark.asyncio
async def test_unfinished_work_markers_are_also_discarded(stub, monkeypatch):
    async def llm(prompt, **kw):
        return "The deadline is TBD according to this page and needs checking."
    async def add(*a, **k):
        pytest.fail("a TBD statement was stored as a claim")
    monkeypatch.setattr(dossier, "_llm", llm)
    monkeypatch.setattr(dossier, "_add_claim", add)
    assert (await dossier.support_analysis(13))["claims"] == 0


@pytest.mark.asyncio
async def test_the_kind_is_one_the_schema_actually_permits(stub):
    """A first version invented "support" and the INSERT died on
    claims_kind_check. The vocabulary already had a word for this."""
    assert dossier.SOLUTION_KIND in {"fact", "gap", "pricing", "competitor",
                                     "feasibility"}


@pytest.mark.asyncio
async def test_markdown_headings_are_not_searched_as_queries(stub, monkeypatch):
    """Observed: "# Search Queries for Subscription Access & Cancellation
    Remedies" was emitted as line 1 and searched verbatim."""
    async def llm(prompt, **kw):
        return ("# Search Queries for Subscription Remedies\n"
                "Here are the queries:\n"
                "FTC unauthorized charges dispute procedure\n"
                "NACHA chargeback resolution process rules\n")
    monkeypatch.setattr(dossier, "_llm", llm)
    out = await dossier.solution_queries(13)
    assert out == ["FTC unauthorized charges dispute procedure",
                   "NACHA chargeback resolution process rules"]


# ── extraction meta-commentary ─────────────────────────────────────────────
# `find_placeholders` catches refusals that OPEN with "I cannot"/"I'm sorry".
# These two walked past it on 2026-08-09 and were stored as cited facts against
# ftc.gov. The tell is not the opening phrase — it is that the sentence talks
# about the EXTRACTION TASK rather than about the world.
@pytest.mark.parametrize("text", [
    "To provide accurate, checkable statements from this FTC announcement, "
    "I would need the full text of the press release.",
    "To extract checkable, useful statements about the rule itself, such as "
    "specific requirements and deadlines, more text is required.",
    "I would need the complete page to identify the deadlines.",
    "The page text provided is truncated and contains only navigation.",
    "No checkable statements are present on this page.",
])
def test_extraction_meta_commentary_is_recognised(text):
    assert dossier._is_extraction_meta(text), text


@pytest.mark.parametrize("text", [
    "Cardholders have 120 days from the transaction date to raise a chargeback.",
    "The FTC's Negative Option Rule requires sellers to provide a simple "
    "cancellation mechanism.",
    "You would need to check your card issuer's stated deadline before day 60.",
    "The page lists four ERP integrations and no others.",
])
def test_a_real_claim_is_not_mistaken_for_meta_commentary(text):
    assert not dossier._is_extraction_meta(text), text


@pytest.mark.asyncio
async def test_the_ftc_shaped_refusal_is_discarded_end_to_end(stub, monkeypatch):
    """The exact text stored against ftc.gov."""
    async def llm(prompt, **kw):
        return ("To provide accurate, checkable statements from this FTC "
                "announcement, I would need the full text of the press release.")

    async def add(*a, **k):
        pytest.fail("extraction meta-commentary was stored as a claim")

    monkeypatch.setattr(dossier, "_llm", llm)
    monkeypatch.setattr(dossier, "_add_claim", add)
    assert (await dossier.support_analysis(13))["claims"] == 0


def test_the_guard_is_local_to_extraction_not_global():
    """"you would need to check your issuer's deadline" is legitimate prose in a
    BUILT ARTIFACT. Adding this to find_placeholders would start failing
    structural verification on good documents."""
    from jarvis.forge.build import find_placeholders
    assert find_placeholders(
        "You would need to check your issuer's deadline before day 60.") == []


# ── option 2: gaps stated as observations, not absences ────────────────────
def test_the_gap_prompt_forbids_absence_phrasing():
    """🔴 Every claim `gap_analysis` produced was an absence — "No mention of
    X", "Lacks Y", "Only Z". That shape is UNVERIFIABLE against an excerpt, and
    three of them (30, 34, 36) each had to be hand-fixed after failing.

    Option 1 made absences decidable. This stops creating them.
    """
    import inspect
    src = inspect.getsource(dossier.gap_analysis)
    assert "STATE EACH GAP AS AN OBSERVATION" in src
    low = src.lower()
    for banned in ("no mention of", "lacks", "does not discuss", "only x"):
        assert banned in low, f"prompt does not forbid {banned!r}"


def test_the_prompt_shows_a_worked_example_of_each():
    """A rule with no example is a rule the model interprets. Both the BAD and
    the GOOD form are shown for two real cases from need 13."""
    import inspect
    src = inspect.getsource(dossier.gap_analysis)
    assert src.count("BAD:") >= 2 and src.count("GOOD:") >= 2
    assert "Tipalti" in src           # the actual claim-30 failure
    assert "invoice OCR" in src       # the actual claim-36 failure
