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

# 新增系统日志功能
system_log = os.path.join(os.getcwd(), 'system.log')

def init_system_log():
    with open(system_log, 'a') as f:
        f.write('\n' + '='*60 + '\n')
        f.write(f'System Initialized at {datetime.datetime.now()}\n')

init_system_log()

def log_system_event(message):
    with open(system_log, 'a') as f:
        f.write(f"[{datetime.datetime.now()}] {message}\n")

def get_cfg(sec, name, default=None):
    value = config.get(sec, name, fallback=default)
    return value.strip('"') if value else value

config = configparser.ConfigParser()
config.read('config.ini')
secs = config.sections()
max_entries = 1000

# 修改为DeepSeek环境变量
DEEPSEEK_API_KEY = os.environ.get('OPEN_API_KEY')
U_NAME = os.environ.get('U_NAME')
DEEPSEEK_PROXY = os.environ.get('DEEPSEEK_PROXY', '')
DEEPSEEK_BASE_URL = os.environ.get('DEEPSEEK_BASE_URL', 'https://api.deepseek.com/v1')
custom_model = 'deepseek-chat'
deployment_url = f'https://{U_NAME}.github.io/RSS-GPT/'
BASE = get_cfg('cfg', 'BASE')
keyword_length = int(get_cfg('cfg', 'keyword_length'))
summary_length = int(get_cfg('cfg', 'summary_length'))
language = get_cfg('cfg', 'language')

def fetch_feed(url, log_file):
    feed = None
    response = None
    headers = {}
    try:
        ua = UserAgent()
        headers['User-Agent'] = ua.random.strip()
        response = requests.get(url, headers=headers, timeout=30)
        if response.status_code == 200:
            feed = feedparser.parse(response.text)
            return {'feed': feed, 'status': 'success'}
        else:
            with open(log_file, 'a') as f:
                f.write(f"Fetch error: {response.status_code}\n")
            return {'feed': None, 'status': response.status_code}
    except requests.RequestException as e:
        with open(log_file, 'a') as f:
            f.write(f"Fetch error: {e}\n")
        return {'feed': None, 'status': 'failed'}

def generate_untitled(entry):
    try:
        return entry.title
    except:
        try:
            return entry.article[:50]
        except:
            return entry.link

def clean_html(html_content):
    soup = BeautifulSoup(html_content, "html.parser")
    for tag in ["script", "style", "img", "a", "video", "audio", "iframe", "input"]:
        for element in soup.find_all(tag):
            element.decompose()
    return soup.get_text()

def filter_entry(entry, filter_apply, filter_type, filter_rule):
    if filter_apply == 'title':
        text = entry.title
    elif filter_apply == 'article':
        text = entry.article
    elif filter_apply == 'link':
        text = entry.link
    elif not filter_apply:
        return True
    else:
        raise Exception('filter_apply not supported')

    if filter_type == 'include':
        return re.search(filter_rule, text)
    elif filter_type == 'exclude':
        return not re.search(filter_rule, text)
    elif filter_type == 'regex match':
        return re.search(filter_rule, text)
    elif filter_type == 'regex not match':
        return not re.search(filter_rule, text)
    elif not filter_type:
        return True
    else:
        raise Exception('filter_type not supported')

def read_entry_from_file(sec):
    out_dir = os.path.join(BASE, get_cfg(sec, 'name'))
    try:
        with open(out_dir + '.xml', 'r') as f:
            rss = f.read()
        feed = feedparser.parse(rss)
        return feed.entries
    except:
        return []

def truncate_entries(entries, max_entries):
    if len(entries) > max_entries:
        entries = entries[:max_entries]
    return entries

def gpt_summary(query, model, language):
    log_tag = f"[DeepSeek-API][{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}]"
    try:
        log_system_event(f"{log_tag} Starting summary generation")
        
        client_params = {
            "api_key": DEEPSEEK_API_KEY,
            "base_url": DEEPSEEK_BASE_URL,
        }
        if DEEPSEEK_PROXY:
            client_params["http_client"] = httpx.Client(proxy=DEEPSEEK_PROXY)

        client = OpenAI(**client_params)

        if language == "zh":
            messages = [
                {"role": "user", "content": query},
                {"role": "assistant", "content": f"请用中文总结这篇文章，先提取出{keyword_length}个关键词，在同一行内输出，然后换行，用中文在{summary_length}字内写一个包含所有要点的总结，按顺序分要点输出，并按照以下格式输出'<br><br>总结:'，<br>是HTML的换行符，输出时必须保留2个，并且必须在'总结:'二字之前"}
            ]
        else:
            messages = [
                {"role": "user", "content": query},
                {"role": "assistant", "content": f"Please summarize this article in {language} language, first extract {keyword_length} keywords, output in the same line, then line break, write a summary containing all the points in {summary_length} words in {language}, output in order by points, and output in the following format '<br><br>Summary:' , <br> is the line break of HTML, 2 must be retained when output, and must be before the word 'Summary:'"}
            ]

        completion = client.chat.completions.create(
            model=model,
            messages=messages,
        )
        
        log_system_event(
            f"{log_tag} Summary success | "
            f"Model: {model} | "
            f"Tokens: {completion.usage.total_tokens} | "
            f"ID: {completion.id}"
        )
        return completion.choices[0].message.content

    except Exception as e:
        error_msg = (
            f"{log_tag} Summary failed | "
            f"Error: {type(e).__name__} | "
            f"Message: {str(e)} | "
            f"Trace: {traceback.format_exc()[:500]}"
        )
        log_system_event(error_msg)
        return None

def output(sec, language):
    log_file = os.path.join(BASE, get_cfg(sec, 'name') + '.log')
    out_dir = os.path.join(BASE, get_cfg(sec, 'name'))
    rss_urls = get_cfg(sec, 'url')
    rss_urls = rss_urls.split(',')

    filter_apply = get_cfg(sec, 'filter_apply')
    filter_type = get_cfg(sec, 'filter_type')
    filter_rule = get_cfg(sec, 'filter_rule')

    if filter_apply and filter_type and filter_rule:
        pass
    elif not filter_apply and not filter_type and not filter_rule:
        pass
    else:
        raise Exception('filter_apply, type, rule must be set together')

    max_items = get_cfg(sec, 'max_items')
    if not max_items:
        max_items = 0
    else:
        max_items = int(max_items)
    cnt = 0
    existing_entries = read_entry_from_file(sec)
    with open(log_file, 'a') as f:
        f.write('------------------------------------------------------\n')
        f.write(f'Started: {datetime.datetime.now()}\n')
        f.write(f'Existing_entries: {len(existing_entries)}\n')
    existing_entries = truncate_entries(existing_entries, max_entries=max_entries)
    append_entries = []

    log_system_event(f"Processing section [{sec}] started")

    for rss_url in rss_urls:
        with open(log_file, 'a') as f:
            f.write(f"Fetching from {rss_url}\n")
            print(f"Fetching from {rss_url}")
        feed = fetch_feed(rss_url, log_file)['feed']
        if not feed:
            with open(log_file, 'a') as f:
                f.write(f"Fetch failed from {rss_url}\n")
            continue
        for entry in feed.entries:
            if cnt > max_entries:
                with open(log_file, 'a') as f:
                    f.write(f"Skip from: [{entry.title}]({entry.link})\n")
                break

            if entry.link.find('#replay') and entry.link.find('v2ex'):
                entry.link = entry.link.split('#')[0]

            if entry.link in [x.link for x in existing_entries]:
                continue

            if entry.link in [x.link for x in append_entries]:
                continue

            entry.title = generate_untitled(entry)

            try:
                entry.article = entry.content[0].value
            except:
                try:
                    entry.article = entry.description
                except:
                    entry.article = entry.title

            cleaned_article = clean_html(entry.article)

            if not filter_entry(entry, filter_apply, filter_type, filter_rule):
                with open(log_file, 'a') as f:
                    f.write(f"Filter: [{entry.title}]({entry.link})\n")
                continue

            cnt += 1
            if cnt > max_items:
                entry.summary = None
            elif DEEPSEEK_API_KEY:
                token_length = len(cleaned_article)
                try:
                    entry.summary = gpt_summary(cleaned_article, model=custom_model, language=language)
                    if entry.summary:
                        with open(log_file, 'a') as f:
                            f.write(f"[Success] Summarized using {custom_model}\n")
                    else:
                        with open(log_file, 'a') as f:
                            f.write("[Failed] Summary returned None\n")
                except Exception as e:
                    entry.summary = None
                    with open(log_file, 'a') as f:
                        f.write(f"[Error] Summarization failed: {str(e)}\n")

            append_entries.append(entry)
            with open(log_file, 'a') as f:
                f.write(f"Append: [{entry.title}]({entry.link})\n")

    with open(log_file, 'a') as f:
        f.write(f'append_entries: {len(append_entries)}\n')

    template = Template(open('template.xml').read())
    
    try:
        rss = template.render(feed=feed, append_entries=append_entries, existing_entries=existing_entries)
        with open(out_dir + '.xml', 'w') as f:
            f.write(rss)
        with open(log_file, 'a') as f:
            f.write(f'Finish: {datetime.datetime.now()}\n')
    except:
        with open(log_file, 'a') as f:
            f.write(f"error when rendering xml, skip {out_dir}\n")
            print(f"error when rendering xml, skip {out_dir}\n")

    log_system_event(f"Processing section [{sec}] completed")

try:
    os.mkdir(BASE)
except:
    pass

feeds = []
links = []

for x in secs[1:]:
    output(x, language=language)
    feed = {"url": get_cfg(x, 'url').replace(',', '<br>'), "name": get_cfg(x, 'name')}
    feeds.append(feed)
    links.append("- " + get_cfg(x, 'url').replace(',', ', ') + " -> " + deployment_url + feed['name'] + ".xml\n")

def append_readme(readme, links):
    with open(readme, 'r') as f:
        readme_lines = f.readlines()
    while readme_lines[-1].startswith('- ') or readme_lines[-1] == '\n':
        readme_lines = readme_lines[:-1]
    readme_lines.append('\n')
    readme_lines.extend(links)
    with open(readme, 'w') as f:
        f.writelines(readme_lines)

append_readme("README.md", links)
append_readme("README-zh.md", links)

with open(os.path.join(BASE, 'index.html'), 'w') as f:
    template = Template(open('template.html').read())
    html = template.render(update_time=datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), feeds=feeds)
    f.write(html)
