import csv
import json
import re
from pathlib import Path

root = Path(__file__).parent
notes = []
frontmatter_re = re.compile(r'^---\s*$(.*?)^---\s*$', re.S | re.M)

for path in sorted(root.rglob('*.md')):
    text = path.read_text(encoding='utf-8')
    frontmatter = {}
    body = text
    m = frontmatter_re.search(text)
    if m:
        fm = m.group(1)
        body = text[m.end():].lstrip('\n')
        for line in fm.splitlines():
            if ':' in line:
                k, v = line.split(':', 1)
                frontmatter[k.strip()] = v.strip()
    notes.append({
        'file': str(path.relative_to(root)).replace('\\', '/'),
        'title': frontmatter.get('title', path.stem),
        'type': frontmatter.get('type', 'note'),
        'domain': frontmatter.get('domain', ''),
        'audience': frontmatter.get('audience', ''),
        'use_case': frontmatter.get('use_case', ''),
        'tone': frontmatter.get('tone', ''),
        'length': frontmatter.get('length', ''),
        'keywords': [k.strip() for k in frontmatter.get('keywords', '').split(',')] if frontmatter.get('keywords') else [],
        'source': frontmatter.get('source', ''),
        'status': frontmatter.get('status', ''),
        'content': body.strip(),
    })

json_path = root / 'obsidian-pack-export.json'
csv_path = root / 'obsidian-pack-export.csv'

with json_path.open('w', encoding='utf-8') as f:
    json.dump(notes, f, ensure_ascii=False, indent=2)

with csv_path.open('w', encoding='utf-8', newline='') as f:
    writer = csv.DictWriter(f, fieldnames=['file', 'title', 'type', 'domain', 'audience', 'use_case', 'tone', 'length', 'keywords', 'source', 'status', 'content'])
    writer.writeheader()
    for note in notes:
        row = note.copy()
        row['keywords'] = ';'.join(note['keywords'])
        writer.writerow(row)

print(f'Exported {len(notes)} notes to {json_path} and {csv_path}')
