"""Phase 2 import and functionality verification."""

from src.core.logger import append_log, truncate
from src.core.output_manager import OutputManager
from src.core.monitor import DefaultToolMonitor
from src.core.registry import DefaultToolRegistry
from src.core.loop_detector import LoopDetector
from src.core.keyword_index import KeywordIndex, extract_keywords
from src.core.instance_manager import InstanceManager

print("All Phase 2 imports OK")

# Test monitor
m = DefaultToolMonitor()
m.record("read_file", 150, True)
m.record("read_file", 200, True)
m.record("exec_cmd", 3000, False)
print(m.report())

# Test registry
r = DefaultToolRegistry()
print(f"Registry tools: {r.list()}")

# Test loop detector
ld = LoopDetector()
ld.record("read_file", {"path": "a.txt"}, "ok")
result = ld.check("read_file", {"path": "a.txt"})
print(f"Loop check: {result.level}")

# Test keyword index
idx = KeywordIndex(state_dir="./state_test")
kw = extract_keywords("我喜欢苹果 and AI")
print(f"Keywords extracted: {len(kw)} items")

# Test instance manager
im = InstanceManager(state_dir="./state_test")
result = im.try_acquire()
print(f"Instance: {result}")
im.release()

# Test logger
import tempfile, os
log_file = os.path.join(tempfile.gettempdir(), "test_log.jsonl")
append_log(log_file, {"phase": "test", "turn": 1, "res": {"content": "hello"}})
with open(log_file) as f:
    content = f.read()
    print(f"Log line length: {len(content.strip())} chars")
os.unlink(log_file)

# Cleanup test dir
import shutil
if os.path.exists("./state_test"):
    shutil.rmtree("./state_test")

print()
print("Phase 2 verification complete")
