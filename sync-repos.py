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
import functools
import subprocess

import requests


# 防止 git 在终端缺失时卡在凭据输入
os.environ.setdefault('GIT_TERMINAL_PROMPT', '0')

# git 命令: Windows 默认 git.exe, 其它平台用 git
GIT = 'git.exe' if os.name == 'nt' else 'git'

GITCODE_KEY = ''
GITEE_KEY = ''

GITCODE = {'name': 'GitCode', 'api': 'https://gitcode.com/api/v5', 'host': 'gitcode.com', 'token': GITCODE_KEY}
GITEE = {'name': 'Gitee', 'api': 'https://gitee.com/api/v5', 'host': 'gitee.com', 'token': GITEE_KEY}


# ---------------------------------------------------------------------------
# 装饰器
# ---------------------------------------------------------------------------
def retry(func):
    """被装饰函数成功时直接返回结果, 失败时打印原因并间隔5秒重试"""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        interval = 5
        while True:
            try:
                ret = func(*args, **kwargs)
                print(f'[信息] {ret}')
                return ret
            except Exception as e:  # noqa: BLE001
                print(f'[重试] {func.__name__} 失败: {e}')
                for i in range(int(interval * 2)):
                    time.sleep(0.5)
    return wrapper


# ---------------------------------------------------------------------------
# 公用函数
# ---------------------------------------------------------------------------
def RunGit(cmd, cwd=None):
    """执行一条 git 子命令, 返回输出文本; git 报错(fatal)时 raise RuntimeError"""
    full = f'{GIT} -C {cwd} {cmd}' if cwd else f'{GIT} {cmd}'
    p = subprocess.Popen(full, shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    result = p.communicate()[0].decode('utf-8', 'replace').rstrip()
    if result:
        print(result)
    if re.search(r'^fatal: ', result, re.M):
        raise RuntimeError(result)
    return result


def GetLocalReleases(rel_base):
    """遍历 rel_base 下的每个 tag 目录, 逐个产出 (tag, doc, asset_paths)"""
    if not os.path.isdir(rel_base):
        return
    for tag in sorted(os.listdir(rel_base)):
        folder = f'{rel_base}/{tag}'
        if not os.path.isdir(folder):
            continue
        readme = f'{folder}/README.md'
        doc = ''
        if os.path.isfile(readme):
            with open(readme, encoding='utf-8') as f:
                doc = f.read().strip()
        asset_paths = []
        for name in sorted(os.listdir(folder)):
            if name != 'README.md' and os.path.isfile(f'{folder}/{name}'):
                asset_paths.append(f'{folder}/{name}')
        yield tag, doc, asset_paths


@retry
def PushRepo(plat, owner, repo, local_dir):
    if not plat['token']:
        return f"{plat['name']} KEY 未配置"
    if not os.path.isdir(local_dir):
        return f'本地仓库不存在: {local_dir}'
    remote = plat['name'].lower()
    push_url = f"https://oauth2:{plat['token']}@{plat['host']}/{owner}/{repo}.git"
    print(f'[信息] 开始推送 {plat["name"]} {owner}/{repo}')
    RunGit(f'remote add {remote} {push_url}', cwd=local_dir)
    RunGit(f'remote set-url {remote} {push_url}', cwd=local_dir)
    RunGit(f'push --force --all {remote}', cwd=local_dir)
    RunGit(f'push --force --tags {remote}', cwd=local_dir)
    return f"完成推送 {plat['name']}"


# ---------------------------------------------------------------------------
# GitHub 下载
# ---------------------------------------------------------------------------
def GithubApi(api_url):
    headers = {'Accept': 'application/vnd.github.v3+json'}
    resp = requests.get(api_url, headers=headers, timeout=60)
    resp.raise_for_status()
    return resp.json()


@retry
def DownloadFile(file_url, path):
    print(f'[信息] 开始下载 {os.path.basename(path)}')
    resp = requests.get(file_url, timeout=60)
    resp.raise_for_status()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'wb') as f:
        f.write(resp.content)
    return f'完成下载 {path}'


@retry
def DownloadGithubRepo(owner, repo):
    """克隆(或拉取)仓库到 repos/REPO_NAME"""
    repo_dir = f'repos/{repo}'
    clone_url = f'https://github.com/{owner}/{repo}.git'
    if os.path.isdir(repo_dir):
        RunGit('pull', cwd=repo_dir)
        return f'已拉取 {repo_dir}'
    os.makedirs('repos', exist_ok=True)
    with open('repos/.gitignore', 'w') as f:
        f.write('*')
    RunGit(f'clone {clone_url} {repo_dir}')
    return f'已克隆 {repo_dir}'


@retry
def DownloadGithubReleases(owner, repo):
    """把最近100条 releases 及其附件下载到 repos/REPO_NAME/releases/TAG/"""
    rels = GithubApi(f'https://api.github.com/repos/{owner}/{repo}/releases?per_page=100&page=1')
    if not rels:
        return '无 releases'
    rel_base = f'repos/{repo}/releases'
    os.makedirs(rel_base, exist_ok=True)
    with open(f'{rel_base}/.gitignore', 'w') as f:
        f.write('*')
    print(f'[信息] 开始下载 releases: {owner}/{repo} ({len(rels)} 个)')
    for rel in rels:
        tag = rel['tag_name']
        folder = f'{rel_base}/{tag}'
        os.makedirs(folder, exist_ok=True)
        body = rel['body']
        with open(f'{folder}/README.md', 'w', encoding='utf-8') as f:
            f.write(f'# {tag}\n\n{body}\n')
        assets = rel.get('assets', [])
        for asset in assets:
            DownloadFile(asset['browser_download_url'], f'{folder}/{asset["name"]}')
        print(f'[信息] 完成下载 release {tag} ({len(assets)} 个附件)')
    return f'releases 已保存到 {rel_base}'


# ---------------------------------------------------------------------------
# GitCode 同步
# ---------------------------------------------------------------------------
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
        return 'GitCode KEY 未配置'
    try:
        GitcodeApi('GET', f"{GITCODE['api']}/repos/{owner}/{repo}")
        GitcodeApi('PATCH', f"{GITCODE['api']}/repos/{owner}/{repo}", {'description': description, 'private': False})
        return f'GitCode 仓库已存在, 已更新描述: {owner}/{repo}'
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 404:
            GitcodeApi('POST', f"{GITCODE['api']}/user/repos", {'name': repo, 'description': description, 'private': False})
            return f'GitCode 仓库已新建: {owner}/{repo}'
        raise


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
    url = f"{GITCODE['api']}/repos/{owner}/{repo}/releases/{tag}/upload_url?access_token={GITCODE['token']}&file_name={requests.utils.quote(file_name)}"
    obj = GitcodeApi('GET', url)
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
    for r in GitcodeApi('GET', f"{GITCODE['api']}/repos/{owner}/{repo}/releases"):
        if r['tag_name'] == tag:
            return [a['name'] for a in r['assets']]
    return []


@retry
def UploadGitcodeReleases(owner, repo, src_owner, src_repo):
    """把本地 releases 同步到 GitCode owner/repo"""
    if not GITCODE['token']:
        return 'GitCode KEY 未配置'
    rel_base = f'repos/{src_repo}/releases'
    if not os.path.isdir(rel_base):
        return '本地无 releases 目录, 跳过'
    repo_obj = GitcodeApi('GET', f"{GITCODE['api']}/repos/{owner}/{repo}")
    count = 0
    print('[信息] 开始同步 GitCode releases')
    for tag, body, files in GetLocalReleases(rel_base):
        existing = GitcodeReleaseAssets(owner, repo, tag)
        if not existing:
            print(f'[信息] 创建 GitCode release {tag}')
            GitcodeApi(
                'POST', f"{GITCODE['api']}/repos/{owner}/{repo}/releases",
                {'tag_name': tag, 'name': tag, 'body': body, 'target_commitish': repo_obj['default_branch']},
            )
        for path in files:
            fname = os.path.basename(path)
            if fname in existing:
                continue
            obj = GetGitcodeUploadUrl(owner, repo, tag, fname)
            PutGitcodeFileSigned(obj['url'], obj['headers'], path)
            print(f'[信息] 成功上传 GitCode {owner}/{repo} {tag}/{fname}')
        count += 1
    return f'完成同步 GitCode releases {count} 个到 {owner}/{repo}'


@retry
def DeleteGitcodeRepo(owner, repo):
    if not GITCODE['token']:
        return 'GitCode KEY 未配置'
    try:
        GitcodeApi('DELETE', f"{GITCODE['api']}/repos/{owner}/{repo}")
        return f'GitCode 仓库已删除: {owner}/{repo}'
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 404:
            return f'GitCode 仓库不存在, 无需删除: {owner}/{repo}'
        raise


# ---------------------------------------------------------------------------
# Gitee 同步
# ---------------------------------------------------------------------------
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
        return 'Gitee KEY 未配置'
    try:
        GiteeApi('GET', f"{GITEE['api']}/repos/{owner}/{repo}")
        GiteeApi('PATCH', f"{GITEE['api']}/repos/{owner}/{repo}", {'description': description, 'name': repo})
        return f'Gitee 仓库已存在, 已更新描述: {owner}/{repo}'
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 404:
            GiteeApi('POST', f"{GITEE['api']}/user/repos", {'name': repo, 'description': description, 'private': False})
            return f'Gitee 仓库已新建: {owner}/{repo}'
        raise


@retry
def PushGiteeRepo(owner, repo, local_dir, description=''):
    EnsureGiteeRepo(owner, repo, description)
    msg = PushRepo(GITEE, owner, repo, local_dir)
    # Gitee 不允许空仓库公开, 需要 push 之后仓库将仓库设为公开
    GiteeApi('PATCH', f"{GITEE['api']}/repos/{owner}/{repo}", {'private': False, 'name': repo})
    return msg


@retry
def GiteeReleaseAssets(owner, repo, tag):
    """返回 (assets_set, release_id), release_id 为 None 代表该 tag 尚无 release"""
    obj = GiteeApi('GET', f"{GITEE['api']}/repos/{owner}/{repo}/releases?page=1&per_page=100")
    for r in obj:
        if r['tag_name'] == tag:
            return [a['name'] for a in r['assets']], r['id']
    return [], None


@retry
def UploadGiteeReleaseAsset(owner, repo, release_id, filepath):
    """把本地文件 multipart POST 到 Gitee release 附件接口, 成功返回响应文本"""
    url = f"{GITEE['api']}/repos/{owner}/{repo}/releases/{release_id}/attach_files?access_token={GITEE['token']}"
    with open(filepath, 'rb') as f:
        resp = requests.post(url, files={'file': (os.path.basename(filepath), f)}, timeout=180)
    if resp.status_code not in (200, 201):
        raise RuntimeError(f"Gitee 附件上传失败 HTTP {resp.status_code}: {resp.text[:200]}")
    return resp.text[:200]


@retry
def UploadGiteeReleases(owner, repo, src_owner, src_repo):
    """把本地 releases 同步到 Gitee owner/repo"""
    if not GITEE['token']:
        return 'Gitee KEY 未配置'
    rel_base = f'repos/{src_repo}/releases'
    if not os.path.isdir(rel_base):
        return '本地无 releases 目录, 跳过'
    repo_obj = GiteeApi('GET', f"{GITEE['api']}/repos/{owner}/{repo}")
    count = 0
    print('[信息] 开始同步 Gitee releases')
    for tag, body, files in GetLocalReleases(rel_base):
        existing, rid = GiteeReleaseAssets(owner, repo, tag)
        if rid is None:
            print(f'[信息] 创建 Gitee release {tag}')
            rel = GiteeApi(
                'POST', f"{GITEE['api']}/repos/{owner}/{repo}/releases",
                {'tag_name': tag, 'name': tag, 'body': body, 'target_commitish': repo_obj['default_branch']},
            )
            if not rel or 'id' not in rel:
                raise RuntimeError(f'Gitee 创建 release {tag} 后未返回 id, 响应: {rel}')
            rid = rel['id']
            existing = set()   # 新建 release 还没有我们的附件, 下方全部上传
        for path in files:
            fname = os.path.basename(path)
            if fname in existing:
                continue
            UploadGiteeReleaseAsset(owner, repo, rid, path)
            print(f'[信息] 成功上传 Gitee {owner}/{repo} {tag}/{fname}')
        count += 1
    return f'完成同步 Gitee releases {count} 个到 {owner}/{repo}'


@retry
def DeleteGiteeRepo(owner, repo):
    if not GITEE['token']:
        return 'Gitee KEY 未配置'
    try:
        GiteeApi('DELETE', f"{GITEE['api']}/repos/{owner}/{repo}")
        return f'Gitee 仓库已删除: {owner}/{repo}'
    except requests.exceptions.HTTPError as e:
        if e.response.status_code == 404:
            return f'Gitee 仓库不存在, 无需删除: {owner}/{repo}'
        raise


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
def SyncRepo(owner, repo, dest_repo=None):
    dest = dest_repo or repo
    local_dir = f'repos/{repo}'
    desc = f'Synced from: https://github.com/{owner}/{repo}'

    print('=' * 20)
    DownloadGithubRepo(owner, repo)
    DownloadGithubReleases(owner, repo)

    print('-' * 20)
    PushGitcodeRepo(owner, dest, local_dir, desc)
    UploadGitcodeReleases(owner, dest, owner, repo)
    # DeleteGitcodeRepo(owner, dest_repo)

    print('-' * 20)
    PushGiteeRepo(owner, dest, local_dir, desc)
    UploadGiteeReleases(owner, dest, owner, repo)
    # DeleteGiteeRepo(owner, dest_repo)


if __name__ == '__main__':
    if len(sys.argv) >= 3:
        owner, repo = sys.argv[1], sys.argv[2]
        dest = sys.argv[3] if len(sys.argv) >= 4 else None
        SyncRepo(owner, repo, dest)
    else:
        repos = [
            # 本项目
            'znsoooo',

            # Python库
            'lishixian', 'IDLE-Advance',

            # Python项目
            'regex-matcher', 'find-in-files', 'binary-image-viewer',
            'mini-editor', 'yolo-labeling', 'toolkits', 'contacts',

            # C/C++项目
            'npc', 'ocr', '2048', 'c-utils', 'localsend',
            'qr-desktop', 'npp-select-in-brackets',

            # Android开发
            'qrscan', 'lanserver',
        ]
        owner = 'znsoooo'
        for repo in repos:
            SyncRepo(owner, repo)
