"""
把 GitHub 上的仓库连同 releases 同步到 GitCode / Gitee

用法:
    python sync-repos.py OWNER REPO [DEST_REPO]

保存路径:
    repos/REPO_NAME/                  该 git 仓库
    repos/REPO_NAME/releases/TAG/     该 tag 的 release 说明与所有附件

"""

import os
import re
import sys
import time
import textwrap
import functools
import subprocess

import requests


# 防止 git 在终端缺失时卡在凭据输入
os.environ.setdefault('GIT_TERMINAL_PROMPT', '0')

# git 命令: Windows 默认 git.exe, 其它平台用 git (可用环境变量 GIT 覆盖)
GIT = os.environ.get('GIT') or ('git.exe' if os.name == 'nt' else 'git')

GITCODE_KEY = ''
GITEE_KEY = ''

GITCODE = {'name': 'GitCode', 'api': 'https://gitcode.com/api/v5', 'host': 'gitcode.com', 'token': GITCODE_KEY}
GITEE = {'name': 'Gitee', 'api': 'https://gitee.com/api/v5', 'host': 'gitee.com', 'token': GITEE_KEY}


# ---------------------------------------------------------------------------
# 装饰器
# ---------------------------------------------------------------------------
def retry(func):
    """被装饰函数成功时直接返回结果; 抛异常则按 times/interval 重试, 全部失败则抛出最后一个异常"""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        times, interval = 3, 5
        last_exc = None
        for attempt in range(1, times + 1):
            try:
                return func(*args, **kwargs)
            except Exception as e:  # noqa: BLE001
                last_exc = e
                if attempt < times:
                    time.sleep(interval)
        raise last_exc
    return wrapper


# ---------------------------------------------------------------------------
# 公用函数
# ---------------------------------------------------------------------------
def RunGit(cmd, cwd=None):
    """执行一条 git 子命令(自动加 -C cwd 指定仓库位置), 返回输出文本; git 报错(fatal)时 raise RuntimeError

    cwd 为 None 时不加 -C, 例如 RunGit('clone https://... ./repos/x')
    指定仓库位置则用 RunGit('remote set-url origin https://...', cwd=local_dir)
    """
    full = f'{GIT} -C {cwd} {cmd}' if cwd else f'{GIT} {cmd}'
    p = subprocess.Popen(full, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    out, _ = p.communicate()
    out = out.decode('utf-8', 'replace')
    if out.strip():
        print(textwrap.indent(out.rstrip(), '  '))
    if re.search(r'^fatal: ', out, re.M):
        raise RuntimeError(f'git 命令失败: {cmd}\n{out.rstrip()}')
    return out.rstrip()


def ReadReleaseBody(folder):
    readme = f'{folder}/README.md'
    if os.path.isfile(readme):
        with open(readme, encoding='utf-8') as f:
            return f.read().strip()
    return ''


@retry
def PushRepo(plat, owner, repo, local_dir):
    if not plat['token']:
        raise ValueError(f"{plat['name']} KEY 未配置")
    if not os.path.isdir(local_dir):
        raise FileNotFoundError(f'本地仓库不存在: {local_dir}')
    remote = plat['name'].lower()
    push_url = f"https://oauth2:{plat['token']}@{plat['host']}/{owner}/{repo}.git"
    existing = RunGit('remote', cwd=local_dir).split()
    if remote in existing:
        RunGit(f'remote set-url {remote} {push_url}', cwd=local_dir)
    else:
        RunGit(f'remote add {remote} {push_url}', cwd=local_dir)
    RunGit(f'push --force --all {remote}', cwd=local_dir)
    RunGit(f'push --force --tags {remote}', cwd=local_dir)
    return f"已推送到 {plat['name']}"


# ---------------------------------------------------------------------------
# GitHub 下载
# ---------------------------------------------------------------------------
@retry
def GithubApi(api_url):
    headers = {'Accept': 'application/vnd.github.v3+json'}
    resp = requests.get(api_url, headers=headers, timeout=60)
    resp.raise_for_status()
    return resp.json()


@retry
def DownloadFile(file_url, path):
    print(f'[download] {file_url} -> {path}')
    resp = requests.get(file_url, timeout=60)
    resp.raise_for_status()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'wb') as f:
        f.write(resp.content)
    return path


def GetAllReleases(owner, repo):
    """分页拉取全部(历史) releases, 返回 release 列表(失败直接 raise)"""
    rels = []
    page = 1
    while True:
        api = f'https://api.github.com/repos/{owner}/{repo}/releases?per_page=100&page={page}'
        batch = GithubApi(api)
        if not batch:
            break
        rels.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return rels


@retry
def DownloadGithubRepo(owner, repo):
    """克隆(或拉取)仓库到 repos/REPO_NAME"""
    d = f'repos/{repo}'
    clone_url = f'https://github.com/{owner}/{repo}.git'
    if os.path.isdir(d):
        RunGit('pull', cwd=d)
        return f'已更新 {d}'
    os.makedirs('repos', exist_ok=True)
    RunGit(f'clone {clone_url} {d}')
    return f'已克隆到 {d}'


@retry
def DownloadGithubReleases(owner, repo):
    """把全部 releases 及其附件下载到 repos/REPO_NAME/releases/TAG/"""
    rels = GetAllReleases(owner, repo)
    if not rels:
        return '无 releases'
    d = f'repos/{repo}'
    rel_base = f'{d}/releases'
    os.makedirs(rel_base, exist_ok=True)
    for rel in rels:
        tag = rel.get('tag_name') or 'untagged'
        folder = f'{rel_base}/{tag}'
        os.makedirs(folder, exist_ok=True)
        body = rel.get('body') or ''
        with open(f'{folder}/README.md', 'w', encoding='utf-8') as f:
            f.write(f'# {tag}\n\n{body}\n')
        for asset in rel.get('assets', []):
            fpath = f'{folder}/{asset["name"]}'
            try:
                DownloadFile(asset['browser_download_url'], fpath)
            except Exception as e:  # noqa: BLE001
                print(f'[warn] 下载附件失败 {asset["name"]}: {e}')
    return f'releases 已保存到 {rel_base}'


# ---------------------------------------------------------------------------
# GitCode 同步
# ---------------------------------------------------------------------------
@retry
def GitcodeApi(method, url, data=None):
    """GitCode API 请求, 成功返回解析后的对象(空响应返回 None), 失败 raise 原生异常"""
    headers = {'Private-Token': GITCODE['token']} if GITCODE['token'] else {}
    if data is not None:
        headers['Content-Type'] = 'application/json'
    resp = requests.request(method, url, headers=headers, json=data, timeout=60)
    resp.raise_for_status()
    if resp.text.strip():
        return resp.json()
    return None


@retry
def EnsureGitcodeRepo(owner, repo, description):
    if not GITCODE['token']:
        raise ValueError('GitCode KEY 未配置')
    api = GITCODE['api']
    exists = True
    try:
        GitcodeApi('GET', f"{api}/repos/{owner}/{repo}")
    except requests.exceptions.HTTPError as e:
        if e.response is not None and e.response.status_code == 404:
            exists = False
        else:
            raise
    if exists:
        GitcodeApi('PATCH', f"{api}/repos/{owner}/{repo}", {'description': description, 'private': False})
        return 'GitCode 仓库已存在, 已更新描述'
    GitcodeApi('POST', f"{api}/user/repos", {'name': repo, 'description': description, 'private': False})
    # 兜底: 某些情况 POST 的 private 可能被忽略, 再 PATCH 一次确保公开
    GitcodeApi('PATCH', f"{api}/repos/{owner}/{repo}", {'private': False})
    return 'GitCode 仓库已新建'


@retry
def PushGitcodeRepo(owner, repo, local_dir, description=''):
    EnsureGitcodeRepo(owner, repo, description)
    return PushRepo(GITCODE, owner, repo, local_dir)


@retry
def GetGitcodeUploadUrl(owner, repo, tag, file_name):
    """获取 release 附件的预签名上传地址(华为 OBS)。返回 {url, headers}

    GitCode 的附件不走 multipart POST, 而是先 GET 该接口拿到预签名 URL 与一组
    必须原样带回的 headers, 再用 HTTP PUT 把文件字节传到该 URL。
    """
    url = (f"{GITCODE['api']}/repos/{owner}/{repo}/releases/{tag}/upload_url"
           f"?access_token={GITCODE['token']}&file_name={requests.utils.quote(file_name)}")
    obj = GitcodeApi('GET', url)
    if not isinstance(obj, dict) or 'url' not in obj or 'headers' not in obj:
        raise ValueError(f'获取上传地址失败: {obj}')
    return obj


@retry
def PutGitcodeFileSigned(url, headers, filepath):
    """把本地文件 PUT 到预签名 URL(华为 OBS), 成功返回响应文本"""
    with open(filepath, 'rb') as f:
        data = f.read()
    resp = requests.put(url, headers=headers, data=data, timeout=180)
    resp.raise_for_status()
    return resp.text[:200]


@retry
def GitcodeReleaseAssets(owner, repo, tag):
    """返回该 tag 下已存在的附件名集合(用于幂等跳过)"""
    obj = GitcodeApi('GET', f"{GITCODE['api']}/repos/{owner}/{repo}/releases")
    for r in (obj or []):
        if r.get('tag_name') == tag:
            return set(a.get('name') for a in r.get('assets', []))
    return set()


def UploadGitcodeReleases(owner, repo, src_owner, src_repo):
    """把本地 releases 同步到 GitCode owner/repo"""
    if not GITCODE['token']:
        raise ValueError('GitCode KEY 未配置')
    rel_base = f'repos/{src_repo}/releases'
    if not os.path.isdir(rel_base):
        return '本地无 releases 目录, 跳过'
    repo_obj = GitcodeApi('GET', f"{GITCODE['api']}/repos/{owner}/{repo}")
    default_branch = (repo_obj or {}).get('default_branch') or 'master'
    count = 0
    for tag in sorted(os.listdir(rel_base)):
        folder = f'{rel_base}/{tag}'
        if not os.path.isdir(folder):
            continue
        body = ReadReleaseBody(folder)
        if not GitcodeReleaseAssets(owner, repo, tag):
            GitcodeApi(
                'POST', f"{GITCODE['api']}/repos/{owner}/{repo}/releases",
                {'tag_name': tag, 'name': tag, 'body': body, 'target_commitish': default_branch},
            )
        existing = GitcodeReleaseAssets(owner, repo, tag)
        for fname in sorted(os.listdir(folder)):
            if fname == 'README.md' or fname in existing:
                continue
            fpath = f'{folder}/{fname}'
            if not os.path.isfile(fpath):
                continue
            obj = GetGitcodeUploadUrl(owner, repo, tag, fname)
            PutGitcodeFileSigned(obj['url'], obj.get('headers'), fpath)
            print(f"[ok] GitCode {tag}/{fname}")
        count += 1
    return f"已同步 {count} 个 release 到 GitCode"


# ---------------------------------------------------------------------------
# Gitee 同步
# ---------------------------------------------------------------------------
@retry
def GiteeApi(method, url, data=None):
    """Gitee API 请求, 成功返回解析后的对象(空响应返回 None), 失败 raise 原生异常"""
    params = {'access_token': GITEE['token']} if GITEE['token'] else None
    resp = requests.request(method, url, params=params, json=data, timeout=60)
    resp.raise_for_status()
    if resp.text.strip():
        return resp.json()
    return None


@retry
def EnsureGiteeRepo(owner, repo, description):
    if not GITEE['token']:
        raise ValueError('Gitee KEY 未配置')
    api = GITEE['api']
    exists = True
    try:
        GiteeApi('GET', f"{api}/repos/{owner}/{repo}")
    except requests.exceptions.HTTPError as e:
        if e.response is not None and e.response.status_code == 404:
            exists = False
        else:
            raise
    if exists:
        # 已存在: 仅更新描述(空仓库不能设为公开, private 留到 push 之后)
        GiteeApi('PATCH', f"{api}/repos/{owner}/{repo}", {'description': description, 'name': repo})
        return 'Gitee 仓库已存在, 已更新描述'
    # 不存在则新建; POST 的 private 可能被账号默认私有策略忽略, 公开留到 push 之后处理
    GiteeApi('POST', f"{api}/user/repos", {'name': repo, 'description': description, 'private': False})
    return 'Gitee 仓库已新建'


@retry
def PushGiteeRepo(owner, repo, local_dir, description=''):
    EnsureGiteeRepo(owner, repo, description)
    msg = PushRepo(GITEE, owner, repo, local_dir)
    # push 之后仓库已有内容, 此时可安全设为公开 (Gitee 不允许空仓库公开)
    GiteeApi('PATCH', f"{GITEE['api']}/repos/{owner}/{repo}", {'private': False, 'name': repo})
    return msg


@retry
def GiteeReleaseAssets(owner, repo, tag):
    """返回 (assets_set, release_id), release_id 为 None 代表该 tag 尚无 release"""
    assets, rid = set(), None
    page = 1
    while True:
        url = f"{GITEE['api']}/repos/{owner}/{repo}/releases?page={page}&per_page=100"
        obj = GiteeApi('GET', url)
        if not obj:
            break
        for r in obj:
            if r.get('tag_name') == tag:
                rid = r.get('id')
                for a in (r.get('assets') or []):
                    assets.add(a.get('name'))
        if len(obj) < 100:
            break
        page += 1
    return assets, rid


@retry
def UploadGiteeReleaseAsset(owner, repo, release_id, filepath):
    """把本地文件 multipart POST 到 Gitee release 附件接口, 成功返回响应文本"""
    url = (f"{GITEE['api']}/repos/{owner}/{repo}/releases/{release_id}/attach_files"
           f"?access_token={GITEE['token']}")
    with open(filepath, 'rb') as f:
        resp = requests.post(url, files={'file': (os.path.basename(filepath), f)}, timeout=180)
    if resp.status_code not in (200, 201):
        raise RuntimeError(f"Gitee 附件上传失败 HTTP {resp.status_code}: {resp.text[:200]}")
    return resp.text[:200]


def UploadGiteeReleases(owner, repo, src_owner, src_repo):
    """把本地 releases 同步到 Gitee owner/repo"""
    if not GITEE['token']:
        raise ValueError('Gitee KEY 未配置')
    rel_base = f'repos/{src_repo}/releases'
    if not os.path.isdir(rel_base):
        return '本地无 releases 目录, 跳过'
    repo_obj = GiteeApi('GET', f"{GITEE['api']}/repos/{owner}/{repo}")
    default_branch = (repo_obj or {}).get('default_branch') or 'master'
    count = 0
    for tag in sorted(os.listdir(rel_base)):
        folder = f'{rel_base}/{tag}'
        if not os.path.isdir(folder):
            continue
        body = ReadReleaseBody(folder)
        existing, rid = GiteeReleaseAssets(owner, repo, tag)
        if rid is None:
            GiteeApi(
                'POST', f"{GITEE['api']}/repos/{owner}/{repo}/releases",
                {'tag_name': tag, 'name': tag, 'body': body, 'target_commitish': default_branch},
            )
            existing, rid = GiteeReleaseAssets(owner, repo, tag)
        if rid is None:
            raise RuntimeError(f"无法获取 release {tag} 的 id")
        for fname in sorted(os.listdir(folder)):
            if fname == 'README.md' or fname in existing:
                continue
            fpath = f'{folder}/{fname}'
            if not os.path.isfile(fpath):
                continue
            UploadGiteeReleaseAsset(owner, repo, rid, fpath)
            print(f"[ok] Gitee {tag}/{fname}")
        count += 1
    return f"已同步 {count} 个 release 到 Gitee"


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
def Main(owner, repo, dest_repo=None):
    dest = dest_repo or repo
    local_dir = f'repos/{repo}'
    desc = f'Synced from https://github.com/{owner}/{repo}'

    print(DownloadGithubRepo(owner, repo))
    print(DownloadGithubReleases(owner, repo))

    print(PushGitcodeRepo(owner, dest, local_dir, desc))
    print(UploadGitcodeReleases(owner, dest, owner, repo))

    print(PushGiteeRepo(owner, dest, local_dir, desc))
    print(UploadGiteeReleases(owner, dest, owner, repo))


if __name__ == '__main__':
    if len(sys.argv) >= 3:
        owner, repo = sys.argv[1], sys.argv[2]
        dest = sys.argv[3] if len(sys.argv) >= 4 else None
    else:
        owner, repo, dest = 'znsoooo', 'regex-matcher', 'regex-matcher-sync-test'
    Main(owner, repo, dest)
