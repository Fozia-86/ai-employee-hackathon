import time
print("LinkedIn Watcher Running persistently...")
try:
    while True:
        # Hackathon simulation logic can be added here
        time.sleep(10)
except KeyboardInterrupt:
    print("Stopping")
