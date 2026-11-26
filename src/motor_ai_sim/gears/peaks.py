"""Peak event fatigue and static safety within explicitly supplied strength limits.

Static allowable stresses are NOT inferred from fatigue plateaux or core yield.
They must describe the actual tooth-root / hardened-flank failure criterion at
the evaluation temperature. No internal-root method is supplied here.
"""
import math
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
from .common import DictResultMixin, ValidityState, require_number, worst_state
from .materials import GearMaterial


@dataclass(frozen=True)
class StaticStrengthLimits(DictResultMixin):
    source: str
    temperature_C: float
    root_MPa: Optional[float] = None
    flank_MPa: Optional[float] = None

    def __post_init__(self):
        if not isinstance(self.source,str) or not self.source.strip(): raise ValueError('Static strength provenance required')
        require_number('temperature_C',self.temperature_C)
        for name in ('root_MPa','flank_MPa'):
            if getattr(self,name) is not None: require_number(name,getattr(self,name),positive=True)
        if self.root_MPa is None and self.flank_MPa is None: raise ValueError('At least one static allowable required')


@dataclass
class PeakMemberResult(DictResultMixin):
    target_id: str
    target_name: str
    mechanism: str
    stress_peak_MPa: float
    static_allowable_MPa: Optional[float]
    static_safety: Optional[float]
    static_state: ValidityState
    static_passed: bool
    peak_events: int
    engagements_per_event: Optional[int]
    total_cycles: Optional[float]
    capacity_cycles: Optional[float]
    damage_D: Optional[float]
    fatigue_state: ValidityState
    state: ValidityState
    applicability_evidence: Dict[str, Any] = field(default_factory=dict)
    missing_inputs: List[str] = field(default_factory=list)
    flags: List[str] = field(default_factory=list)
    method: str = 'Peak_static_stress_ratio_and_discrete_Miner_events'


def rate_peak_member(*, target_id, target_name, mechanism, stress_MPa,
                     material: GearMaterial, fatigue_allowable_factor,
                     events, engagements_per_event, speed_known,
                     static_allowable_MPa, static_source, required_static_safety,
                     static_upstream='estimated', fatigue_upstream='estimated'):
    require_number('stress_MPa',stress_MPa,minimum=0.)
    require_number('events',events,integer=True,minimum=0)
    require_number('fatigue_allowable_factor',fatigue_allowable_factor,positive=True)
    require_number('required_static_safety',required_static_safety,positive=True)
    if engagements_per_event is not None:
        require_number('engagements_per_event',engagements_per_event,integer=True,minimum=0)
    if static_allowable_MPa is not None: require_number('static_allowable_MPa',static_allowable_MPa,positive=True)
    missing, flags = [], []
    safety = None
    static_state = static_upstream
    if static_allowable_MPa is None or not static_source:
        static_state = worst_state([static_state,'unsupported'])
        missing.append('provenanced_static_allowable_MPa_at_evaluation_temperature')
    elif static_state not in ('failed','unsupported'):
        safety = static_allowable_MPa/stress_MPa if stress_MPa > 0. else math.inf
        if safety < required_static_safety: static_state = 'failed'
    count = capacity = damage = None
    fatigue_state = fatigue_upstream
    if events == 0:
        count, damage = 0., 0.
    elif engagements_per_event is None or not speed_known:
        fatigue_state = worst_state([fatigue_state,'unsupported'])
        if engagements_per_event is None: missing.append('per_member_tooth_engagements_per_peak_event')
        if not speed_known: missing.append('peak_speed_out_rpm')
    else:
        count = events*engagements_per_event
        if fatigue_state not in ('failed','unsupported'):
            if mechanism == 'bending_root':
                capacity = material.cycles_for_bending_stress(stress_MPa,allowable_factor=fatigue_allowable_factor)
            elif mechanism == 'pitting_flank':
                capacity = material.cycles_for_contact_stress(stress_MPa,allowable_factor=fatigue_allowable_factor)
            else: raise ValueError('Unknown peak fatigue mechanism')
            if capacity is None:
                fatigue_state = "unsupported"
                flags.append("stress_above_static_plateau_outside_SN_domain")
                missing.append("fatigue_model_above_static_plateau")
            else:
                damage = 0. if capacity == math.inf else count/capacity
    return PeakMemberResult(target_id,target_name,mechanism,stress_MPa,static_allowable_MPa,
        safety,static_state,static_state not in ('failed','unsupported') and safety is not None and safety >= required_static_safety,
        events,engagements_per_event,count,capacity,damage,fatigue_state,
        worst_state([static_state,fatigue_state]),
        applicability_evidence={'static_source':static_source,'fatigue_material':material.name,
            'fatigue_allowable_factor':fatigue_allowable_factor,
            'count_basis':'per tooth per block: peak_events * engagements_per_event',
            'static_basis':'supplied allowable / calculated local peak stress'},
        missing_inputs=missing,flags=flags)
