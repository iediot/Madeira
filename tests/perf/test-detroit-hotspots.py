from pathlib import Path
import importlib.util
import sys
sys.dont_write_bytecode=True
root=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('hotspots',root/'tools/detroit-hotspots.py')
hotspots=importlib.util.module_from_spec(spec)
spec.loader.exec_module(hotspots)
functions=[(0x100,0x128,0x100),(0x128,0x160,0x128),(0x180,0x1a0,0x100)]
assert hotspots.bucket_functions(functions,0x110)=={0x100}
assert hotspots.bucket_functions(functions,0x120)=={0x100,0x128}
assert hotspots.bucket_functions(functions,0x170)==set()
assert hotspots.bucket_functions(functions,0x180)=={0x100}
assert hotspots.bucket_functions(functions,0x90)==set()
print('PASS hotspot mapper: function, ambiguous boundary, leaf gap, chained fragment, pre-image address')
