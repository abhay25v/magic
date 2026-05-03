#!/usr/bin/env python3
"""
Vera — Message Engine for Merchants
fastapi server implementing the 5 required endpoints + composition logic
"""

import os
import json
import asyncio
import re
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
    auto_reply_count: int = 0  # Track auto-reply detections for early exit

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
        
        # Check for STOP message - must always return action='end'
        if self._detect_stop_message(reply_body):
            return {
                "accepted": True,
                "conversation_id": conversation_id,
                "vera_response": "Got it. We'll stop reaching out.",
                "action": "end",
                "cta": "none",
                "rationale": "STOP message detected"
            }
        
        # Check for auto-reply patterns (WhatsApp Business canned responses)
        is_auto_reply = self._detect_auto_reply(reply_body, reply_from)
        
        # Handle auto-reply detection: wait on first, end after 2 detections
        if is_auto_reply and reply_from == "merchant":
            conv.auto_reply_count += 1
            if conv.auto_reply_count >= 2:
                return {
                    "accepted": True,
                    "conversation_id": conversation_id,
                    "vera_response": "Got it. We'll follow up when you're available.",
                    "action": "end",
                    "cta": "none",
                    "rationale": "Auto-reply detected twice"
                }
        
        # Store the reply
        conv.messages.append(Message(
            ts=datetime.utcnow().isoformat() + "Z",
            from_=reply_from,
            body=reply_body,
            engagement="auto_reply" if is_auto_reply else None
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
            # Choose composition strategy based on from_role (customer vs merchant)
            if reply_from == "customer":
                # Customer message - provide customer-voiced response
                response_data = self._compose_customer_reply(
                    category_ctx.payload if category_ctx else {},
                    merchant_data,
                    customer_data if customer_data else {},
                    reply_body,
                    conv.messages
                )
            elif is_auto_reply:
                # Merchant auto-reply - wait and acknowledge
                response_data = self._compose_auto_reply_handling(
                    category_ctx.payload if category_ctx else {},
                    merchant_data,
                    reply_body
                )
            else:
                # Regular merchant message - merchant-focused response
                response_data = self._compose_reply(
                    category_ctx.payload if category_ctx else {},
                    merchant_data,
                    reply_body,
                    customer_data,
                    conv.messages,
                    reply_from
                )
            
            # Ensure response has non-empty body
            vera_response = response_data.get("body", "").strip()
            if not vera_response:
                vera_response = self._get_fallback_response(reply_from, customer_data is not None)
            
            # Add Vera's response to conversation
            conv.messages.append(Message(
                ts=datetime.utcnow().isoformat() + "Z",
                from_="vera",
                body=vera_response
            ))
            
            return {
                "accepted": True,
                "conversation_id": conversation_id,
                "vera_response": vera_response,
                "action": response_data.get("action", "continue"),
                "cta": response_data.get("cta", "open_ended"),
                "rationale": response_data.get("rationale", "Contextual response"),
                "auto_reply_detected": is_auto_reply
            }
        except Exception as e:
            print(f"Error in reply composition: {e}")
            # Return fallback response to ensure no empty body
            fallback = self._get_fallback_response(reply_from, customer_data is not None)
            return {
                "accepted": True,
                "conversation_id": conversation_id,
                "vera_response": fallback,
                "action": "continue",
                "cta": "open_ended",
                "rationale": "Fallback response due to composition error"
            }
    
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
    
    def _compose_reply(self, category: Dict, merchant: Dict, user_message: str, customer: Optional[Dict], conversation_history: List[Message], reply_from: str = "merchant") -> Dict[str, Any]:
        """Compose a reply to a merchant/customer message"""
        
        prompt = self._build_reply_prompt(category, merchant, user_message, customer, conversation_history, reply_from)
        
        content = self._call_groq(prompt, max_tokens=300)
        
        try:
            json_match = re.search(r'\{[\s\S]*\}', content)
            if json_match:
                result = json.loads(json_match.group())
                # Ensure body is not empty
                if not result.get("body", "").strip():
                    result["body"] = content[:300]
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
    
    def _detect_stop_message(self, message: str) -> bool:
        """Detect STOP message"""
        stop_patterns = [
            r"^\s*stop\s*$",
            r"^\s*stop\s+messaging\s*$",
            r"^\s*no\s+further\s*$",
            r"^\s*unsubscribe\s*$"
        ]
        
        message_lower = message.strip().lower()
        for pattern in stop_patterns:
            if re.search(pattern, message_lower):
                return True
        
        return False
    
    def _detect_auto_reply(self, message: str, from_user: str) -> bool:
        """Detect WhatsApp Business auto-reply patterns"""
        if from_user != "merchant":
            return False
        
        auto_reply_patterns = [
            r"thank you for contacting",
            r"we.?ll get back to you",
            r"appreciate your message",
            r"auto.?reply",
            r"will respond soon",
            r"during business hours",
            r"out of office",
            r"auto response"
        ]
        
        message_lower = message.lower()
        for pattern in auto_reply_patterns:
            if re.search(pattern, message_lower):
                return True
        
        return False
    
    def _get_fallback_response(self, reply_from: str, has_customer: bool) -> str:
        """Get a safe fallback response"""
        if reply_from == "customer":
            return "Awesome! Let me connect you with the merchant for booking."
        return "Got it! Thanks for letting me know."
    
    def _compose_customer_reply(self, category: Dict, merchant: Dict, customer: Dict, user_message: str, conversation_history: List[Message]) -> Dict[str, Any]:
        """Compose reply when customer messages"""
        prompt = self._build_customer_reply_prompt(category, merchant, customer, user_message, conversation_history)
        content = self._call_groq(prompt, max_tokens=350)
        
        try:
            json_match = re.search(r'\{[\s\S]*\}', content)
            if json_match:
                result = json.loads(json_match.group())
                if not result.get("body", "").strip():
                    result["body"] = content[:350]
            else:
                result = {"body": content[:350], "cta": "confirm_booking"}
        except:
            result = {"body": content[:350], "cta": "confirm_booking"}
        
        return result
    
    def _compose_auto_reply_handling(self, category: Dict, merchant: Dict, auto_reply_text: str) -> Dict[str, Any]:
        """Handle merchant's auto-reply by waiting and asking for actual response"""
        prompt = self._build_auto_reply_prompt(category, merchant, auto_reply_text)
        content = self._call_groq(prompt, max_tokens=200)
        
        try:
            json_match = re.search(r'\{[\s\S]*\}', content)
            if json_match:
                result = json.loads(json_match.group())
                if not result.get("body", "").strip():
                    result["body"] = "Got your auto-reply. When you're available, let me know how I can help."
            else:
                result = {"body": content[:200], "cta": "wait"}
        except:
            result = {"body": "Got your auto-reply. When you're available, let me know how I can help.", "cta": "wait"}
        
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
        """Build the LLM prompt for message composition with STRICT voice enforcement"""
        
        merchant_name = merchant.get("identity", {}).get("name", "Merchant")
        owner_first = merchant.get("identity", {}).get("owner_first_name", "")
        category_slug = merchant.get("category_slug", "")
        
        offer_catalog = category.get("offer_catalog", [])
        voice = category.get("voice", {})
        peer_stats = category.get("peer_stats", {})
        
        # Performance context
        performance = merchant.get("performance", {})
        signals = merchant.get("signals", [])
        
        # Handle perf_dip trigger: fix delta_pct parsing
        trigger_processed = dict(trigger)
        if trigger_processed.get("trigger_type") == "perf_dip" and "payload" in trigger_processed:
            payload = trigger_processed["payload"]
            if "delta_pct" in payload:
                # Interpret as percentage: -0.50 means -50%, multiply by 100
                delta_pct = payload["delta_pct"]
                if isinstance(delta_pct, (int, float)) and -1 < delta_pct < 1:
                    trigger_processed["payload"]["delta_pct"] = delta_pct * 100
        
        # Extract voice constraints
        tone = voice.get('tone', 'professional')
        vocabulary = voice.get('vocabulary', [])
        taboos = voice.get('taboos', [])
        
        # Build tone guidance based on category
        tone_guidance = self._get_tone_guidance(category_slug, tone)
        
        prompt = f"""You are Vera, an AI assistant for {category_slug} merchants. CRITICAL: Your tone and language MUST match this category.

MERCHANT:
{merchant_name} (Owner: {owner_first})
Performance (30d): {performance.get('views', 0)} views, {performance.get('calls', 0)} calls, CTR {performance.get('ctr', 0):.3f}
Active offers: {', '.join([o.get('title', '') for o in merchant.get('offers', [])])}

CATEGORY VOICE PROFILE:
- Tone: {tone_guidance}
- Acceptable vocabulary: {', '.join(vocabulary[:5]) if vocabulary else 'professional, informative, supportive'}
- FORBIDDEN phrases: {', '.join(taboos) if taboos else 'none'}
- Available services to reference: {', '.join([o.get('title', '') for o in offer_catalog[:5]])}

PEER BENCHMARKS:
- Avg rating: {peer_stats.get('avg_rating', 0)}/5
- Avg CTR: {peer_stats.get('avg_ctr', 0):.3f}

TRIGGER:
{json.dumps(trigger_processed, indent=2)}

🔴 STRICT REQUIREMENTS:
1. Use ONLY category-appropriate tone and vocabulary
2. NEVER use formal phrases like "Dear", "Request your preparedness" or retail-promo spam language
3. Use warm, peer-to-peer tone like talking to a colleague
4. Include specific service names, prices, or dates (not generic "deals")
5. Max 320 characters
6. Be professional, helpful, and relevant to the trigger

Respond ONLY with valid JSON:
{{"body": "message (max 320 chars)", "cta": "call_to_action", "suppression_key": "key", "rationale": "why this message"}}
"""
        return prompt
    
    def _get_tone_guidance(self, category: str, tone: str) -> str:
        """Get category-specific tone guidance"""
        guidance_map = {
            "dentists": "Warm, peer-like, health-focused. Speak like a colleague. Use clinical terms naturally. Focus on patient care and health outcomes. Talk about specific services (e.g., 'Root Canal' not 'treatment'). AVOID formal phrases like 'Dear' or 'request your preparedness'.",
            "salons": "Warm, friendly, expert. Like chatting with a stylist colleague. Focus on specific services. Use accessible language. AVOID corporate-sounding pitch.",
            "restaurants": "Friendly, food-focused, inviting. Like recommending to a friend. Use dish/cuisine names. Focus on experience. AVOID robotic marketing tone.",
            "pharmacies": "Helpful, accessible, health-conscious. Speak naturally like a healthcare professional. Use clear language. Focus on wellness. AVOID formal bureaucratic tone.",
            "gyms": "Encouraging, friendly, supportive. Like talking to a fitness buddy. Focus on goals and progress. AVOID motivational speaker clichés."
        }
        return guidance_map.get(category, f"Be warm and peer-like, not corporate. Use natural language. Avoid formal, corporate tone.")
    
    def _build_reply_prompt(self, category: Dict, merchant: Dict, user_message: str, customer: Optional[Dict], conversation_history: List[Message], reply_from: str = "merchant") -> str:
        """Build the LLM prompt for merchant reply composition"""
        
        merchant_name = merchant.get("identity", {}).get("name", "Merchant")
        category_slug = merchant.get("category_slug", "")
        voice = category.get("voice", {})
        tone_guidance = self._get_tone_guidance(category_slug, voice.get('tone', 'professional'))
        
        prompt = f"""You are Vera helping {merchant_name} respond to a message in the {category_slug} category.

MERCHANT: {merchant_name}
MERCHANT'S MESSAGE: {user_message}
CATEGORY TONE: {tone_guidance}

Recent conversation:
"""
        for msg in conversation_history[-5:]:  # Last 5 messages
            prompt += f"\n- {msg.from_}: {msg.body}"
        
        prompt += f"""

Compose a warm, peer-like response that:
1. Acknowledges their message naturally
2. Is helpful and actionable
3. Max 300 characters
4. Sounds like a colleague, not a corporate bot (NO formal phrases like 'Dear' or 'Reply YES to confirm')
5. Offers clear next steps

Respond ONLY with valid JSON:
{{"body": "your response (max 300 chars)", "cta": "continue", "rationale": "why this response"}}
"""
        return prompt
    
    def _build_customer_reply_prompt(self, category: Dict, merchant: Dict, customer: Dict, user_message: str, conversation_history: List[Message]) -> str:
        """Build the LLM prompt for customer reply composition - warm, peer-like, customer-voiced"""
        
        merchant_name = merchant.get("identity", {}).get("name", "Merchant")
        category_slug = merchant.get("category_slug", "")
        customer_name = customer.get("identity", {}).get("name", "Customer")
        voice = category.get("voice", {})
        tone_guidance = self._get_tone_guidance(category_slug, voice.get('tone', 'professional'))
        
        # Build customer-specific tone
        customer_tone = f"{tone_guidance}\nSPECIFIC FOR CUSTOMERS: Be warm, encouraging, and helpful. Speak directly to the customer. Sound like a friend helping them book."
        
        prompt = f"""You are Vera helping {customer_name} engage with {merchant_name} ({category_slug}).

CUSTOMER MESSAGE: {user_message}
MERCHANT: {merchant_name}
CATEGORY: {category_slug}
TONE GUIDANCE: {customer_tone}

Recent conversation:
"""
        for msg in conversation_history[-5:]:
            prompt += f"\n- {msg.from_}: {msg.body}"
        
        prompt += f"""

Compose a warm, customer-voiced response that:
1. Confirms or clarifies what the customer wants
2. Provides specific next steps (time, service name, price)
3. Sounds warm and encouraging, like a friend helping them
4. Max 350 characters
5. Clear path to booking confirmation

Respond ONLY with valid JSON:
{{"body": "your response (max 350 chars)", "action": "continue", "cta": "confirm_booking", "rationale": "booking flow"}}
"""
        return prompt
    
    def _build_auto_reply_prompt(self, category: Dict, merchant: Dict, auto_reply_text: str) -> str:
        """Build prompt for handling merchant auto-reply"""
        
        merchant_name = merchant.get("identity", {}).get("name", "Merchant")
        category_slug = merchant.get("category_slug", "")
        
        prompt = f"""You detected a WhatsApp Business auto-reply from {merchant_name}.

Auto-reply text: {auto_reply_text}
Merchant: {merchant_name}
Category: {category_slug}

Compose a brief acknowledgment that:
1. Recognizes this is an auto-reply
2. Doesn't repeat the previous ask
3. Suggests follow-up timing
4. Max 200 characters
5. Professional tone

Respond ONLY with valid JSON:
{{"body": "brief acknowledgment", "cta": "wait", "rationale": "auto-reply handling"}}
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
