"""Rules for interpreting a robot's fixed-link assembly in collision checks."""
from __future__ import annotations


def fixed_link_groups(parents: list[int], fixed_links: set[int]) -> dict[int, int]:
    """Map each link to its rigid component, including the base link (-1).

    Link i is attached to parents[i] by joint i. Only fixed joints merge
    components; a revolute or prismatic joint always separates components.
    """
    roots = {i:i for i in range(-1,len(parents))}

    def find(index: int) -> int:
        while roots[index] != index:
            roots[index] = roots[roots[index]]
            index = roots[index]
        return index

    for link in fixed_links:
        if link < 0 or link >= len(parents):
            raise ValueError(f"invalid fixed link index {link}")
        parent = parents[link]
        if parent not in roots:
            raise ValueError(f"invalid parent index {parent}")
        roots[find(link)] = find(parent)
    return {index:find(index) for index in roots}
