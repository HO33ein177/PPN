from waitress import serve
from Run import app
import os

# 1. Fetch Port (Default to 80 for production, or 5000)
# Note: Port 80 requires running CMD/PowerShell as Administrator
PORT = int(os.environ.get('PORT', 80))

print(f"Starting Production Server on Port {PORT}...")
print("Press Ctrl+C to stop.")

# 2. Start the Server
# threads=6 allows 6 concurrent users to request pages at once
serve(app, host='0.0.0.0', port=PORT, threads=6)