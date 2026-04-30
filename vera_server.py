#!/usr/bin/env python3
"""
Vera — Message Engine for Merchants
fastapi server implementing the 5 required endpoints + composition logic
"""

import os
import json
import asyncio
from datetime import datetime
from typing import Optional, Dict, List, Any
from dataclasses import dataclass, field, asdict
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
import uvicorn
from dotenv import load_dotenv
from groq import Groq

load_dotenv()

# =============================================================================
# DATA MODELS
# =============================================================================

@dataclass
class ContextState:
    """Stores versioned context for each scope"""
    version: int
    payload: Dict[str, Any]
    delivered_at: str
    
@dataclass
class Message:
    """A single message in conversation"""
    ts: str
    from_: str  # "vera", "merchant", "customer"
    body: str
    engagement: Optional[str] = None
    
@dataclass
class ConversationState:
    """Tracks in-flight conversations"""
    conversation_id: str
    merchant_id: str
    customer_id: Optional[str]
    messages: List[Message] = field(default_factory=list)
    trigger_id: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")

# =============================================================================
# VERA SERVER
# =============================================================================

class VeraServer:
    def __init__(self):
        self.app = FastAPI()
        self.start_time = datetime.utcnow()
        
        # Context storage: scope -> context_id -> ContextState
        self.contexts: Dict[str, Dict[str, ContextState]] = {
            "category": {},
            "merchant": {},
            "customer": {},
            "trigger": {}
        }
        
        # Conversation storage: conversation_id -> ConversationState
        self.conversations: Dict[str, ConversationState] = {}
        
        # Last seen version for each context_id (for idempotency)
        self.context_versions: Dict[str, int] = {}
        
        # LLM client
        self.llm_api_key = os.environ.get("GROQ_API_KEY")
        self.llm_model = os.environ.get("GROQ_MODEL", "llama-3.1-8b-instant")
        self.client = Groq(api_key=self.llm_api_key) if self.llm_api_key else None
        
        # Setup routes
        self._setup_routes()
        
    def _setup_routes(self):
        """Register all API endpoints"""
        self.app.get("/v1/healthz")(self.healthz)
        self.app.get("/v1/metadata")(self.metadata)
        self.app.post("/v1/context")(self.context)
        self.app.post("/v1/tick")(self.tick)
        self.app.post("/v1/reply")(self.reply)
    
    # =========================================================================
    # ENDPOINT: GET /v1/healthz
    # =========================================================================
    async def healthz(self):
        """Health check endpoint"""
        uptime = (datetime.utcnow() - self.start_time).total_seconds()
        return {
            "status": "ok",
            "uptime_seconds": int(uptime),
            "contexts_loaded": {
                "category": len(self.contexts["category"]),
                "merchant": len(self.contexts["merchant"]),
                "customer": len(self.contexts["customer"]),
                "trigger": len(self.contexts["trigger"])
            }
        }
    
    # =========================================================================
    # ENDPOINT: GET /v1/metadata
    # =========================================================================
    async def metadata(self):
        """Return bot metadata"""
        return {
            "team_name": "Vera Implementation",
            "team_members": ["AI Assistant"],
            "model": self.llm_model,
            "approach": "4-context framework with Groq-powered message composition and stateful conversation management",
            "contact_email": "vera@magicpin.io",
            "version": "1.0.0",
            "submitted_at": "2026-04-26T09:00:00Z"
        }
    
    # =========================================================================
    # ENDPOINT: POST /v1/context
    # =========================================================================
    async def context(self, request: Request):
        """Store context updates (category, merchant, customer, trigger)"""
        try:
            body = await request.json()
        except:
            return JSONResponse(
                status_code=400,
                content={"accepted": False, "reason": "invalid_json", "details": "Could not parse JSON"}
            )
        
        # Validate required fields
        required = ["scope", "context_id", "version", "payload"]
        if not all(k in body for k in required):
            return JSONResponse(
                status_code=400,
                content={"accepted": False, "reason": "missing_fields", "details": f"Missing one of {required}"}
            )
        
        scope = body["scope"]
        context_id = body["context_id"]
        version = body["version"]
        payload = body["payload"]
        delivered_at = body.get("delivered_at", datetime.utcnow().isoformat() + "Z")
        
        # Validate scope
        if scope not in ["category", "merchant", "customer", "trigger"]:
            return JSONResponse(
                status_code=400,
                content={"accepted": False, "reason": "invalid_scope", "details": f"Unknown scope: {scope}"}
            )
        
        # Check for version conflict
        context_key = f"{scope}:{context_id}"
        if context_key in self.context_versions and self.context_versions[context_key] >= version:
            return JSONResponse(
                status_code=409,
                content={
                    "accepted": False,
                    "reason": "stale_version",
                    "current_version": self.context_versions[context_key]
                }
            )
        
        # Store context
        self.contexts[scope][context_id] = ContextState(
            version=version,
            payload=payload,
            delivered_at=delivered_at
        )
        self.context_versions[context_key] = version
        
        return {
            "accepted": True,
            "ack_id": f"ack_{context_id}_v{version}",
            "stored_at": datetime.utcnow().isoformat() + "Z"
        }
    
    # =========================================================================
    # ENDPOINT: POST /v1/tick
    # =========================================================================
    async def tick(self, request: Request):
        """Periodic wake-up: bot can initiate proactive messages"""
        try:
            body = await request.json()
        except:
            return JSONResponse(
                status_code=400,
                content={"accepted": False, "reason": "invalid_json"}
            )
        
        now = body.get("now", datetime.utcnow().isoformat() + "Z")
        available_triggers = body.get("available_triggers", [])
        
        actions = []
        
        # For each merchant, decide whether to send a proactive message
        for merchant_id, merchant_ctx in self.contexts["merchant"].items():
            # Get the merchant's category
            merchant_data = merchant_ctx.payload
            category_slug = merchant_data.get("category_slug")
            
            # Get category context
            if category_slug not in self.contexts["category"]:
                continue
            
            category_ctx = self.contexts["category"][category_slug]
            
            # Pick a trigger if available
            trigger_id = None
            if available_triggers:
                # Use the first available trigger
                trigger_id = available_triggers[0]
                if trigger_id not in self.contexts["trigger"]:
                    trigger_id = None
            
            if not trigger_id:
                continue
            
            trigger_ctx = self.contexts["trigger"][trigger_id]
            
            # Compose message
            try:
                message_data = self._compose_message(
                    category_ctx.payload,
                    merchant_data,
                    trigger_ctx.payload,
                    None  # No customer context
                )
                
                # Create conversation
                conv_id = f"conv_{merchant_id}_{int(datetime.utcnow().timestamp())}"
                action = {
                    "conversation_id": conv_id,
                    "merchant_id": merchant_id,
                    "customer_id": None,
                    "send_as": "vera",
                    "trigger_id": trigger_id,
                    "template_name": "vera_composed_v1",
                    "template_params": [],
                    "body": message_data["body"],
                    "cta": message_data.get("cta", "open_ended"),
                    "suppression_key": message_data.get("suppression_key", f"vera:{merchant_id}:{now}"),
                    "rationale": message_data.get("rationale", "Context-aware merchant engagement")
                }
                
                actions.append(action)
                
                # Store conversation
                self.conversations[conv_id] = ConversationState(
                    conversation_id=conv_id,
                    merchant_id=merchant_id,
                    customer_id=None,
                    trigger_id=trigger_id
                )
                
            except Exception as e:
                print(f"Error composing message for {merchant_id}: {e}")
                continue
        
        return {"actions": actions}
    
    # =========================================================================
    # ENDPOINT: POST /v1/reply
    # =========================================================================
    async def reply(self, request: Request):
        """Handle merchant/customer reply"""
        try:
            body = await request.json()
        except:
            return JSONResponse(
                status_code=400,
                content={"accepted": False, "reason": "invalid_json"}
            )
        
        conversation_id = body.get("conversation_id")
        reply_from = body.get("from")  # "merchant" or "customer"
        reply_body = body.get("body")
        
        if not conversation_id or conversation_id not in self.conversations:
            return JSONResponse(
                status_code=404,
                content={"accepted": False, "reason": "conversation_not_found"}
            )
        
        conv = self.conversations[conversation_id]
        
        # Store the reply
        conv.messages.append(Message(
            ts=datetime.utcnow().isoformat() + "Z",
            from_=reply_from,
            body=reply_body
        ))
        
        # Get contexts
        merchant_ctx = self.contexts["merchant"].get(conv.merchant_id)
        if not merchant_ctx:
            return JSONResponse(
                status_code=404,
                content={"accepted": False, "reason": "merchant_context_not_found"}
            )
        
        merchant_data = merchant_ctx.payload
        category_slug = merchant_data.get("category_slug")
        category_ctx = self.contexts["category"].get(category_slug)
        
        # Compose a response
        customer_data = None
        if conv.customer_id and conv.customer_id in self.contexts["customer"]:
            customer_data = self.contexts["customer"][conv.customer_id].payload
        
        try:
            response_data = self._compose_reply(
                category_ctx.payload if category_ctx else {},
                merchant_data,
                reply_body,
                customer_data,
                conv.messages
            )
            
            # Add Vera's response to conversation
            conv.messages.append(Message(
                ts=datetime.utcnow().isoformat() + "Z",
                from_="vera",
                body=response_data["body"]
            ))
            
            return {
                "accepted": True,
                "conversation_id": conversation_id,
                "vera_response": response_data["body"],
                "cta": response_data.get("cta", "open_ended"),
                "rationale": response_data.get("rationale", "Contextual response")
            }
        except Exception as e:
            return JSONResponse(
                status_code=500,
                content={"accepted": False, "reason": "composition_error", "details": str(e)}
            )
    
    # =========================================================================
    # MESSAGE COMPOSITION LOGIC
    # =========================================================================
    
    def _compose_message(self, category: Dict, merchant: Dict, trigger: Dict, customer: Optional[Dict]) -> Dict[str, Any]:
        """Compose a message using the 4-context framework with LLM"""
        
        # Build the composition prompt
        prompt = self._build_composition_prompt(category, merchant, trigger, customer)
        
        # Call LLM
        content = self._call_groq(prompt, max_tokens=500)
        
        # Extract JSON from response
        try:
            # Look for JSON in the response
            import re
            json_match = re.search(r'\{[\s\S]*\}', content)
            if json_match:
                result = json.loads(json_match.group())
            else:
                result = {
                    "body": content[:320],
                    "cta": "open_ended",
                    "rationale": "Generated message"
                }
        except:
            result = {
                "body": content[:320],
                "cta": "open_ended",
                "rationale": "Generated message"
            }
        
        return result
    
    def _compose_reply(self, category: Dict, merchant: Dict, user_message: str, customer: Optional[Dict], conversation_history: List[Message]) -> Dict[str, Any]:
        """Compose a reply to a merchant/customer message"""
        
        prompt = self._build_reply_prompt(category, merchant, user_message, customer, conversation_history)
        
        content = self._call_groq(prompt, max_tokens=300)
        
        try:
            import re
            json_match = re.search(r'\{[\s\S]*\}', content)
            if json_match:
                result = json.loads(json_match.group())
            else:
                result = {
                    "body": content[:300],
                    "cta": "open_ended"
                }
        except:
            result = {
                "body": content[:300],
                "cta": "open_ended"
            }
        
        return result

    def _call_groq(self, prompt: str, max_tokens: int = 500) -> str:
        """Call Groq using the official SDK."""

        if not self.client:
            raise RuntimeError("GROQ_API_KEY not configured")

        completion = self.client.chat.completions.create(
            model=self.llm_model,
            messages=[
                {
                    "role": "user",
                    "content": prompt,
                }
            ],
            temperature=0.2,
            max_tokens=max_tokens,
        )

        return completion.choices[0].message.content
    
    def _build_composition_prompt(self, category: Dict, merchant: Dict, trigger: Dict, customer: Optional[Dict]) -> str:
        """Build the LLM prompt for message composition"""
        
        merchant_name = merchant.get("identity", {}).get("name", "Merchant")
        owner_first = merchant.get("identity", {}).get("owner_first_name", "")
        category_slug = merchant.get("category_slug", "")
        
        offer_catalog = category.get("offer_catalog", [])
        voice = category.get("voice", {})
        peer_stats = category.get("peer_stats", {})
        
        # Performance context
        performance = merchant.get("performance", {})
        signals = merchant.get("signals", [])
        
        prompt = f"""You are Vera, an AI assistant for merchants. Compose a message for {merchant_name} ({owner_first}).

MERCHANT CONTEXT:
- Category: {category_slug}
- Performance (30d): {performance.get('views', 0)} views, {performance.get('calls', 0)} calls, CTR {performance.get('ctr', 0):.3f}
- Signals: {', '.join(signals) if signals else 'none'}
- Active offers: {', '.join([o.get('title', '') for o in merchant.get('offers', [])])}

CATEGORY VOICE:
- Tone: {voice.get('tone', 'professional')}
- Available offers: {', '.join([o.get('title', '') for o in offer_catalog[:3]])}
- Peer stats: avg rating {peer_stats.get('avg_rating', 0)}, avg CTR {peer_stats.get('avg_ctr', 0)}

TRIGGER:
{json.dumps(trigger, indent=2)}

REQUIREMENTS:
1. Message must be specific (include numbers, dates, or actual offers)
2. Max 320 characters
3. Fit the merchant's context and performance
4. Be actionable and compelling

Respond ONLY with valid JSON:
{{"body": "your message here", "cta": "call_to_action", "suppression_key": "key", "rationale": "why this message"}}
"""
        return prompt
    
    def _build_reply_prompt(self, category: Dict, merchant: Dict, user_message: str, customer: Optional[Dict], conversation_history: List[Message]) -> str:
        """Build the LLM prompt for reply composition"""
        
        merchant_name = merchant.get("identity", {}).get("name", "Merchant")
        category_slug = merchant.get("category_slug", "")
        
        prompt = f"""You are Vera helping {merchant_name} in the {category_slug} category.

MERCHANT: {merchant_name}
USER MESSAGE: {user_message}

Conversation history:
"""
        for msg in conversation_history[-5:]:  # Last 5 messages
            prompt += f"\n- {msg.from_}: {msg.body}"
        
        prompt += f"""

Compose a contextual, helpful response that:
1. Acknowledges their message
2. Is specific and actionable
3. Max 300 characters
4. Maintains merchant's brand voice

Respond ONLY with valid JSON:
{{"body": "your response here", "cta": "next_action"}}
"""
        return prompt


# =============================================================================
# MAIN
# =============================================================================

def main():
    server = VeraServer()
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run(server.app, host="0.0.0.0", port=port)

if __name__ == "__main__":
    main()
