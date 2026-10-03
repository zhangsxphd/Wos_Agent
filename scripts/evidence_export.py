"""Join validated evidence to canonical records, without screening away rows."""

from .extractors.schema_validator import EvidenceValidator


EVIDENCE_COLUMNS = ["uid", "doi", "title", "crop", "soil_type", "experimental_scale", "irrigation", "water_regime",
                    "amendments", "carbon_metrics", "nitrogen_metrics", "microbial_metrics", "ghg_metrics",
                    "yield_metrics", "water_metrics", "methods", "main_findings", "explicit_mechanisms", "explicit_limitations", "needs_fulltext"]
INFERENCE_COLUMNS = ["uid", "title", "mechanistic_interpretation", "connection_to_user_research", "possible_gap", "transferable_idea"]


def identity(record):
    return record.get("uid"), record.get("doi"), record.get("title")


def evidence_tables(canonical, matrix):
    validator = EvidenceValidator()
    mapped = {}
    for row in matrix:
        key = identity(row)
        if key in mapped:
            raise ValueError("Evidence input contains duplicate identities")
        mapped[key] = row
    if set(mapped) != {identity(record) for record in canonical} or len(mapped) != len(canonical):
        raise ValueError("Evidence must match every canonical record exactly; no missing or extra rows")
    evidence_rows, inference_rows = [], []
    for record in canonical:
        row = mapped[identity(record)]
        validator.validate(row, record)
        flags = row.get("completeness")
        if flags is None:
            row = validator.normalize(row, record)
            flags = row["completeness"]
        e, inf = row["evidence"], row["inference"]
        system, treatments, measurements = e["study_system"], e["treatments"], e["measurements"]
        join = lambda values: "\n".join(values)
        evidence_rows.append([
            row["uid"], row["doi"], row["title"], join(system["crop"]), join(system["soil_type"]), system["experimental_scale"],
            join(treatments["irrigation"]), join(treatments["water_regime"]), join(treatments["amendments"]),
            join(measurements["carbon"]), join(measurements["nitrogen"]), join(measurements["microbial"]),
            join(measurements["greenhouse_gases"]), join(measurements["yield"]), join(measurements["water_use"]),
            join(e["methods"]), join([item["claim"] for item in e["findings"]]), join(e["mechanisms_explicit"]),
            join(e["limitations_explicit"]), flags["needs_fulltext"],
        ])
        inference_rows.append([row["uid"], row["title"], *[
            "\n".join("[inference] " + item["statement"] + " [" + "; ".join(item["evidence_anchors"]) + "]" for item in inf[key])
            for key in INFERENCE_COLUMNS[2:]
        ]])
    return evidence_rows, inference_rows
