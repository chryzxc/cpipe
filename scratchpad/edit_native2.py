from pathlib import Path
import re as _re
p = Path('cpipe/native.py')
s = p.read_text()
s = s.replace('import json\nimport logging\nimport os\nfrom pathlib import Path\n', 'import json\nimport logging\nimport os\nfrom pathlib import Path\nimport re\n', 1)
start = s.index("                run_key = _intent_key(token)\n")
end = s.index("                from .delivery.approach import execute as execute_run\n")
block = s[start:end]
s = s[:start] + s[end:]
anchor = "                # The receipt's expiry bounds starting work; a frozen delivery can always be continued.\n"
s = s.replace(anchor, block + anchor)
s = s.replace("""                        if op != 'prepare' or run_record['expires'] <= time.time():
                            raise ValueError('start a task with prepare')
""", """                        if op != 'prepare':
                            raise ValueError('start a task with prepare')
""")
p.write_text(s)
