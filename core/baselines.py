"""Explicit, compatible, completed and quality-approved baseline selection."""
from core.reports import Completion, FingerprintReport, Provenance
from db.crud import get_run_by_id, get_fingerprint_by_run
from db.models import RunStatus


async def resolve_baseline(db, baseline_run_id, current_run_id, provenance):
    if not baseline_run_id:
        raise ValueError("baseline_run_id is required unless baseline_bootstrap=true")
    if str(baseline_run_id) == str(current_run_id):
        raise ValueError("The current run cannot be its own baseline")
    run = await get_run_by_id(db, str(baseline_run_id))
    if run is None:
        raise ValueError("Baseline run does not exist; bootstrap and review a complete quality-passing run first")
    if run.status != RunStatus.COMPLETE:
        raise ValueError("Baseline run must be complete")
    completion = Completion.model_validate(run.completion)
    if not completion.complete:
        raise ValueError("Baseline evaluation is incomplete")
    baseline_provenance = Provenance.model_validate(run.provenance)
    if (provenance.dataset_sha256, provenance.configuration_sha256) != (baseline_provenance.dataset_sha256, baseline_provenance.configuration_sha256):
        raise ValueError("Baseline has an incompatible dataset or evaluation configuration; bootstrap a compatible baseline")
    fp = await get_fingerprint_by_run(db, str(baseline_run_id))
    report = FingerprintReport.model_validate(fp.report if fp else None)
    if report.run_id != run.id or report.provenance != baseline_provenance or report.completion != completion:
        raise ValueError("Baseline report identity or provenance is inconsistent")
    if not report.quality_passed or (report.comparison_status == "compared" and not report.gate_passed):
        raise ValueError("Baseline did not pass required quality/regression thresholds")
    if len(run.scores) != completion.expected or {c.category: c.question_count for c in run.categories} != provenance.configuration["category_counts"]:
        raise ValueError("Baseline persisted score coverage is incomplete")
    return run, baseline_provenance
