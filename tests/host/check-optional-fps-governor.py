#!/usr/bin/env python3
"""Link and execute the production governor call with and without its provider."""
from pathlib import Path
import os
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
source = (root / 'dxmt/src/winemetal/unix/winemetal_unix.c').read_text()
start = source.index('  {   /* madeira-bcd fps-gov')
end = source.index('\n  }', start) + len('\n  }')
block = source[start:end]
fallback_start = source.find('/* Optional native FPS governor.')
fallback = source[fallback_start:source.index('static NTSTATUS\n_MTLCommandBuffer_presentDrawable', fallback_start)] if fallback_start >= 0 else ''
program = '''#include <assert.h>
static unsigned long long paced;
static void ios_frame_limiter(unsigned long long ns) { paced += ns; }
''' + fallback + '''
static void present(void) {
''' + block + '''
}
int main(void) {
    present();
#ifdef HAS_PROVIDER
    assert(paced == 123);
#else
    assert(paced == 0);
#endif
    return 0;
}
'''
with tempfile.TemporaryDirectory() as tmp:
    tmp = Path(tmp)
    (tmp / 'main.c').write_text(program)
    (tmp / 'provider.c').write_text('unsigned long long ios_fps_cap_dxmt_pace(void) { return 123; }\n')
    for provider in (False, True):
        args = [os.environ.get('CC', 'clang'), '-O1', '-Wall', '-Werror', str(tmp / 'main.c')]
        if provider:
            args += ['-DHAS_PROVIDER', str(tmp / 'provider.c')]
        exe = tmp / ('with-provider' if provider else 'without-provider')
        subprocess.run(args + ['-o', str(exe)], check=True)
        subprocess.run([str(exe)], check=True)
        print('PASS: production FPS hook links and runs ' + ('with provider; pacing is retained' if provider else 'without provider; pacing is unchanged'))
