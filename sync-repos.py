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
import json
import time
import textwrap
import functools
import subprocess
import urllib.error
import urllib.parse
import urllib.request

import requests


# 防止 git 在终端缺失时卡在凭据输入
os.environ.setdefault('GIT_TERMINAL_PROMPT', '0')

# git 命令: Windows 默认 git.exe, 其它平台用 git (可用环境变量 GIT 覆盖)
GIT = os.environ.get('GIT') or ('git.exe' if os.name == 'nt' else 'git')

GITHUB_TOKEN = ''
GITCODE_KEY = ''
GITEE_KEY = ''

GITCODE = {'name': 'GitCode', 'api': 'https://gitcode.com/api/v5', 'host': 'gitcode.com', 'token': GITCODE_KEY}
GITEE = {'name': 'Gitee', 'api': 'https://gitee.com/api/v5', 'host': 'gitee.com', 'token': GITEE_KEY}


# ---------------------------------------------------------------------------
# 装饰器
# ---------------------------------------------------------------------------
def retry(func):
    """被装饰函数(返回 (ret, msg))在 ret 为 False 时自动重试"""
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        times = 3
        interval = 5
        last_msg = ''
        for attempt in range(1, times + 1):
            try:
                ret, msg = func(*args, **kwargs)
            except Exception as e:  # noqa: BLE001
                ret, msg = False, f'exception: {e}'
            if ret:
                return True, msg
            last_msg = msg
            if attempt < times:
                time.sleep(interval)
        return False, last_msg
    return wrapper


# ---------------------------------------------------------------------------
# 公用函数
# ---------------------------------------------------------------------------
def Popen(cmd, cwd=None):
    p = subprocess.Popen(
        cmd, shell=True, cwd=cwd,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    out, _ = p.communicate()
    return out.decode('utf-8', 'replace')


def RunCmd(cmd, cwd=None):
    """执行一条 shell 命令, 返回 (ret, msg)"""
    out = Popen(cmd, cwd)
    if out.strip():
        print(textwrap.indent(out.rstrip(), '  '))
    ok = not re.search(r'^fatal: ', out, re.M)
    return ok, out.rstrip()


def RepoDir(owner, repo):
    # 返回绝对路径, 避免 cwd + 相对目标 叠加造成 repos/repos 嵌套
    return os.path.abspath(os.path.join('repos', repo))


def InjectKey(url, key):
    # https://host/path -> https://oauth2:key@host/path
    return re.sub(r'^(https?://)', fr'\1oauth2:{key}@', url)


def ApiRequest(method, url, token, data=None, auth='header'):
    """通用 JSON API 请求, 返回 (ret, obj)

    auth='header': 加 Private-Token 头 (GitCode)
    auth='query' : url 追加 access_token 参数 (Gitee)
    """
    headers = {}
    if auth == 'header' and token:
        headers['Private-Token'] = token
    if data is not None:
        headers['Content-Type'] = 'application/json'
    if auth == 'query' and token:
        sep = '&' if '?' in url else '?'
        url = f'{url}{sep}access_token={urllib.parse.quote(token)}'
    body = json.dumps(data).encode('utf-8') if data is not None else None
    try:
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode('utf-8', 'replace')
        return True, (json.loads(raw) if raw.strip() else None)
    except urllib.error.HTTPError as e:
        detail = e.read().decode('utf-8', 'replace')[:300]
        return False, f'HTTP {e.code}: {detail}'
    except Exception as e:  # noqa: BLE001
        return False, f'{method} {url} 失败: {e}'


@retry
def EnsureRemoteRepo(plat, auth, owner, repo, description):
    """若远端仓库不存在则通过 API 新建(通用底层, 对外函数名带平台后缀)"""
    if not plat['token']:
        return False, f"{plat['name']} KEY 未配置"
    ret, _ = ApiRequest('GET', f"{plat['api']}/repos/{owner}/{repo}", plat['token'], auth=auth)
    if ret:
        # 已存在: 统一把描述更新为 Synced from ...; 并兜底确保公开(private=False)
        # Gitee PATCH 需带 name
        patch = {'description': description, 'private': False}
        if auth == 'query':
            patch['name'] = repo
        ApiRequest('PATCH', f"{plat['api']}/repos/{owner}/{repo}", plat['token'], patch, auth=auth)
        return True, f"{plat['name']} 仓库已存在, 已更新描述"
    ret, obj = ApiRequest(
        'POST', f"{plat['api']}/user/repos", plat['token'],
        {'name': repo, 'description': description, 'private': False}, auth=auth,
    )
    if ret:
        # 兜底: 某些平台(如 Gitee)账号默认私有, POST 的 private 可能被忽略, 再 PATCH 一次确保公开
        patch = {'private': False}
        if auth == 'query':
            patch['name'] = repo
        ApiRequest('PATCH', f"{plat['api']}/repos/{owner}/{repo}", plat['token'], patch, auth=auth)
        return True, f"{plat['name']} 仓库已新建"
    return False, f"{plat['name']} 新建仓库失败: {obj}"


@retry
def PushRepo(plat, owner, repo, local_dir):
    """把本地仓库推送到目标平台(通用底层, 对外函数名带平台后缀)"""
    if not plat['token']:
        return False, f"{plat['name']} KEY 未配置"
    if not os.path.isdir(local_dir):
        return False, f'本地仓库不存在: {local_dir}'
    remote = plat['name'].lower()
    push_url = InjectKey(f"https://{plat['host']}/{owner}/{repo}.git", plat['token'])
    existing = Popen(f"{GIT} -C {local_dir} remote").split()
    if remote in existing:
        RunCmd(f"{GIT} -C {local_dir} remote set-url {remote} {push_url}")
    else:
        RunCmd(f"{GIT} -C {local_dir} remote add {remote} {push_url}")
    ok1, m1 = RunCmd(f"{GIT} -C {local_dir} push --force --all {remote}")
    ok2, m2 = RunCmd(f"{GIT} -C {local_dir} push --force --tags {remote}")
    if ok1 and ok2:
        return True, f"已推送到 {plat['name']}"
    return False, f"{plat['name']} 推送失败: {m1} | {m2}"


def ReadReleaseBody(folder):
    readme = os.path.join(folder, 'README.md')
    if os.path.isfile(readme):
        with open(readme, encoding='utf-8') as f:
            return f.read().strip()
    return ''


# ---------------------------------------------------------------------------
# GitHub 下载侧
# ---------------------------------------------------------------------------
@retry
def GithubApi(api_url):
    headers = {'Accept': 'application/vnd.github.v3+json'}
    if GITHUB_TOKEN:
        headers['Authorization'] = f'token {GITHUB_TOKEN}'
    try:
        req = urllib.request.Request(api_url, headers=headers)
        data = urllib.request.urlopen(req, timeout=60).read()
    except urllib.error.URLError as e:
        return False, f'github api 失败: {e}'
    try:
        return True, json.loads(data)
    except ValueError as e:
        return False, f'json 解析失败: {e}'


@retry
def DownloadFile(file_url, path):
    print(f'[download] {file_url} -> {path}')
    try:
        headers = {}
        if GITHUB_TOKEN:
            headers['Authorization'] = f'token {GITHUB_TOKEN}'
        req = urllib.request.Request(file_url, headers=headers)
        with urllib.request.urlopen(req, timeout=60) as resp, open(path, 'wb') as f:
            f.write(resp.read())
        return True, path
    except Exception as e:  # noqa: BLE001
        return False, f'下载失败: {e}'


def GetAllReleases(owner, repo):
    """分页拉取全部(历史) releases, 返回 (ret, list)"""
    rels = []
    page = 1
    while True:
        api = f'https://api.github.com/repos/{owner}/{repo}/releases?per_page=100&page={page}'
        ret, batch = GithubApi(api)
        if not ret:
            return False, f'获取 releases 失败: {batch}'
        if not batch:
            break
        rels.extend(batch)
        if len(batch) < 100:
            break
        page += 1
    return True, rels


@retry
def DownloadGithubRepo(owner, repo):
    """克隆(或拉取)仓库到 repos/REPO_NAME"""
    d = RepoDir(owner, repo)
    clone_url = f'https://github.com/{owner}/{repo}.git'
    if os.path.isdir(d):
        return RunCmd(f'{GIT} -C {d} pull')
    os.makedirs('repos', exist_ok=True)
    return RunCmd(f'{GIT} clone {clone_url} {d}')


@retry
def DownloadGithubReleases(owner, repo):
    """把全部 releases 及其附件下载到 repos/REPO_NAME/releases/TAG/"""
    ret, rels = GetAllReleases(owner, repo)
    if not ret:
        return False, rels
    if not rels:
        return True, '无 releases'
    d = RepoDir(owner, repo)
    rel_base = os.path.join(d, 'releases')
    os.makedirs(rel_base, exist_ok=True)
    for rel in rels:
        tag = rel.get('tag_name') or 'untagged'
        folder = os.path.join(rel_base, tag)
        os.makedirs(folder, exist_ok=True)
        body = rel.get('body') or ''
        with open(os.path.join(folder, 'README.md'), 'w', encoding='utf-8') as f:
            f.write(f'# {tag}\n\n{body}\n')
        for asset in rel.get('assets', []):
            ok, m = DownloadFile(asset['browser_download_url'], os.path.join(folder, asset['name']))
            if not ok:
                print(f'[warn] {m}')
    return True, f'releases 已保存到 {rel_base}'


# ---------------------------------------------------------------------------
# GitCode 同步
# ---------------------------------------------------------------------------
@retry
def GetGitcodeUploadUrl(owner, repo, tag, file_name):
    """获取 release 附件的预签名上传地址(华为 OBS)。返回 (ret, obj)

    GitCode 的附件不走 multipart POST, 而是先 GET 该接口拿到预签名 URL 与一组
    必须原样带回的 headers, 再用 HTTP PUT 把文件字节传到该 URL。
    """
    url = (f"{GITCODE['api']}/repos/{owner}/{repo}/releases/{tag}/upload_url"
           f"?access_token={GITCODE['token']}&file_name={urllib.parse.quote(file_name)}")
    ret, obj = ApiRequest('GET', url, GITCODE['token'], auth='header')
    if not ret or not isinstance(obj, dict) or 'url' not in obj or 'headers' not in obj:
        return False, f'获取上传地址失败: {obj}'
    return True, obj


@retry
def PutGitcodeFileSigned(url, headers, filepath):
    """把本地文件 PUT 到预签名 URL(华为 OBS), 返回 (ret, msg)"""
    try:
        with open(filepath, 'rb') as f:
            data = f.read()
    except OSError as e:
        return False, f'读取文件失败: {e}'
    req = urllib.request.Request(url, data=data, method='PUT')
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            return True, resp.read().decode('utf-8', 'replace')[:200]
    except urllib.error.HTTPError as e:
        return False, f'HTTP {e.code}: {e.read().decode("utf-8", "replace")[:200]}'
    except Exception as e:  # noqa: BLE001
        return False, f'PUT 失败: {e}'


def GitcodeReleaseAssets(owner, repo, tag):
    """返回该 tag 下已存在的附件名集合(用于幂等跳过)"""
    ret, obj = ApiRequest('GET', f"{GITCODE['api']}/repos/{owner}/{repo}/releases", GITCODE['token'], auth='header')
    if not ret:
        return set()
    for r in (obj or []):
        if r.get('tag_name') == tag:
            return set(a.get('name') for a in r.get('assets', []))
    return set()


@retry
def EnsureGitcodeRepo(owner, repo, description):
    return EnsureRemoteRepo(GITCODE, 'header', owner, repo, description)


@retry
def PushGitcodeRepo(owner, repo, local_dir):
    return PushRepo(GITCODE, owner, repo, local_dir)


def UploadGitcodeReleases(owner, repo, src_owner, src_repo):
    """把本地 releases 同步到 GitCode owner/repo(附件走 OBS 预签名 PUT)"""
    if not GITCODE['token']:
        return False, 'GitCode KEY 未配置'
    rel_base = os.path.join(RepoDir(src_owner, src_repo), 'releases')
    if not os.path.isdir(rel_base):
        return True, '本地无 releases 目录, 跳过'
    _, repo_obj = ApiRequest('GET', f"{GITCODE['api']}/repos/{owner}/{repo}", GITCODE['token'], auth='header')
    default_branch = (repo_obj or {}).get('default_branch') or 'master'
    count = 0
    for tag in sorted(os.listdir(rel_base)):
        folder = os.path.join(rel_base, tag)
        if not os.path.isdir(folder):
            continue
        body = ReadReleaseBody(folder)
        if not GitcodeReleaseAssets(owner, repo, tag):
            ret, obj = ApiRequest(
                'POST', f"{GITCODE['api']}/repos/{owner}/{repo}/releases", GITCODE['token'],
                {'tag_name': tag, 'name': tag, 'body': body, 'target_commitish': default_branch}, auth='header',
            )
            if not ret:
                return False, f"创建 release {tag} 失败: {obj}"
        existing = GitcodeReleaseAssets(owner, repo, tag)
        for fname in sorted(os.listdir(folder)):
            if fname == 'README.md' or fname in existing:
                continue
            fpath = os.path.join(folder, fname)
            if not os.path.isfile(fpath):
                continue
            ok, obj = GetGitcodeUploadUrl(owner, repo, tag, fname)
            if not ok:
                return False, f"获取 {fname} 上传地址失败: {obj}"
            ok, m = PutGitcodeFileSigned(obj['url'], obj.get('headers'), fpath)
            if not ok:
                return False, f"上传附件 {fname} 失败: {m}"
            print(f"[ok] GitCode {tag}/{fname}")
        count += 1
    return True, f"已同步 {count} 个 release 到 GitCode"


# ---------------------------------------------------------------------------
# Gitee 同步
# ---------------------------------------------------------------------------
@retry
def GetGiteeUser():
    """返回当前 Gitee 登录用户名(用于确定目标仓库 owner)"""
    ret, obj = ApiRequest('GET', f"{GITEE['api']}/user", GITEE['token'], auth='query')
    if not ret:
        return False, obj
    login = (obj or {}).get('login')
    if not login:
        return False, f'无法解析 Gitee 用户名: {obj}'
    return True, login


def GiteeReleaseAssets(owner, repo, tag):
    """返回 (assets_set, release_id), release_id 为 None 代表该 tag 尚无 release"""
    assets, rid = set(), None
    page = 1
    while True:
        url = f"{GITEE['api']}/repos/{owner}/{repo}/releases?page={page}&per_page=100"
        ret, obj = ApiRequest('GET', url, GITEE['token'], auth='query')
        if not ret or not obj:
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
    """把本地文件 multipart POST 到 Gitee release 附件接口, 返回 (ret, msg)"""
    url = (f"{GITEE['api']}/repos/{owner}/{repo}/releases/{release_id}/attach_files"
           f"?access_token={GITEE['token']}")
    try:
        with open(filepath, 'rb') as f:
            resp = requests.post(url, files={'file': (os.path.basename(filepath), f)}, timeout=180)
    except Exception as e:  # noqa: BLE001
        return False, f'Gitee 附件上传异常: {e}'
    if resp.status_code in (200, 201):
        return True, resp.text[:200]
    return False, f"HTTP {resp.status_code}: {resp.text[:200]}"


@retry
def EnsureGiteeRepo(owner, repo, description):
    return EnsureRemoteRepo(GITEE, 'query', owner, repo, description)


@retry
def PushGiteeRepo(owner, repo, local_dir):
    return PushRepo(GITEE, owner, repo, local_dir)


def UploadGiteeReleases(owner, repo, src_owner, src_repo):
    """把本地 releases 同步到 Gitee owner/repo(附件走 multipart POST)"""
    if not GITEE['token']:
        return False, 'Gitee KEY 未配置'
    rel_base = os.path.join(RepoDir(src_owner, src_repo), 'releases')
    if not os.path.isdir(rel_base):
        return True, '本地无 releases 目录, 跳过'
    _, repo_obj = ApiRequest('GET', f"{GITEE['api']}/repos/{owner}/{repo}", GITEE['token'], auth='query')
    default_branch = (repo_obj or {}).get('default_branch') or 'master'
    count = 0
    for tag in sorted(os.listdir(rel_base)):
        folder = os.path.join(rel_base, tag)
        if not os.path.isdir(folder):
            continue
        body = ReadReleaseBody(folder)
        existing, rid = GiteeReleaseAssets(owner, repo, tag)
        if rid is None:
            ret, obj = ApiRequest(
                'POST', f"{GITEE['api']}/repos/{owner}/{repo}/releases", GITEE['token'],
                {'tag_name': tag, 'name': tag, 'body': body, 'target_commitish': default_branch}, auth='query',
            )
            if not ret:
                return False, f"创建 release {tag} 失败: {obj}"
            existing, rid = GiteeReleaseAssets(owner, repo, tag)
        if rid is None:
            return False, f"无法获取 release {tag} 的 id"
        for fname in sorted(os.listdir(folder)):
            if fname == 'README.md' or fname in existing:
                continue
            fpath = os.path.join(folder, fname)
            if not os.path.isfile(fpath):
                continue
            ok, m = UploadGiteeReleaseAsset(owner, repo, rid, fpath)
            if not ok:
                return False, f"上传附件 {fname} 失败: {m}"
            print(f"[ok] Gitee {tag}/{fname}")
        count += 1
    return True, f"已同步 {count} 个 release 到 Gitee"


# ---------------------------------------------------------------------------
# 平台调度
# ---------------------------------------------------------------------------
def SyncOne(plat, auth, push_fn, upload_fn, owner, repo, dest_repo, src_owner, src_repo):
    dest = dest_repo or repo
    desc = f'Synced from https://github.com/{src_owner}/{src_repo}'
    print(f"=== 同步到 {plat['name']} ({owner}/{dest}) ===")
    ok, m = EnsureRemoteRepo(plat, auth, owner, dest, desc)
    print(('OK  ' if ok else 'FAIL') + f': {m}')
    if not ok:
        return
    ok, m = push_fn(owner, dest, RepoDir(src_owner, src_repo))
    print(('OK  ' if ok else 'FAIL') + f': {m}')
    ok, m = upload_fn(owner, dest, src_owner, src_repo)
    print(('OK  ' if ok else 'FAIL') + f': {m}')


def SyncGitcode(owner, repo, dest_repo=None):
    SyncOne(GITCODE, 'header', PushGitcodeRepo, UploadGitcodeReleases,
            owner, repo, dest_repo, owner, repo)


def SyncGitee(owner, repo, dest_repo=None):
    ret, gitee_owner = GetGiteeUser()
    if not ret:
        print('FAIL: 获取 Gitee 登录用户失败: ' + str(gitee_owner))
        return
    SyncOne(GITEE, 'query', PushGiteeRepo, UploadGiteeReleases,
            gitee_owner, repo, dest_repo, owner, repo)


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
def Main(owner, repo, dest_repo=None):
    print(f'=== 下载 GitHub 仓库 {owner}/{repo} ===')
    ok, m = DownloadGithubRepo(owner, repo)
    print(('OK  ' if ok else 'FAIL') + f': {m}')

    print('=== 下载 releases ===')
    ok, m = DownloadGithubReleases(owner, repo)
    print(('OK  ' if ok else 'FAIL') + f': {m}')

    SyncGitcode(owner, repo, dest_repo)
    SyncGitee(owner, repo, dest_repo)


if __name__ == '__main__':
    if len(sys.argv) >= 3:
        owner, repo = sys.argv[1], sys.argv[2]
        dest = sys.argv[3] if len(sys.argv) >= 4 else None
    else:
        owner, repo, dest = 'znsoooo', 'regex-matcher', 'regex-matcher-sync-test'
    Main(owner, repo, dest)
