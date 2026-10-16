Real BOM files written by the server's CAD build (`cad/bom.py` `write_csv`, 2026-10-14) for the public dies CIANO14 40 (L12, L20)
and CIANO28 85 (L13). They hold part names, materials, dimensions and masses of public motors only (checked: no patent, NDA or
price content; the notes column states modelling basis). Used by `tests/test_full_motor_mass_server_bom.py` so that the parser and
the full-mass rules are tested on the real file format (BOM-encoded header, quoted cells, extra rows, T / C / E summary rows).
