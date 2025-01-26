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

# 配置文件处理
def get_cfg(sec, name, default=None):
    value = config.get(sec, name, fallback=default)
    return value.strip('"') if value else value

config = configparser.ConfigParser()
config.read('config.ini')
secs = config.sections()
max_entries = 1000

# 环境变量配置
OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY')  # 修改环境变量名以明确用途
U_NAME = os.environ.get('U_NAME')
OPENAI_PROXY = os.environ.get('PROXY_URL', '')
OPENAI_BASE_URL = os.environ.get('DEEPSEEK_BASE_URL', 'https://api.deepseek.com/v1/')
custom_model = 'deepseek-chat'
deployment_url = f'https://{U_NAME}.github.io/RSS-GPT/'
BASE = get_cfg('cfg', 'BASE', './output')
keyword_length = int(get_cfg('cfg', 'keyword_length', '5'))
summary_length = int(get_cfg('cfg', 'summary_length', '200'))
language = get_cfg('cfg', 'language', 'zh')

# 初始化日志
def init_logger():
    log_dir = os.path.join(BASE, 'system.log')
    with open(log_dir, 'a') as f:
        f.write('\n' + '='*60 + '\n')
        f.write(f'Initialization at {datetime.datetime.now()}\n')
        f.write('Configuration:\n')
        f.write(f'- BASE: {BASE}\n')
        f.write(f'- API_KEY: {"*****" if OPENAI_API_KEY else "None"}\n')
        f.write(f'- BASE_URL: {OPENAI_BASE_URL}\n')
        f.write(f'- PROXY: {OPENAI_PROXY if OPENAI_PROXY else "No proxy"}\n')
    return log_dir

system_log = init_logger()

# RSS抓取模块
def fetch_feed(url, log_file):
    headers = {'User-Agent': UserAgent().random.strip()}
    try:
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        return {
            'feed': feedparser.parse(response.text),
            'status': 'success',
            'status_code': response.status_code
        }
    except Exception as e:
        error_msg = f"抓取失败: {str(e)}"
        with open(log_file, 'a') as f:
            f.write(f"[{datetime.datetime.now()}] {error_msg}\n")
        return {
            'feed': None,
            'status': 'error',
            'error': error_msg
        }

# 内容清理模块
def clean_html(html_content):
    try:
        soup = BeautifulSoup(html_content, "html.parser")
        # 移除不需要的标签
        for tag in ["script", "style", "img", "a", "video", "audio", "iframe", "input"]:
            for element in soup.find_all(tag):
                element.decompose()
        # 处理空白字符
        text = soup.get_text()
        text = re.sub(r'\s+', ' ', text).strip()
        return text
    except Exception as e:
        with open(system_log, 'a') as f:
            f.write(f"HTML清理失败: {str(e)}\n")
        return html_content  # 返回原始内容作为后备

# 过滤模块
def filter_entry(entry, filter_apply, filter_type, filter_rule, log_file):
    try:
        text = ''
        if filter_apply == 'title':
            text = getattr(entry, 'title', '')
        elif filter_apply == 'article':
            text = getattr(entry, 'article', '')
        elif filter_apply == 'link':
            text = getattr(entry, 'link', '')
        
        with open(log_file, 'a') as f:
            f.write(f"\n过滤检查 -> 类型: {filter_apply}\n")
            f.write(f"原始内容: {text[:200]}...\n")
            f.write(f"规则: {filter_type} -> {filter_rule}\n")

        if filter_type == 'include':
            result = re.search(filter_rule, text)
        elif filter_type == 'exclude':
            result = not re.search(filter_rule, text)
        elif filter_type == 'regex match':
            result = re.fullmatch(filter_rule, text)
        elif filter_type == 'regex not match':
            result = not re.fullmatch(filter_rule, text)
        else:
            result = True

        with open(log_file, 'a') as f:
            f.write(f"过滤结果: {'保留' if result else '过滤'}\n")
        return result

    except Exception as e:
        with open(log_file, 'a') as f:
            f.write(f"过滤错误: {str(e)}\n")
        return True  # 出错时默认保留

# DeepSeek集成模块
def deepseek_summary(content, log_file):
    try:
        client = OpenAI(
            api_key=OPENAI_API_KEY,
            base_url=OPENAI_BASE_URL,
            http_client=httpx.Client(
                proxies=OPENAI_PROXY,
                timeout=30.0
            ) if OPENAI_PROXY else None,
            default_headers={
                "HTTP-Referer": deployment_url,
                "X-Title": "RSS-GPT",
                "User-Agent": f"RSS-GPT/1.0 (GitHub; {U_NAME})"
            }
        )

        messages = [{
            "role": "user",
            "content": f"请用{language}总结以下内容，先提取{keyword_length}个关键词，然后进行分要点总结，总字数不超过{summary_length}字：\n{content}"
        }]

        with open(log_file, 'a') as f:
            f.write("\n正在请求DeepSeek API...\n")
            f.write(f"请求内容长度: {len(content)} 字符\n")
            f.write(f"模型: {custom_model}\n")

        response = client.chat.completions.create(
            model=custom_model,
            messages=messages,
            temperature=0.7,
            max_tokens=summary_length*2
        )

        summary = response.choices[0].message.content
        cost = response.usage.total_tokens

        with open(log_file, 'a') as f:
            f.write(f"API响应成功！\n")
            f.write(f"使用Token数: {cost}\n")
            f.write(f"摘要内容: {summary[:200]}...\n")

        return summary

    except Exception as e:
        error_detail = f"""
        [!] 摘要生成失败！
        错误类型: {type(e).__name__}
        错误信息: {str(e)}
        请求URL: {OPENAI_BASE_URL}
        代理状态: {'使用中' if OPENAI_PROXY else '未使用'}
        跟踪信息:
        {traceback.format_exc()}
        """
        with open(log_file, 'a') as f:
            f.write(error_detail)
        return None

# RSS生成核心逻辑
def process_feed(sec):
    feed_name = get_cfg(sec, 'name', 'default-feed')
    log_file = os.path.join(BASE, f"{feed_name}.log")
    output_file = os.path.join(BASE, f"{feed_name}.xml")

    # 初始化日志
    with open(log_file, 'a') as f:
        f.write('\n' + '='*60 + '\n')
        f.write(f"处理开始时间: {datetime.datetime.now()}\n")
        f.write(f"配置参数:\n")
        f.write(f"- 源URL: {get_cfg(sec, 'url')}\n")
        f.write(f"- 最大条目: {max_entries}\n")
        f.write(f"- 关键词数量: {keyword_length}\n")
        f.write(f"- 摘要长度: {summary_length}字\n")

    # 读取现有内容
    existing_entries = []
    try:
        if os.path.exists(output_file):
            with open(output_file, 'r') as f:
                existing_feed = feedparser.parse(f.read())
                existing_entries = existing_feed.entries[:max_entries]
            with open(log_file, 'a') as f:
                f.write(f"发现现有文件，已加载 {len(existing_entries)} 条历史条目\n")
    except Exception as e:
        with open(log_file, 'a') as f:
            f.write(f"读取历史文件失败: {str(e)}\n")

    # 处理每个RSS源
    new_entries = []
    rss_urls = [url.strip() for url in get_cfg(sec, 'url', '').split(',') if url.strip()]

    for url in rss_urls:
        with open(log_file, 'a') as f:
            f.write(f"\n正在处理源: {url}\n")

        feed_data = fetch_feed(url, log_file)
        if not feed_data['feed'] or not feed_data['feed'].entries:
            with open(log_file, 'a') as f:
                f.write("! 无效的RSS源或没有条目\n")
            continue

        for entry in feed_data['feed'].entries:
            try:
                # 基础信息处理
                entry.link = entry.link.split('#')[0]  # 清理URL
                entry.title = getattr(entry, 'title', entry.link[:50])
                entry.article = clean_html(
                    getattr(entry, 'content', [{}])[0].get('value', 
                    getattr(entry, 'description', entry.title))
                )

                # 去重检查
                if any(e.link == entry.link for e in existing_entries + new_entries):
                    with open(log_file, 'a') as f:
                        f.write(f"跳过重复条目: {entry.title}\n")
                    continue

                # 内容过滤
                filter_apply = get_cfg(sec, 'filter_apply')
                filter_type = get_cfg(sec, 'filter_type')
                filter_rule = get_cfg(sec, 'filter_rule')
                if not filter_entry(entry, filter_apply, filter_type, filter_rule, log_file):
                    continue

                # 生成摘要
                entry.summary = None
                if OPENAI_API_KEY and len(new_entries) < int(get_cfg(sec, 'max_items', 50)):
                    entry.summary = deepseek_summary(entry.article, log_file)
                    if not entry.summary:
                        entry.article = "摘要生成失败，显示部分原文：" + entry.article[:500]

                new_entries.append(entry)
                with open(log_file, 'a') as f:
                    f.write(f"√ 添加新条目: {entry.title}\n")

                # 数量限制
                if len(new_entries) >= max_entries:
                    with open(log_file, 'a') as f:
                        f.write("达到最大条目限制，停止处理\n")
                    break

            except Exception as e:
                with open(log_file, 'a') as f:
                    f.write(f"处理条目时发生错误: {str(e)}\n")
                continue

    # 合并新旧内容
    all_entries = new_entries + existing_entries
    final_entries = all_entries[:max_entries]

    # 生成RSS文件
             try:
                template = Template(open('template.xml').read())
                
                # 构建符合模板要求的数据结构
                processed_feed = {
                    # 第一层feed对象
                    'feed': {
                        'title': get_cfg(sec, 'name', 'AI摘要RSS'),
                        'link': deployment_url,
                        # 从原始feed中获取描述（如果存在）
                        'description': getattr(feed, 'feed', {}).get('description', 'AI生成的摘要内容')
                    },
                    # 合并新旧条目
                    'append_entries': append_entries,
                    'existing_entries': existing_entries
                }
        
                # 确保条目结构正确
                for entry_list in [processed_feed['append_entries'], processed_feed['existing_entries']]:
                    for entry in entry_list:
                        # 强制添加必要属性
                        if not hasattr(entry, 'content'):
                            entry.content = [{'value': getattr(entry, 'article', '')}]
                        # 处理日期字段
                        if not hasattr(entry, 'published'):
                            entry.published = datetime.datetime.now().strftime('%a, %d %b %Y %H:%M:%S GMT')
        
                rss = template.render(feed=processed_feed)
                
                with open(out_dir + '.xml', 'w') as f:
                    f.write(rss)
        
            except Exception as e:
                with open(log_file, 'a') as f:
                    f.write(f"RSS生成失败: {str(e)}\n")
                    f.write(f"数据结构验证:\n")
                    f.write(f"- feed层级: {processed_feed.keys()}\n")
                    f.write(f"- feed.feed存在: {'feed' in processed_feed}\n")
                    f.write(f"- 条目数: append={len(append_entries)}, existing={len(existing_entries)}\n")

# 主程序
if __name__ == "__main__":
    # 创建输出目录
    os.makedirs(BASE, exist_ok=True)

    # 处理所有配置节
    for section in secs[1:]:  # 跳过默认的cfg节
        try:
            with open(system_log, 'a') as f:
                f.write(f"\n开始处理配置节: {section}\n")
            process_feed(section)
        except Exception as e:
            with open(system_log, 'a') as f:
                f.write(f"处理配置节 {section} 时发生严重错误: {str(e)}\n")

    # 生成索引页面
    try:
        feeds_list = []
        for section in secs[1:]:
            feeds_list.append({
                'name': get_cfg(section, 'name'),
                'url': f"{deployment_url}{get_cfg(section, 'name')}.xml"
            })

        with open('template.html') as f:
            template = Template(f.read())
        
        index_content = template.render(
            update_time=datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
            feeds=feeds_list
        )

        with open(os.path.join(BASE, 'index.html'), 'w') as f:
            f.write(index_content)

        with open(system_log, 'a') as f:
            f.write("\n索引页面生成成功\n")

    except Exception as e:
        with open(system_log, 'a') as f:
            f.write(f"生成索引页面失败: {str(e)}\n")

    print("处理完成！请检查输出目录和日志文件")
