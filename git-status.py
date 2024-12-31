import os
import textwrap
import subprocess

popen = lambda cmd: subprocess.Popen(cmd, -1, None, -1, -1, -1, shell=True, encoding='u8').stdout.read()

os.chdir('..')

for name in os.listdir():
    if os.path.isdir(name):
        os.chdir(name)
        log = popen('git.exe log --oneline origin...head')
        if log:
            print(f"Repo '{name}':")
            print(textwrap.indent(log, '  '))
        os.chdir('..')

os.system('pause')
