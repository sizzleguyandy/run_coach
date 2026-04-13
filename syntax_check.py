import ast, os, sys
errors = []
skip_dirs = {'__pycache__', '.git', '.venv', 'venv', 'htmlcov'}
skip_names = {'test_check.py', 'test_check2.py', 'test_check3.py', 'test_check4.py'}
for root, dirs, files in os.walk('.'):
    dirs[:] = [d for d in dirs if d not in skip_dirs]
    for f in files:
        if not f.endswith('.py') or f in skip_names: continue
        try:
            ast.parse(open(os.path.join(root, f), encoding='utf-8').read())
        except SyntaxError as e:
            errors.append(f'{f}: {e}')
        except (UnicodeDecodeError, Exception):
            pass  # skip non-text or binary files
if errors:
    for e in errors: print('ERROR:', e)
    sys.exit(1)
else:
    print('All files OK')
