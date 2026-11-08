"""
Wikimedia Commons API client for creating categories and uploading .map files.
Adheres strictly to Wikimedia API policies (User-Agent, maxlag, rate-limiting, bot passwords).
"""

import http.cookiejar
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional, Tuple

from glottography_to_commons.config import Config

logger = logging.getLogger(__name__)


class WikimediaAPIError(Exception):
    """Raised when the MediaWiki API returns an error structure."""
    def __init__(self, code: str, info: str, raw: Optional[dict] = None):
        super().__init__(f"MediaWiki API error [{code}]: {info}")
        self.code = code
        self.info = info
        self.raw = raw or {}


class WikimediaClient:
    """Client for Wikimedia Commons MediaWiki Action API."""

    def __init__(self, config: Config):
        self.config = config
        self.cookie_jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cookie_jar)
        )
        self.csrf_token: Optional[str] = None
        self.is_authenticated = False
        self._last_request_time = 0.0

    def _rate_limit_wait(self) -> None:
        """Enforce delay between edit requests."""
        elapsed = time.time() - self._last_request_time
        if elapsed < self.config.request_delay:
            wait_time = self.config.request_delay - elapsed
            logger.debug("Rate limiting: sleeping %.2f seconds", wait_time)
            time.sleep(wait_time)
        self._last_request_time = time.time()

    def _api_request(
        self,
        method: str,
        params: Dict[str, Any],
        data: Optional[Dict[str, Any]] = None,
        retries: int = 5
    ) -> Dict[str, Any]:
        """
        Executes a MediaWiki API request with exponential backoff, maxlag handling,
        and User-Agent compliance.
        """
        self._rate_limit_wait()

        # Always request JSON format
        params = dict(params)
        params["format"] = "json"
        params["formatversion"] = "2"

        url = f"{self.config.commons_api_url}?{urllib.parse.urlencode(params)}"
        headers = {
            "User-Agent": self.config.user_agent,
            "Accept": "application/json",
        }

        body = None
        if data is not None:
            body = urllib.parse.urlencode(data).encode("utf-8")
            headers["Content-Type"] = "application/x-www-form-urlencoded"

        req = urllib.request.Request(url, data=body, headers=headers, method=method)

        for attempt in range(1, retries + 1):
            try:
                with self.opener.open(req, timeout=45) as resp:
                    resp_bytes = resp.read()
                    resp_json = json.loads(resp_bytes.decode("utf-8"))

                    # Handle MediaWiki maxlag error
                    if "error" in resp_json:
                        err = resp_json["error"]
                        code = err.get("code", "unknown")
                        info = err.get("info", "")

                        if code == "maxlag":
                            wait = 5.0
                            # Try to extract suggested wait from headers or response
                            logger.warning("Wikimedia maxlag hit. Backing off for %.1f seconds (attempt %d/%d)", wait, attempt, retries)
                            time.sleep(wait)
                            continue

                        if code in ("readonly", "ratelimited") and attempt < retries:
                            wait = 2.0 ** attempt
                            logger.warning("MediaWiki [%s]: %s. Backing off %.1f seconds", code, info, wait)
                            time.sleep(wait)
                            continue

                        raise WikimediaAPIError(code, info, resp_json)

                    return resp_json

            except urllib.error.HTTPError as e:
                logger.warning("HTTP %d from Commons API (attempt %d/%d)", e.code, attempt, retries)
                if e.code in (429, 500, 502, 503, 504) and attempt < retries:
                    retry_after = e.headers.get("Retry-After")
                    wait = float(retry_after) if retry_after else (2.0 ** attempt)
                    time.sleep(wait)
                else:
                    raise

            except (urllib.error.URLError, TimeoutError) as e:
                logger.warning("Network error contacting Commons API: %s (attempt %d/%d)", e, attempt, retries)
                if attempt < retries:
                    time.sleep(2.0 ** attempt)
                else:
                    raise

        raise RuntimeError(f"Failed Wikimedia API request after {retries} retries: {url}")

    def login(self) -> bool:
        """
        Authenticate using MediaWiki Bot Passwords.
        Returns True on successful login.
        """
        if self.config.dry_run:
            logger.info("[DRY RUN] Skipping Wikimedia login.")
            self.is_authenticated = True
            return True

        if not self.config.commons_username or not self.config.commons_bot_password:
            logger.warning(
                "COMMONS_USERNAME or COMMONS_BOT_PASSWORD not configured. "
                "Running in unauthenticated / read-only mode."
            )
            return False

        logger.info("Logging into Wikimedia Commons as %s...", self.config.commons_username)

        # 1. Get login token
        res = self._api_request("GET", {"action": "query", "meta": "tokens", "type": "login"})
        login_token = res.get("query", {}).get("tokens", {}).get("logintoken")
        if not login_token:
            raise RuntimeError("Failed to retrieve login token from Wikimedia Commons")

        # 2. Execute login
        login_data = {
            "action": "login",
            "lgname": self.config.commons_username,
            "lgpassword": self.config.commons_bot_password,
            "lgtoken": login_token,
        }
        login_res = self._api_request("POST", {"action": "login"}, data=login_data)
        login_status = login_res.get("login", {}).get("result")

        if login_status != "Success":
            reason = login_res.get("login", {}).get("reason", login_status)
            raise WikimediaAPIError("login_failed", f"Failed to log into Wikimedia Commons: {reason}", login_res)

        logger.info("Successfully authenticated to Wikimedia Commons!")
        self.is_authenticated = True

        # 3. Refresh CSRF token after login
        self.get_csrf_token(force_refresh=True)
        return True

    def get_csrf_token(self, force_refresh: bool = False) -> str:
        """Retrieve and cache a CSRF token for editing."""
        if self.csrf_token and not force_refresh:
            return self.csrf_token

        if self.config.dry_run:
            self.csrf_token = "dry-run-csrf-token"
            return self.csrf_token

        res = self._api_request("GET", {"action": "query", "meta": "tokens", "type": "csrf"})
        token = res.get("query", {}).get("tokens", {}).get("csrftoken")
        if not token:
            raise RuntimeError("Failed to retrieve CSRF token from Wikimedia Commons")

        self.csrf_token = token
        return self.csrf_token

    def page_exists(self, title: str) -> Tuple[bool, Optional[str]]:
        """
        Check if a page exists on Commons.
        Returns (exists: bool, content: Optional[str]).
        """
        if self.config.dry_run:
            return False, None

        params = {
            "action": "query",
            "titles": title,
            "prop": "revisions",
            "rvprop": "content",
            "rvslots": "main",
        }
        res = self._api_request("GET", params)
        pages = res.get("query", {}).get("pages", [])
        if not pages:
            return False, None

        page = pages[0]
        if page.get("missing", False) or page.get("pageid", 0) <= 0:
            return False, None

        revs = page.get("revisions", [])
        if revs:
            slots = revs[0].get("slots", {})
            content = slots.get("main", {}).get("content")
            return True, content

        return True, None

    def ensure_category(self) -> bool:
        """
        Checks whether the target category ('Category:Map data of languages') exists on Wikimedia Commons.
        If not, creates it as a subcategory of 'Category:Map data by object'.
        """
        cat_title = f"Category:{self.config.category_name}"
        logger.info("Checking existence of category: %s", cat_title)

        if self.config.dry_run:
            logger.info("[DRY RUN] Would check and ensure category: %s", cat_title)
            return True

        exists, _ = self.page_exists(cat_title)
        if exists:
            logger.info("Category %s already exists on Commons.", cat_title)
            return True

        # Wikitext definition
        wikitext = (
            "{{CategoryMapData}}\n"
            "Map data files (.map) in the Data: namespace representing geographical "
            "speaker areas and distribution boundaries for the world's languages.\n\n"
            f"[[Category:{self.config.parent_category}|{self.config.parent_sortkey}]]\n"
        )

        summary = (
            f"Creating subcategory [[Category:{self.config.category_name}]] "
            f"under [[Category:{self.config.parent_category}]] for Glottography language maps"
        )

        if self.config.dry_run:
            logger.info("[DRY RUN] Would create category: %s with wikitext:\n%s", cat_title, wikitext)
            return True

        logger.info("Creating category page %s...", cat_title)
        token = self.get_csrf_token()
        edit_data = {
            "action": "edit",
            "title": cat_title,
            "text": wikitext,
            "summary": summary,
            "token": token,
            "bot": "1",
            "assert": "user" if self.is_authenticated else "anon",
            "maxlag": str(self.config.maxlag),
        }

        res = self._api_request("POST", {"action": "edit"}, data=edit_data)
        result = res.get("edit", {}).get("result")
        if result == "Success":
            logger.info("Successfully created %s on Wikimedia Commons!", cat_title)
            return True
        else:
            logger.error("Failed to create category %s: %s", cat_title, res)
            return False

    def ensure_talk_page(self, page_title: str, language_name: str) -> bool:
        """
        Categorizes the Data talk: page under 'Category:Map data of languages'.
        """
        talk_title = page_title.replace("Data:", "Data talk:")
        sort_key = language_name.strip() if language_name else ""
        wikitext = f"[[Category:{self.config.talk_category}|{sort_key}]]"
        summary = f"Categorizing map data under Category:{self.config.talk_category}"

        if self.config.dry_run:
            logger.info("[DRY RUN] Would ensure talk page %s with content: %s", talk_title, wikitext)
            return True

        exists, current_text = self.page_exists(talk_title)
        if exists and current_text and f"Category:{self.config.talk_category}" in current_text:
            return True

        logger.info("Setting talk page category for %s...", talk_title)
        token = self.get_csrf_token()
        edit_data = {
            "action": "edit",
            "title": talk_title,
            "text": wikitext,
            "summary": summary,
            "token": token,
            "bot": "1",
            "assert": "user" if self.is_authenticated else "anon",
            "maxlag": str(self.config.maxlag),
        }
        res = self._api_request("POST", {"action": "edit"}, data=edit_data)
        return res.get("edit", {}).get("result") == "Success"

    def ensure_language_category(self, language_name: str) -> bool:
        """
        Ensures 'Category:Linguistic maps of the <Language> language' exists under
        'Category:Linguistic maps by language mapped'.
        """
        from glottography_to_commons.map_builder import MapBuilder
        lang_cat = MapBuilder.get_language_map_category(language_name)
        cat_title = f"Category:{lang_cat}"
        clean_lang = language_name.strip()
        wikitext = f"[[Category:{self.config.parent_linguistic_category}|{clean_lang}]]\n"
        summary = f"Creating linguistic map subcategory for {language_name} under [[Category:{self.config.parent_linguistic_category}]]"

        if self.config.dry_run:
            logger.info("[DRY RUN] Would ensure language category %s with content: %s", cat_title, wikitext.strip())
            return True

        exists, _ = self.page_exists(cat_title)
        if exists:
            return True

        logger.info("Creating language category page %s...", cat_title)
        token = self.get_csrf_token()
        edit_data = {
            "action": "edit",
            "title": cat_title,
            "text": wikitext,
            "summary": summary,
            "token": token,
            "bot": "1",
            "assert": "user" if self.is_authenticated else "anon",
            "maxlag": str(self.config.maxlag),
        }
        res = self._api_request("POST", {"action": "edit"}, data=edit_data)
        return res.get("edit", {}).get("result") == "Success"

    def upload_map(
        self,
        page_title: str,
        content_json: str,
        summary: str,
        language_name: str = "",
        recreate: bool = False
    ) -> Dict[str, Any]:
        """
        Edits / creates a .map page in the Data: namespace on Wikimedia Commons.
        Also categorizes the talk page under 'Category:Map data of languages'
        and ensures the language category exists.
        """
        if self.config.dry_run:
            logger.info("[DRY RUN] Would upload map to %s (summary: %s)", page_title, summary)
            if language_name:
                self.ensure_language_category(language_name)
                self.ensure_talk_page(page_title, language_name)
            return {
                "result": "Success",
                "page_title": page_title,
                "url": f"https://commons.wikimedia.org/wiki/{urllib.parse.quote(page_title)}",
                "dry_run": True
            }

        # Check existing content
        if not recreate:
            exists, existing_content = self.page_exists(page_title)
            if exists and existing_content:
                # Compare stripped content
                if existing_content.strip() == content_json.strip():
                    logger.info("Page %s already exists with identical content. Skipping.", page_title)
                    return {
                        "result": "Skipped",
                        "page_title": page_title,
                        "url": f"https://commons.wikimedia.org/wiki/{urllib.parse.quote(page_title)}",
                        "reason": "identical_content"
                    }

        logger.info("Uploading map data to %s...", page_title)
        token = self.get_csrf_token()

        edit_data = {
            "action": "edit",
            "title": page_title,
            "text": content_json,
            "summary": summary,
            "token": token,
            "bot": "1",
            "assert": "user" if self.is_authenticated else "anon",
            "maxlag": str(self.config.maxlag),
        }

        res = self._api_request("POST", {"action": "edit"}, data=edit_data)
        edit_info = res.get("edit", {})

        if edit_info.get("result") == "Success":
            logger.info("Successfully uploaded %s to Commons (rev %s)", page_title, edit_info.get("newrevid"))
            if language_name:
                try:
                    self.ensure_language_category(language_name)
                    self.ensure_talk_page(page_title, language_name)
                except Exception as cat_err:
                    logger.warning("Could not set categories/talk page for %s: %s", page_title, cat_err)

            return {
                "result": "Success",
                "page_title": page_title,
                "newrevid": edit_info.get("newrevid"),
                "url": f"https://commons.wikimedia.org/wiki/{urllib.parse.quote(page_title)}",
            }
        else:
            raise WikimediaAPIError("upload_failed", f"Failed to upload {page_title}: {res}", res)
