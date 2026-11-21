"""Reviewed EXACT source tuples for the unchanged nominal command mirror.

The original Task08 tuple remains in motion_prediction.AUDITED_SOURCE_SHA256.
This adds main 3b95a75 without overwriting it or accepting arbitrary revisions.
Reviewed diff from 69f410a: control adds default-false wrist collision option
and wide_strict_v3; simulation conditionally disables a collision pair. Sequence
commands and contracts are byte-identical. This is NOT collision/IK validation,
nor permission to execute historical frozen plans with new code.
"""
from types import MappingProxyType

MAIN_3B95A75_NOMINAL_SOURCES = MappingProxyType({
    "grasp_a/sequence.py": "7b9c14b6ef05016599fb23155b9e7814c67cd305ead061aa7246738a2ce65b29",
    "grasp_a/simulation.py": "eb1aa3dbcebc48e300f33e416375b31f0cd2a20e80f7aa3aced465cdaf75545c",
    "grasp_a/control.py": "66448b4bd2259e8dc011422c49c384e04fee0cf7ebc3b91728ab1c927e128ae5",
    "grasp_a/sequence_contracts.py": "e1dd49fa119f2e9e16933145e42699ddb4f034d1411dc83124e0ed7296a128e0",
})


# Main 259d308 adds postponed annotation evaluation to control.py only.
# Removing that future import gives the same reviewed AST as main 3b95a75.
# Exact whole-source identity remains required; mixed tuples stay rejected.
MAIN_259D308_NOMINAL_SOURCES = MappingProxyType({
    **MAIN_3B95A75_NOMINAL_SOURCES,
    "grasp_a/control.py": "c2166345fa2531af263a3ae620b5854f0c2bab4bfa9c0494a50aa5092ba1419e",
})


def is_reviewed_nominal_profile(hashes):
    return (hashes == MAIN_3B95A75_NOMINAL_SOURCES or
            hashes == MAIN_259D308_NOMINAL_SOURCES)
