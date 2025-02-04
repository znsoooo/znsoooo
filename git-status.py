import os
import textwrap
import subprocess

popen = lambda cmd, cwd=None: subprocess.Popen(cmd, -1, None, -1, -1, -2, shell=True, cwd=cwd).stdout.read().decode()

for name in os.listdir('..'):
    if os.path.isdir(f'../{name}') and os.path.isdir(f'../{name}/.git'):
        log = popen('git.exe log --oneline origin...head', f'../{name}').strip()
        if log:
            print(f"Repo '{name}':")
            print(textwrap.indent(log, '  ') + '\n')

os.system('pause')
