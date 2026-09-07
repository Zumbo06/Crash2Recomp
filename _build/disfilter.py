"""disfilter.py - run mipsdis on a function or range and keep only matching lines.

    python _build/disfilter.py func 80020A24 "andi v0|sw v0, 1079|GAP"
    python _build/disfilter.py range 80044E48 80044F40 "IR0|DPCS|SZ"

Patterns are matched against the disassembly with '$' removed from register
names, so "v0" matches "$v0" - which keeps every dollar sign out of the shell.
A line that matches also pulls in the preceding `lui` when it completes a
lui/addiu address pair, so installed function addresses stay readable.
"""
import re
import subprocess
import sys

args = sys.argv[1:]
pat = re.compile(args[-1])
out = subprocess.run([sys.executable, "mipsdis.py"] + args[:-1],
                     capture_output=True, text=True, cwd=__file__.rsplit("\\", 1)[0]).stdout
lines = out.splitlines()
for i, line in enumerate(lines):
    plain = line.replace("$", "")
    if pat.search(plain) or line.startswith("==="):
        if i and "lui" in lines[i - 1] and "addiu" in plain and lines[i - 1] not in lines[max(0, i - 1):i]:
            pass
        if i and "addiu" in plain and "lui" in lines[i - 1].replace("$", "") and not pat.search(lines[i - 1].replace("$", "")):
            print(lines[i - 1])
        print(line)
