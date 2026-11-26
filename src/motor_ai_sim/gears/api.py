"""Versioned core entry points for route, storage and viewer adapters (no IO)."""
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Union, Literal
import uuid
import hashlib
import json
from .common import DictResultMixin, require_number, FieldValidationError
from .gearbox import (StageSpec, Gearbox, DutySpectrum, GearboxEvaluationResult,
                      PairStageSpec, PlanetaryStageSpec, stage_member_ids)
from .lubrication import GearLubricant
from .outline import AssemblyDescription, AssemblyPoseMember, build_chain_assembly

CORE_VERSION = 'gear-core-r4'


def request_hash(request):
    """SHA-256 of the full canonical v1 request, including IDs and viewer options."""
    data = json.dumps(request.to_dict(), sort_keys=True, separators=(',', ':'), allow_nan=False)
    return hashlib.sha256(data.encode('utf8')).hexdigest()


def member_instances(stages):
    """Aggregate target ID -> physical viewer member IDs (carrier is conceptual)."""
    result = {}
    for stage in stages:
        for role, identity in stage_member_ids(stage).items():
            result[identity] = ([identity+'_'+str(i) for i in range(stage.n_planets)]
                if isinstance(stage, PlanetaryStageSpec) and role in ('planet', 'planet2') else [identity])
    return result


@dataclass
class AssemblyPose(DictResultMixin):
    input_angle_deg: float
    members: Dict[str, AssemblyPoseMember]


@dataclass
class GearboxRequest(DictResultMixin):
    stages: List[StageSpec]
    operation: Literal['point', 'spectrum', 'assembly', 'pose'] = 'point'
    request_id: str = field(default_factory=lambda: 'request_'+uuid.uuid4().hex)
    name: str = 'gearbox'
    lubricant: Optional[Union[str, GearLubricant]] = None
    input_speed_rpm: float = 1000.
    torque_Nm: float = 1.
    torque_at: Literal['input', 'output'] = 'input'
    power_direction: Literal['forward', 'reverse'] = 'forward'
    temp_C: float = 70.
    preset_name: str = 'standard'
    spectrum: Optional[DutySpectrum] = None
    housing_area_m2: Optional[float] = None
    h_conv_W_m2K: float = 15.
    input_shaft_mm: float = 10.
    output_shaft_mm: float = 16.
    input_angle_deg: float = 0.
    outline_stride: int = 1
    outline_once: bool = False

    def __post_init__(self):
        if self.operation not in ('point','spectrum','assembly','pose'):
            raise FieldValidationError('/operation', 'Unknown operation')
        if self.torque_at not in ('input','output'):
            raise FieldValidationError('/torque_at', 'Invalid torque boundary')
        if self.power_direction not in ('forward','reverse'):
            raise FieldValidationError('/power_direction', 'Invalid power direction')
        if not isinstance(self.request_id,str) or not self.request_id:
            raise FieldValidationError('/request_id', 'request_id required')
        for key in ('input_speed_rpm','torque_Nm','temp_C'): require_number(key,getattr(self,key))
        for key in ('h_conv_W_m2K','input_shaft_mm','output_shaft_mm'):
            require_number(key,getattr(self,key),positive=True)
        if self.housing_area_m2 is not None: require_number('housing_area_m2',self.housing_area_m2,positive=True)
        require_number('input_angle_deg', self.input_angle_deg)
        require_number('outline_stride', self.outline_stride, positive=True, integer=True)
        if type(self.outline_once) is not bool: raise FieldValidationError('/outline_once', 'Expected bool')
        if (self.operation == 'spectrum') != (self.spectrum is not None):
            raise FieldValidationError('/spectrum', 'Only spectrum operation requires a spectrum')
        if self.spectrum is not None and not isinstance(self.spectrum, DutySpectrum):
            raise FieldValidationError('/spectrum', 'Expected DutySpectrum')
        if not isinstance(self.stages,list) or not self.stages:
            raise FieldValidationError('/stages', 'Expected non-empty stage list')
        from .presets import get_preset
        from .materials import get_gear_material
        from .lubrication import get_gear_lubricant
        try: get_preset(self.preset_name)
        except KeyError as exc: raise FieldValidationError('/preset_name',str(exc),'unknown_reference') from exc
        if isinstance(self.lubricant,str):
            try: get_gear_lubricant(self.lubricant)
            except KeyError as exc: raise FieldValidationError('/lubricant',str(exc),'unknown_reference') from exc
        elif self.lubricant is not None and not isinstance(self.lubricant,GearLubricant):
            raise FieldValidationError('/lubricant', 'Expected catalogue name or GearLubricant')
        seen_ids, seen_names = set(), set()
        for i, stage in enumerate(self.stages):
            path = '/stages/'+str(i)
            if not isinstance(stage,(PairStageSpec,PlanetaryStageSpec)):
                raise FieldValidationError(path,'Expected a stage specification')
            for key, seen in (('stage_id',seen_ids),('name',seen_names)):
                value = getattr(stage,key)
                if value in seen: raise FieldValidationError(path+'/'+key, 'Duplicate '+key)
                seen.add(value)
            for role in ('gear1','gear2','sun','planet','planet2','ring'):
                gear = getattr(stage,role,None)
                if gear is not None:
                    try: get_gear_material(gear.material_name)
                    except KeyError as exc:
                        raise FieldValidationError(path+'/'+role+'/material_name',str(exc),'unknown_reference') from exc
        self.build_gearbox()  # Validate identities before storage or execution.

    def build_gearbox(self):
        return Gearbox(self.stages,name=self.name,lubricant=self.lubricant,
                       housing_area_m2=self.housing_area_m2,h_conv_W_m2K=self.h_conv_W_m2K,
                       input_shaft_mm=self.input_shaft_mm,output_shaft_mm=self.output_shaft_mm)

    def execute(self):
        gb = self.build_gearbox()
        if self.operation in ('assembly', 'pose'):
            assembly = build_chain_assembly(self.stages)
            result = (AssemblyPose(self.input_angle_deg, assembly.pose_at(self.input_angle_deg))
                if self.operation == 'pose' else assembly.with_outline_options(
                    stride=self.outline_stride, once=self.outline_once))
        elif self.operation == 'spectrum':
            result = gb.evaluate_spectrum(self.spectrum,preset_name=self.preset_name,temp_C=self.temp_C)
        else:
            result = gb.evaluate_point(input_speed_rpm=self.input_speed_rpm,torque_Nm=self.torque_Nm,
                torque_at=self.torque_at,power_direction=self.power_direction,temp_C=self.temp_C,
                preset_name=self.preset_name)
        return GearboxResponse(self.request_id, self.operation, result,
            core_version=CORE_VERSION, request_hash=request_hash(self),
            member_instances=member_instances(self.stages))


@dataclass
class GearboxResponse(DictResultMixin):
    request_id: str
    operation: Literal['point','spectrum','assembly','pose']
    result: Union[GearboxEvaluationResult, AssemblyDescription, AssemblyPose]
    core_version: str
    request_hash: str
    member_instances: Dict[str, List[str]]

    def __post_init__(self):
        expected = (AssemblyDescription if self.operation == 'assembly' else
                    AssemblyPose if self.operation == 'pose' else GearboxEvaluationResult)
        if self.operation not in ('point','spectrum','assembly','pose') or not isinstance(self.result,expected):
            raise ValueError('Result does not match requested operation')
        if not isinstance(self.core_version, str) or not self.core_version:
            raise FieldValidationError('/core_version', 'Core version required')
        if not isinstance(self.request_hash,str) or len(self.request_hash) != 64 or any(
                c not in '0123456789abcdef' for c in self.request_hash):
            raise FieldValidationError('/request_hash', 'Expected lowercase SHA-256')


def list_material_catalogue():
    from .materials import list_gear_materials, get_gear_material
    keys = ('name', 'designation', 'heat_treatment', 'quality_class', 'sigma_Hlim_MPa',
            'sigma_Flim_MPa', 'yield_strength_MPa', 'E_MPa', 'density_kg_m3', 'sources', 'flags')
    return [{k: get_gear_material(n).to_dict()[k] for k in keys} for n in list_gear_materials()]


def list_lubricant_catalogue():
    from .lubrication import BUILTIN_GEAR_LUBRICANTS
    return [card.to_dict() for _, card in sorted(BUILTIN_GEAR_LUBRICANTS.items())]


def list_bearing_catalogue():
    from .bearings import repo_bearings, _VALID_BEARING_TYPES
    result = []
    for name in repo_bearings.list_bearings():
        b = repo_bearings.get_bearing(name)
        result.append(dict(name=name, bearing_type=b.type, d_mm=b.d, D_mm=b.D, B_mm=b.B,
            C_kn=b.C_kn, C0_kn=b.C0_kn, contact_angle_deg=b.contact_angle_deg,
            core_supported=b.type in _VALID_BEARING_TYPES and b.C_kn is not None and b.C0_kn is not None))
    return result


def list_preset_catalogue():
    from .presets import BUILTIN_PRESETS
    return [card.to_dict() for _, card in sorted(BUILTIN_PRESETS.items())]
