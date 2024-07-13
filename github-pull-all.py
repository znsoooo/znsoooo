import os
import re
import json
import textwrap
import subprocess
import urllib.request


popen = lambda cmd: subprocess.Popen(cmd, -1, None, -1, -1, -2, shell=True).stdout.read().decode()


def RetryPopen(cmd):
    while True:
        ret = popen(cmd)
        print(textwrap.indent(ret, '  '))
        if not re.search(r'^fatal:', ret, re.M):
            return


def GetRepoList(user_name):
    url = f'https://api.github.com/users/{user_name}/repos'
    headers = {'Accept': 'application/vnd.github.v3+json'}
    request = urllib.request.Request(url, headers=headers)
    data = urllib.request.urlopen(request).read()
    repos = json.loads(data)
    for repo in repos:
        if not repo['fork']:
            yield repo['name']


def GetRepo(user_name, repo_name):
    url = f'https://github.com/{user_name}/{repo_name}/'
    if os.path.isdir(repo_name):
        os.chdir(repo_name)
        RetryPopen(f'git pull {url}')
        os.chdir('..')
    else:
        RetryPopen(f'git clone {url}')


if __name__ == '__main__':
    os.chdir('..')
    user_name = 'znsoooo'
    for repo_name in GetRepoList(user_name):
        print(f"Repo: '{repo_name}'")
        GetRepo(user_name, repo_name)
