"""Batch refactor route error responses to use error_response helper."""
import re
import os

routes_dir = r'C:\Users\juanm\Documents\GitHub\AllYouNeedIsWheel_moomoo\api\routes'
files = [f for f in os.listdir(routes_dir) if f.endswith('.py') and f not in ('__init__.py', 'utils.py')]

for fname in files:
    fpath = os.path.join(routes_dir, fname)
    with open(fpath, 'rt', encoding='utf-8') as f:
        content = f.read()
    original = content

    # Pattern 1: return jsonify({'error': X}), YYY  ->  return error_response(X, status_code=YYY)
    content = re.sub(
        r"""return jsonify\(\{\s*'error'\s*:\s*(f?'[^']*'|f\"[^\"]*\"|str\([^)]*\)|\w+)\s*\}\)\s*,\s*(\d+)""",
        r'return error_response(\1, status_code=\2)',
        content,
    )

    # Pattern 2: return jsonify({'success': False, 'error': X}), YYY  ->  return error_response(X, status_code=YYY)
    content = re.sub(
        r"""return jsonify\(\{\s*'success'\s*:\s*False\s*,\s*'error'\s*:\s*(f?'[^']*'|f\"[^\"]*\"|str\([^)]*\))\s*\}\)\s*,\s*(\d+)""",
        r'return error_response(\1, status_code=\2)',
        content,
    )

    # Add import if not present
    if 'error_response' not in content:
        content = re.sub(
            r'(from flask import .*?\n)',
            r'\1from api.routes.utils import error_response, success_response\n',
            content,
            count=1,
        )

    if content != original:
        with open(fpath, 'wt', encoding='utf-8') as f:
            f.write(content)
        print(f'{fname}: modified')
    else:
        print(f'{fname}: no changes (import may still be added)')

print('\n--- Done ---')
