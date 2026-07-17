# Business Rules and Discount Policy

## Standard Discount Boundaries
1. **Autonomous Authorization**: The AI Agent is authorized to autonomously offer a discount of up to **20%** if the deal value is justified and the customer has a valid history inside Odoo.
2. **Escalation Constraint**: Any requested discount strictly greater than **20%** cannot be authorized autonomously. The agent must immediately cease execution and generate a Markdown approval request in the `/Pending_Approval/` directory.

## System Workflow Configuration
- All successful negotiations require a draft invoice log in Odoo.
- Transaction data must be encrypted before local storage.