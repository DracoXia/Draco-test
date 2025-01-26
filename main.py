import feedparser
import configparser
import os
import httpx
from openai import OpenAI
from jinja2 import Template
from bs4 import BeautifulSoup
import re
import datetime
import requests
from fake_useragent import UserAgent
import traceback

# 先读取配置
config = configparser.ConfigParser()
try:
    config.read('config.ini')
except Exception as e:
    # 临时用 print 输出错误，因为此时日志系统还未初始化
    print(f"CRITICAL ERROR: Failed to read config.ini - {str(e)}")
    raise

# 定义配置参数（必须在日志系统之前）
def get_cfg(sec, name, default=None):
    try:
        value = config.get(sec, name, fallback=default)
        return value.strip('"') if value else value
    except Exception as e:
        # 临时用 print 输出错误
        print(f"ERROR reading config: {sec}.{name} - {str(e)}")
        return default

# 关键配置项定义
BASE = get_cfg('cfg', 'BASE', './docs')  # 默认改为 docs 目录
keyword_length = int(get_cfg('cfg', 'keyword_length', 5))
summary_length = int(get_cfg('cfg', 'summary_length', 200))
language = get_cfg('cfg', 'language', 'zh')
max_entries = int(get_cfg('cfg', 'max_entries', 20))  # 新增的 max_entries

# 创建输出目录（确保后续代码能访问）
os.makedirs(BASE, exist_ok=True)

# 现在定义日志系统（依赖已定义的 BASE）
system_log = os.path.join(BASE, 'rss_system.log')  # 日志文件保存在 docs/rss_system.log

def log_system(event_type, message, error=None):
    """增强型日志记录函数"""
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_entry = f"[{timestamp}][{event_type.upper()}] {message}"
    if error:
        log_entry += f"\nERROR DETAIL:\n{str(error)}\nTRACEBACK:\n{traceback.format_exc()[:500]}"
    try:
        with open(system_log, 'a', encoding='utf-8') as f:
            f.write(log_entry + "\n")
    except Exception as e:
        print(f"!!! 无法写入日志文件: {system_log} - {str(e)}")

# 初始化系统日志
log_system('system', 'Application started')

# 环境变量验证
DEEPSEEK_API_KEY = 'sk-10db767782cf4af78e50305aa46ca1dc'
if not DEEPSEEK_API_KEY:
    log_system('critical', 'DEEPSEEK_API_KEY environment variable not set!')
    raise ValueError("DEEPSEEK_API_KEY is required")

# 其他配置参数
U_NAME = os.environ.get('U_NAME')
DEEPSEEK_PROXY = os.environ.get('DEEPSEEK_PROXY', '')
DEEPSEEK_BASE_URL = os.environ.get('DEEPSEEK_BASE_URL', 'https://api.deepseek.com/v1')
custom_model = 'deepseek-chat'
deployment_url = f'https://{U_NAME}.github.io/RSS-GPT/'

# ... 后续的 fetch_feed、gpt_summary、output 等函数保持不变 ...

def fetch_feed(url, log_file):
    """带详细日志的RSS抓取"""
    log_system('debug', f'Fetching feed: {url}')
    try:
        headers = {'User-Agent': UserAgent().random.strip()}
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        
        log_system('debug', f'Response status: {response.status_code} for {url}')
        return {'feed': feedparser.parse(response.text), 'status': 'success'}
    except Exception as e:
        log_system('error', f'Failed to fetch {url}', e)
        return {'feed': None, 'status': 'failed'}

def gpt_summary(query, language):
    """DeepSeek集成核心"""
    log_system('api', f'Starting summary generation (Length: {len(query)})')
    try:
        client = OpenAI(
            api_key=DEEPSEEK_API_KEY,
            base_url=DEEPSEEK_BASE_URL,
            http_client=httpx.Client(proxy=DEEPSEEK_PROXY) if DEEPSEEK_PROXY else None
        )

        messages = [
            {"role": "user", "content": query},
            {"role": "assistant", "content": 
                f"请用{language}总结，先提取{keyword_length}个关键词，然后分要点总结（{summary_length}字内）"
                "格式：'<br><br>总结：'（保留两个换行）" 
                if language == "zh" else
                f"Summarize in {language}, {keyword_length} keywords first, then bullet points ({summary_length} words)"
                "Format: '<br><br>Summary:' (keep two line breaks)"}
        ]

        response = client.chat.completions.create(
            model=custom_model,
            messages=messages,
            temperature=0.7,
            max_tokens=summary_length*2
        )

        log_system('api', 
            f"API Success | Tokens used: {response.usage.total_tokens}",
        )
        return response.choices[0].message.content
    except Exception as e:
        log_system('api_error', 
            f"API Failed | Error: {type(e).__name__} | Msg: {str(e)}",
            e
        )
        return None

def process_entry(entry, config_section, log_file):
    """处理单个条目"""
    try:
        # 清理链接
        if '#replay' in entry.link and 'v2ex' in entry.link:
            entry.link = entry.link.split('#')[0]

        # 内容提取
        entry.article = clean_html(
            getattr(entry, 'content', [{}])[0].get('value', 
            getattr(entry, 'description', getattr(entry, 'title', '')))
        )

        # AI总结
        if DEEPSEEK_API_KEY and len(entry.article) > 100:
            entry.summary = gpt_summary(entry.article, language)
            if entry.summary:
                log_system('debug', f"Summary generated for {entry.link[:50]}...")
            else:
                entry.article = f"[摘要失败] {entry.article[:500]}"
                log_system('warning', f"Summary failed for {entry.link}")
        return True
    except Exception as e:
        log_system('error', f"Entry processing failed: {entry.link}", e)
        return False

def output(section, language):
    """核心处理流程"""
    section_log = os.path.join(BASE, f"{get_cfg(section, 'name')}.log")  # docs/section.log
    log_system('process', f"Processing section: {section}")

    try:
        # 读取现有条目
        existing_entries = []
        feed_file = os.path.join(BASE, f"{get_cfg(section, 'name')}.xml")
        if os.path.exists(feed_file):
            with open(feed_file, 'r') as f:
                existing_entries = feedparser.parse(f.read()).entries[:max_entries]

        # 处理新条目
        new_entries = []
        for url in get_cfg(section, 'url', '').split(','):
            if not url.strip():
                continue
            
            feed_data = fetch_feed(url.strip(), section_log)
            if not feed_data['feed'] or not feed_data['feed'].entries:
                continue

            for entry in feed_data['feed'].entries:
                if process_entry(entry, section, section_log):
                    new_entries.append(entry)

        # 生成最终RSS
        with open('template.xml') as f:
            template = Template(f.read())

        rss_content = template.render(
            feed={
                'title': f"{get_cfg(section, 'name')} - AI Summary",
                'link': deployment_url,
                'entries': new_entries + existing_entries[:max_entries]
            }
        )

        with open(feed_file, 'w') as f:
            f.write(rss_content)

        log_system('success', f"Generated {len(new_entries)} new entries for {section}")
    except Exception as e:
        log_system('critical', f"Section processing failed: {section}", e)

if __name__ == "__main__":
    log_system('system', 'Starting main processing')
    for section in config.sections()[1:]:  # Skip [cfg]
        output(section, language)
    
    # 生成索引页面（保持不变）
    with open(os.path.join(BASE, 'index.html'), 'w') as f:
        template = Template(open('template.html').read())
        html = template.render(
            update_time=datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            feeds=[{"name": get_cfg(s, 'name'), "url": f"{deployment_url}{get_cfg(s, 'name')}.xml"} 
                  for s in config.sections()[1:]]
        )
        f.write(html)
    
    log_system('system', 'Processing completed')
