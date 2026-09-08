"""Dental Intelligence v2 typed contracts.

Keep package import side effects deliberately minimal: archive/provider adapters depend
on the legacy ai package, so importing them here would create an ai<->intelligence
cycle during compatibility-mode startup.
"""

from .concepts import DentalConceptResolver, concept_variant_groups
from .facets import FACET_SPECS, FacetSpec, detect_facets, facet_spec
from .models import *
from .understanding import QuestionContext, understand_question

__all__ = [
    "DentalConceptResolver", "concept_variant_groups", "FacetSpec", "FACET_SPECS",
    "detect_facets", "facet_spec", "QuestionContext", "understand_question",
]
