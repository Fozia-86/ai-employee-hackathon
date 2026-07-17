"""
Standalone test for triage_email() (mcp_servers/vault_server.py).

Runs 3 dummy/test emails through the triage logic and reports the outcome for
each. Uses only hardcoded test data -- no real Gmail connection. Real Gmail
integration is a separate, future task (see CLAUDE.md).

Usage: venv/bin/python3 test_email_triage.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "mcp_servers"))
import vault_server as vs

TEST_EMAILS = [
    {
        "label": "business_inquiry",
        "subject": "Interested in your Agentic Workflow Setup service",
        "sender": "prospect@acme-corp.com",
        "body": (
            "Hi, we came across your agentic workflow offering and would like "
            "a quote / proposal for a project starting next quarter."
        ),
    },
    {
        "label": "spam_irrelevant",
        "subject": "Congratulations you won!!!",
        "sender": "prize-alert@totally-legit-lottery.biz",
        "body": "Claim your prize now! Click here now to unlock free money.",
    },
    {
        "label": "discount_request_over_ceiling",
        "subject": "Request for 35% discount on our next project",
        "sender": "client_d@example.com",
        "body": (
            "We've worked with you for 5 projects now and would like a 35% "
            "discount on the upcoming Agentic Workflow Setup."
        ),
    },
]

def main():
    for case in TEST_EMAILS:
        print(f"\n=== Test case: {case['label']} ===")
        result = vs.triage_email(case["subject"], case["sender"], case["body"])
        print(result)

if __name__ == "__main__":
    main()
