import requests

def test_api_connection(api_url, api_key):
    headers = {
        'Authorization': f'Bearer {api_key}',
        'Content-Type': 'application/json'
    }
    try:
        response = requests.get(api_url, headers=headers, timeout=10)
        print(f"Status Code: {response.status_code}")
        if response.status_code == 200:
            print("API connection successful!")
            print("Response:", response.json())  # 打印API返回的数据
        else:
            print("API connection failed. Please check the URL, API Key, and network settings.")
            print("Response:", response.text)  # 打印API返回的错误信息
    except requests.RequestException as e:
        print(f"API connection failed: {e}")

if __name__ == "__main__":
    api_url = "https://api.deepseek.com/v1/models"
    api_key = "sk-10db767782cf4af78e50305aa46ca1dc"  # 替换为你的DeepSeek API Key
    test_api_connection(api_url, api_key)
