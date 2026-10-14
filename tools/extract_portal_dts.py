import re, glob

files = sorted(glob.glob("sharm_dreams_group/portal/*.py"))
calls = re.compile(
    r'frappe\.[A-Za-z_.]+\(\s*(?:["\']([A-Za-z ]+)["\']|\{[^}]*doctype["\']\s*:\s*["\']([A-Za-z ]+)["\'])'
)
for f in files:
    text = open(f).read()
    for m in calls.finditer(text):
        dt = m.group(1) or m.group(2)
        if dt in ("DocType", "doc", "role", "user", "Guest", "Administrator", "System User"):
            continue
        line = text[: m.start()].count("\n") + 1
        src = text.splitlines()[line - 1].strip()
        print(f"{f.split('/')[-1]}:{line:4d}  {dt:<28} {src[:90]}")