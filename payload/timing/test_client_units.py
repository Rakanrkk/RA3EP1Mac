"""Distinguish client-frame durations/deadlines from authored GPU particle units."""
from pathlib import Path
from fractions import Fraction
import re

source = (Path(__file__).parent / 'uprising60.c').read_text()
assert 'redirect(0x6c1b5a,2,particle_scale,sizeof(particle_scale),&effect_frame_scale)' in source
assert 'redirect(0x6ce6d9,3,expiry,sizeof(expiry),&effect_fps)' in source
assert '0xbbe9e1' not in source and '0xbbe9e6' not in source
fields = re.search(r'derived\[\]\s*=\s*\{([^}]+)\}', source)[1].split(',')
values = re.search(r'after\[\]\s*=\s*\{([^}]+)\}', source)[1].split(',')
assert values[fields.index('0xcef50c')].strip() == '0.06f'

fps, duration_ms = 60, 10000
client_scale, gpu_scale = Fraction(fps, 1000), Fraction(30, 1000)
for start_ms in [0, 1000, 20000, 600000]:
    current_frame = start_ms * client_scale
    end_frame = (start_ms + duration_ms) * client_scale
    assert (end_frame - current_frame) / fps == Fraction(duration_ms, 1000)
    assert duration_ms * gpu_scale / fps == 5  # v0.7 relative duration: twice fast.
    if start_ms >= duration_ms:
        assert (start_ms + duration_ms) * gpu_scale <= current_frame  # v0.7 deadline: already past.
    particle_birth = start_ms * gpu_scale
    particle_death = particle_birth + 60
    assert (particle_death - particle_birth) / 30 == 2
print('PASS: relative/absolute client timers use 60 units; GPU lifetime retains 30 units.')
