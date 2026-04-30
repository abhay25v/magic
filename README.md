# Vera — AI Message Engine for Merchants

> Build context-aware, high-quality messages for merchants using four input layers: Category, Merchant, Trigger, Customer.

This is a complete implementation of the **magicpin AI Challenge** — a production-ready system that:

✅ Composes specific, timely messages for merchants (restaurants, dentists, salons, gyms, pharmacies)
✅ Uses a 4-context framework for rich message personalization
✅ Implements 5 required HTTP endpoints as specified
✅ Maintains stateful conversations with idempotent context storage
✅ Uses Groq-powered LLMs for intelligent message generation
✅ Deploys to public URLs (Render, Railway, Docker, Heroku)

---

## The Challenge: Build Vera

magicpin's Vera is an AI assistant that engages merchants on WhatsApp. The challenge is to build a system that:

1. **Receives context** via 4 layers (category, merchant, trigger, customer)
2. **Composes messages** that are:
   - Specific (real numbers, dates, actual offers)
   - Context-aware (fits merchant type, performance, history)
   - Timely (responds to triggers)
3. **Manages stateful conversations** (remembers history, idempotent updates)
4. **Exposes 5 HTTP endpoints** for integration

---

## Solution Overview

### System Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                      Judge Harness (Test System)                 │
│                                                                   │
│  Sends:  category, merchant, trigger, customer contexts         │
│  Expects: stateful messages, proactive engagement, replies       │
└──────────────────────────────────────────────────────────────────┘
                    │
                    │ HTTP/JSON
                    ▼
┌──────────────────────────────────────────────────────────────────┐
│                    Vera Server (Your Bot)                         │
│                                                                   │
│  POST /v1/context   ──► Store versioned contexts (idempotent)   │
│  POST /v1/tick      ──► Compose & initiate messages             │
│  POST /v1/reply     ──► Generate replies to merchant responses  │
│  GET /v1/healthz    ──► Health check (uptime, contexts loaded)  │
│  GET /v1/metadata   ──► Bot info (model, approach, version)     │
│                                                                   │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │ In-Memory Storage:                                       │   │
│  │  • contexts[scope][context_id] → {version, payload}     │   │
│  │  • conversations[conv_id] → {messages, merchant_id, ...} │   │
│  └──────────────────────────────────────────────────────────┘   │
│                                                                   │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │ LLM Composition (Groq SDK):                              │   │
│  │  • Build prompt with 4 contexts                          │   │
│  │  • Generate message (specific, contextual)              │   │
│  │  • Extract JSON: {body, cta, rationale}                 │   │
│  └──────────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────────┘
```

### The 4-Context Framework

Every message = `compose(category, merchant, trigger, customer?)`

| Context | Purpose | Refresh | Example |
|---------|---------|---------|---------|
| **Category** | How to talk to this vertical | Weekly | Dentist voice: clinical, no hype; offers: "Cleaning @ ₹299" |
| **Merchant** | This specific business state | Daily | Dr. Meera: 2,410 views, CTR 0.021, high risk-adult cohort |
| **Trigger** | Why message now | Per-event | Research digest released, recall due, perf spike |
| **Customer** | (Optional) Direct outreach context | Per-visit | Priya: lapsed, had 4 cleanings, prefers evening slots |

---

## 5 Required Endpoints

### 1. `GET /v1/healthz` — Health Check
```bash
curl https://your-bot.onrender.com/v1/healthz
```
Response:
```json
{
  "status": "ok",
  "uptime_seconds": 2481,
  "contexts_loaded": {
    "category": 5,
    "merchant": 50,
    "customer": 200,
    "trigger": 12
  }
}
```

### 2. `GET /v1/metadata` — Bot Metadata
```bash
curl https://your-bot.onrender.com/v1/metadata
```
Response:
```json
{
  "team_name": "Vera Implementation",
  "model": "llama-3.1-8b-instant",
  "approach": "4-context framework with LLM composition",
  "version": "1.0.0"
}
```

### 3. `POST /v1/context` — Receive Context Updates
```bash
curl -X POST https://your-bot.onrender.com/v1/context \
  -d '{
    "scope": "merchant",
    "context_id": "m_001_drmeera",
    "version": 1,
    "payload": { ... }
  }'
```

**Features**:
- Idempotent by `(context_id, version)`
- Returns 409 if version already exists
- Returns 200 if version is new or higher
- Atomically replaces lower versions

### 4. `POST /v1/tick` — Initiate Proactive Messages
```bash
curl -X POST https://your-bot.onrender.com/v1/tick \
  -d '{
    "now": "2026-04-26T10:30:00Z",
    "available_triggers": ["trg_digest", "trg_perf_spike"]
  }'
```

Response: Array of actions with composed messages
```json
{
  "actions": [
    {
      "conversation_id": "conv_001",
      "merchant_id": "m_001_drmeera",
      "body": "Dr. Meera, JIDA Oct issue has 3-month fluoride recall data...",
      "cta": "learn_more",
      "rationale": "Research digest trigger"
    }
  ]
}
```

### 5. `POST /v1/reply` — Handle Replies
```bash
curl -X POST https://your-bot.onrender.com/v1/reply \
  -d '{
    "conversation_id": "conv_001",
    "from": "merchant",
    "body": "Yes, tell me more about the recall"
  }'
```

Response:
```json
{
  "accepted": true,
  "vera_response": "Fluoride varnish at 3-month intervals cuts..."
}
```

---

## Quick Start

### 1. Local Development (2 minutes)

```bash
# Clone or download workspace
cd magicpin-ai-challenge

# Install dependencies
pip install -r requirements.txt

# Create .env
cp .env.example .env
# Edit .env and add your API key from https://console.groq.com/keys

# Run server
python vera_server.py

# Test endpoints
curl http://localhost:8000/v1/healthz
```

### 2. Deploy to Render Free Tier (5 minutes) — **Recommended**

1. Push code to GitHub
2. Go to https://dashboard.render.com
3. Click "New +" → "Web Service" → Connect GitHub repo
4. Build command: `pip install -r requirements.txt`
5. Start command: `python vera_server.py`
6. Add environment variable: `GROQ_API_KEY` = your key
7. Optional: `GROQ_MODEL` = `llama-3.1-8b-instant`
8. Deploy!
9. Get public URL: `https://vera-XXXXX.onrender.com`

### 3. Deploy to Railway (3 minutes)

```bash
# Install Railway CLI
npm i -g @railway/cli

# Login
railway login

# Initialize project
railway init

# Add API key
railway variables set GROQ_API_KEY=gsk_...
railway variables set GROQ_MODEL=llama-3.1-8b-instant

# Deploy
git push
```

Railway auto-detects Python and deploys. Your URL is auto-generated.

### 4. Docker (Build anywhere)

```bash
docker build -t vera .
docker run -e GROQ_API_KEY="gsk_..." -e GROQ_MODEL="llama-3.1-8b-instant" -p 8000:8000 vera
```

---

## Key Features

### 1. Idempotent Context Storage
- Incoming `POST /v1/context` by `(context_id, version)` are deduplicated
- Same version twice → 409 Conflict
- Higher version → replaces lower version atomically
- Prevents duplicate message compositions

### 2. Stateful Conversations
- Each conversation tracked with full message history
- Memories across multiple exchanges
- Supports merchant-to-bot and customer-to-bot interactions

### 3. 4-Context Composition
```python
# Vera receives all 4 contexts
message = compose(
    category=CategoryContext(
        slug="dentists",
        offer_catalog=[...],
        voice={...},
        peer_stats={...}
    ),
    merchant=MerchantContext(
        identity={...},
        performance={...},
        signals=[...]
    ),
    trigger=TriggerContext(
        id="trg_digest",
        kind="research",
        payload={...}
    ),
    customer=CustomerContext(...)  # optional
)

# Returns: {body, cta, rationale, suppression_key}
```

### 4. Specific Message Content
- **Numbers**: Real CTR, views, calls (from merchant performance)
- **Offers**: Actual offer catalog entries (not generic)
- **Dates**: Timestamps, timeframes (from trigger context)
- **Names**: Merchant name, owner first name (from identity)

Example:
> "Dr. Meera, JIDA's Oct issue has **3-month fluoride recall** data — **38% better caries outcomes**. Relevant for your **124 high-risk adults**. Details: […]"

### 5. LLM-Powered (Groq SDK)
- Flexible composition (not templated)
- Context-aware tone and vocabulary
- Generates structured JSON (body, CTA, rationale)
- Falls back gracefully on LLM failures

---

## Files Included

```
vera_server.py              ← Main FastAPI server (all 5 endpoints + logic)
requirements.txt            ← Python dependencies
Dockerfile                  ← Docker container config
render.yaml                 ← Render deployment config
railway.toml                ← Railway deployment config
heroku.json                 ← Heroku deployment config
app.py                      ← Cloud entry point
.env.example                ← API key template
SUBMISSION.md               ← Submission checklist
DEPLOYMENT.md               ← Detailed deployment guide
README.md                   ← This file
quickstart.py               ← Local development helper
test_integration.py         ← Integration test suite
judge_simulator.py          ← Official test harness (provided)
dataset/                    ← Sample data
├── merchants_seed.json     ← 10 merchants (expanded to 50)
├── customers_seed.json     ← 15 customers (expanded to 200)
├── triggers_seed.json      ← Trigger types
└── categories/
    ├── dentists.json       ← Category context
    ├── salons.json
    ├── restaurants.json
    ├── gyms.json
    └── pharmacies.json
examples/                   ← API examples
├── api-call-examples.md
└── case-studies.md
```

---

## Testing

### Local Integration Tests
```bash
# Start server
python vera_server.py

# In another terminal
python test_integration.py
```

Tests:
- ✅ `healthz` endpoint
- ✅ `metadata` endpoint
- ✅ `context` idempotency
- ✅ `tick` message generation
- ✅ `reply` handling

### Official Judge Simulator
```bash
# Edit configuration in judge_simulator.py
BOT_URL = "https://your-bot.onrender.com"
LLM_API_KEY = "your-api-key"

# Run tests
python judge_simulator.py
```

Scenarios:
- ✅ Warmup (context push, health checks)
- ✅ Phase 2 (merchant engagement)
- ✅ Auto-reply detection (WhatsApp canned responses)
- ✅ Intent transitions (merchant state changes)
- ✅ Hostile input (edge cases, malformed)

---

## Evaluation Criteria

The judge scores on:

1. **Specificity** — Does the message include real numbers, dates, offers?
   - ❌ Bad: "Get a discount on your offer"
   - ✅ Good: "Your 2,410 views are +18% vs last week; try our 'Cleaning @ ₹299' offer"

2. **Contextuality** — Does the message fit this merchant and category?
   - ❌ Bad: Generic template
   - ✅ Good: "Dr. Meera, your high-risk adult patients (124) benefit from 3-month recall — JIDA reports 38% better outcomes"

3. **Timing** — Does the message respond appropriately to the trigger?
   - ❌ Bad: Ignore the trigger
   - ✅ Good: Research digest trigger → share relevant clinical data

4. **Diversity** — Is there variety in conversation types (not just reminders)?
   - ✅ Research digest, performance insights, customer lapse alerts, appointment reminders, offer promotions

5. **Engagement** — Will the merchant actually reply/engage?
   - ✅ Curiosity-driven: "JIDA's new research..."
   - ✅ Performance-driven: "Your CTR is down 5%..."
   - ✅ Action-driven: "Merchant wants to join a campaign..."

---

## Deployment Checklist

- [ ] API key obtained from https://console.groq.com/keys
- [ ] Server runs locally: `python vera_server.py`
- [ ] Endpoints respond: `curl http://localhost:8000/v1/healthz`
- [ ] Deployed to public URL (Render recommended)
- [ ] All 5 endpoints live and responding
- [ ] Context storage working (version idempotency)
- [ ] Messages are specific (numbers, offers, dates)
- [ ] Judge simulator passes all scenarios
- [ ] Ready to submit public URL

---

## Submission Format

When ready, submit:

```
PUBLIC URL: https://vera-XXXXX.onrender.com

STATUS:
✅ All 5 endpoints live
✅ Stateful conversation management
✅ 4-context composition
✅ Specific message content (numbers, offers, dates)
✅ LLM-powered (Groq SDK)
✅ Judge simulator passing all scenarios

TEAM: [Your Name]
EMAIL: [Your Email]
```

---

## FAQ

**Q: Can I use a different LLM?**
A: Yes! Edit `vera_server.py` to use GPT-4, Gemini, etc. Just update the `_compose_message` method.

**Q: How do I store conversations permanently?**
A: Add a database (PostgreSQL, MongoDB, etc.). Currently, conversations are in-memory (fine for testing).

**Q: What if the LLM API fails?**
A: The `try/except` blocks catch errors and return fallback messages. You can enhance with retries or circuit breakers.

**Q: Can I modify the judge simulator?**
A: No — the judge is provided. Your bot must comply with the 5 endpoints exactly.

**Q: How long should messages be?**
A: Max 320 characters (fits WhatsApp message body).

**Q: What if there are no available triggers?**
A: Return `{"actions": []}` — empty list of actions. That's valid.

---

## Performance Notes

- **Context storage**: O(1) lookup by context_id
- **Message composition**: 1-2 seconds per message (LLM latency)
- **Idempotency**: O(1) version check
- **Conversation history**: Linear search (fine for <100 messages)

For production, add:
- PostgreSQL for persistence
- Redis for caching
- Async message queues (Celery, Bull)
- Rate limiting
- Request logging

---

## Support Resources

- **Challenge brief**: Read [challenge-brief.md](challenge-brief.md)
- **Testing contract**: Read [challenge-testing-brief.md](challenge-testing-brief.md)
- **API examples**: See [examples/api-call-examples.md](examples/api-call-examples.md)
- **Vera design**: Read [engagement-design.md](engagement-design.md)
- **Dataset**: Explore [dataset/](dataset/) folder

---

## Next Steps

1. **Get API key** from https://console.groq.com/keys
2. **Run locally** to test
3. **Deploy to Render** (5 minutes)
4. **Run judge simulator** to validate
5. **Submit public URL**

Good luck! 🚀

---

*Built for the magicpin AI Challenge — Apr 2026*
