import requests
r = requests.get('http://localhost:8000/api/setup-status')
print(r.json())