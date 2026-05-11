# TrainMyBot

Multi-tenant AI chatbot SaaS platform. Clients sign up, upload documents, and get a custom AI chatbot widget for their website — trained on their own content.

**Live:** https://course-ai-assistant.onrender.com

## Features

### Plans & Limits
| Feature | Trial (Free) | Starter ($97/mo) | Professional ($197/mo) | Enterprise ($497/mo) |
|---------|:-:|:-:|:-:|:-:|
| Files | 1 | 5 | 20 | Unlimited |
| Pages | 10 | 50 | 200 | Unlimited |
| Queries/day | 10 | Unlimited | Unlimited | Unlimited |
| Custom Branding | ❌ | ❌ | ✅ | ✅ |
| API Access | ❌ | ❌ | ❌ | ✅ |
| Duration | 7 days | — | — | — |

### Core Features
- **Free trial signup** — No credit card, 7-day trial with upgrade anytime
- **Stripe billing** — Checkout, subscriptions, prorated upgrades, cancel/resubscribe
- **Billing portal** — Stripe-hosted portal for invoices, payment method updates
- **Unified auth** — Single login for admins and clients, 24h session expiry
- **Document upload** — PDF & TXT with page counting and plan-based limits
- **File management** — Upload, replace (same filename), delete
- **Custom branding** — Bot name, welcome message, primary color (Professional+)
- **API access** — REST API with Bearer token auth (Enterprise)
- **Embeddable widget** — `<script>` tag with customizable color, title, position
- **Admin dashboard** — Client management, MRR tracking, multi-admin with permissions
- **Client reviews** — Submit & display reviews, real-time polling (30s, pauses when tab hidden)
- **Forgot password** — Gmail SMTP password reset flow
- **Mobile responsive** — All pages optimized for mobile (768px/480px breakpoints)

## Tech Stack

- **Backend:** Python/Flask
- **Database:** PostgreSQL (Render) / SQLite (local dev)
- **AI:** Groq (llama-3.1-8b-instant) + TF-IDF retrieval
- **Payments:** Stripe (subscriptions, webhooks, proration, customer portal)
- **Email:** Gmail SMTP (password resets, welcome emails)
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
│   ├── signup.html     # Free trial registration
│   ├── dashboard.html  # Role-based dashboard (admin + client)
│   ├── index.html      # Chat page (with branding)
│   ├── embed.html      # Widget embed instructions
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
- `customer.subscription.updated` — Plan changes
- `invoice.payment_failed` — Mark as past due

## Deployment (Render)

1. Push to GitHub (auto-deploys)
2. Set environment variables on Render
3. Configure Stripe webhook endpoint
4. Add required webhook events
