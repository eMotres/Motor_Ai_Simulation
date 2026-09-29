from motor_ai_sim.routes.optimization import (
    _eval_cache_key as K, _config_fingerprint as F, _EVAL_CACHE as C,
)
fp = F()
# point that WAS computed (γ=-30, I=100) → must be a cache HIT
k_hit = K({}, 100.0, 60, 120.0, 1.0, -30.0, 4.0, 0.3, -1, True, True, fp)
# the NEW point the extension adds (γ=40, I=100) → must be a MISS
k_new = K({}, 100.0, 60, 120.0, 1.0, 40.0, 4.0, 0.3, -1, True, True, fp)
# other current of a computed point (γ=-30, I=110) → HIT
k_hit2 = K({}, 110.0, 60, 120.0, 1.0, -30.0, 4.0, 0.3, -1, True, True, fp)
print("cache entries:", len(C))
print("config_fp:", fp)
print("HIT  gamma=-30 I=100 :", k_hit in C)
print("HIT  gamma=-30 I=110 :", k_hit2 in C)
print("MISS gamma=40  I=100 :", k_new in C)
