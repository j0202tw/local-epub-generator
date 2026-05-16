

## 1. 项目概述

构建一个可在本地运行的工具，用户输入**书名**、**作者**和**包含章节链接的目录页网址**，工具自动递归抓取每个章节的正文内容，生成符合 EPUB 3 标准的电子书文件，并提供下载功能。

### 1.1 核心功能

- 用户界面（Web 或命令行）接收输入
    
- 解析目录页，提取章节标题与 URL
    
- 按顺序抓取每个章节的正文（支持重试、编码自动检测）
    
- 清洗 HTML，保留必要样式（如粗体、斜体、段落、图片）
    
- 生成 EPUB 文件（含元数据、目录导航、章节分页）
    
- 提供文件下载（若使用 Web 界面）
    
- 所有依赖本地运行，无需外部 API
    

### 1.2 非功能性要求

- 跨平台（Windows/macOS/Linux）
    
- 对常见小说、技术文档类网站有较好兼容性
    
- 错误处理：单章失败不影响其余章节，最终报告失败列表
    
- 支持中英文及 UTF-8 字符
    

---

## 2. 技术选型

|组件|技术方案|理由|
|---|---|---|
|语言|Python 3.9+|生态丰富，epub 库成熟|
|Web 框架（可选）|Flask / Streamlit|轻量，快速实现本地 Web 界面|
|HTML 解析|BeautifulSoup4 + lxml|容错性强，易提取文本和链接|
|请求库|requests + urllib3|支持重试、超时、会话保持|
|编码检测|chardet / cchardet|处理非 UTF-8 页面|
|EPUB 生成|ebooklib / pypandoc（或简单使用 zip + 模板）|ebooklib 功能全面，支持添加图片、样式|
|并发控制|concurrent.futures.ThreadPoolExecutor|提高抓取速度，可配置线程数|

### 2.1 数据存储（临时）

- 抓取的内容暂存于本地临时目录（如 `./temp_epub_xxxxx/`）
    
- 完成后自动清理或提供手动清除选项
    

---

## 3. 系统架构

### 3.1 模块职责

- **目录解析器**：从目录页 URL 提取所有 `<a>` 链接，过滤出章节链接（支持用户自定义 CSS 选择器或自动识别）。
    
- **章节抓取器**：根据 URL 获取 HTML，提取正文内容（基于可配置的正文选择器，如 `#content`, `article`, `.chapter`），同时下载内嵌图片并转为本地相对路径。
    
- **EPUB 构建器**：根据抓取的章节列表，生成 `content.opf`, `toc.ncx` 等文件，打包为 `.epub`。
    
- **控制器**：协调流程，处理错误与进度反馈。
    

---

## 4. 详细执行步骤

### 4.1 项目初始化

1. 创建项目目录结构：
    
    text
    
    epub_generator/
    ├── app.py               # 主入口（Flask）
    ├── cli.py               # 命令行入口
    ├── core/
    │   ├── __init__.py
    │   ├── catalog_parser.py
    │   ├── chapter_fetcher.py
    │   ├── epub_builder.py
    │   └── utils.py
    ├── templates/           # HTML 模板（若使用 Web）
    ├── static/
    ├── downloads/           # 生成的 epub 临时存放
    └── requirements.txt
    
2. 安装依赖：`requests`, `beautifulsoup4`, `lxml`, `ebooklib`, `chardet`, `Flask`
    

### 4.2 实现目录解析器 (`catalog_parser.py`)

**输入**：目录页 URL  
**输出**：列表 `[{"title": "第1章", "url": "http://..."}, ...]`

**逻辑**：

1. 发送 GET 请求（设置 User-Agent，超时 10s）
    
2. 使用 `lxml` 解析 HTML
    
3. 提取所有 `<a>` 标签，过滤掉非章节链接：
    
    - 默认规则：链接文本长度 > 2，且链接 href 不包含 `javascript`、`#`，且链接指向同一域名或相对路径。
        
    - 允许用户通过配置文件提供 CSS 选择器（如 `ul.chapter-list a`）
        
4. 将相对路径转换为绝对 URL（使用 `urllib.parse.urljoin`）
    
5. 去重（基于 URL），保留原始顺序
    
6. 返回列表
    

**异常处理**：网络错误→重试3次；无链接→报错并退出。

### 4.3 实现章节抓取器 (`chapter_fetcher.py`)

**输入**：章节 URL，正文选择器（可选）  
**输出**：结构化的章节内容（标题、HTML 正文、图片列表）

**逻辑**：

1. 下载章节页面（同上述请求策略）
    
2. 检测编码（优先使用 `charset` meta，否则 chardet）
    
3. 提取标题：优先使用 `<h1>` 或 `<title>`，若无则用目录解析器传入的标题
    
4. 提取正文：
    
    - 若用户提供选择器（如 `#content`、`.story`），使用 `select_one`
        
    - 否则自动猜测：查找内容最长的 `<div>` 或 `<article>` 元素（启发式）
        
5. 清洗 HTML：
    
    - 移除脚本、样式、广告（规则可自定义）
        
    - 保留 `<p>`, `<h1>`~`<h6>`, `<strong>`, `<em>`, `<br>`, `<img>`, `<ul>`, `<ol>` 等
        
    - 为图片添加本地缓存：下载 `src` 中的图片（HTTP/HTTPS），保存到临时目录，替换 `src` 为相对路径（如 `../images/ch1_img1.jpg`）
        
6. 返回字典：`{"title": str, "body_html": str, "images": [{"local_path":..., "media_type":...}]}`
    

**性能优化**：支持多线程抓取（线程数可配置，默认5），每个线程独立处理一章。

### 4.4 实现 EPUB 构建器 (`epub_builder.py`)

使用 `ebooklib` 库：

**步骤**：

1. 创建 `Book()` 对象，设置语言（`zh-CN` 或 `en`）、书名、作者、唯一标识符（UUID）。
    
2. 添加封面页（可选）：若临时目录有 `cover.jpg`，则自动添加。
    
3. 添加 CSS 样式（预设干净样式，用户可替换）。
    
4. 按章节顺序添加 Spine 和导航：
    
    - 每章作为单独 `.xhtml` 文件，内容为清洗后的 body 部分。
        
    - 使用 `add_item()` 添加内容文档。
        
    - 创建 `Nav` 文档（EPUB 3 导航）或 `Ncx`（EPUB 2）。
        
5. 将下载的图片添加为 `ImageItem`，并引用到对应的章节中。
    
6. 写出为 `.epub` 文件到 `downloads/` 目录，文件名格式：`书名_作者_时间戳.epub`。
    

### 4.5 实现用户界面

#### 选项 A：Flask Web 界面

- 路由：
    
    - `/`：显示表单（书名、作者、目录URL、可选正文选择器、线程数）
        
    - `/generate`：POST 接收参数，启动后台任务（使用 `threading` 避免阻塞），返回任务 ID
        
    - `/status/<task_id>`：轮询进度（已完成章节数/总章节数）
        
    - `/download/<task_id>`：下载生成的 epub 文件
        
- 使用 `sessions` 或临时文件存储任务状态。
    

#### 选项 B：命令行界面

bash

python cli.py --title "书名" --author "作者" --catalog "https://example.com/toc" --selector "#chapter-content"

- 实时打印抓取日志
    
- 完成后输出 `.epub` 路径
    

### 4.6 错误处理与日志

- 统一日志格式：时间、级别、模块、信息
    
- 单章抓取失败 → 记录错误，继续下一章
    
- 最终生成报告：成功章节数 / 总章节数，以及失败列表
    
- 网络请求自动重试（指数退避）
    

---

## 5. 未提及的重要细节（用户需注意）

### 5.1 网站反爬策略

- **User-Agent 轮换**：可提供随机 UA 列表
    
- **Cookies 处理**：若需要登录，本项目暂不实现，但可提示用户使用浏览器导出 Cookies 并作为参数传入（未来扩展）
    
- **Robots.txt**：忽略检查（由用户自行遵守法律）
    
- **请求间隔**：为了避免被封，可设置可配置的延迟（默认0.5秒）
    

### 5.2 正文提取的准确性

- 不同网站结构差异大 → 必须提供 **CSS 选择器** 参数作为高级选项
    
- 默认算法可能抓取到评论区、侧边栏 → 提供黑名单关键词（如 `comment`, `sidebar`, `ad`）
    
- 对于分页章节（如“下一页”），不支持自动拼接，需要用户提供完整 URL
    

### 5.3 图片处理

- 图片可能为懒加载（`data-src` → 需要额外处理）
    
- 相对路径转换为绝对路径
    
- 失败图片：记录警告并跳过，不影响 epub 生成
    
- 大图压缩：可选项，避免 epub 体积过大
    

### 5.4 EPUB 兼容性

- 使用 EPUB 3 标准，同时生成 NCX 以兼容老旧阅读器
    
- 图片必须使用 `mime-type` 正确声明
    
- 特殊字符（如数学公式）需使用 Unicode 或 MathML（不强制支持）
    

### 5.5 本地运行的安全与资源

- 防止用户输入恶意 URL（SSRF）：仅允许 HTTP/HTTPS，禁止访问内网地址（可配置白名单）
    
- 临时目录定期清理（每次启动时删除超过1小时的旧目录）
    

### 5.6 用户引导

- 提供一个示例网站配置（如笔趣阁、起点等）供测试，但注意版权提醒
    
- 在 UI 中加入“正文选择器探测”小工具：用户输入一个章节 URL，点击试抓取，显示提取到的正文预览，帮助用户编写正确的选择器
    

### 5.7 法律与道德

- 工具不得用于盗版侵权内容抓取，仅限个人学习或合法授权的网站
    
- 在 README 中加入免责声明
    

---

## 6. 实现优先级（MVP 到进阶）

### MVP 核心

- 命令行版本 + 固定正文选择器（例如用户必须提供 `--selector`）
    
- 支持纯文本 + 基本 HTML 标签
    
- 忽略图片下载（只保留 alt 文本）
    
- 单线程抓取，无进度提示
    
- 使用 `ebooklib` 生成简单 EPUB
    

### 增强版（建议实现）

- Web 界面 + 实时进度
    
- 图片下载与嵌入
    
- 自动检测正文（heuristic）
    
- 多线程抓取
    
- 请求重试与延迟配置
    
- 提供常见网站预设（如用户可选择“知乎专栏”、“某中文小说站”等内置规则）
    

### 可选高级功能

- 支持登录（Cookies 注入）
    
- 支持 JavaScript 渲染页面（Selenium/Playwright）— 但会大大增加复杂度
    
- 生成 MOBI/AZW3 格式（使用 Calibre 命令行转换）
    

---

## 7. 验收标准

1. 用户提供有效的目录 URL，能成功生成包含所有可抓取章节的 EPUB。
    
2. 生成的 EPUB 在 Calibre、手机阅读器中打开，目录导航正常，正文可读。
    
3. 网络故障时，程序不会崩溃，并给出明确错误提示。
    
4. 多个章节抓取无明显重复内容或缺失内容。
    
5. 图片（若下载）在 EPUB 中正常显示。
    

---

## 8. 开发者指引（Agent 实施清单）

- 初始化 Python 项目，配置虚拟环境，添加 `requirements.txt`
    
- 实现 `utils.py`：含 `download_page`, `retry`, `url_join`, `sanitize_filename`
    
- 实现 `catalog_parser.py` 单元测试（使用本地 mock HTML）
    
- 实现 `chapter_fetcher.py` 单元测试（包含多种正文结构）
    
- 实现 `epub_builder.py` 并测试生成一个简单 EPUB
    
- 集成主流程 (`cli.py`)：顺序调用三个模块
    
- 添加 Web 界面（Flask）
    
- 实现异步任务管理（使用 `celery` 或简单线程池 + 状态字典）
    
- 编写用户文档（README.md）：安装步骤、使用示例、常见问题
    
- 打包发布：使用 `pyinstaller` 生成单文件 exe（可选）
    

---

## 9. 附录：示例代码片段（核心逻辑示意）

### 目录解析示例

python

def parse_catalog(url, selector="a"):
    soup = fetch_soup(url)
    links = soup.select(selector)
    chapters = []
    for a in links:
        href = a.get('href')
        title = a.get_text(strip=True)
        if href and title and not href.startswith('#'):
            full_url = urljoin(url, href)
            chapters.append({"title": title, "url": full_url})
    return chapters

### 正文提取启发式

python

def extract_body(soup):
    candidates = soup.find_all(['article', 'div', 'section'])
    best = max(candidates, key=lambda x: len(x.get_text()))
    return best

### EPUB 添加章节

python

from ebooklib import epub
book = epub.EpubBook()
chapter = epub.EpubHtml(title=title, file_name=f'chap_{i}.xhtml', lang='zh')
chapter.content = f'<h1>{title}</h1>{body_html}'
book.add_item(chapter)

---

**文档版本**：1.0  
**最后更新**：2026-05-16  
**适用 Agent 执行角色**：全栈 Python 开发者

该规格说明书已涵盖架构、步骤、潜在风险及实现细节。请按照上述清单依次实现，并通过本地测试验证。

本回答由 AI 生成，内容仅供参考，请仔细甄别。