import os
import re
import json
import textwrap
import subprocess
import urllib.request


popen = lambda cmd, cwd=None: subprocess.Popen(cmd, -1, None, -1, -1, -2, shell=True, cwd=cwd).stdout.read().decode()


def RetryPopen(cmd, cwd=None):
    while True:
        ret = popen(cmd, cwd)
        print(textwrap.indent(ret.rstrip(), '  '))
        if not re.search(r'^fatal: (unable to access|expected flush) ', ret, re.M):
            return print()


def GetRepoList(user_name):
    url = f'https://api.github.com/users/{user_name}/repos?sort=updated&direction=dsc&per_page=100'
    headers = {'Accept': 'application/vnd.github.v3+json'}
    request = urllib.request.Request(url, headers=headers)
    data = urllib.request.urlopen(request).read()
    repos = json.loads(data)
    for repo in repos:
        if not repo['fork']:
            yield repo['name']


def GetRepo(user_name, repo_name):
    url = f'https://github.com/{user_name}/{repo_name}/'
    if os.path.isdir(f'../{repo_name}'):
        RetryPopen(f'git.exe pull', f'../{repo_name}')
    else:
        RetryPopen(f'git.exe clone {url}', '..')


if __name__ == '__main__':
    user_name = 'znsoooo'
    for repo_name in GetRepoList(user_name):
        print(f"Repo: '{repo_name}'")
        GetRepo(user_name, repo_name)
    os.system('pause')
