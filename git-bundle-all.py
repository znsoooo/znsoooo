import os
import shutil
import subprocess

popen = lambda cmd: subprocess.Popen(cmd, -1, None, -1, -1, -2, shell=True).stdout.read().decode()

os.makedirs('bundles', exist_ok=True)
bundle_path = os.path.realpath('bundles')
open('bundles/.gitignore', 'w').write('*')
print(f'Save to: "{bundle_path}"')

os.chdir('..')

for name in os.listdir():
    if os.path.isdir(name):
        bundle_file = f'{name}.git'
        os.chdir(name)
        popen(f'git bundle create "{bundle_file}" --all')
        if os.path.isfile(bundle_file):
            print(f' - Saving: "{bundle_file}"')
            shutil.move(bundle_file, f'{bundle_path}/{bundle_file}')
        os.chdir('..')

os.system('pause')
