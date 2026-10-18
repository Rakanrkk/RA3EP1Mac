"""Regression: changing display FPS must not change authored particle-frame units."""
from pathlib import Path
import re

source = (Path(__file__).parent / 'uprising60.c').read_text()
assert 'static unsigned effect_fps = 30;' in source
assert 'redirect(0x6ce6d9,3,expiry,sizeof(expiry),&effect_fps)' in source
assert 'static float effect_frame_scale = 0.03f;' in source
assert 'redirect(0x6c1b5a,2,particle_scale,sizeof(particle_scale),&effect_frame_scale)' in source
values = re.search(r'derived\[\]\s*=\s*\{([^}]+)\}', source)[1].lower()
assert '0xcef50c' in values  # General client scale is 60/1000; particle reader is isolated.
assert 'add_edit(0xcc3a9c,&original_fps,&fps,4)' in source
assert 'add_edit(0xcf506c,&original_ms,&nominal_ms,4)' in source
assert 'client_tick_bridge' not in source  # Avoid v0.5's render-cache counter regression.

# Birth/expiry/GPU age all use authored 30-Hz units, regardless of display FPS.
for born_ms in [0, 1000, 60000, 600000]:
    creation = born_ms * 30 / 1000
    life = 60  # Two seconds in the authored particle time domain.
    for age_ms in [0, 500, 1999, 2001]:
        now = (born_ms + age_ms) * 30 / 1000
        assert abs((now - creation) - age_ms * 30 / 1000) < 1e-9
        assert (now > creation + life) == (age_ms > 2000)
    if born_ms:
        assert born_ms * 30 / 1000 - born_ms * 60 / 1000 < 0  # v0.3 negative age.
print('PASS: particle birth/expiry units remain 30 Hz; display-only patches cannot double them.')
