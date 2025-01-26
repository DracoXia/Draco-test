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
#from dateutil.parser import parse

def get_cfg(sec, name, default=None):
    value=config.get(sec, name, fallback=default)
    if value:
        return value.strip('"')

config = configparser.ConfigParser()
config.read('config.ini')
secs = config.sections()
# Maxnumber of entries to in a feed.xml file
max_entries = 1000

OPENAI_API_KEY = os.environ.get('OPEN_API_KEY')
CRYPTO_STATUS = os.environ.get('CRYPTO_STATUS')
STOCKS_STATUS = os.environ.get('STOCKS_STATUS')
U_NAME = os.environ.get('U_NAME')
OPENAI_PROXY = os.environ.get('OPENAI_PROXY', '')
OPENAI_BASE_URL = 'https://api.deepseek.com/v1'
custom_model = 'deepseek-chat'
deployment_url = f'https://{U_NAME}.github.io/RSS-GPT/'
BASE =get_cfg('cfg', 'BASE')
keyword_length = int(get_cfg('cfg', 'keyword_length'))
summary_length = int(get_cfg('cfg', 'summary_length'))
language = get_cfg('cfg', 'language')

def generate_analysis_prompt(language):
    """生成持仓分析提示模板"""
    analysis_instruction = (
        "\n\n当前持仓状态："
        "\n- 加密货币：{crypto_status}"
        "\n- 股票持仓：{stocks_status}"
        "\n请根据最新资讯内容，结合以下规则分析持仓合理性："
        "\n1. 若资讯涉及持仓标的的重大利好/利空，建议调整对应仓位"
        "\n2. 若资讯影响股息，分红，建议调整对应仓位"
        "\n3. 黑天鹅事件预警，例如虚拟币交易所出现问题，建议调整对应仓位"
        "\n输出格式：'<br>持仓建议：...'（如无必要调整，则显示：当前持仓状况健康）"
        if language == "zh" else
        "\n\nCurrent Holdings："
        "\n- Crypto: {crypto_status}"
        "\n- Stocks: {stocks_status}"
        "\nAnalyze portfolio based on news content with rules:"
        "\n1. Adjust positions if major news affects holdings"
        "\n2. Single asset class ≤50% of total"
        "\n3. Keep cash ratio 10%-30%"
        "\nFormat: '<br>Portfolio Advice：...' (Show: Current portfolio is healthy if no changes needed)"
    )
    return analysis_instruction.format(
        crypto_status=CRYPTO_STATUS or "未配置",
        stocks_status=STOCKS_STATUS or "未配置"
    )
    
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
    try: return entry.title
    except: 
        try: return entry.article[:50]
        except: return entry.link


def clean_html(html_content):
    """
    This function is used to clean the HTML content.
    It will remove all the <script>, <style>, <img>, <a>, <video>, <audio>, <iframe>, <input> tags.
    Returns:
        Cleaned text for summarization
    """
    soup = BeautifulSoup(html_content, "html.parser")

    for script in soup.find_all("script"):
        script.decompose()

    for style in soup.find_all("style"):
        style.decompose()

    for img in soup.find_all("img"):
        img.decompose()

    for a in soup.find_all("a"):
        a.decompose()

    for video in soup.find_all("video"):
        video.decompose()

    for audio in soup.find_all("audio"):
        audio.decompose()
    
    for iframe in soup.find_all("iframe"):
        iframe.decompose()
    
    for input in soup.find_all("input"):
        input.decompose()

    return soup.get_text()

def filter_entry(entry, filter_apply, filter_type, filter_rule):
    """
    This function is used to filter the RSS feed.

    Args:
        entry: RSS feed entry
        filter_apply: title, article or link
        filter_type: include or exclude or regex match or regex not match
        filter_rule: regex rule or keyword rule, depends on the filter_type

    Raises:
        Exception: filter_apply not supported
        Exception: filter_type not supported
    """
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
    """
    This function is used to read the RSS feed entries from the feed.xml file.

    Args:
        sec: section name in config.ini
    """
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
    """整合总结与持仓分析的 DeepSeek 接口"""
    try:
        # 组合查询内容与分析提示
        combined_query = query + generate_analysis_prompt(language)
        
        # 初始化 DeepSeek 客户端
        client = OpenAI(
            api_key=OPENAI_API_KEY,
            base_url=OPENAI_BASE_URL,
            http_client=httpx.Client(proxy=os.environ.get('OPENAI_PROXY')) if os.environ.get('OPENAI_PROXY') else None
        )

        # 构建消息模板
        messages = [
            {"role": "user", "content": combined_query},
            {"role": "assistant", "content": 
                f"请用{language}总结，先提取{keyword_length}个关键词，然后分要点总结（{summary_length}字内）\n"
                "格式：'<br><br>总结：'（保留两个换行）" + 
                ("\n最后用'<br>持仓建议：'附加分析" if language == "zh" else "\nAppend analysis with '<br>Portfolio Advice：'")
            }
        ]

        # API 调用
        completion = client.chat.completions.create(
            model="deepseek-chat",
            messages=messages,
            temperature=0.5,
            max_tokens=summary_length*2 + 100
        )

        # 解析响应
        result = completion.choices[0].message.content
        
        # 提取建议并记录日志
        advice = "无建议生成"
        if '<br>持仓建议：' in result:
            advice = result.split('<br>持仓建议：')[-1].strip()
        elif '<br>Portfolio Advice：' in result:
            advice = result.split('<br>Portfolio Advice：')[-1].strip()
        log_system('analysis', f"建议摘要：{advice[:50]}")

        return result  # 确保此行在函数体内且正确缩进

    except Exception as e:
        log_system('error', "API 调用失败", e)
        return None

def output(sec, language):
    """ output
    This function is used to output the summary of the RSS feed.

    Args:
        sec: section name in config.ini

    Raises:
        Exception: filter_apply, type, rule must be set together in config.ini
    """
    log_file = os.path.join(BASE, get_cfg(sec, 'name') + '.log')
    out_dir = os.path.join(BASE, get_cfg(sec, 'name'))
    # read rss_url as a list separated by comma
    rss_urls = get_cfg(sec, 'url')
    rss_urls = rss_urls.split(',')

    # RSS feed filter apply, filter title, article or link, summarize title, article or link
    filter_apply = get_cfg(sec, 'filter_apply')

    # RSS feed filter type, include or exclude or regex match or regex not match
    filter_type = get_cfg(sec, 'filter_type')

    # Regex rule or keyword rule, depends on the filter_type
    filter_rule = get_cfg(sec, 'filter_rule')

    # filter_apply, type, rule must be set together
    if filter_apply and filter_type and filter_rule:
        pass
    elif not filter_apply and not filter_type and not filter_rule:
        pass
    else:
        raise Exception('filter_apply, type, rule must be set together')

    # Max number of items to summarize
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
    # Be careful when the deleted ones are still in the feed, in that case, you will mess up the order of the entries.
    # Truncating old entries is for limiting the file size, 1000 is a safe number to avoid messing up the order.
    append_entries = []

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
                try: entry.article = entry.description
                except: entry.article = entry.title

            cleaned_article = clean_html(entry.article)

            if not filter_entry(entry, filter_apply, filter_type, filter_rule):
                with open(log_file, 'a') as f:
                    f.write(f"Filter: [{entry.title}]({entry.link})\n")
                continue


#            # format to Thu, 27 Jul 2023 13:13:42 +0000
#            if 'updated' in entry:
#                entry.updated = parse(entry.updated).strftime('%a, %d %b %Y %H:%M:%S %z')
#            if 'published' in entry:
#                entry.published = parse(entry.published).strftime('%a, %d %b %Y %H:%M:%S %z')

            cnt += 1
            if cnt > max_items:
                entry.summary = None
            elif OPENAI_API_KEY:
                token_length = len(cleaned_article)
                if custom_model:
                    try:
                        entry.summary = gpt_summary(cleaned_article,model=custom_model, language=language)
                        with open(log_file, 'a') as f:
                            f.write(f"Token length: {token_length}\n")
                            f.write(f"Summarized using {custom_model}\n")
                    except Exception as e:
                        entry.summary = None
                        with open(log_file, 'a') as f:
                            f.write(f"Summarization failed, append the original article\n")
                            f.write(f"error: {e}\n")
                else:
                    try:
                        entry.summary = gpt_summary(cleaned_article,model="gpt-4o-mini", language=language)
                        with open(log_file, 'a') as f:
                            f.write(f"Token length: {token_length}\n")
                            f.write(f"Summarized using gpt-4o-mini\n")
                    except:
                        try:
                            entry.summary = gpt_summary(cleaned_article,model="gpt-4o", language=language)
                            with open(log_file, 'a') as f:
                                f.write(f"Token length: {token_length}\n")
                                f.write(f"Summarized using GPT-4o\n")
                        except Exception as e:
                            entry.summary = None
                            with open(log_file, 'a') as f:
                                f.write(f"Summarization failed, append the original article\n")
                                f.write(f"error: {e}\n")

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
        with open (log_file, 'a') as f:
            f.write(f"error when rendering xml, skip {out_dir}\n")
            print(f"error when rendering xml, skip {out_dir}\n")

try:
    os.mkdir(BASE)
except:
    pass

feeds = []
links = []

for x in secs[1:]:
    output(x, language=language)
    feed = {"url": get_cfg(x, 'url').replace(',','<br>'), "name": get_cfg(x, 'name')}
    feeds.append(feed)  # for rendering index.html
    links.append("- "+ get_cfg(x, 'url').replace(',',', ') + " -> " + deployment_url + feed['name'] + ".xml\n")

def append_readme(readme, links):
    with open(readme, 'r') as f:
        readme_lines = f.readlines()
    while readme_lines[-1].startswith('- ') or readme_lines[-1] == '\n':
        readme_lines = readme_lines[:-1]  # remove 1 line from the end for each feed
    readme_lines.append('\n')
    readme_lines.extend(links)
    with open(readme, 'w') as f:
        f.writelines(readme_lines)

append_readme("README.md", links)
append_readme("README-zh.md", links)

# Rendering index.html used in my GitHub page, delete this if you don't need it.
# Modify template.html to change the style
with open(os.path.join(BASE, 'index.html'), 'w') as f:
    template = Template(open('template.html').read())
    html = template.render(update_time=datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"), feeds=feeds)
    f.write(html)
