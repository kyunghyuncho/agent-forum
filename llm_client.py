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
        
        clean_content = clean_content.strip()
        
        # Try to extract JSON object/array if there's extra text
        # Find the first { or [ and last } or ]
        start_obj = clean_content.find('{')
        start_arr = clean_content.find('[')
        
        if start_obj == -1 and start_arr == -1:
            logger.error(f"No JSON object or array found in response: {content[:500]}")
            return None
        
        if start_arr == -1 or (start_obj != -1 and start_obj < start_arr):
            # Object
            start = start_obj
            end = clean_content.rfind('}')
            if end == -1:
                logger.error(f"No closing brace found in response: {content[:500]}")
                return None
            clean_content = clean_content[start:end+1]
        else:
            # Array
            start = start_arr
            end = clean_content.rfind(']')
            if end == -1:
                logger.error(f"No closing bracket found in response: {content[:500]}")
                return None
            clean_content = clean_content[start:end+1]
            
        try:
            return json.loads(clean_content)
        except json.JSONDecodeError as e:
            # Try to fix common issues
            try:
                # Sometimes LLMs output control characters that break JSON
                import re
                # Remove control characters except \n, \r, \t
                fixed_content = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', clean_content)
                return json.loads(fixed_content)
            except json.JSONDecodeError:
                logger.error(f"Failed to parse JSON response: {content[:1000]}... Error: {e}")
                return None

llm_client = LLMClient()
