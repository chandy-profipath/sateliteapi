import requests

url = "http://localhost:8000/analyze"

data = {
    "type": "Feature",
    "properties": {},
    "geometry": {
        "type": "Polygon",
        "coordinates": [[
            [31.0267, -17.8292],
            [31.0297, -17.8292],
            [31.0297, -17.8322],
            [31.0267, -17.8322],
            [31.0267, -17.8292]
        ]]
    }
}

print(f"Sending request to {url}")
try:
    response = requests.post(url, json=data, timeout=60)
    print(f"Status Code: {response.status_code}")
    if response.status_code == 200:
        print("Analysis Results:")
        import json
        print(json.dumps(response.json(), indent=2))
    else:
        print(f"Error: {response.text}")
except Exception as e:
    print(f"Request failed: {e}")
