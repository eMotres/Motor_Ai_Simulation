"""Regenerates the JSON fixtures next to this file from the gear core (schema v1).

Run from a checkout with the core on PYTHONPATH:  python make_fixtures.py <out_dir>
The web tests only read the JSON; nothing here runs in CI.
"""
import json
import sys
from motor_ai_sim.routes.gears import build_template, _humanoid_default
from motor_ai_sim.gears import (
    GearboxRequest, FieldValidationError, list_material_catalogue, list_lubricant_catalogue,
    list_bearing_catalogue, list_preset_catalogue)

OUT = sys.argv[1]


def dump(name, obj):
    with open(OUT + '/' + name + '.json', 'w') as fh:
        json.dump(obj, fh, sort_keys=True, separators=(',', ':'), allow_nan=False)


def run(name, **kw):
    """The plain editor body -> /template -> the complete record the page sends -> the response."""
    plain = dict(request_id='fixture_' + name, **kw)
    req_dict = build_template(plain)
    dump(name + '.request', req_dict)
    dump(name + '.response', GearboxRequest.from_dict(req_dict).execute().to_dict())


run('point_forward', operation='point')
run('spectrum', spectrum={'bins': [
    {'torque_out_Nm': 9.8, 'speed_out_rpm': 247., 'duration_h': 6000., 'ambient_temp_C': 25., 'label': 'rated'},
    {'torque_out_Nm': 4.9, 'speed_out_rpm': 300., 'duration_h': 4000., 'ambient_temp_C': 25., 'label': 'light'}],
    'peak_torque_out_Nm': 24., 'peak_events': 1000, 'peak_speed_out_rpm': 20.})
run('assembly', operation='assembly', outline_once=True, outline_stride=16)
with_bearings = _humanoid_default()['stages'][:1]
with_bearings[0]['bearings'] = {'input_bearing': '618/8-2Z', 'output_bearing': '618/8-2Z'}
run('point_bearings', operation='point', stages=with_bearings, input_speed_rpm=3000., torque_Nm=0.7, torque_at='input')
run('pose', operation='pose', input_angle_deg=37.)
bad = build_template({'request_id': 'fixture_bad'})
bad['stages'][0]['sun']['z'] = -1
try:
    GearboxRequest.from_dict(bad)
except FieldValidationError as exc:
    dump('error_422', {'detail': {'error': 'validation', 'errors': [exc.to_dict()]}})
dump('catalogues', {'materials': list_material_catalogue(), 'lubricants': list_lubricant_catalogue(),
                    'bearings': list_bearing_catalogue()[:3], 'presets': list_preset_catalogue()})
