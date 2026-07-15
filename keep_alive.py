from flask import Flask
from threading import Thread

app = Flask('')

@app.route('/')
def home():
    return "ServAuth is alive and kicking!"

def run():
    # Render automatically sets a PORT environment variable, defaulting to 8080 or 10000
    import os
    port = int(os.environ.get("PORT", 8080))
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run)
    t.start()
