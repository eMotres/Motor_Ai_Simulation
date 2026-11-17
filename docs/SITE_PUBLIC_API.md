# Motors for emotres.com (public API, schema version 1)

Owner 2026-11-17: the application is the one source of motor data; the site reads the published motors at build time.

## Endpoints (no sign-in, read-only, cached)

| Path | Answer |
|---|---|
| `GET /api/public/site/motors` | `{schema_version, generated_at, count, motors: [motor]}` |
| `GET /api/public/site/motors/{slug}` | the same envelope with the one motor in `motors` and again as `motor`; 404 for any other slug |

* Answer shape: `docs/schemas/site_motors.v1.json` (JSON Schema 2020-12, `additionalProperties: false` everywhere). The site build
  validates the answer and fails when `schema_version` is not 1 or a field appears or disappears.
* CORS: `Access-Control-Allow-Origin` is echoed for `https://emotres.com` and `https://www.emotres.com` (override with the
  environment variable `SITE_PUBLIC_ORIGINS`, comma separated); `Vary: Origin`.
* Cache: `Cache-Control: public, max-age=300`, a strong `ETag` (it ignores `generated_at`), the server also keeps the built list for
  about a minute and drops it when an admin saves a record. Build-time use only; no per-visitor calls.

## A motor

`id` registry id (stable across renames; null until the registry knows the motor), `slug` (the typed site slug, else the lower-case
hyphenated designation), `designation` (the complete standard designation, see `motor_designation`), `family`, `outer_diameter_mm`,
`slots`, `poles`, `stack_length_mm`, `winding` (`code` = the turns/wire/connection/star-delta part of the designation, `words` = the
same in words), `steel` (library name) and `steel_class`, `mass_kg` (of the rated point, else the first published point),
`rated_point` and `points` (only the stored points the owner marked for publication: speed, torque, power, phase current, efficiency,
peak line voltage, nominal DC bus, mass), `availability` (`in_production` | `to_order` | `engineering_sample`), `lead_time_text`,
`price_text` (a sentence the owner typed, nothing computes it), `image_url` (typed, https), `updated_at`.

## Never published

Customer names, projects, productions, people, an incomplete designation (the motor is left out), results not marked for
publication, bench-test points, lamination geometry (so no section thumbnail: the picture is the typed `image_url`), anything of a
motor without the flag. A motor is published only when an admin sets "show on the site" AND its availability AND the designation is
complete. Only motors of the shared catalog can be flagged.

## Admin side (admins only, audited as `site.publication`)

`GET /api/admin/site-publication` (flags of all), `GET|PUT /api/admin/site-publication/{die}/{config}` (record, saved points to pick
from, problems and the "what the site will show" preview; PUT takes `show`, `availability`, `lead_time_text`, `slug`, `price_text`,
`image_url`, `points`, `rated_point`). The record lives in `<shared>/object_registry/site_publication.json`, never in a `die.yaml`;
a die rename or a configuration move carries it.
