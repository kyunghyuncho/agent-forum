import openai
from config import settings
import json
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class LLMClient:
    def __init__(self):
        self.reinitialize()

    def reinitialize(self):
        """Reinitialize the client with current settings (useful when API key changes)."""
        self.client = openai.OpenAI(
            base_url=settings.OPENROUTER_BASE_URL,
            api_key=settings.OPENROUTER_API_KEY
        )
        self.model = settings.MODEL_NAME

    def chat_completion(self, messages, temperature=0.7):
        """
        Wrapper for chat completion.
        """
        try:
            # check if api key is set
            if not settings.OPENROUTER_API_KEY:
                logger.warning("OPENROUTER_API_KEY is not set. LLM calls will fail.")
                return None

            response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=temperature,
            )
            return response.choices[0].message.content
        except Exception as e:
            logger.error(f"LLM API Error: {e}")
            return None

    def get_json_response(self, messages, temperature=0.7):
        """
        Helper that ensures the response is parsed as JSON.
        """
        # Append a clear instruction for JSON if not present (optional, but good practice)
        # We rely on the prompt templates to be strong, but a little nudge helps.
        
        content = self.chat_completion(messages, temperature)
        if not content:
            return None
        
        # Clean up markdown code blocks if present
        clean_content = content.strip()
        if clean_content.startswith("```json"):
            clean_content = clean_content[7:]
        elif clean_content.startswith("```"):
            clean_content = clean_content[3:]
        
        if clean_content.endswith("```"):
            clean_content = clean_content[:-3]
            
        try:
            return json.loads(clean_content.strip())
        except json.JSONDecodeError:
            logger.error(f"Failed to parse JSON response: {content}")
            return None

llm_client = LLMClient()
