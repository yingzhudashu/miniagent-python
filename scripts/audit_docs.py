"""Audit docstring coverage across the project."""
import os, ast, sys

root = r"D:\AIHub\miniagent-python\src"
results = []

for dirpath, _, filenames in os.walk(root):
    if "__pycache__" in dirpath:
        continue
    for fn in filenames:
        if not fn.endswith(".py"):
            continue
        fpath = os.path.join(dirpath, fn)
        rel = os.path.relpath(fpath, r"D:\AIHub\miniagent-python")
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                source = f.read()
            tree = ast.parse(source, filename=fn)
            lines = source.splitlines()
            total_lines = len(lines)
            
            has_module_doc = ast.get_docstring(tree) is not None
            
            funcs = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
            classes = [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
            
            funcs_with_doc = sum(1 for f in funcs if ast.get_docstring(f))
            classes_with_doc = sum(1 for c in classes if ast.get_docstring(c))
            
            func_total = len(funcs)
            class_total = len(classes)
            
            doc_ratio = round((funcs_with_doc + classes_with_doc) / max(func_total + class_total, 1) * 100, 1)
            
            results.append((doc_ratio, total_lines, has_module_doc, funcs_with_doc, func_total, classes_with_doc, class_total, rel))
        except Exception as e:
            results.append((0, 0, False, 0, 0, 0, 0, f"{rel} (ERROR: {e})"))

results.sort(key=lambda x: (x[0], x[1]))

print(f"{'Doc%':>6} {'Lines':>5} {'Mod':>3} {'FDoc':>4} {'FTot':>4} {'CDoc':>4} {'CTot':>4} | File")
print("-" * 90)
for doc_ratio, total_lines, has_mod, fd, ft, cd, ct, rel in results:
    mod = "Y" if has_mod else "N"
    print(f"{doc_ratio:6.1f} {total_lines:5d} {mod:>3} {fd:4d} {ft:4d} {cd:4d} {ct:4d} | {rel}")

print(f"\nTotal files: {len(results)}")
low = [r for r in results if r[0] < 50]
print(f"Files with <50% doc coverage: {len(low)}")
for r in low:
    print(f"  {r[7]}")
