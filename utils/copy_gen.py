import anthropic
import openai
from google import genai as google_genai
from mistralai.client import Mistral
from groq import Groq


def _sanitise(text: str, brand_name: str = "") -> str:
    """
    Post-process AI output to enforce hard formatting rules
    regardless of what the model returns.
    - Replaces em dash with comma+space
    - Replaces en dash separator with pipe
    - Strips surrounding quotes the model sometimes adds
    - Restores exact brand name casing if model changed it
    """
    if not text:
        return text
    import re
    text = re.sub(r'\s*—\s*', ', ', text)
    text = re.sub(r' – ', ' | ', text)
    text = text.strip('"').strip("'")
    if brand_name and brand_name.strip():
        text = re.sub(
            re.escape(brand_name.strip()),
            brand_name.strip(),
            text,
            flags=re.IGNORECASE
        )
    return text.strip()


# Business type guidance injected into prompts
BUSINESS_TYPE_CONTEXT = {
    "b2b": {
        "buyer": "procurement managers, R&D teams, and business buyers",
        "intent": "evaluating suppliers, sourcing products, or requesting quotes",
        "tone": "professional, specific, and credibility-focused",
        "cta_examples": "Request a sample, Get specifications, Contact our team, Request a quote",
        "avoid": "consumer-facing CTAs like Shop Now or Buy Today",
        "title_pattern": "lead with the product/service capability, end with brand",
        "desc_pattern": "lead with what you supply or manufacture, include a B2B-specific CTA"
    },
    "b2c": {
        "buyer": "individual consumers",
        "intent": "researching, comparing, or ready to purchase",
        "tone": "direct, benefit-driven, and engaging",
        "cta_examples": "Shop now, Explore the range, Find yours, Order today",
        "avoid": "jargon, overly technical language",
        "title_pattern": "lead with the product benefit or keyword, end with brand",
        "desc_pattern": "highlight the key benefit, create urgency or desire, end with CTA"
    },
    "ecommerce": {
        "buyer": "online shoppers",
        "intent": "browsing products, comparing options, ready to buy",
        "tone": "punchy, benefit-focused, conversion-oriented",
        "cta_examples": "Shop now, Browse the collection, Order today, Free shipping",
        "avoid": "vague descriptions with no product specifics",
        "title_pattern": "product name or category first, include a differentiator if space allows, end with brand",
        "desc_pattern": "lead with the product, include a key benefit, end with action-oriented CTA"
    },
    "service": {
        "buyer": "people looking for professional help or a solution to a problem",
        "intent": "evaluating providers, understanding what a service includes, or ready to contact",
        "tone": "confident, outcome-focused, and trustworthy",
        "cta_examples": "Get a free quote, Book a consultation, Talk to an expert, Get started",
        "avoid": "vague fluff like world-class or industry-leading",
        "title_pattern": "lead with the service outcome or what you do, include location if local, end with brand",
        "desc_pattern": "state what you do and who you help, include a benefit, end with a direct CTA"
    },
    "local": {
        "buyer": "local searchers with high purchase or visit intent",
        "intent": "finding a nearby provider, checking hours or location, ready to call or visit",
        "tone": "direct, local, and action-oriented",
        "cta_examples": "Call us today, Visit our showroom, Get directions, Book an appointment",
        "avoid": "national/generic copy with no local relevance",
        "title_pattern": "service + location + brand",
        "desc_pattern": "mention the city or area, include what you offer, end with a local-specific CTA"
    },
    "general": {
        "buyer": "general web visitors",
        "intent": "informational or navigational",
        "tone": "clear and informative",
        "cta_examples": "Learn more, Explore, Find out more",
        "avoid": "overly salesy language for informational pages",
        "title_pattern": "topic first, brand last",
        "desc_pattern": "summarize what the page covers clearly, include a soft CTA"
    }
}


TITLE_PROMPT = """You are a senior SEO copywriter with deep knowledge of how different business types require different copy strategies.

Write a title tag for the following page.

Hard rules:
- Maximum 60 characters. Count carefully. This is a strict limit.
- Include the target keyword naturally, ideally near the start
- No all-caps, excessive punctuation, or clickbait
- No padding or filler words
- Never use em dashes (—) anywhere in the output
- If brand name is provided, append it at the end after a pipe character
- Use the brand name EXACTLY as provided, preserving capitalisation and full name (e.g. "DSB Law Firm" not "dsb" or "DSB")
- Return ONLY the title tag text. No explanation, no quotes, no extra text.

Business context:
- Business type: {business_type}
- Target buyer: {buyer}
- Buyer intent: {intent}
- Recommended title pattern: {title_pattern}
- Avoid: {avoid}

Page details:
- URL: {url}
- Page type: {page_type}
- Target keyword: {keyword}
- Brand name: {brand_name}
- Current H1 (use as topic signal for what this page is actually about): {h1}
- Forbidden phrases: {forbidden_phrases}
- Additional context: {context}

Important: The H1 tells you the current page topic. Use it to ensure the title tag reflects the actual page content and differentiates product variations from each other."""


DESCRIPTION_PROMPT = """You are a senior SEO copywriter with deep knowledge of how different business types require different copy strategies.

Write a meta description for the following page.

Hard rules:
- Maximum 155 characters. Count carefully. This is a strict limit.
- Include the target keyword naturally
- Do not duplicate the title tag
- No all-caps, excessive punctuation, or clickbait
- Never use em dashes (—) anywhere in the output. Use a comma or rewrite the sentence instead.
- Use the brand name EXACTLY as provided, preserving capitalisation and full name
- Return ONLY the meta description text. No explanation, no quotes, no extra text.

Business context:
- Business type: {business_type}
- Target buyer: {buyer}
- Buyer intent: {intent}
- Recommended tone: {tone}
- Good CTA examples for this type: {cta_examples}
- Recommended description pattern: {desc_pattern}
- Avoid: {avoid}

Page details:
- URL: {url}
- Page type: {page_type}
- Target keyword: {keyword}
- Brand name: {brand_name}
- Current H1 (use as topic signal for what this page is actually about): {h1}
- Forbidden phrases: {forbidden_phrases}
- Additional context: {context}

Important: The H1 tells you the current page topic. Use it to write a description that accurately reflects the page content and stands out from similar pages on the same site."""



H1_PROMPT = """You are a senior SEO copywriter. Write an optimised H1 tag for the following page.

Hard rules:
- No hard character limit but aim for under 70 characters
- Include the target keyword naturally, ideally near the start
- Do NOT include the brand name (H1 is on-page copy, brand is not needed)
- No all-caps, excessive punctuation, or clickbait
- Never use em dashes (—) anywhere in the output
- Do not duplicate the title tag word for word — the H1 should be distinct
- Return ONLY the H1 text. No explanation, no quotes, no extra text.

Business context:
- Business type: {business_type}
- Target buyer: {buyer}
- Buyer intent: {intent}
- Recommended tone: {tone}
- Avoid: {avoid}

Page details:
- URL: {url}
- Page type: {page_type}
- Target keyword: {keyword}
- Current H1 (use as reference to improve on, not to copy): {h1}
- Forbidden phrases: {forbidden_phrases}
- Additional context: {context}

Important: The current H1 shows what topic the page covers. Your job is to improve it by making it more specific, keyword-focused, and aligned with what the target buyer is searching for. Do not produce the same H1 unless it is already optimal."""

def _build_prompt(template: str, url: str, keyword: str, page_type: str,
                  brand_name: str, forbidden_phrases: str, context: str,
                  business_type: str = "general", h1: str = "") -> str:
    btype = business_type.lower().strip()
    bcontext = BUSINESS_TYPE_CONTEXT.get(btype, BUSINESS_TYPE_CONTEXT["general"])
    return template.format(
        url=url,
        keyword=keyword,
        page_type=page_type,
        brand_name=brand_name or "N/A",
        forbidden_phrases=forbidden_phrases or "None",
        context=context or "None",
        h1=h1 or "Not provided",
        business_type=btype,
        buyer=bcontext["buyer"],
        intent=bcontext["intent"],
        tone=bcontext["tone"],
        cta_examples=bcontext["cta_examples"],
        avoid=bcontext["avoid"],
        title_pattern=bcontext["title_pattern"],
        desc_pattern=bcontext["desc_pattern"]
    )


# ── Claude ────────────────────────────────────────────────────────────────────
def generate_copy_claude(api_key: str, url: str, keyword: str, page_type: str = "general",
                         brand_name: str = "", forbidden_phrases: str = "", context: str = "",
                         business_type: str = "general", h1: str = "") -> dict:
    client = anthropic.Anthropic(api_key=api_key)

    def call(template):
        msg = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=256,
            messages=[{"role": "user", "content": _build_prompt(template, url, keyword, page_type, brand_name, forbidden_phrases, context, business_type, h1)}]
        )
        return msg.content[0].text.strip()

    return {"title": _sanitise(call(TITLE_PROMPT), brand_name), "description": _sanitise(call(DESCRIPTION_PROMPT), brand_name), "h1_optimised": _sanitise(call(H1_PROMPT))}


# ── OpenAI ────────────────────────────────────────────────────────────────────
def generate_copy_openai(api_key: str, url: str, keyword: str, page_type: str = "general",
                         brand_name: str = "", forbidden_phrases: str = "", context: str = "",
                         business_type: str = "general", h1: str = "") -> dict:
    client = openai.OpenAI(api_key=api_key)

    def call(template):
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            max_tokens=256,
            messages=[{"role": "user", "content": _build_prompt(template, url, keyword, page_type, brand_name, forbidden_phrases, context, business_type, h1)}]
        )
        return resp.choices[0].message.content.strip()

    return {"title": _sanitise(call(TITLE_PROMPT), brand_name), "description": _sanitise(call(DESCRIPTION_PROMPT), brand_name), "h1_optimised": _sanitise(call(H1_PROMPT))}


# ── Gemini ────────────────────────────────────────────────────────────────────
def generate_copy_gemini(api_key: str, url: str, keyword: str, page_type: str = "general",
                         brand_name: str = "", forbidden_phrases: str = "", context: str = "",
                         business_type: str = "general", h1: str = "") -> dict:
    client = google_genai.Client(api_key=api_key)

    def call(template):
        resp = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=_build_prompt(template, url, keyword, page_type, brand_name, forbidden_phrases, context, business_type, h1)
        )
        return resp.text.strip()

    return {"title": _sanitise(call(TITLE_PROMPT), brand_name), "description": _sanitise(call(DESCRIPTION_PROMPT), brand_name), "h1_optimised": _sanitise(call(H1_PROMPT))}


# ── Mistral ───────────────────────────────────────────────────────────────────
def generate_copy_mistral(api_key: str, url: str, keyword: str, page_type: str = "general",
                          brand_name: str = "", forbidden_phrases: str = "", context: str = "",
                          business_type: str = "general", h1: str = "") -> dict:
    client = Mistral(api_key=api_key)

    def call(template):
        resp = client.chat.complete(
            model="mistral-small-latest",
            max_tokens=256,
            messages=[{"role": "user", "content": _build_prompt(template, url, keyword, page_type, brand_name, forbidden_phrases, context, business_type, h1)}]
        )
        return resp.choices[0].message.content.strip()

    return {"title": _sanitise(call(TITLE_PROMPT), brand_name), "description": _sanitise(call(DESCRIPTION_PROMPT), brand_name), "h1_optimised": _sanitise(call(H1_PROMPT))}


# ── Groq ──────────────────────────────────────────────────────────────────────
def generate_copy_groq(api_key: str, url: str, keyword: str, page_type: str = "general",
                       brand_name: str = "", forbidden_phrases: str = "", context: str = "",
                       business_type: str = "general", h1: str = "") -> dict:
    client = Groq(api_key=api_key)

    def call(template):
        resp = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            max_tokens=256,
            messages=[{"role": "user", "content": _build_prompt(template, url, keyword, page_type, brand_name, forbidden_phrases, context, business_type, h1)}]
        )
        return resp.choices[0].message.content.strip()

    return {"title": _sanitise(call(TITLE_PROMPT), brand_name), "description": _sanitise(call(DESCRIPTION_PROMPT), brand_name), "h1_optimised": _sanitise(call(H1_PROMPT))}


# ── Router ────────────────────────────────────────────────────────────────────
PROVIDERS = {
    "Claude": generate_copy_claude,
    "OpenAI": generate_copy_openai,
    "Gemini (free)": generate_copy_gemini,
    "Mistral (free tier)": generate_copy_mistral,
    "Groq (free tier)": generate_copy_groq,
}

def generate_copy(provider: str, api_key: str, **kwargs) -> dict:
    fn = PROVIDERS.get(provider)
    if not fn:
        raise ValueError(f"Unknown provider: {provider}")
    return fn(api_key, **kwargs)
