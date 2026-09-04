"""
db/seed.py
──────────
Seed data for EvalCI's test suite.

Provides ``seed_questions()`` — a function that returns 50 realistic SaaS
customer-support questions, 5 per category, covering:
billing, login, security, refunds, technical, pricing, account,
shipping, privacy, integrations.

These questions are used as the fixed evaluation harness: every EvalCI run
scores the RAG system against the same 50 questions so that regression
detection is meaningful and reproducible.
"""

from __future__ import annotations


def seed_questions() -> list[dict]:
    """Return the full 50-question EvalCI test suite.

    Each dict contains:
        id (str):           Unique identifier in the format "q001" … "q050".
        question (str):     A realistic SaaS customer-support question.
        ground_truth (str): A 2–3 sentence correct answer.
        category (str):     One of the 10 supported category labels.

    Returns:
        List of 50 question dicts, ordered by category then question number.
    """
    return [
        # ── billing (q001–q005) ───────────────────────────────────────────────
        {
            "id": "q001",
            "question": "Why was I charged twice for my subscription this month?",
            "ground_truth": (
                "Duplicate charges can occur if a payment method was updated "
                "while a billing cycle was already in progress. Please check "
                "your invoice history in Settings → Billing. If you see two "
                "successful charges for the same period, contact support and "
                "we will issue a refund for the duplicate within 3–5 business days."
            ),
            "category": "billing",
        },
        {
            "id": "q002",
            "question": "How do I update my credit card on file?",
            "ground_truth": (
                "Navigate to Settings → Billing → Payment Methods and click "
                "'Add Payment Method'. Enter your new card details and set it "
                "as the default. The old card will no longer be charged on the "
                "next billing cycle."
            ),
            "category": "billing",
        },
        {
            "id": "q003",
            "question": "Can I switch from monthly to annual billing?",
            "ground_truth": (
                "Yes. Go to Settings → Billing → Subscription Plan and select "
                "'Switch to Annual'. You will be billed for the remainder of "
                "the current month pro-rated, and then the annual fee will be "
                "charged immediately. Annual plans receive a 20% discount."
            ),
            "category": "billing",
        },
        {
            "id": "q004",
            "question": "What happens to my data if my invoice goes unpaid?",
            "ground_truth": (
                "If an invoice remains unpaid for 7 days, your account is "
                "suspended — you lose access to the product but your data is "
                "retained for 30 days. After 30 days of suspension without "
                "payment, the account is scheduled for deletion and data "
                "cannot be recovered."
            ),
            "category": "billing",
        },
        {
            "id": "q005",
            "question": "Do you charge sales tax on SaaS subscriptions?",
            "ground_truth": (
                "Sales tax applicability depends on your billing address. "
                "We automatically collect and remit tax in jurisdictions where "
                "we are registered (currently US states that tax SaaS, and EU "
                "VAT regions). Your final invoice will itemise any applicable tax."
            ),
            "category": "billing",
        },

        # ── login (q006–q010) ─────────────────────────────────────────────────
        {
            "id": "q006",
            "question": "I forgot my password. How do I reset it?",
            "ground_truth": (
                "Click 'Forgot password?' on the login page and enter your "
                "registered email address. You will receive a password reset "
                "link within 2 minutes. The link expires after 30 minutes; "
                "if it expires, simply request a new one."
            ),
            "category": "login",
        },
        {
            "id": "q007",
            "question": "My account is locked after too many failed login attempts. What should I do?",
            "ground_truth": (
                "Accounts are locked for 15 minutes after 10 consecutive failed "
                "login attempts. After the lockout period, you can try again or "
                "use 'Forgot password?' to reset your credentials immediately. "
                "If you believe this was unauthorised activity, contact support."
            ),
            "category": "login",
        },
        {
            "id": "q008",
            "question": "Can I log in with Google or GitHub instead of a password?",
            "ground_truth": (
                "Yes, single sign-on (SSO) via Google and GitHub is supported. "
                "On the login page, click 'Continue with Google' or 'Continue "
                "with GitHub'. If your email matches an existing account, the "
                "accounts are merged automatically on first SSO login."
            ),
            "category": "login",
        },
        {
            "id": "q009",
            "question": "I'm not receiving the magic link email. How do I fix this?",
            "ground_truth": (
                "First, check your spam or junk folder. Magic link emails are "
                "sent from noreply@evalci.io — adding this address to your "
                "allowlist prevents filtering. If the email still does not "
                "arrive after 5 minutes, try requesting a new link or use a "
                "password to log in."
            ),
            "category": "login",
        },
        {
            "id": "q010",
            "question": "How do I enable two-factor authentication on my account?",
            "ground_truth": (
                "Go to Settings → Security → Two-Factor Authentication and "
                "click 'Enable 2FA'. Scan the QR code with an authenticator "
                "app such as Google Authenticator or Authy, then enter the "
                "6-digit code to confirm. Store your backup codes in a safe place."
            ),
            "category": "login",
        },

        # ── security (q011–q015) ──────────────────────────────────────────────
        {
            "id": "q011",
            "question": "I think my account has been compromised. What should I do immediately?",
            "ground_truth": (
                "Immediately reset your password using 'Forgot password?' and "
                "revoke all active sessions from Settings → Security → Active "
                "Sessions. Enable 2FA if not already on, and review the audit "
                "log for any unauthorised actions. Contact support@evalci.io "
                "to flag the incident."
            ),
            "category": "security",
        },
        {
            "id": "q012",
            "question": "Where can I see a log of all recent login activity on my account?",
            "ground_truth": (
                "The audit log is available at Settings → Security → Audit Log. "
                "It shows the timestamp, IP address, and device for every login "
                "event over the past 90 days. You can export the log as a CSV "
                "for compliance purposes."
            ),
            "category": "security",
        },
        {
            "id": "q013",
            "question": "Does EvalCI support SAML-based SSO for enterprise customers?",
            "ground_truth": (
                "Yes, SAML 2.0 SSO is available on the Enterprise plan. Once "
                "provisioned, your Identity Provider (Okta, Azure AD, etc.) "
                "manages authentication. Contact your account manager or "
                "support to begin the SAML configuration process."
            ),
            "category": "security",
        },
        {
            "id": "q014",
            "question": "How does EvalCI encrypt data at rest and in transit?",
            "ground_truth": (
                "All data in transit is encrypted with TLS 1.2 or higher. "
                "Data at rest is encrypted using AES-256 on our cloud "
                "infrastructure (AWS). Database backups are also encrypted "
                "and stored in a separate region for disaster recovery."
            ),
            "category": "security",
        },
        {
            "id": "q015",
            "question": "Can I restrict API access to specific IP addresses?",
            "ground_truth": (
                "IP allowlisting for API tokens is available on the Business "
                "and Enterprise plans. Navigate to Settings → API → API Tokens, "
                "select a token, and add CIDR ranges to its allowlist. Requests "
                "from non-allowlisted IPs will receive a 403 response."
            ),
            "category": "security",
        },

        # ── refunds (q016–q020) ───────────────────────────────────────────────
        {
            "id": "q016",
            "question": "Can I get a refund if I cancel within the first week?",
            "ground_truth": (
                "Yes. We offer a 7-day money-back guarantee on all new "
                "subscriptions. If you cancel within 7 days of your first "
                "charge and have not exceeded the free-tier usage limits, "
                "submit a refund request through Settings → Billing → Request "
                "Refund and we will process it within 5 business days."
            ),
            "category": "refunds",
        },
        {
            "id": "q017",
            "question": "I was charged for a plan upgrade I didn't authorise. Can I get a refund?",
            "ground_truth": (
                "Unauthorised charges are taken seriously. Contact "
                "support@evalci.io within 30 days of the charge with your "
                "invoice number and a brief description. We will investigate "
                "and issue a full refund if the charge was not authorised by "
                "any admin on your account."
            ),
            "category": "refunds",
        },
        {
            "id": "q018",
            "question": "How long does a refund take to appear on my bank statement?",
            "ground_truth": (
                "Refunds are processed within 3–5 business days on our side. "
                "Depending on your bank or card issuer, it may take an "
                "additional 5–10 business days for the credit to appear on "
                "your statement. You will receive an email confirmation when "
                "the refund is initiated."
            ),
            "category": "refunds",
        },
        {
            "id": "q019",
            "question": "Are annual plan payments refundable if I cancel mid-year?",
            "ground_truth": (
                "Annual plans are partially refundable. If you cancel before "
                "the renewal date, you receive a pro-rated refund for the "
                "unused full months remaining. Partial months are not refunded. "
                "Contact support to calculate the exact refund amount."
            ),
            "category": "refunds",
        },
        {
            "id": "q020",
            "question": "Can I get a refund on add-ons I purchased but never used?",
            "ground_truth": (
                "Add-on refunds are evaluated case-by-case. If the add-on was "
                "purchased within the last 14 days and has zero usage, we "
                "typically approve a full refund. Submit a request via "
                "Settings → Billing → Request Refund and include the add-on "
                "name and purchase date."
            ),
            "category": "refunds",
        },

        # ── technical (q021–q025) ─────────────────────────────────────────────
        {
            "id": "q021",
            "question": "The API is returning 429 errors. What does that mean?",
            "ground_truth": (
                "A 429 Too Many Requests response means you have exceeded your "
                "plan's API rate limit. The response includes a "
                "'Retry-After' header indicating how many seconds to wait. "
                "Consider implementing exponential back-off or upgrading your "
                "plan for higher rate limits."
            ),
            "category": "technical",
        },
        {
            "id": "q022",
            "question": "How do I set up a webhook to receive real-time evaluation events?",
            "ground_truth": (
                "Go to Settings → Integrations → Webhooks and click 'Add "
                "Webhook'. Enter your endpoint URL and select the events to "
                "subscribe to (e.g. eval.completed, eval.failed). EvalCI signs "
                "all webhook payloads with an HMAC-SHA256 signature using the "
                "secret shown on the webhook config page."
            ),
            "category": "technical",
        },
        {
            "id": "q023",
            "question": "The evaluation run is stuck in 'running' status. How do I resolve this?",
            "ground_truth": (
                "A run that stays in 'running' for more than 30 minutes likely "
                "indicates a Celery worker crash or a timeout. Re-trigger the "
                "evaluation via POST /evaluate with the same commit SHA — "
                "EvalCI will detect the stale run and start a fresh one. "
                "Check the Flower dashboard for worker health."
            ),
            "category": "technical",
        },
        {
            "id": "q024",
            "question": "How do I increase the timeout for long-running evaluation runs?",
            "ground_truth": (
                "Set the CELERY_TASK_TIME_LIMIT environment variable to the "
                "desired number of seconds before starting the worker. The "
                "default is 1800 (30 minutes). You can also set "
                "CELERY_TASK_SOFT_TIME_LIMIT to send a graceful warning signal "
                "before the hard limit kills the task."
            ),
            "category": "technical",
        },
        {
            "id": "q025",
            "question": "Can I run EvalCI against a locally hosted RAG endpoint?",
            "ground_truth": (
                "Yes. Set the RAG_ENDPOINT environment variable to your local "
                "URL (e.g. http://localhost:8000/query) before starting the "
                "API server and Celery worker. You can also override it "
                "per-request by including a 'rag_endpoint' field in the "
                "POST /evaluate request body."
            ),
            "category": "technical",
        },

        # ── pricing (q026–q030) ───────────────────────────────────────────────
        {
            "id": "q026",
            "question": "What is included in the free plan?",
            "ground_truth": (
                "The free plan includes up to 3 evaluation runs per month, "
                "a maximum of 10 test questions per run, and 7 days of result "
                "history. It supports one RAG endpoint and does not include "
                "the multi-LLM judge or regression fingerprinting features."
            ),
            "category": "pricing",
        },
        {
            "id": "q027",
            "question": "How does seat-based pricing work for teams?",
            "ground_truth": (
                "The Pro and Business plans are priced per seat (active user "
                "who can trigger evaluations or view results). Seats are billed "
                "monthly and you can add or remove seats at any time. Added "
                "seats are charged pro-rated for the remainder of the billing "
                "cycle; removed seats take effect at the next renewal."
            ),
            "category": "pricing",
        },
        {
            "id": "q028",
            "question": "Is there a discount for non-profit organisations?",
            "ground_truth": (
                "Yes, we offer a 40% discount for registered non-profit "
                "organisations. Email support@evalci.io with your non-profit "
                "registration number and the plan you are interested in. "
                "Our team will apply the discount to your account within "
                "2 business days."
            ),
            "category": "pricing",
        },
        {
            "id": "q029",
            "question": "What happens if I exceed my monthly evaluation run quota?",
            "ground_truth": (
                "On the Pro plan, additional runs beyond the monthly quota are "
                "billed at $0.10 per run. On the free plan, further runs are "
                "blocked until the next billing cycle. You will receive an "
                "email notification when you reach 80% of your quota."
            ),
            "category": "pricing",
        },
        {
            "id": "q030",
            "question": "Do you offer a free trial of the Enterprise plan?",
            "ground_truth": (
                "Yes, we offer a 14-day free trial of Enterprise features "
                "including SAML SSO, IP allowlisting, and dedicated support. "
                "No credit card is required for the trial. Contact "
                "sales@evalci.io or click 'Start Enterprise Trial' on the "
                "pricing page to get started."
            ),
            "category": "pricing",
        },

        # ── account (q031–q035) ───────────────────────────────────────────────
        {
            "id": "q031",
            "question": "How do I transfer account ownership to another user?",
            "ground_truth": (
                "Only the current account owner can transfer ownership. Go to "
                "Settings → Team → Members, find the target user, and select "
                "'Transfer Ownership' from their options menu. The recipient "
                "will receive an email to confirm acceptance before the "
                "transfer is finalised."
            ),
            "category": "account",
        },
        {
            "id": "q032",
            "question": "Can I have multiple workspaces under one account?",
            "ground_truth": (
                "Multiple workspaces are supported on the Business and "
                "Enterprise plans. Each workspace has its own members, "
                "evaluation runs, and billing. Switch between workspaces "
                "from the top-left workspace selector in the dashboard."
            ),
            "category": "account",
        },
        {
            "id": "q033",
            "question": "How do I delete my account permanently?",
            "ground_truth": (
                "Go to Settings → Account → Danger Zone and click 'Delete "
                "Account'. You will be asked to type your email address to "
                "confirm. Deletion is permanent: all runs, results, and stored "
                "data are removed within 30 days and cannot be recovered."
            ),
            "category": "account",
        },
        {
            "id": "q034",
            "question": "How do I change the email address associated with my account?",
            "ground_truth": (
                "Go to Settings → Profile → Email and enter your new address. "
                "A verification email is sent to the new address; you must "
                "click the link within 24 hours to complete the change. "
                "Your old email remains active until verification is complete."
            ),
            "category": "account",
        },
        {
            "id": "q035",
            "question": "How do I invite a team member to my workspace?",
            "ground_truth": (
                "Go to Settings → Team → Members and click 'Invite Member'. "
                "Enter the email address and select a role (Viewer, Editor, or "
                "Admin). The invitee will receive an email with a link to "
                "accept the invitation, valid for 72 hours."
            ),
            "category": "account",
        },

        # ── shipping (q036–q040) ──────────────────────────────────────────────
        {
            "id": "q036",
            "question": "How long does it take to receive the EvalCI hardware security key after purchasing?",
            "ground_truth": (
                "Hardware security keys ship within 2 business days of purchase "
                "via standard postal mail. Domestic delivery typically takes "
                "3–7 business days; international delivery takes 7–14 business "
                "days. A tracking number is emailed when the item ships."
            ),
            "category": "shipping",
        },
        {
            "id": "q037",
            "question": "Do you ship the hardware key internationally?",
            "ground_truth": (
                "Yes, we ship to over 60 countries. International orders may "
                "be subject to import duties and taxes, which are the "
                "responsibility of the recipient. Some countries with "
                "import restrictions on cryptographic hardware are excluded "
                "— see the full list at evalci.io/shipping-policy."
            ),
            "category": "shipping",
        },
        {
            "id": "q038",
            "question": "My hardware key arrived damaged. What should I do?",
            "ground_truth": (
                "Take a photo of the damaged item and packaging, then contact "
                "support@evalci.io within 14 days of delivery. Include your "
                "order number and photos. We will ship a replacement at no "
                "cost within 3 business days of confirming the damage."
            ),
            "category": "shipping",
        },
        {
            "id": "q039",
            "question": "Can I change the shipping address after placing an order?",
            "ground_truth": (
                "Shipping address changes are possible if the order has not "
                "yet been dispatched. Contact support@evalci.io with your "
                "order number and the new address as soon as possible. "
                "Once the item has shipped, the address cannot be changed."
            ),
            "category": "shipping",
        },
        {
            "id": "q040",
            "question": "How do I track my hardware key shipment?",
            "ground_truth": (
                "You will receive a shipping confirmation email with a tracking "
                "number once your order is dispatched. Use the carrier's "
                "tracking page (DHL for international, USPS for domestic) or "
                "visit the Orders section in your account dashboard."
            ),
            "category": "shipping",
        },

        # ── privacy (q041–q045) ───────────────────────────────────────────────
        {
            "id": "q041",
            "question": "Does EvalCI store the content of my RAG system's answers?",
            "ground_truth": (
                "Yes, answers and retrieved context chunks are stored in the "
                "EvalCI database so you can review them in the results "
                "dashboard and audit past runs. You can delete individual run "
                "data from the dashboard or request bulk deletion via "
                "support@evalci.io."
            ),
            "category": "privacy",
        },
        {
            "id": "q042",
            "question": "Is EvalCI GDPR compliant?",
            "ground_truth": (
                "EvalCI is GDPR compliant. We act as a data processor for "
                "personal data you submit during evaluations. Our Data "
                "Processing Agreement (DPA) is available at "
                "evalci.io/legal/dpa and can be countersigned on request. "
                "EU customer data is stored in the eu-west-1 AWS region."
            ),
            "category": "privacy",
        },
        {
            "id": "q043",
            "question": "Can I request a copy of all data EvalCI holds about me?",
            "ground_truth": (
                "Yes. Submit a Subject Access Request (SAR) by emailing "
                "privacy@evalci.io with 'SAR Request' in the subject line. "
                "We will verify your identity and deliver a structured data "
                "export within 30 days, as required by GDPR."
            ),
            "category": "privacy",
        },
        {
            "id": "q044",
            "question": "Does EvalCI use my evaluation data to train its models?",
            "ground_truth": (
                "No. EvalCI does not use your evaluation questions, answers, "
                "or documents to train or fine-tune any models. Your data is "
                "used solely to compute evaluation metrics for your runs. "
                "This commitment is documented in our Privacy Policy and DPA."
            ),
            "category": "privacy",
        },
        {
            "id": "q045",
            "question": "How long does EvalCI retain evaluation run data by default?",
            "ground_truth": (
                "Evaluation run data (scores, answers, fingerprints) is retained "
                "for 12 months by default. After 12 months, run data is "
                "automatically purged. Enterprise customers can request custom "
                "retention periods from 30 days up to 5 years."
            ),
            "category": "privacy",
        },

        # ── integrations (q046–q050) ──────────────────────────────────────────
        {
            "id": "q046",
            "question": "How do I connect EvalCI to my GitHub repository?",
            "ground_truth": (
                "Install the EvalCI GitHub App from the GitHub Marketplace and "
                "grant it access to the target repository. Then copy the "
                "EVALCI_API_URL and EVALCI_API_KEY values from Settings → "
                "Integrations → GitHub and add them as repository secrets. "
                "The provided workflow file will trigger on every push."
            ),
            "category": "integrations",
        },
        {
            "id": "q047",
            "question": "Can EvalCI post evaluation results as comments on GitHub pull requests?",
            "ground_truth": (
                "Yes. When the GitHub Actions workflow completes, EvalCI "
                "automatically posts a summary comment to the PR including "
                "the overall score, per-category results, and the regression "
                "fingerprint. The comment is updated on each new push to the "
                "same PR."
            ),
            "category": "integrations",
        },
        {
            "id": "q048",
            "question": "Does EvalCI integrate with Slack for evaluation alerts?",
            "ground_truth": (
                "Yes. Go to Settings → Integrations → Slack and click "
                "'Connect to Slack'. Authorise the EvalCI Slack app in your "
                "workspace, then choose a channel to receive notifications. "
                "Alerts are sent for completed runs and whenever a HIGH-"
                "severity regression is detected."
            ),
            "category": "integrations",
        },
        {
            "id": "q049",
            "question": "Can I send evaluation metrics to Datadog or Prometheus?",
            "ground_truth": (
                "EvalCI exposes a Prometheus-compatible metrics endpoint at "
                "GET /metrics. For Datadog, install the Datadog agent and "
                "configure it to scrape /metrics, or use our official Datadog "
                "integration available in Settings → Integrations → Datadog "
                "to push metrics automatically after each run."
            ),
            "category": "integrations",
        },
        {
            "id": "q050",
            "question": "Is there a Terraform provider or infrastructure-as-code support for EvalCI?",
            "ground_truth": (
                "A community-maintained Terraform provider for EvalCI is "
                "available at registry.terraform.io/providers/evalci/evalci. "
                "It supports managing API tokens, webhook configurations, and "
                "team memberships as code. Official IaC support is on the "
                "Enterprise roadmap."
            ),
            "category": "integrations",
        },
    ]


if __name__ == "__main__":
    import json
    questions = seed_questions()
    assert len(questions) == 50, f"Expected 50 questions, got {len(questions)}"
    categories = {q["category"] for q in questions}
    assert len(categories) == 10, f"Expected 10 categories, got {len(categories)}"
    print(json.dumps(questions, indent=2))
