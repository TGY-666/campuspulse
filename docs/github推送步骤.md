# 推送到 GitHub 的完整步骤

> 本文档对应仓库当前状态：分支 `main`、**27 次提交**、107 个跟踪文件、工作区干净、尚未配置远程仓库。
> 按顺序做完前四步即可上线；第五节是出错时的对照表，第六节是公开前的安全清单。

---

## 零、推送前先确认当前状态

在项目根目录执行：

```bash
cd /e/舆情分析YM
git log --oneline -1
git status --porcelain
git remote -v
```

期望结果：

| 命令 | 期望输出 | 说明 |
|---|---|---|
| `git log --oneline -1` | `2e8e5c2 docs: 提交清单与实际历史对齐…` | 说明 27 次提交都在 |
| `git status --porcelain` | **空**（什么都不打印） | 空代表没有未提交改动 |
| `git remote -v` | **空** | 空代表还没关联远程仓库，正常 |

> 已为仓库配置 `credential.helper=manager`，推送时会直接弹出浏览器授权窗口，**不需要手动生成 Token**。
> 如果你的终端所在的 Git 版本较旧，弹窗可能变成命令行输入用户名密码，那种情况请看第三节的 Token 方案。

---

## 一、在 GitHub 上创建空仓库

1. 浏览器打开 **https://github.com/new**（先确认已登录自己的账号）
2. 填写：
   - **Repository name**：`campuspulse`
   - **Description**（可选）：`校园舆情 AI 分析平台 · CampusPulse`
   - 可见性选 **Public**（评委可直接访问）或 **Private**（需逐个邀请，见第六节）
3. **下面三个复选框全部不要勾**：
   - ☐ Add a README file
   - ☐ Add .gitignore
   - ☐ Choose a license

   > **这一步最容易出错。** GitHub 只要勾了任何一项，远程仓库就会自带一个初始 commit，和你本地 27 次提交变成两条互不相关的历史，`git push` 会被直接拒绝（报 `rejected / fetch first`）。而且事后合并会产生一个丑陋的 merge commit，破坏你辛苦整理的提交序列。
4. 点 **Create repository**
5. 创建成功后页面会显示一堆命令。**不要照着它执行 `git init`**（你已经有仓库了），只记下页面上的仓库地址，格式为：

```
https://github.com/<你的GitHub用户名>/campuspulse.git
```

---

## 二、关联远程仓库并推送

```bash
cd /e/舆情分析YM

# 1) 关联远程仓库，命名为 origin
git remote add origin https://github.com/<你的GitHub用户名>/campuspulse.git

# 2) 核对地址是否正确（拼错会推到一个不存在的仓库）
git remote -v

# 3) 推送 main 分支，并让它跟踪远程
git push -u origin main
```

**执行第 3 步后会发生什么：**

1. 弹出 **Git Credential Manager** 窗口（标题类似 “Connect to GitHub”）
2. 选择 **Sign in with your browser**（不要选 Token，那需要你提前生成）
3. 浏览器自动打开 GitHub 授权页 → 点 **Authorize**（首次会要求登录账号）
4. 窗口自动关闭，终端开始上传，结束后打印类似：

```
Enumerating objects: 130, done.
Writing objects: 100% (130/130), 1.2 MiB, done.
To https://github.com/<用户名>/campuspulse.git
 * [new branch]      main -> main
branch 'main' set up to track 'origin/main'.
```

看到 `* [new branch] main -> main` 就是成功了。

**`-u` 的作用**：把本地 `main` 和 `origin/main` 绑定。之后你在 `main` 分支上只需要敲 `git push`，不用再写 `origin main`。

---

## 三、备用方案：用 Personal Access Token 推送

如果第二节的浏览器弹窗没有出现、或反复失败（常见于无图形界面环境），改用 Token。

### 3.1 生成 Token

打开 **https://github.com/settings/tokens/new**（Classic 类型，对新手最省事）

- **Note**：填 `campuspulse-push`（方便日后识别与吊销）
- **Expiration**：建议 `30 days`（比赛期间够用，到期自动失效更安全）
- **Select scopes**：勾选 **`repo`** 整行（这一项就包含推送权限）
- 拉到最底部点 **Generate token**

生成后页面只显示一次，形如 `ghp_xxxxxxxxxxxxxxxx`。**立刻复制保存**，关掉页面就再也看不到。

> 更细粒度的做法是 Fine-grained token（https://github.com/settings/tokens?type=beta），
> 需要额外选 `Repository access → Only select repositories → campuspulse`，
> 并把 `Repository permissions → Contents` 设为 **Read and write**。
> 权限更小但容易漏配，导致 403。

### 3.2 用它推送

```bash
# 让 Git 每次明确询问凭据
git config --local credential.helper manager

git push -u origin main
```

弹窗或终端提示时：

| 字段 | 填什么 |
|---|---|
| Username | 你的 GitHub 用户名（不是邮箱） |
| Password | **粘贴 Token**（`ghp_...`），不是账号密码 |

> GitHub 自 2021 年起就关闭了账号密码推送。如果填了真实密码，会报
> `Support for password authentication was removed`。

---

## 四、推送后验证

```bash
# 1) 远程分支已存在，且与本地一致
git branch -vv

# 2) 本地与远程的提交哈希应当相同
git rev-parse HEAD
git rev-parse origin/main

# 3) 最直接的证据：用浏览器打开
#    https://github.com/<用户名>/campuspulse
```

`git rev-parse HEAD` 与 `git rev-parse origin/main` 输出**完全相同的 40 位哈希**，说明远程和本地一模一样。

浏览器打开仓库首页，应当看到：

- 提交数 **27 commits**
- 文件列表里是 `campuspulse/`、`static/`、`tests/`、`tools/`、`docs/`、`README.md` 等
- **不应该出现** `.env`、`data/`、`screenshots/`、`.venv/`、`output/`

在页面上点 **Commits**，能看到按数据流向排列的 27 条提交记录——这就是评审要看的开发轨迹。

---

## 五、常见报错对照表

| 报错信息 | 原因 | 解决 |
|---|---|---|
| `remote: Support for password authentication was removed` | 用了账号密码 | 改用 Token（第三节） |
| `! [rejected] main -> main (fetch first)` | 远程已有 commit（建仓库时勾了 README/.gitignore/license） | 见下方「远程非空」处理 |
| `remote: Permission to xxx denied to yyy` | Token 无 `repo` 权限，或账号无该仓库写权限 | 重新生成 Token 并勾 `repo` |
| `Repository not found` | 仓库地址拼错，或 Private 仓库未被授权 | `git remote -v` 核对；Private 需在 Token 授权范围内 |
| `fatal: remote origin already exists` | 之前加过 remote | 先 `git remote remove origin` 再重新 add |
| `OpenSSL SSL_read: Connection was reset` / `Failed to connect to github.com port 443` | 网络不通（国内访问 GitHub 常见） | 重试 2–3 次；有代理则 `git config --global http.proxy http://127.0.0.1:<端口>`；仍不行改用 SSH 或 Gitee 镜像 |
| `Could not resolve host: github.com` | DNS 解析失败 | 换网络（手机热点）或配置 DNS |
| 推送卡在 `Writing objects` 很久 | 首次上传 + 网络慢 | 正常，等待即可；`.git` 仅 4.8MB，不会超时 |
| 提交信息里中文显示为 `\344\270\255` | `core.quotepath` 未关 | 已在本仓库设为 `false`，正常情况不会出现 |

### 「远程非空」的处理

如果已经踩坑（建仓库时勾了初始化文件），有两种解法：

**解法一（推荐，干净）**：删除 GitHub 上的仓库，重新建一个什么都不勾的空仓库，回到第二节。

**解法二（保历史，但会多一个 merge commit）**：

```bash
git pull origin main --allow-unrelated-histories --no-rebase
git push -u origin main
```

> 这会把你 27 条提交和 GitHub 自动生成的 1 条合并成两条并行的历史线。
> 评审看到分叉的历史，会怀疑是拼接出来的，**所以优先用解法一**。

---

## 六、公开仓库前必须做的两件事

### 6.1 吊销并更换 API Key（重要）

虽然 `.env` 已被 `.gitignore` 排除（已验证 `git log -p | grep "sk-"` 无任何输出），
但**当前这个 DeepSeek Key 曾以明文出现在开发过程的对话与截图中**。仓库一旦 Public：

1. 到 https://platform.deepseek.com/api_keys 吊销旧 Key、新建一个
2. 只写进本地 `.env`（第 4 行），不要写进任何文档
3. 重启服务使其生效

### 6.2 安全自检命令

推送前后都可以跑，四条命令全部无输出才算干净：

```bash
# 1) 历史里有没有密钥
git log --all -p | grep -iE "sk-[a-zA-Z0-9]{20,}" 

# 2) 有没有把敏感文件纳入跟踪
git ls-files | grep -E "^\.env$|\.db$|demo_accounts|initial_admin|session_key"

# 3) 工作区是否干净
git status --porcelain

# 4) 远程与本地是否一致（推送后跑）
git rev-parse HEAD origin/main
```

---

## 七、后续日常提交（三次命令）

首次推送完成后，以后改动这样提交：

```bash
cd /e/舆情分析YM

git status                    # 看改了哪些文件
git add -A                    # 暂存全部改动
git commit -m "feat(模块): 做了什么" -m "为什么这样做、影响是什么"
git push                      # 已设 -u，直接 push
```

**提交信息沿用现有规范**（评审会看）：

| 前缀 | 用途 |
|---|---|
| `feat(模块)` | 新增功能 |
| `fix(模块)` | 修 bug |
| `docs` | 文档 |
| `test` | 测试与验收脚本 |
| `chore` | 构建、依赖、脚本等杂项 |

正文写**问题—方案—影响**，不要只写「改了什么」。仓库里前 27 条提交可以当范例。

---

## 八、可选：用 gh CLI 一条命令完成

如果觉得网页操作麻烦，可以装 GitHub CLI 后一步到位：

```bash
# 1) 安装（Windows，任选其一）
winget install --id GitHub.cli
# 或下载 https://cli.github.com/

# 2) 登录（选 GitHub.com → HTTPS → Login with a web browser）
gh auth login

# 3) 在项目目录内一条命令建仓库并推送
cd /e/舆情分析YM
gh repo create campuspulse --public --source=. --remote=origin --push
```

`gh` 会自动创建仓库、配置 remote、完成推送，不需要手动建仓库和复制地址。
代价是多装一个工具，且同样需要一个能上 GitHub 的网络环境。

---

## 九、常见疑问

**Q：现在这个名字 `CampusPulse Team` 会显示成作者，能改成真名吗？**

```bash
git config user.name "你的名字"
git config user.email "你的邮箱"
```

只对**之后**的提交生效，已有 27 条不会变。团队赛用团队名其实更合适，
如果一定要改历史，需要 `git rebase` 重写全部提交哈希，风险大于收益，不建议。

**Q：Private 仓库评委怎么看？**

GitHub → 仓库 → **Settings → Collaborators → Add people**，逐个邀请评委账号（或让对方提供 GitHub 用户名）。
Public 则无需任何操作。**多数比赛要求 Public，先确认赛制要求再选。**

**Q：能不能同时推到 Gitee？**

可以。Gitee 访问更稳定，很多国内赛事也接受：

```bash
git remote add gitee https://gitee.com/<用户名>/campuspulse.git
git push -u gitee main
```

之后 `git push origin main && git push gitee main` 即可双推，两个 remote 互不影响。

**Q：推错了想把仓库删了重来？**

GitHub 仓库 → Settings → 最下方 **Danger Zone → Delete this repository**。
本地不用动，重新建空仓库后再次 `git remote set-url origin <新地址>` 即可，提交历史都在本地。
