import re
import json
import textwrap
import lsx

def GetReadmeRepoList():
    return re.findall(r'https://github.com/\S*', lsx.read('README.md'))

def GetGithubRepoList():
    url = f'https://api.github.com/users/znsoooo/repos?sort=created&direction=asc&per_page=100'
    headers = {'Accept': 'application/vnd.github.v3+json'}
    data = lsx.urlopen(url, headers=headers)
    repos = json.loads(data)
    return {repo['html_url']: repo['created_at'][:10].replace('-', '') for repo in repos if not repo['fork']}

def CheckUpdate():
    old = GetReadmeRepoList()
    new = GetGithubRepoList()
    append = '\n'.join(repo for repo in new if repo not in old)
    remove = '\n'.join(repo for repo in old if repo not in new)
    if append:
        print(f"Append repos:\n{textwrap.indent(append, '  ')}\n")
    if remove:
        print(f"Remove repos:\n{textwrap.indent(remove, '  ')}\n")

def UpdateDate():
    def repl(m):
        if (url := m.group(2)) in repos:
            return f"<!--{repos[url]}--> {url}"
        else:
            return url

    repos = GetGithubRepoList()
    old = lsx.read('README.md')
    new = re.sub(r'(<!--.*?--> *)?(https://github\.com/\S+)', repl, old)
    # print(new)
    lsx.write('README.md', new)
    print('Date updated in "README.md"')

if __name__ == '__main__':
    CheckUpdate()
    UpdateDate()
