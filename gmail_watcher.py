import time
print("Gmail Watcher Running persistently...")
try:
    while True:
        # Hackathon simulation logic can be added here
        time.sleep(10)
except KeyboardInterrupt:
    print("Stopping")
