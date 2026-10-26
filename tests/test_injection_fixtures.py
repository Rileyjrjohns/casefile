"""The broader injection detector keeps flagging the frozen injection cases and nothing else."""

import json
from pathlib import Path

from casefile.bootstrap.evaluations import ScenarioDocument
from casefile.features.decisioning.domain.injection import looks_like_instruction
from casefile.features.evaluation.adapters.outbound.golden import load_suite
from casefile.features.investigation.adapters.outbound.agent.codec import AgentJsonCodec
from casefile.features.investigation.adapters.outbound.tools.seed import load_seed
from casefile.features.investigation.application.tools import CaseDocument, InvestigatorNote

QUOTED_ATTACHMENT = "note-002"


def test_seed_notes_and_documents_are_flagged_only_for_the_quoted_attachment() -> None:
    seed = load_seed(banking=True)
    flagged = {note.record_id for note in seed.notes if looks_like_instruction(note.text)}
    assert flagged == {QUOTED_ATTACHMENT}
    for document in seed.documents:
        text = " ".join((document.title, document.text, *(f.value for f in document.fields)))
        assert not looks_like_instruction(text), document.record_id


def test_golden_records_are_flagged_exactly_where_an_instruction_is_expected() -> None:
    root, codec = Path("evals"), AgentJsonCodec()
    cases = load_suite(root).cases
    assert len(cases) == 30
    for case in cases:
        scenario = ScenarioDocument.model_validate_json(case.scenario_json)
        expected = {s.key for s in case.expected.signals if s.key.startswith("instruction:")}
        flagged: set[str] = set()
        for payload in scenario.evidence:
            for record in codec.evidence(json.dumps(payload)).records:
                if isinstance(record, InvestigatorNote):
                    text = record.text
                elif isinstance(record, CaseDocument):
                    text = " ".join((record.text, *(f.value for f in record.fields)))
                else:
                    continue
                if looks_like_instruction(text):
                    flagged.add(f"instruction:{record.record_id}")
        assert flagged == expected, case.id
        assert looks_like_instruction(json.dumps(scenario.evidence)) is bool(expected), case.id
