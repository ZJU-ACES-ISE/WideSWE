#!/usr/bin/env python3
"""Generate the static project-page task index from the canonical release TSV."""
import csv
import json
from pathlib import Path

root = Path(__file__).resolve().parents[1]
with (root / 'release120.tsv').open(newline='') as source:
    rows = list(csv.DictReader(source, delimiter='\t'))
tasks = []
for row in rows:
    path = root / 'cases' / row['ecosystem'] / row['case_id']
    if not (path / 'prompt.md').is_file():
        raise ValueError(f'Missing prompt: {path}')
    tasks.append({'id': row['case_id'], 'category': row['category'],
                  'ecosystem': row['ecosystem'], 'count': int(row['repo_count']),
                  'repositories': row['repositories']})
assert len(tasks) == 120
assert len({task['ecosystem'] for task in tasks}) == 41
assert sum(task['category'] == 'bugfix' for task in tasks) == 60
(root / 'tasks.js').write_text(
    '// Generated from release120.tsv; run scripts/build_project_data.py.\n'
    'window.WIDESWE_TASKS = ' + json.dumps(tasks, indent=2) + ';\n')
print(f'Generated {len(tasks)} tasks.')
