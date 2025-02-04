import os
import subprocess

popen = lambda cmd, cwd=None: subprocess.Popen(cmd, -1, None, -1, -1, -2, shell=True, cwd=cwd).stdout.read().decode()

os.makedirs('bundles', exist_ok=True)
open('bundles/.gitignore', 'w').write('*')

for name in os.listdir('..'):
    if os.path.isdir(f'../{name}'):
        print(f"Saving: '{name}'")
        popen('git.exe bundle create "%s" --all' % os.path.realpath(f'bundles/{name}.git'), f'../{name}')

os.system('pause')
