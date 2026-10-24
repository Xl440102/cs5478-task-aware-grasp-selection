"""Prepare a shared B candidate/score artifact without filtering or execution."""
from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
from pathlib import Path

from grasp_a.contracts import candidate_sha256
from grasp_b.features import feature_sidecar
from grasp_b.generation import DEFAULT_CONFIG, ROOT, generate_candidates, write_candidates
from grasp_b.geometry import read_geometry
from grasp_b.prototype import DEFAULT_SCORING, assign_geometry_scores
from grasp_b.scoring import GEOMETRY_SCORE_VERSION, normalized_weights

from .baselines import EXPERIMENTAL_SCOPE
from .cache import canonical_json


def _config(value, default: Path, label: str) -> tuple[dict, Path | None]:
    if isinstance(value, dict):
        config, source = deepcopy(value), None
    else:
        source = default if value is None else Path(value).resolve()
        config = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise ValueError(f"{label} must be a JSON object")
    canonical_json(config)
    return config, source


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value) -> None:
    with path.open("x", encoding="utf-8") as output:
        output.write(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def prepare_b_inputs(output_dir, *, generation_config=None, scoring_config=None) -> dict:
    """Write new scored candidates, a public provenance file and private audits.

    Configurations may be dictionaries or JSON paths; omitted values use B's
    checked-in defaults. The output directory must not exist. Generation and
    score exclusions depend only on static models/configuration, never episodes.
    All compared C methods must use the returned shared candidate file.
    """
    output = Path(output_dir).resolve()
    if output.exists():
        raise FileExistsError(f"B input output directory already exists: {output}")
    generation, generation_path = _config(generation_config, DEFAULT_CONFIG, "generation_config")
    scoring, scoring_path = _config(scoring_config, DEFAULT_SCORING, "scoring_config")
    if (scoring.get("version") != GEOMETRY_SCORE_VERSION
            or scoring.get("scope") != EXPERIMENTAL_SCOPE):
        raise ValueError("requires B's experimental geometry scoring version and scope")
    normalized = normalized_weights(scoring.get("weights"))
    import pybullet_data
    mug = ROOT / "assets/mug.urdf"
    panda = Path(pybullet_data.getDataPath()) / "franka_panda/panda.urdf"
    geometry = read_geometry(mug, panda)
    candidates, generated = generate_candidates(generation, geometry)
    scored, rows = assign_geometry_scores(candidates, geometry, scoring["weights"])
    if not scored:
        raise ValueError("B scoring produced no usable candidates; inspect generation/configuration")
    import grasp_a.contracts as contracts_module
    from grasp_b import features, generation as generation_module, geometry as geometry_module
    from grasp_b import prototype, scoring as scoring_module, selectors
    source_paths = [mug, panda, Path(__file__), Path(contracts_module.__file__),
                    *(Path(module.__file__) for module in
                      (features, generation_module, geometry_module, prototype, scoring_module, selectors))]
    source_paths.extend(path for path in (generation_path, scoring_path) if path is not None)
    source_hashes = {str(path.resolve()): _sha(path) for path in source_paths}
    output.mkdir(parents=True, exist_ok=False)
    candidates_path = output / "candidates.scored.json"
    # B's writer is safe here because this directory was exclusively created.
    scored = write_candidates(candidates_path, scored)
    features_path = output / "features.internal.json"
    _write_json(features_path, feature_sidecar(scored, geometry, source_hashes))
    report_path = output / "report.internal.json"
    report = {
        "schema_version": "grasp_c_b_report_v1",
        "visibility": "internal_diagnostics_not_selector_input",
        "scope": EXPERIMENTAL_SCOPE, "score_version": GEOMETRY_SCORE_VERSION,
        "generation_config": generation, "scoring_config": scoring,
        "generation_config_canonical_sha256": hashlib.sha256(canonical_json(generation).encode()).hexdigest(),
        "scoring_config_canonical_sha256": hashlib.sha256(canonical_json(scoring).encode()).hexdigest(),
        "geometry": asdict(geometry), "generated_candidates": generated,
        "generated_candidate_count": len(candidates), "scored_candidate_count": len(scored),
        "score_breakdown": rows,
        "excluded_ids": [row["candidate_id"] for row in rows if row["status"] != "ok"],
        "candidate_sha256": candidate_sha256(scored),
        "source_sha256": source_hashes,
        "input_policy": "static generation, known URDF geometry and predeclared weights only",
        "unimplemented": ["formal_M1_validation", "M2_task_rule", "independent_task_suitability"],
    }
    _write_json(report_path, report)
    provenance = {
        "schema_version": "grasp_c_b_inputs_v1", "scope": EXPERIMENTAL_SCOPE,
        "score_version": GEOMETRY_SCORE_VERSION, "score_status": "scored_experimental",
        "weights": deepcopy(scoring["weights"]), "normalized_weights": normalized,
        "generation_version": generation["version"], "candidate_count": len(scored),
        "candidate_sha256": candidate_sha256(scored), "candidate_file_sha256": _sha(candidates_path),
        "report_file": report_path.name, "report_sha256": _sha(report_path),
        "features_file": features_path.name, "features_sha256": _sha(features_path),
        "source_sha256": source_hashes,
    }
    provenance_path = output / "provenance.json"
    _write_json(provenance_path, provenance)
    return {"candidates": str(candidates_path), "provenance": str(provenance_path),
            "report": str(report_path), "features": str(features_path)}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True)
    parser.add_argument("--generation-config")
    parser.add_argument("--scoring-config")
    args = parser.parse_args(argv)
    result = prepare_b_inputs(args.out, generation_config=args.generation_config,
                              scoring_config=args.scoring_config)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
