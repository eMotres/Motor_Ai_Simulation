"""Version 1 strict recursive JSON records; units remain part of field names.

Each record has schema_version, schema_type and per-scalar metric_availability.
None means unavailable, never zero. Endurance and nonfinite diagnostic values
are distinguished explicitly. Only loaded gears dataclasses may be decoded.
"""
import math
import types
import re
from .common import FieldValidationError
from dataclasses import fields, is_dataclass
from typing import Any, Union, Literal, get_args, get_origin, get_type_hints

SCHEMA_VERSION = 1
META = {'schema_version', 'schema_type', 'metric_availability'}


def _token(key):
    return str(key).replace('~', '~0').replace('/', '~1')


def _availability(obj, name, value):
    state = getattr(obj, 'state', 'valid')
    if name.startswith(('S_F', 'sigma_F', 'Y_')):
        state = getattr(obj, 'bending_state', state)
    elif name.startswith(('S_H', 'sigma_H', 'Z_')):
        state = getattr(obj, 'pitting_state', state)
    if type(obj).__name__ == 'PeakMemberResult':
        if name.startswith('static_'): state = obj.static_state
        if name in ('capacity_cycles','damage_D','total_cycles'): state = obj.fatigue_state
    flags = getattr(obj, 'flags', [])
    if name == 'min_bearing_life_h' and getattr(obj, 'bearing_spectrum_lives', None):
        from .common import worst_state
        state = worst_state([b.state for b in obj.bearing_spectrum_lives])
    if name == 'min_gear_life_h' and getattr(obj, 'gear_miner_lives', None):
        from .common import worst_state
        state = worst_state([m.state for m in obj.gear_miner_lives])
    if value == math.inf and name == 'min_gear_life_h' and state not in ('failed', 'unsupported'):
        return {'status': 'endurance', 'reason': 'zero_damage_in_supported_model',
                'state': state, 'nonfinite': 'positive_infinity'}
    if isinstance(value, float) and not math.isfinite(value):
        token = 'positive_infinity' if value == math.inf else 'negative_infinity' if value == -math.inf else 'nan'
        return {'status': 'unbounded' if value == math.inf else 'invalid',
                'reason': 'nonfinite_diagnostic_value', 'state': state, 'nonfinite': token}
    if value is not None:
        return {'status': 'diagnostic' if state in ('failed', 'unsupported') else 'available',
                'reason': 'computed', 'state': state}
    if 'zero_fatigue_damage_under_miner_model' in flags and name == 'predicted_life_hours' and state not in ('unsupported', 'failed'):
        return {'status': 'endurance', 'reason': 'zero_damage_in_supported_model', 'state': state}
    if name in ('parts_summary', 'thermal_balance', 'preset_evaluation', 'min_gear_life_h', 'min_bearing_life_h') and getattr(obj, 'duty_source', None) == 'single_operating_point':
        return {'status': 'not_requested', 'reason': 'requires_spectrum_evaluation', 'state': state}
    if any('zero_speed' in f or 'standstill' in f for f in flags) and ('efficiency' in name or 'life' in name.lower() or 'L10' in name):
        return {'status': 'undefined', 'reason': 'zero_speed', 'state': state}
    return {'status': 'unavailable', 'reason': 'unsupported_or_missing_input' if state == 'unsupported' else
            'failed_upstream' if state == 'failed' else 'not_supplied_or_not_applicable', 'state': state}


def encode_record(obj):
    from .common import serialize_value
    out = {f.name: serialize_value(getattr(obj, f.name)) for f in fields(obj)}
    if type(obj).__name__ == 'PlanetaryStageSpec': out['kind'] = obj.kind
    availability = {}

    def visit(value, path, field_name):
        if is_dataclass(value): return  # Nested records carry their own contract.
        if isinstance(value, dict):
            for k, v in value.items(): visit(v, path+'/'+_token(k), field_name)
        elif isinstance(value, (list, tuple)):
            for i, v in enumerate(value): visit(v, path+'/'+str(i), field_name)
        elif value is None or isinstance(value, (float, int)) and not isinstance(value, bool):
            availability[path] = _availability(obj, field_name, value)

    for f in fields(obj): visit(getattr(obj, f.name), '/'+f.name, f.name)
    out.update(schema_version=SCHEMA_VERSION, schema_type=type(obj).__name__, metric_availability=availability)
    return out


def _registry():
    from .common import DictResultMixin
    return {cls.__name__: cls for cls in DictResultMixin.__subclasses__()
            if cls.__module__.startswith('motor_ai_sim.gears.') and is_dataclass(cls)}


def _json_only(value, path=""):
    """Reject non-JSON leaves at their precise request location."""
    if value is None or type(value) in (str, bool, int): return
    if type(value) is float and math.isfinite(value): return
    if isinstance(value, list):
        for i, v in enumerate(value): _json_only(v, path+"/"+str(i))
        return
    if isinstance(value, dict) and all(isinstance(k, str) for k in value):
        for k, v in value.items(): _json_only(v, path+"/"+_token(k))
        return
    raise FieldValidationError(path, 'Decoder accepts strict JSON values only')


def _typed(value, hint, path=""):
    try:
        return _typed_impl(value, hint, path)
    except FieldValidationError:
        raise
    except (ValueError, TypeError) as exc:
        raise FieldValidationError(path, str(exc)) from exc


def _typed_impl(value, hint, path):
    origin, args = get_origin(hint), get_args(hint)
    if hint is Any:
        if isinstance(value, dict):
            if 'schema_type' in value: return _decode(value, path=path)
            return {k: _typed(v, Any, path+"/"+_token(k)) for k, v in value.items()}
        if isinstance(value, list): return [_typed(v, Any, path+"/"+str(i)) for i, v in enumerate(value)]
        return value
    if origin in (Union, types.UnionType):
        if isinstance(value, dict) and 'schema_type' in value:
            matches = [choice for choice in args if isinstance(choice, type) and
                       choice.__name__ == value['schema_type']]
            if matches: return _typed(value, matches[0], path)
        for choice in args:
            try: return _typed(value, choice, path)
            except (ValueError, TypeError): pass
        raise ValueError(f'Value does not match {hint}')
    if origin is Literal:
        if value not in args or isinstance(value, bool): raise ValueError(f'Expected one of {args}')
        return value
    if origin in (list, tuple):
        if not isinstance(value, list): raise ValueError('Expected JSON array')
        if origin is list: return [_typed(v,args[0],path+"/"+str(i)) for i,v in enumerate(value)]
        if len(args) == 2 and args[1] is Ellipsis: return tuple(_typed(v,args[0],path+"/"+str(i)) for i,v in enumerate(value))
        if len(value) != len(args): raise ValueError('Tuple length mismatch')
        return tuple(_typed(v,h,path+"/"+str(i)) for i,(v,h) in enumerate(zip(value,args)))
    if origin is dict:
        if not isinstance(value, dict): raise ValueError('Expected JSON object')
        if args[0] is not str: raise ValueError('Only string-key maps supported')
        return {k:_typed(v,args[1],path+"/"+_token(k)) for k,v in value.items()}
    if isinstance(hint, type) and is_dataclass(hint): return _decode(value, hint, path)
    if hint is type(None):
        if value is not None: raise ValueError('Expected null')
    elif hint is float:
        if isinstance(value,bool) or not isinstance(value,(int,float)): raise ValueError('Expected number')
        return float(value)
    elif hint in (str,int,bool):
        if type(value) is not hint: raise ValueError(f'Expected {hint.__name__}')
    else: raise ValueError(f'Unsupported schema annotation {hint}')
    return value


def _decode(data, expected=None, path=""):
    try:
        return _decode_impl(data, expected, path)
    except FieldValidationError:
        raise
    except (ValueError, TypeError, KeyError) as exc:
        raise FieldValidationError(path, str(exc)) from exc


def _decode_impl(data, expected, path):
    if not isinstance(data, dict) or type(data.get('schema_version')) is not int or data['schema_version'] != SCHEMA_VERSION:
        raise FieldValidationError(path+'/schema_version', 'Unsupported or missing schema_version')
    cls = _registry().get(data.get('schema_type'))
    if cls is None or expected is not None and cls is not expected: raise FieldValidationError(path+'/schema_type', 'Unexpected schema_type')
    names = {f.name for f in fields(cls)}
    extra = {'kind'} if cls.__name__ == 'PlanetaryStageSpec' else set()
    expected_names = names | META | extra
    if set(data) != expected_names:
        unknown, missing = set(data)-expected_names, expected_names-set(data)
        key = sorted(unknown or missing)[0]
        raise FieldValidationError(path+'/'+_token(key), 'Unknown field' if unknown else 'Missing field',
                                   'unknown_field' if unknown else 'missing_field')
    availability = data['metric_availability']
    if not isinstance(availability, dict): raise ValueError('metric_availability must be an object')

    def restore(value, path):
        if isinstance(value,dict):
            if 'schema_type' in value: return value
            return {k:restore(v,path+'/'+_token(k)) for k,v in value.items()}
        if isinstance(value,list): return [restore(v,path+'/'+str(i)) for i,v in enumerate(value)]
        info = availability.get(path,{})
        if not isinstance(info,dict): raise ValueError('Malformed metric availability')
        if 'nonfinite' in info:
            if value is not None: raise ValueError('Nonfinite metric must have null value')
            table = {'positive_infinity':math.inf,'negative_infinity':-math.inf,'nan':math.nan}
            if info['nonfinite'] not in table: raise ValueError('Unknown nonfinite metric')
            return table[info['nonfinite']]
        return value

    hints = get_type_hints(cls)
    kwargs = {name:_typed(restore(data[name],'/'+name),hints[name],path+'/'+name) for name in sorted(names)}
    try:
        obj = cls(**kwargs)
    except FieldValidationError as exc:
        raise FieldValidationError(path+exc.path, exc.message, exc.code) from exc
    except (ValueError, TypeError, KeyError) as exc:
        message = str(exc)
        # Existing constructor errors name their field; cross-field errors point
        # at the enclosing record when no single field can be identified.
        match = next((name for name in sorted(names) if re.match(r'\b'+re.escape(name)+r'\b',message)), None)
        raise FieldValidationError(path+('/'+match if match else ''), message) from exc
    if extra and data['kind'] != obj.kind: raise FieldValidationError(path+'/kind', 'Stage kind does not match members')
    # Availability is derived, never a client-controlled qualification override.
    if encode_record(obj) != data: raise FieldValidationError(path+'/metric_availability', 'Noncanonical or inconsistent availability/record')
    return obj


def decode_record(data, expected=None):
    _json_only(data)
    return _decode(data, expected)
