"""Portable identity and cone-nesting checks extracted unchanged from final audits.

No historical dataset, reconstruction batch, scoring or physics entrypoint.
"""
import hashlib
import json
from pathlib import Path
import numpy as np


def exact_candidates(records):
    """Validate frozen public records without quaternion renormalization."""
    from grasp_a.contracts import Candidate, Pose
    result = {}
    for v in records:
        Candidate.from_dict(v)  # Validation only; never substitute normalized values.
        if v['id'] in result:
            raise ValueError('duplicate frozen candidate ID')
        result[v['id']] = Candidate(**dict(v,
            grasp_pose=Pose(tuple(v['grasp_pose']['position']), tuple(v['grasp_pose']['quaternion'])),
            pregrasp_pose=Pose(tuple(v['pregrasp_pose']['position']), tuple(v['pregrasp_pose']['quaternion']))))
    return result



def verify_manifest(folder):
    folder=Path(folder);checks=json.loads((folder/'checksums.json').read_text())
    for filename,expected in checks.items():
        if hashlib.sha256((folder/filename).read_bytes()).hexdigest()!=expected:
            raise ValueError('frozen artifact identity mismatch: '+filename)
    return len(checks)



def verify_nested(low,high):
    if high.requested_directions%low.requested_directions: return False
    ratio=high.requested_directions//low.requested_directions
    columns={(c.contact_index,c.friction_direction_index):i for i,c in enumerate(high.columns)}
    for i,col in enumerate(low.columns):
        j=columns.get((col.contact_index,col.friction_direction_index*ratio))
        if j is None or not np.array_equal(low.matrix[:,i],high.matrix[:,j]):return False
    return True
