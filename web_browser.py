"""
Web Browser Module for LocalBBS Agents

Provides controlled web browsing capability for agents to fetch and summarize
content from URLs. Supports two safety modes:
1. Allowlist mode (default): Only allow URLs from a curated list of trusted domains
2. Safe Browsing mode: Use Google Safe Browsing API to check URL safety
"""

import logging
import socket
import ipaddress
from urllib.parse import urlparse, quote_plus, parse_qs
from typing import Optional
import hashlib
import time

import httpx
from bs4 import BeautifulSoup

from config import settings

logger = logging.getLogger(__name__)

# Default allowed domains - curated list of factual sources
DEFAULT_ALLOWED_DOMAINS = [
    "wikipedia.org",
    "arxiv.org",
    "pubmed.ncbi.nlm.nih.gov",
    "plato.stanford.edu",
    "who.int",
    "nature.com",
    "science.org",
    "reuters.com",
    "apnews.com",
    "bbc.com",
]

# Allowed TLDs - entire top-level domains that are trusted (used in allowlist mode)
ALLOWED_TLDS = [
    ".gov",   # Government sites (US and others like .gov.uk)
    ".edu",   # Educational institutions
]

# Always blocked domains (applied in all modes)
DEFAULT_BLOCKED_DOMAINS = [
    "localhost",
    "127.0.0.1",
    "0.0.0.0",
]

# Maximum characters of content to pass to LLM for summarization
MAX_CONTENT_FOR_SUMMARY = 8000

# Google Safe Browsing API endpoint
SAFE_BROWSING_API_URL = "https://safebrowsing.googleapis.com/v4/threatMatches:find"

# Cache for Safe Browsing results (URL hash -> (is_safe, timestamp))
_safe_browsing_cache: dict[str, tuple[bool, float]] = {}
CACHE_TTL_SECONDS = 3600  # 1 hour cache


class WebBrowser:
    """
    Controlled web browser for fetching and summarizing web pages.
    
    Supports two safety modes:
    - "allowlist": Only allows access to a curated list of trusted domains (default)
    - "safebrowsing": Uses Google Safe Browsing API to check URL safety
    """

    def __init__(
        self,
        timeout: int = 10,
        max_content_length: int = 50000,
        allowed_domains: list = None,
        safety_mode: str = None,
        safe_browsing_api_key: str = None,
    ):
        self.timeout = timeout
        self.max_content_length = max_content_length
        self.allowed_domains = allowed_domains or DEFAULT_ALLOWED_DOMAINS
        self.blocked_domains = getattr(settings, 'WEB_BROWSE_BLOCKED_DOMAINS', DEFAULT_BLOCKED_DOMAINS)
        
        # Safety mode: "allowlist" or "safebrowsing"
        self.safety_mode = safety_mode or getattr(settings, 'WEB_BROWSE_SAFETY_MODE', 'allowlist')
        self.safe_browsing_api_key = safe_browsing_api_key or getattr(settings, 'GOOGLE_SAFE_BROWSING_API_KEY', '')
        
        # Validate configuration
        if self.safety_mode == "safebrowsing" and not self.safe_browsing_api_key:
            logger.warning("Safe Browsing mode enabled but no API key provided. Falling back to allowlist mode.")
            self.safety_mode = "allowlist"

    def _is_blocked_domain(self, domain: str) -> bool:
        """Check if domain is in the always-blocked list."""
        for blocked in self.blocked_domains:
            if domain == blocked or domain.endswith("." + blocked):
                return True
        return False

    def _check_safe_browsing(self, url: str) -> tuple[bool, Optional[str]]:
        """
        Check URL against Google Safe Browsing API.
        
        Returns: (is_safe: bool, error_message: str or None)
        """
        if not self.safe_browsing_api_key:
            return False, "Google Safe Browsing API key not configured"
        
        # Check cache first
        url_hash = hashlib.sha256(url.encode()).hexdigest()
        if url_hash in _safe_browsing_cache:
            is_safe, timestamp = _safe_browsing_cache[url_hash]
            if time.time() - timestamp < CACHE_TTL_SECONDS:
                logger.debug(f"Safe Browsing cache hit for {url}: safe={is_safe}")
                return is_safe, None if is_safe else "URL flagged as unsafe (cached)"
        
        try:
            api_url = f"{SAFE_BROWSING_API_URL}?key={self.safe_browsing_api_key}"
            
            payload = {
                "client": {
                    "clientId": "localbbs",
                    "clientVersion": "1.0.0"
                },
                "threatInfo": {
                    "threatTypes": [
                        "MALWARE",
                        "SOCIAL_ENGINEERING",
                        "UNWANTED_SOFTWARE",
                        "POTENTIALLY_HARMFUL_APPLICATION"
                    ],
                    "platformTypes": ["ANY_PLATFORM"],
                    "threatEntryTypes": ["URL"],
                    "threatEntries": [{"url": url}]
                }
            }
            
            with httpx.Client(timeout=5) as client:
                response = client.post(api_url, json=payload)
                response.raise_for_status()
                
                result = response.json()
                
                # If matches is present, the URL is unsafe
                if result.get("matches"):
                    threat_types = [m.get("threatType", "UNKNOWN") for m in result["matches"]]
                    logger.warning(f"URL {url} flagged by Safe Browsing: {threat_types}")
                    _safe_browsing_cache[url_hash] = (False, time.time())
                    return False, f"URL flagged as unsafe: {', '.join(threat_types)}"
                
                # No matches = URL is safe
                _safe_browsing_cache[url_hash] = (True, time.time())
                return True, None
                
        except httpx.HTTPStatusError as e:
            logger.error(f"Safe Browsing API HTTP error: {e}")
            # On API error, fail open but log warning (don't block access)
            return True, None
        except Exception as e:
            logger.error(f"Safe Browsing API error: {e}")
            # On error, fail open but log warning
            return True, None

    def is_allowed_by_allowlist(self, url: str) -> tuple[bool, str]:
        """
        Check if URL domain is in the allowlist or has an allowed TLD.
        
        Returns: (is_allowed: bool, error_message: str or None)
        """
        try:
            parsed = urlparse(url)
            
            # Must be http or https
            if parsed.scheme not in ("http", "https"):
                return False, "Invalid URL scheme (must be http or https)"
            
            # Extract domain
            domain = parsed.netloc.lower()
            
            # Remove port if present
            if ":" in domain:
                domain = domain.split(":")[0]
            
            # Check blocked domains first
            if self._is_blocked_domain(domain):
                return False, f"Domain '{domain}' is blocked"
            
            # Check against allowed TLDs (.gov, .edu, etc.)
            for tld in ALLOWED_TLDS:
                if domain.endswith(tld):
                    return True, None
            
            # Check against allowlist (match domain or subdomain)
            for allowed in self.allowed_domains:
                if domain == allowed or domain.endswith("." + allowed):
                    return True, None
            
            allowed_list = ", ".join(self.allowed_domains[:5]) + ", .gov, .edu, ..."
            return False, f"Domain '{domain}' not in allowed list. Permitted sources: {allowed_list}"
            
        except Exception as e:
            return False, f"Invalid URL: {str(e)}"

    def is_allowed(self, url: str) -> tuple[bool, str]:
        """
        Check if URL is allowed based on the configured safety mode.
        
        Returns: (is_allowed: bool, error_message: str or None)
        """
        try:
            parsed = urlparse(url)
            
            # Must be http or https
            if parsed.scheme not in ("http", "https"):
                return False, "Invalid URL scheme (must be http or https)"
            
            # Extract domain
            domain = parsed.netloc.lower()
            if ":" in domain:
                domain = domain.split(":")[0]
            
            # Always check blocked domains first
            if self._is_blocked_domain(domain):
                return False, f"Domain '{domain}' is blocked"
            
            # Use appropriate safety check based on mode
            if self.safety_mode == "safebrowsing":
                return self._check_safe_browsing(url)
            else:
                # Default to allowlist mode
                return self.is_allowed_by_allowlist(url)
            
        except Exception as e:
            return False, f"Invalid URL: {str(e)}"

    def _is_safe_ip(self, hostname: str) -> tuple[bool, str]:
        """
        Check if a hostname resolves to a safe (non-private) IP address.
        Prevents SSRF attacks targeting internal networks.
        
        Returns: (is_safe: bool, error_message: str or None)
        """
        try:
            # Resolve hostname to IP addresses
            addr_info = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC)
            
            for family, _, _, _, sockaddr in addr_info:
                ip_str = sockaddr[0]
                
                try:
                    ip = ipaddress.ip_address(ip_str)
                except ValueError:
                    continue
                
                # Block private, loopback, link-local, and reserved addresses
                if ip.is_private:
                    return False, f"Blocked: '{hostname}' resolves to private IP {ip_str}"
                if ip.is_loopback:
                    return False, f"Blocked: '{hostname}' resolves to loopback IP {ip_str}"
                if ip.is_link_local:
                    return False, f"Blocked: '{hostname}' resolves to link-local IP {ip_str}"
                if ip.is_reserved:
                    return False, f"Blocked: '{hostname}' resolves to reserved IP {ip_str}"
                if ip.is_multicast:
                    return False, f"Blocked: '{hostname}' resolves to multicast IP {ip_str}"
                
                # Additional check for IPv4 mapped IPv6 addresses
                if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
                    mapped_ip = ip.ipv4_mapped
                    if mapped_ip.is_private or mapped_ip.is_loopback or mapped_ip.is_link_local:
                        return False, f"Blocked: '{hostname}' resolves to unsafe IPv4-mapped address {ip_str}"
            
            return True, None
            
        except socket.gaierror as e:
            return False, f"DNS resolution failed for '{hostname}': {str(e)}"
        except Exception as e:
            return False, f"IP validation failed for '{hostname}': {str(e)}"

    def fetch(self, url: str) -> dict:
        """
        Fetch a URL and extract main text content.
        
        Returns: {
            "success": bool,
            "content": str,
            "title": str,
            "error": str or None
        }
        """
        # Validate URL format
        if not url or not isinstance(url, str):
            return {
                "success": False,
                "content": "",
                "title": "",
                "error": "No URL provided",
            }

        url = url.strip()
        
        # Check allowlist
        is_allowed, error = self.is_allowed(url)
        if not is_allowed:
            return {
                "success": False,
                "content": "",
                "title": "",
                "error": error,
            }
        
        # SSRF protection: verify hostname doesn't resolve to internal IPs
        parsed = urlparse(url)
        is_safe, error = self._is_safe_ip(parsed.netloc.split(":")[0])
        if not is_safe:
            return {
                "success": False,
                "content": "",
                "title": "",
                "error": error,
            }

        try:
            with httpx.Client(
                timeout=self.timeout,
                follow_redirects=True,
                max_redirects=5,
            ) as client:
                response = client.get(
                    url,
                    headers={
                        "User-Agent": "LocalBBS/1.0 (https://github.com/agent-forum; Educational AI Research Project)",
                        "Accept": "text/html,text/plain,application/xhtml+xml",
                        "Accept-Language": "en-US,en;q=0.9",
                    },
                )
                
                # Check if redirected to a non-allowed domain
                final_url = str(response.url)
                is_allowed, error = self.is_allowed(final_url)
                if not is_allowed:
                    return {
                        "success": False,
                        "content": "",
                        "title": "",
                        "error": f"Redirected to non-allowed domain: {error}",
                    }
                
                # SSRF protection: also check final URL after redirects
                final_parsed = urlparse(final_url)
                is_safe, error = self._is_safe_ip(final_parsed.netloc.split(":")[0])
                if not is_safe:
                    return {
                        "success": False,
                        "content": "",
                        "title": "",
                        "error": f"Redirected to unsafe IP: {error}",
                    }
                
                response.raise_for_status()

                content_type = response.headers.get("content-type", "").lower()
                if "text/html" not in content_type and "text/plain" not in content_type:
                    return {
                        "success": False,
                        "content": "",
                        "title": "",
                        "error": f"Unsupported content type: {content_type}",
                    }

                # Don't truncate HTML before parsing - it can break tags
                # We'll truncate the extracted text instead
                html = response.text

                # Parse HTML
                soup = BeautifulSoup(html, "html.parser")

                # Extract title
                title = ""
                title_tag = soup.find("title")
                if title_tag:
                    title = title_tag.get_text(strip=True)

                # Remove unwanted elements
                for tag in soup(
                    ["script", "style", "nav", "header", "footer", "aside", "noscript", "iframe"]
                ):
                    tag.decompose()

                # Try to find main content (after removing unwanted elements)
                main_content = soup.find("main") or soup.find("article") or soup.find("body")
                if main_content:
                    text = main_content.get_text(separator="\n", strip=True)
                else:
                    text = soup.get_text(separator="\n", strip=True)

                # Clean up whitespace
                lines = [line.strip() for line in text.split("\n") if line.strip()]
                text = "\n".join(lines)

                # Truncate if too long
                if len(text) > self.max_content_length:
                    text = text[:self.max_content_length] + "\n\n[... content truncated ...]"

                if not text:
                    return {
                        "success": False,
                        "content": "",
                        "title": title,
                        "error": "Page returned no readable content",
                    }

                return {
                    "success": True,
                    "content": text,
                    "title": title,
                    "error": None,
                }

        except httpx.TimeoutException:
            return {
                "success": False,
                "content": "",
                "title": "",
                "error": f"Request timed out after {self.timeout} seconds",
            }
        except httpx.HTTPStatusError as e:
            return {
                "success": False,
                "content": "",
                "title": "",
                "error": f"HTTP {e.response.status_code}: {e.response.reason_phrase}",
            }
        except httpx.RequestError as e:
            return {
                "success": False,
                "content": "",
                "title": "",
                "error": f"Request failed: {str(e)}",
            }
        except Exception as e:
            logger.exception(f"Unexpected error fetching {url}")
            return {
                "success": False,
                "content": "",
                "title": "",
                "error": f"Unexpected error: {str(e)}",
            }

    def summarize(self, content: str, url: str, reason: str, llm_client, api_key: str = None) -> str:
        """
        Use LLM to summarize the fetched content.
        
        Args:
            content: The extracted text content
            url: The source URL
            reason: Why the agent wanted to browse this page
            llm_client: The LLM client instance
            api_key: Optional API key to use for the LLM call
            
        Returns: A summary string
        """
        if not content:
            return "(No content to summarize)"

        prompt = f"""Summarize the following web page content concisely (max 500 words).
Focus on information relevant to: {reason}

URL: {url}
Content:
{content[:MAX_CONTENT_FOR_SUMMARY]}

Provide a factual, objective summary. Include key facts, statistics, and conclusions.
If the content seems irrelevant to the stated reason, mention that briefly."""

        messages = [{"role": "user", "content": prompt}]
        
        try:
            summary = llm_client.chat_completion(messages, temperature=0.3, api_key=api_key)
            return summary or "(Failed to generate summary)"
        except Exception as e:
            logger.error(f"Error summarizing content: {e}")
            return f"(Summarization failed: {str(e)})"

    def get_allowed_domains_description(self) -> str:
        """Get a human-readable description of allowed domains/safety mode for prompts."""
        if self.safety_mode == "safebrowsing":
            return "You can browse ANY URL - all URLs are checked for safety via Google Safe Browsing (malware, phishing, and harmful sites are blocked)"
        
        # Allowlist mode description
        domain_names = {
            "wikipedia.org": "Wikipedia",
            "arxiv.org": "arXiv",
            "pubmed.ncbi.nlm.nih.gov": "PubMed",
            "plato.stanford.edu": "Stanford Encyclopedia of Philosophy",
            "who.int": "WHO",
            "nature.com": "Nature",
            "science.org": "Science",
            "reuters.com": "Reuters",
            "apnews.com": "AP News",
            "bbc.com": "BBC",
        }
        names = [domain_names.get(d, d) for d in self.allowed_domains]
        # Add TLD descriptions
        names.extend(["any .gov site", "any .edu site"])
        return "Allowed sources: " + ", ".join(names)

    def search(self, query: str, num_results: int = 5) -> dict:
        """
        Search using DuckDuckGo and return top results.
        
        Args:
            query: The search query
            num_results: Maximum number of results to return
            
        Returns: {
            "success": bool,
            "results": list of {"title": str, "url": str, "snippet": str},
            "error": str or None
        }
        """
        if not query or not isinstance(query, str):
            return {
                "success": False,
                "results": [],
                "error": "No search query provided",
            }
        
        query = query.strip()
        if len(query) < 2:
            return {
                "success": False,
                "results": [],
                "error": "Search query too short",
            }
        
        try:
            # Use DuckDuckGo HTML search (no API key needed)
            search_url = f"https://html.duckduckgo.com/html/?q={quote_plus(query)}"
            
            with httpx.Client(timeout=self.timeout, follow_redirects=True) as client:
                response = client.get(
                    search_url,
                    headers={
                        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                        "Accept-Language": "en-US,en;q=0.9",
                    },
                )
                
                # Handle 202 Accepted - DuckDuckGo sometimes returns this for verification
                # Retry after a short delay
                if response.status_code == 202:
                    logger.info("DuckDuckGo returned 202 Accepted, retrying after delay...")
                    time.sleep(2)
                    response = client.get(
                        search_url,
                        headers={
                            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                            "Accept-Language": "en-US,en;q=0.9",
                        },
                    )
                
                # If still not 200, try raising for status
                if response.status_code != 200:
                    logger.warning(f"DuckDuckGo returned status {response.status_code}")
                    if response.status_code == 202:
                        return {
                            "success": False,
                            "results": [],
                            "error": "Search service busy (202 Accepted). Please try again in a moment.",
                        }
                    response.raise_for_status()
                
                soup = BeautifulSoup(response.text, "html.parser")
                
                # Check for rate limiting or captcha pages
                page_text = soup.get_text().lower()
                if "blocked" in page_text or "captcha" in page_text or "robot" in page_text:
                    logger.warning("DuckDuckGo may be rate limiting requests")
                    return {
                        "success": False,
                        "results": [],
                        "error": "Search temporarily blocked (rate limited). Try again later.",
                    }
                
                results = []
                # DuckDuckGo HTML results are in divs with class "result"
                result_divs = soup.select(".result")[:num_results]
                
                for result_div in result_divs:
                    # Title and URL - the title link has class "result__a"
                    title_elem = result_div.select_one("a.result__a")
                    if not title_elem:
                        continue
                    
                    title = title_elem.get_text(strip=True)
                    
                    # Skip results that look like captcha/rate limit responses
                    if title.lower() in ["accepted", "blocked", "captcha"]:
                        continue
                    
                    # DuckDuckGo uses redirect URLs like /l/?...&uddg=<encoded_url>
                    # Extract actual URL from the uddg query parameter
                    raw_url = title_elem.get("href", "")
                    url = ""
                    if raw_url:
                        parsed_redirect = urlparse(raw_url)
                        query_params = parse_qs(parsed_redirect.query)
                        if "uddg" in query_params and query_params["uddg"]:
                            url = query_params["uddg"][0]
                        else:
                            # Fallback: use raw URL if uddg not found (and it looks like a real URL)
                            if raw_url.startswith("http"):
                                url = raw_url
                    
                    # Extract snippet
                    snippet_elem = result_div.select_one("a.result__snippet")
                    snippet = snippet_elem.get_text(strip=True) if snippet_elem else ""
                    
                    if title and url:
                        results.append({
                            "title": title,
                            "url": url,
                            "snippet": snippet,
                        })
                
                if not results:
                    # Log the HTML for debugging
                    logger.warning(f"No results found. Page title: {soup.title.string if soup.title else 'None'}")
                    logger.debug(f"Page HTML snippet: {str(soup)[:1000]}")
                    return {
                        "success": False,
                        "results": [],
                        "error": "No search results found",
                    }
                
                return {
                    "success": True,
                    "results": results,
                    "error": None,
                }
                
        except httpx.TimeoutException:
            return {
                "success": False,
                "results": [],
                "error": f"Search timed out after {self.timeout} seconds",
            }
        except httpx.HTTPStatusError as e:
            return {
                "success": False,
                "results": [],
                "error": f"Search failed: HTTP {e.response.status_code}",
            }
        except Exception as e:
            logger.exception(f"Unexpected error during search: {e}")
            return {
                "success": False,
                "results": [],
                "error": f"Search failed: {str(e)}",
            }

    def format_search_results(self, query: str, results: list) -> str:
        """Format search results as markdown for TEMP.md, indicating which URLs can be browsed."""
        if not results:
            return f"\n\n## Search Results for: \"{query}\"\nNo results found.\n"
        
        output = f"\n\n## Search Results for: \"{query}\"\n\n"
        browsable_count = 0
        for i, r in enumerate(results, 1):
            url = r['url']
            is_allowed, _ = self.is_allowed(url)
            status = "✓ BROWSABLE" if is_allowed else "✗ not browsable"
            if is_allowed:
                browsable_count += 1
            
            output += f"{i}. **{r['title']}** [{status}]\n"
            output += f"   {r['snippet']}\n"
            output += f"   **URL:** {url}\n"
            output += f"   **Ready-to-use link:** [{r['title']}]({url})\n\n"
        
        output += "\n**IMPORTANT:** When you POST, copy the 'Ready-to-use link' above directly into your post to cite sources!\n\n"
        
        if self.safety_mode == "safebrowsing":
            if browsable_count > 0:
                output += f"_You can BROWSE the URLs marked ✓ BROWSABLE ({browsable_count} available). URLs are verified safe by Google Safe Browsing._\n"
            else:
                output += "_None of these URLs passed safety verification. You can still use the snippets above in your response._\n"
        else:
            if browsable_count > 0:
                output += f"_You can BROWSE the URLs marked ✓ BROWSABLE ({browsable_count} available). Allowed sources: Wikipedia, arXiv, PubMed, .gov, .edu sites, and major news outlets._\n"
            else:
                output += "_None of these URLs are from allowed sources for browsing. You can still use the snippets above in your response._\n"
        return output


# Singleton instance
web_browser = WebBrowser()
