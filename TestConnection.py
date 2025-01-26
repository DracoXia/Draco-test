import requests

def test_api_connection(url):
    try:
        response = requests.get(url, timeout=10)
        print(f"Status Code: {response.status_code}")
        if response.status_code == 200:
            print("API connection successful!")
        else:
            print("API connection failed. Please check the URL and network settings.")
    except requests.RequestException as e:
        print(f"API connection failed: {e}")

if __name__ == "__main__":
    url = "https://api.deepseek.com/v1/models"
    test_api_connection(url)
