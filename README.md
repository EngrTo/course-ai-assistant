# AI Assistant SaaS Platform

Multi-tenant AI chatbot platform with self-service signup, Stripe subscriptions, and plan-based limits. Clients upload documents, get a custom AI chatbot widget for their website.

**Live:** https://course-ai-assistant.onrender.com

## Features

### Plans & Limits
| Feature | Starter ($97/mo) | Professional ($197/mo) | Enterprise ($497/mo) |
|---------|:-:|:-:|:-:|
| Files | 5 | 20 | Unlimited |
| Pages | 50 | 200 | Unlimited |
| Custom Branding | ❌ | ✅ | ✅ |
| API Access | ❌ | ❌ | ✅ |
| Prorated Upgrades | ✅ | ✅ | — |

### Core Features
- **Self-service signup** — Stripe checkout → set password → dashboard
- **Unified auth** — Single login for admins and clients
- **Document upload** — PDF & TXT with page counting and limits
- **File management** — Upload, replace (same filename), delete
- **Custom branding** — Bot name, welcome message, primary color (Professional+)
- **API access** — REST API with Bearer token auth (Enterprise)
- **Embeddable widget** — `<script>` tag with branding support
- **Prorated upgrades** — Stripe handles billing automatically
- **Admin dashboard** — Client management, MRR tracking, multi-admin support
- **Forgot password** — Gmail SMTP password reset flow

## Tech Stack

- **Backend:** Python/Flask
- **Database:** PostgreSQL (Render) / SQLite (local dev)
- **AI:** Groq (llama-3.1-8b-instant) + TF-IDF retrieval
- **Payments:** Stripe (subscriptions, webhooks, proration)
- **Email:** Gmail SMTP (password resets)
- **Hosting:** Render (auto-deploy from GitHub)

## Quick Start (Local)

```bash
cd course-qa-agent
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # Fill in your keys
python app.py
```

Open http://localhost:5000

## Environment Variables

```env
GROQ_API_KEY=gsk_...
STRIPE_SECRET_KEY=sk_test_...
STRIPE_PUBLISHABLE_KEY=pk_test_...
STRIPE_WEBHOOK_SECRET=whsec_...
SECRET_KEY=your-session-secret
ADMIN_EMAIL=admin@example.com
ADMIN_PASSWORD=your-admin-password
GMAIL_EMAIL=your@gmail.com
GMAIL_APP_PASSWORD=xxxx-xxxx-xxxx-xxxx
DATABASE_URL=postgresql://...  # Omit for SQLite locally
```

Optional (auto-created if not set):
```env
STRIPE_PRICE_STARTER=price_xxx
STRIPE_PRICE_PROFESSIONAL=price_xxx
STRIPE_PRICE_ENTERPRISE=price_xxx
```

## Project Structure

```
course-qa-agent/
├── app.py              # Flask server — all routes
├── database.py         # PostgreSQL/SQLite dual-mode DB layer
├── agent.py            # RAG agent (Groq + TF-IDF)
├── ingest.py           # Document ingestion pipeline
├── requirements.txt    # Python dependencies
├── static/
│   └── widget.js       # Embeddable chat widget
├── templates/
│   ├── landing.html    # Public pricing page
│   ├── login.html      # Unified login
│   ├── dashboard.html  # Role-based dashboard
│   ├── index.html      # Chat page (with branding)
│   ├── set_password.html
│   ├── forgot_password.html
│   └── reset_password.html
└── clients/            # Per-client documents & indexes
    └── <client_id>/
        └── documents/
```

## API (Enterprise)

```bash
curl -X POST https://course-ai-assistant.onrender.com/api/v1/<client_id>/ask \
  -H "Authorization: Bearer ak_xxx" \
  -H "Content-Type: application/json" \
  -d '{"question": "How do I get started?"}'
```

Response:
```json
{"answer": "To get started...", "client_id": "your-business"}
```

## Stripe Webhooks

Endpoint: `/webhook/stripe`

Required events:
- `checkout.session.completed` — New signup or upgrade
- `customer.subscription.deleted` — Cancellation
- `invoice.payment_failed` — Mark as past due
- `customer.subscription.updated` — Plan changes

## Deployment (Render)

1. Push to GitHub (auto-deploys)
2. Set environment variables on Render
3. Configure Stripe webhook endpoint
4. Add required webhook events
