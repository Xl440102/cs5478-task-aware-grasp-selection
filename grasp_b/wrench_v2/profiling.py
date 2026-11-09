"""Repeatable static full-pool timing; returns data and never writes results.

Cold means cleared process-local geometry caches, NOT a flushed OS file cache.
Fresh cold runs reload assets. Warm runs reuse caches but still rehash files.
Mesh microbenchmarks are standalone components and are not added to totals.
"""
import platform
from time import perf_counter

import numpy as np

from grasp_b.models import read_model
from grasp_b.multifamily import VERSION, MultiFamilyConfig, generate_multifamily, pruned_config
from .adapter import audit_candidates
from .geometry import (
    ObjectGeometry, _finger_faces, clear_geometry_caches, geometry_cache_info,
    prepare_finger, read_gripper_geometry, transform_finger_mesh,
)


def _summary(seconds):
    values = np.asarray(seconds)*1000
    return dict(median_ms=float(np.median(values)), p95_ms=float(np.percentile(values, 95)),
                minimum_ms=float(np.min(values)), maximum_ms=float(np.max(values)))


def benchmark_geometry(panda_path, *, pruned=True, runs=3, progress=None):
    if type(runs) is not int or runs < 2:
        raise ValueError("at least two independent runs are required")
    config = pruned_config() if pruned else MultiFamilyConfig()
    rows = []
    for reference in (True, False):
        for mode in ("cold", "warm"):
            clear_geometry_caches()
            if mode == "warm":
                model = read_model("hollow", panda_path)
                obj, gripper = ObjectGeometry.from_model(model), read_gripper_geometry(panda_path, reference=reference)
                candidates, records = generate_multifamily(model, config)
                audit_candidates(candidates[:1], obj, gripper, records=records[:1],
                                 generator_name="grasp_b.multifamily", generator_version=VERSION, reference=reference)
            for repeat in range(runs):
                if mode == "cold":
                    clear_geometry_caches()
                started = perf_counter()
                model = read_model("hollow", panda_path)
                obj = ObjectGeometry.from_model(model)
                gripper = read_gripper_geometry(panda_path, reference=reference)
                asset_s = perf_counter()-started
                # Independent microbenchmarks: no samples/tolerance are changed.
                started = perf_counter()
                for finger in gripper.fingers:
                    transform_finger_mesh(finger)
                transform_s = perf_counter()-started
                _finger_faces.cache_clear()
                started = perf_counter()
                for finger in gripper.fingers:
                    prepare_finger(finger)
                precompute_s = perf_counter()-started
                started = perf_counter()
                for finger in gripper.fingers:
                    prepare_finger(finger)
                hot_lookup_s = perf_counter()-started
                candidates, records = generate_multifamily(model, config)
                result = audit_candidates(candidates, obj, gripper, records=records,
                                         generator_name="grasp_b.multifamily", generator_version=VERSION,
                                         reference=reference, profile=True)
                row = dict(backend="reference" if reference else "optimized", cache=mode, repeat=repeat,
                           asset_preprocessing_s=asset_s, finger_transform_micro_s=transform_s,
                           finger_precompute_cold_micro_s=precompute_s,
                           finger_precompute_hot_micro_s=hot_lookup_s,
                           pool_total_s=result["total_seconds"],
                           first_candidate_s=result["candidates"][0]["seconds"],
                           candidates=result["candidates"], by_family=result["by_family"],
                           cache_info=geometry_cache_info())
                rows.append(row)
                if progress:
                    progress(dict(backend=row["backend"], cache=mode, repeat=repeat,
                                  candidates=len(candidates), seconds=row["pool_total_s"]))
    summary = {}
    for backend in ("reference", "optimized"):
        for mode in ("cold", "warm"):
            subset = [r for r in rows if r["backend"] == backend and r["cache"] == mode]
            samples = [c for r in subset for c in r["candidates"]]
            summary[backend+"_"+mode] = dict(
                asset_preprocessing=_summary([r["asset_preprocessing_s"] for r in subset]),
                finger_transform_micro=_summary([r["finger_transform_micro_s"] for r in subset]),
                finger_precompute_cold_micro=_summary([r["finger_precompute_cold_micro_s"] for r in subset]),
                finger_precompute_hot_micro=_summary([r["finger_precompute_hot_micro_s"] for r in subset]),
                pool_total=_summary([r["pool_total_s"] for r in subset]),
                first_candidate=_summary([r["first_candidate_s"] for r in subset]),
                per_candidate=_summary([r["seconds"] for r in samples]),
                stages={name: _summary([r["stages_s"][name] for r in samples]) for name in
                        ("setup_s", "primitive_intersection_s", "contact_sampling_s",
                         "surface_validation_s", "contactspec_construction_s")})
    return dict(python=platform.python_version(), numpy=np.__version__, runs_per_configuration=runs,
                candidate_count=len(rows[0]["candidates"]), summary=summary, runs=rows,
                baseline_coverage=rows[0]["by_family"], asset_sha256=model.asset_sha256,
                panda_sha256=gripper.panda_sha256, os_cache_flushed=False,
                microbenchmarks_are_not_additive=True)
