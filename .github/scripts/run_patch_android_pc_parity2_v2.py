from pathlib import Path

source_path = Path(".github/scripts/patch_android_pc_parity2.py")
source = source_path.read_text(encoding="utf-8")
old = '''if segment.count("payloadJson = encoded") != 2:
    raise RuntimeError(f"offline outbox payload assignments: {segment.count('payloadJson = encoded')}")
segment = segment.replace("payloadJson = encoded", "payloadJson = mutationEncoded", 2)'''
new = '''if segment.count("payloadJson = encoded") != 3:
    raise RuntimeError(f"offline replica/outbox payload assignments: {segment.count('payloadJson = encoded')}")
first = segment.index("payloadJson = encoded") + len("payloadJson = encoded")
segment = segment[:first] + segment[first:].replace("payloadJson = encoded", "payloadJson = mutationEncoded", 2)'''
if source.count(old) != 1:
    raise RuntimeError("one-shot patch source anchor changed")
source = source.replace(old, new, 1)
exec(compile(source, str(source_path), "exec"), {"__name__": "__main__"})
