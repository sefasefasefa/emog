import os
import django
import json
import argparse
import sys


os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'emog_project.settings')
django.setup()

from agent_app.views import run_self_healing_loop

if __name__ == '__main__':
    # Ensure stdout uses UTF-8 to avoid Windows cp1252 errors when printing Turkish text
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', '-o', help='Optional output file to write JSON result (utf-8).')
    args = parser.parse_args()

    task = "1'den 100'e kadar olan asal sayıları listeleyen bir fonksiyon yaz ve çıktıyı ekrana yazdır."
    print('Running task: ', task)
    # pass a wrapper dict with __meta__ to enable console logging
    wrapper = {"__meta__": {"console": True}, "prompt": task}
    res = run_self_healing_loop(wrapper, model_name=None, max_iterations=3)
    out = json.dumps(res, ensure_ascii=False, indent=2)
    if args.output:
        with open(args.output, 'w', encoding='utf-8') as f:
            f.write(out)
        print(f'Wrote result to {args.output}')
    else:
        print(out)
