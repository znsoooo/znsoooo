import os
import textwrap
import subprocess

popen = lambda cmd: subprocess.Popen(cmd, -1, None, -1, -1, -1, shell=True).stdout.read().decode()

os.chdir('..')

for name in os.listdir():
    if os.path.isdir(name):
        os.chdir(name)
        if log := popen('git log --oneline origin..HEAD'):
            print(f"Repo: '{name}'")
            print(textwrap.indent(log, '  '))
        os.chdir('..')

os.system('pause')
